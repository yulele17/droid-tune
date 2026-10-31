"""adb 定位与执行封装 —— 项目里唯一和 adb 打交道的地方。

设计取舍：本项目**不**用 adbutils / python-adb，原因是实测踩过：
adbutils 自带的 `adb.exe` 单文件复制到别处后执行必然失败（exit 127、无任何输出），
因为官方 adb 还依赖同目录的 `AdbWinApi.dll` / `AdbWinUsbApi.dll`。
与其在依赖里埋一个排查黑洞，不如要求用户装一份官方 platform-tools。

另一个必须在本层解决的历史 bug：Windows 下 `adb shell` 的输出是 CRLF，
包名尾巴会带上 `\\r`，交给 pm 后一律报 `Unknown package`。
因此本模块对所有返回结果统一 `normalize_text()` 清洗，上层拿到的永远是干净字符串。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

# pm disable/enable 的语义结果
ST_OK = "ok"                    # 状态确实改变了
ST_SYSTEM_LOCKED = "locked"     # 被 ROM 标记为不可禁用的系统核心包
ST_NOT_INSTALLED = "missing"    # 这台机器上没装该包
ST_UNKNOWN = "unknown"          # 其它返回，交给上层决定


class AdbNotFoundError(RuntimeError):
    pass


NOT_FOUND_HINT = """\
没找到可用的 adb。

请安装官方 platform-tools（一次性成本，之后所有安卓设备通用）：
  https://developer.android.com/tools/releases/platform-tools
解压后把目录加入 PATH，然后 `adb version` 应能正常打印版本号。

常见误区：复制 adbutils / python-adb 自带的 adb.exe 是不够的，
Windows 下还需同目录的 AdbWinApi.dll 与 AdbWinUsbApi.dll，否则执行无任何输出、退出码 127。
"""


def normalize_text(s: str) -> str:
    """清洗 adb 输出：去掉 CR、首尾空白。所有上层都依赖这个函数，别绕过它。"""
    return s.replace("\r", "").strip()


def find_adb(override: Optional[str] = None) -> Optional[str]:
    """定位 adb 可执行文件。优先级：显式指定 > 环境变量 ADB > PATH > 常见安装位置。"""
    cands: List[str] = []
    if override:
        cands.append(override)
    env_adb = os.environ.get("ADB")
    if env_adb:
        cands.append(env_adb)
    which = shutil.which("adb")
    if which:
        cands.append(which)
    home = os.path.expanduser("~")
    cands += [
        os.path.join(home, "platform-tools", "adb.exe"),
        os.path.join(home, "platform-tools", "adb"),
        os.path.join(home, "AppData", "Local", "Android", "Sdk", "platform-tools", "adb.exe"),
    ]
    for c in cands:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


@dataclass
class PmResult:
    """一次 pm enable/disable 的语义化结果。"""
    package: str
    status: str
    raw: str


class Adb:
    def __init__(self, serial: Optional[str] = None, adb_bin: Optional[str] = None, timeout: int = 30):
        self.serial = serial
        self.timeout = timeout
        self.adb_bin = adb_bin or find_adb()
        if not self.adb_bin:
            raise AdbNotFoundError(NOT_FOUND_HINT)

    # ---------- 底层 ----------
    def _base(self) -> List[str]:
        cmd = [self.adb_bin]
        if self.serial:
            cmd += ["-s", self.serial]
        return cmd

    def run(self, args: Sequence[str], timeout: Optional[int] = None) -> Tuple[int, str, str]:
        """执行 adb 命令，返回 (returncode, stdout, stderr)。已做 CRLF 清洗。"""
        proc = subprocess.run(
            self._base() + list(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout or self.timeout,
            # 关键：切断 stdin。在 bash 的 `while read` 循环里调用 adb，
            # adb 会把重定向给循环的文件当作自己的 stdin 吃掉，导致只读一行就 EOF。
            stdin=subprocess.DEVNULL,
        )
        out = proc.stdout.decode("utf-8", errors="replace")
        err = proc.stderr.decode("utf-8", errors="replace")
        return proc.returncode, normalize_text(out), normalize_text(err)

    def shell(self, cmd: str, timeout: Optional[int] = None) -> str:
        rc, out, err = self.run(["shell", cmd], timeout=timeout)
        return out if rc == 0 else (out + "\n" + err).strip()

    # ---------- 设备 ----------
    def devices(self) -> List[Tuple[str, str]]:
        """返回 [(serial, state)]，state 形如 device / unauthorized / offline。"""
        rc, out, _ = self.run(["devices"])
        res = []
        if rc != 0:
            return res
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2:
                res.append((parts[0], parts[1]))
        return res

    def getprop(self, key: str, default: str = "") -> str:
        val = self.shell(f"getprop {key}")
        return default if val in ("", "getprop: ...") else val

    # ---------- settings ----------
    def settings_get(self, namespace: str, key: str) -> str:
        # settings get global <key> 对未设置的 key 会输出 "null"
        return self.shell(f"settings get {namespace} {key}")

    def settings_put(self, namespace: str, key: str, value: str) -> bool:
        out = self.shell(f"settings put {namespace} {key} {value}")
        return out == ""

    def settings_delete(self, namespace: str, key: str) -> bool:
        out = self.shell(f"settings delete {namespace} {key}")
        return True

    def settings_list(self, namespace: str) -> dict:
        raw = self.shell(f"settings list {namespace}")
        d = {}
        for line in raw.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                d[k.strip()] = v.strip()
        return d

    # ---------- 包管理 ----------
    def list_packages(self, flag: str = "") -> List[str]:
        """flag 可以是 ''（全部）/ '-d'（已禁用）/ '-e'（已启用）/ '-s'（系统应用）/ '-3'（第三方）。"""
        args = ["shell", "pm", "list", "packages"]
        if flag:
            args.append(flag)
        rc, out, _ = self.run(args, timeout=60)
        pkgs = []
        for line in out.splitlines():
            if line.startswith("package:"):
                pkgs.append(normalize_text(line[len("package:"):]))
        return sorted(set(pkgs))

    def set_package_enabled(self, package: str, enabled: bool) -> PmResult:
        """冻结/解冻一个包，返回语义化结果而不是原始字符串。

        实测三种返回（不同 ROM/AOSP 版本措辞略有出入，这里按关键词匹配）：
          Success / new state: disabled-user          → ST_OK
          SecurityException: Cannot disable system... → ST_SYSTEM_LOCKED
          Unknown package / Failure [INSTALL_FAILED]  → ST_NOT_INSTALLED
        """
        package = normalize_text(package)  # 防御：调用方可能传入带 \\r 的包名
        verb = "enable" if enabled else "disable-user --user 0"
        raw = self.shell(f"pm {verb} {package}")
        low = raw.lower()
        if "new state: disabled-user" in low or "new state: enabled" in low or low.startswith("success") or raw == "":
            return PmResult(package, ST_OK, raw)
        if "unknown package" in low or "not found" in low:
            return PmResult(package, ST_NOT_INSTALLED, raw)
        if "cannot disable" in low or "securityexception" in low or "not allowed" in low:
            return PmResult(package, ST_SYSTEM_LOCKED, raw)
        if low == "" and raw == "":
            return PmResult(package, ST_OK, raw)
        return PmResult(package, ST_UNKNOWN, raw)
