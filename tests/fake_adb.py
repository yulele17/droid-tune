"""假 adb：把 adb.run() 换成本地内存实现，用于在没有真机的情况下跑逻辑测试。

之所以不用真 subprocess + 伪脚本：Windows 下 .py / .sh 都不能被 subprocess 直接当可执行文件
召唤（还得处理 shebang 与关联程序），跨平台上很脆。直接替换 run() 更稳。
"""

from typing import Dict, List, Set

from tune.adb import Adb, ST_OK, normalize_text


class FakeAdb(Adb):
    """模拟一台 ROM：部分包可禁、部分被锁、部分没装。"""

    def __init__(self, packages: List[str], locked: Set[str] = None, props: Dict[str, str] = None):
        self.serial = "TEST0001"
        self.adb_bin = "fake"
        self.timeout = 5
        # 设备状态：包名 -> enabled
        self.packages = {p: True for p in packages}
        self.locked = set(locked or [])
        self.props = dict(props or {})
        self.global_settings: Dict[str, str] = {}

    # ---------- 唯一需要覆写的底层方法 ----------
    def run(self, args, timeout=None):
        if args[0] == "devices":
            return 0, "List of devices attached\nTEST0001\tdevice", ""
        if args[0] == "shell":
            return self._shell(" ".join(args[1:]))
        return 0, "", ""

    def _shell(self, cmd: str):
        cmd = normalize_text(cmd)

        if cmd.startswith("pm list packages"):
            parts = cmd.split()
            flag = parts[3] if len(parts) > 3 else ""
            out = []
            for pkg, enabled in sorted(self.packages.items()):
                if flag == "-d" and enabled:
                    continue
                out.append(f"package:{pkg}")
            return 0, "\n".join(out), ""

        if cmd.startswith("pm disable-user --user 0"):
            pkg = cmd.rsplit(" ", 1)[-1]
            if pkg not in self.packages:
                return 0, f"Failure [INSTALL_FAILED]: Unknown package: {pkg}", ""
            if pkg in self.locked:
                return 0, ("Exception occurred while executing 'disable-user': "
                           "java.lang.SecurityException: Cannot disable system packages"), ""
            self.packages[pkg] = False
            return 0, f"Package {pkg} new state: disabled-user", ""

        if cmd.startswith("pm enable"):
            pkg = cmd.rsplit(" ", 1)[-1]
            if pkg not in self.packages:
                return 0, f"Unknown package: {pkg}", ""
            self.packages[pkg] = True
            return 0, f"Package {pkg} new state: enabled", ""

        if cmd.startswith("settings get"):
            _, _, ns, key = cmd.split()
            return 0, self.global_settings.get(key, "null"), ""

        if cmd.startswith("settings put"):
            _, _, ns, key, val = cmd.split()
            self.global_settings[key] = val
            return 0, "", ""

        if cmd.startswith("settings delete"):
            _, _, ns, key = cmd.split()
            self.global_settings.pop(key, None)
            return 0, "", ""

        if cmd.startswith("settings list"):
            _, _, ns = cmd.split()
            return 0, "\n".join(f"{k}={v}" for k, v in self.global_settings.items()), ""

        if cmd.startswith("getprop"):
            parts = cmd.split()
            key = parts[1] if len(parts) > 1 else ""
            val = self.props.get(key, "")
            return 0, val, ""

        return 0, "", ""
