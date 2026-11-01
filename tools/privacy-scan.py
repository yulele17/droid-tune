#!/usr/bin/env python3
"""提交前隐私自查 —— 找出可能被泄漏的个人信息痕迹。

设计前提（都是踩过的坑）：
  1. **只扫当前文件不够**。序列号可能躺在「被 `git add` 暂存过、但从未 commit」的
     悬空 blob 里 —— 而 `git push` 只推送可达对象，所以这类东西**看似没进仓库、
     其实一直躺在 .git 里**。本脚本用 `--history` 把对象库整个翻一遍，并区分
     「已发布（可达）」和「仅本地（悬空）」。
  2. **唯一标识要靠 denylist，不能只靠正则**。序列号、UDID 这类没有固定格式，
     正则一定会误判；所以支持本地 `.privacy-local.json` 放你自己的真实值。
  3. **图片会带元数据**。PNG 的 tEXt/iTXt、JPEG 的 EXIF 里可能塞着 GPS 和机型。
  4. **commit 作者信息是永久的**。邮箱不是 noreply 就会公开暴露真邮箱。

用法：
    python tools/privacy-scan.py                 # 扫工作区已跟踪文件 + commit 元数据 + 图片元数据
    python tools/privacy-scan.py --staged        # 只扫暂存区（提交前用）
    python tools/privacy-scan.py --history       # 额外扫 git 对象库全部 blob（含悬空）
    python tools/privacy-scan.py --remote        # 额外扫 GitHub 线上已发布文件
    python tools/privacy-scan.py --local my.json # 指定本地 denylist 配置

退出码：有 ERROR 级命中返回 1，否则 0（可直接用于 CI 阻断）。

零第三方依赖，只用 Python 标准库。
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import struct
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SELF = os.path.abspath(__file__)

ERROR = "ERROR"
WARN = "WARN"
INFO = "INFO"

# 默认放行：这些路径不参与扫描（脚本自身、规则示例等）
DEFAULT_SKIP = ("tools/privacy-scan.py", "tools/privacy-scan.json")


# ---------------------------------------------------------------- 规则定义
@dataclass
class Rule:
    rid: str
    severity: str
    pattern: str
    desc: str
    advice: str
    regex: re.Pattern = field(repr=False, default=None)
    validator: Optional[str] = None   # luhn / None
    exclude: Optional[str] = None     # 命中后再过一道：匹配这个正则的则丢弃

    def compiled(self) -> re.Pattern:
        if self.regex is None:
            self.regex = re.compile(self.pattern)
        return self.regex

    def excluded(self, value: str) -> bool:
        return bool(self.exclude) and bool(re.search(self.exclude, value))


def _luhn_ok(digits: str) -> bool:
    """Luhn 校验，用来压掉「纯数字随机串」的误报（IMEI / 银行卡）。"""
    nums = [int(c) for c in digits if c.isdigit()]
    if len(nums) < 12:
        return False
    total = 0
    for i, d in enumerate(reversed(nums)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


RULES: List[Rule] = [
    Rule("cn-mobile", ERROR, r"(?<!\d)1[3-9]\d{9}(?!\d)",
         "疑似中国大陆手机号", "改成脱敏形式，如 138****5678"),
    Rule("cn-idcard", ERROR, r"(?<!\d)\d{17}[\dXx](?!\d)",
         "疑似中国身份证号", "整段删除或用 * 遮蔽"),
    Rule("bank-card", ERROR, r"(?<!\d)\d{16,19}(?!\d)",
         "疑似银行卡号", "删除；本项目示例请写成 6222****1234",
         validator="luhn"),
    Rule("imei", ERROR, r"(?<!\d)\d{15}(?!\d)",
         "疑似 IMEI / MEID", "删除；不要在实测报告里贴设备标识",
         validator="luhn"),
    Rule("ios-udid-new", ERROR, r"\b[0-9A-Fa-f]{8}-[0-9A-Fa-f]{16}\b",
         "疑似 iOS UDID（新格式，40 位带横线）", "写成 00008030-************"),
    Rule("ios-udid-legacy", ERROR, r"\b[0-9A-Fa-f]{40}\b",
         "疑似 iOS UDID（旧格式 40 位）", "只保留前 8 位并加 *"),
    Rule("mac-address", WARN, r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b",
         "疑似 MAC 地址", "这是网口/蓝牙标识，删掉"),
    Rule("windows-user-path", ERROR, r"[A-Za-z]:\\Users\\[^\s\"']+",
         "泄露了本机 Windows 用户名与目录", "换成 <用户名> 或 ~/ 写法"),
    Rule("posix-user-path", WARN, r"/(?:home|Users)/[A-Za-z0-9._-]+/",
         "泄露了本机用户目录", "换成 ~/"),
    Rule("private-email", ERROR,
         r"\b[A-Za-z0-9._%+-]+@(?!users\.noreply\.github\.com|example\.(?:com|org))[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
         "非 noreply 邮箱（可能被用来定位你）",
         "换成 <用户名>@users.noreply.github.com，或在 .privacyignore 里放行",
         exclude=r"^(git|noreply|email|not\.set)@"),
    Rule("public-ipv4", WARN,
         r"(?<![\d.])(?!10\.|127\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|0\.|169\.254\.)"
         r"(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(?![\d.])",
         "公网 IPv4 地址", "改成内网地址或删掉"),
    Rule("totp-secret", WARN, r"\b(?:[A-Z2-7]{16}|[A-Z2-7]{32})\b",
         "疑似 TOTP / 2FA 种子密钥（base32）",
         "**立刻作废该密钥** —— 一旦泄漏，攻击者可以永久生成你的验证码"),
    Rule("gps-precision", WARN, r"\b(?:[3-9]\d{1,2}\.\d{4,}|[1-8]\d\.\d{4,})\s*[,，]\s*(?:[1-9]\d{1,2}\.\d{4,}|[1-8]\d\.\d{4,})\b",
         "疑似高精度经纬度（可精确定位到街道）", "降到小数点后 2 位（约 1 公里）"),
    Rule("exact-timestamp", INFO, r"\b20\d{2}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}\b",
         "精确到秒的时间戳（会暴露操作时刻）",
         "如果只是示例，写成占位符；真实数据请说明为什么需要精确到秒"),
]


# ---------------------------------------------------------------- 工具
def git(*args: str, cwd: str = ROOT) -> Tuple[int, str]:
    """调 git。必须断 stdin，否则在 shell 循环里会把循环的输入吃掉。"""
    try:
        p = subprocess.run(["git", *args], cwd=cwd, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        return p.returncode, p.stdout.decode("utf-8", "replace")
    except FileNotFoundError:
        return 127, "git not found"
    except Exception as e:  # noqa: BLE001
        return 1, str(e)


def iter_tracked_files() -> List[str]:
    code, out = git("ls-files")
    if code != 0:
        return []
    return [x for x in out.splitlines() if x.strip()]


def iter_staged_files() -> List[str]:
    code, out = git("diff", "--cached", "--name-only")
    if code != 0:
        return []
    return [x for x in out.splitlines() if x.strip()]


def iter_all_blobs() -> List[Tuple[str, str]]:
    """对象库里所有 blob，**含从未 commit 的悬空对象**。返回 [(sha, path提示)]。"""
    code, out = git("cat-file", "--batch-all-objects", "--batch-check=%(objectname) %(objecttype)")
    if code != 0:
        return []
    shas = [ln.split()[0] for ln in out.splitlines() if ln.endswith(" blob")]
    return [(s, "") for s in shas]


def reachable_blobs() -> set:
    code, out = git("rev-list", "--objects", "--all")
    if code != 0:
        return set()
    return {ln.split()[0] for ln in out.splitlines() if ln.strip()}


def blob_text(sha: str) -> str:
    code, out = git("cat-file", "blob", sha)
    return out if code == 0 else ""


def load_ignore(extra: Iterable[str] = ()) -> List[str]:
    pats = list(DEFAULT_SKIP) + list(extra)
    f = os.path.join(ROOT, ".privacyignore")
    if os.path.isfile(f):
        for line in open(f, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#"):
                pats.append(line)
    return pats


def is_ignored(path: str, pats: List[str]) -> bool:
    p = path.replace("\\", "/")
    for pat in pats:
        pat = pat.replace("\\", "/")
        if fnmatch.fnmatch(p, pat) or fnmatch.fnmatch(os.path.basename(p), pat):
            return True
        if p.startswith(pat.rstrip("/*") + "/"):
            return True
    return False


def image_meta(path: str) -> List[str]:
    """提取 PNG 文本块 / JPEG EXIF 里的人类可读元数据。"""
    try:
        data = open(path, "rb").read()
    except OSError:
        return []
    found = []
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        i = 8
        while i < len(data) - 8:
            ln = struct.unpack(">I", data[i:i + 4])[0]
            typ = data[i + 4:i + 8].decode("latin1", "replace")
            if typ in ("tEXt", "iTXt", "zTXt"):
                found.append(f"PNG {typ} 文本块")
            if typ == "eXIf":
                found.append("PNG eXIf 块")
            if typ == "IEND":
                break
            i += 12 + ln
    elif data[:2] == b"\xff\xd8":
        # JPEG：EXIF 段里的 GPS/机型关键字
        for key in (b"GPS", b"Exif", b"Apple", b"Photoshop"):
            if key in data[:20000]:
                found.append(f"JPEG 含 {key.decode()} 段")
    return found


# ---------------------------------------------------------------- 扫描主体
@dataclass
class Hit:
    severity: str
    rid: str
    where: str
    detail: str
    advice: str


def scan_text(text: str, where: str, hits: List[Hit], denylist: List[str]) -> None:
    for rule in RULES:
        for m in rule.compiled().finditer(text):
            val = m.group(0)
            if rule.validator == "luhn" and not _luhn_ok(val):
                continue
            if rule.excluded(val):
                continue
            hits.append(Hit(rule.severity, rule.rid, where, val[:60], rule.advice))
    for lit in denylist:
        if lit and lit in text:
            hits.append(Hit(ERROR, "local-denylist", where, lit[:60],
                            "这是你自己标记的敏感值，必须删除或脱敏"))


def scan_files(paths: List[str], pats: List[str], denylist: List[str]) -> List[Hit]:
    hits: List[Hit] = []
    for rel in paths:
        rel = rel.replace("\\", "/")
        if is_ignored(rel, pats):
            continue
        full = os.path.join(ROOT, rel)
        if not os.path.isfile(full):
            continue
        if os.path.splitext(full)[1].lower() in (".png", ".jpg", ".jpeg"):
            for note in image_meta(full):
                hits.append(Hit(WARN, "image-metadata", rel, note,
                                "用工具清掉图片元数据再提交（如 exiftool）"))
            continue
        try:
            text = open(full, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        if "\x00" in text[:2048]:
            continue  # 二进制
        scan_text(text, rel, hits, denylist)
    return hits


def scan_history(denylist: List[str]) -> Tuple[List[Hit], dict]:
    hits: List[Hit] = []
    stats = {"blobs": 0, "reachable": 0, "unreachable": 0}
    reach = reachable_blobs()
    for sha, _ in iter_all_blobs():
        stats["blobs"] += 1
        state = "已发布" if sha in reach else "仅本地"
        if sha in reach:
            stats["reachable"] += 1
        else:
            stats["unreachable"] += 1
        text = blob_text(sha)
        if not text or "\x00" in text[:2048]:
            continue
        sub: List[Hit] = []
        scan_text(text, "", sub, denylist)
        for h in sub:
            hits.append(Hit(h.severity, h.rid, f"{sha[:8]}({state})", h.detail, h.advice))
    return hits, stats


def scan_commit_meta() -> List[Hit]:
    hits: List[Hit] = []
    code, out = git("log", "--all", "--format=%an <%ae>|%cn <%ce>")
    if code != 0:
        return hits
    seen = set()
    for line in out.splitlines():
        if line.strip() and line not in seen:
            seen.add(line)
            m = re.search(r"<([^>]+)>", line)
            if not m:
                continue
            email = m.group(1)
            if email.endswith("@users.noreply.github.com"):
                continue
            if email in ("", "not.set@not.set"):
                hits.append(Hit(ERROR, "git-identity", "commit metadata", email,
                                "git user.email 未设置，提交会写入系统用户名。"
                                "请 git config user.email '<用户名>@users.noreply.github.com'"))
                continue
            hits.append(Hit(WARN, "git-identity", "commit metadata", email,
                            "这个邮箱会永久公开在 GitHub 上。"
                            "建议改用 GitHub noreply 邮箱并 git commit --amend --reset-author"))
    return hits


def scan_remote(denylist: List[str]) -> Tuple[List[Hit], int]:
    """直接扫 GitHub 线上已发布文件 —— 本地干净不代表线上干净。"""
    hits: List[Hit] = []
    code, out = git("remote", "get-url", "origin")
    if code != 0 or "github.com" not in out:
        return hits, 0
    url = out.strip()
    # git@github.com:owner/repo.git  →  owner/repo
    m = re.search(r"github\.com[:/]([^/]+)/([^/\s]+?)(?:\.git)?$", url)
    if not m:
        return [Hit(INFO, "remote-scan", "线上", f"无法解析远端地址 {url[:40]}",
                    "只支持 GitHub 远端")], 0
    owner, repo = m.group(1), m.group(2)
    api = f"https://api.github.com/repos/{owner}/{repo}"
    raw_base = f"https://raw.githubusercontent.com/{owner}/{repo}/HEAD"
    import urllib.request
    import urllib.parse
    try:
        req = urllib.request.Request(api + "/git/trees/HEAD?recursive=1",
                                     headers={"User-Agent": "droid-tune-privacy-scan"})
        with urllib.request.urlopen(req, timeout=30) as r:
            tree = json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:  # noqa: BLE001
        return [Hit(INFO, "remote-scan", "线上", str(e)[:60],
                    "网络不可用或仓库私有，跳过线上扫描")], 0
    files = [t for t in tree.get("tree", []) if t.get("type") == "blob"]
    n = 0
    for f in files:
        path = f["path"]
        if path.lower().endswith((".png", ".jpg", ".jpeg", ".pdf", ".zip", ".pyc")):
            continue
        u = f"{raw_base}/{urllib.parse.quote(path)}"
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "droid-tune-privacy-scan"})
            with urllib.request.urlopen(req, timeout=30) as r:
                text = r.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            continue
        n += 1
        sub: List[Hit] = []
        scan_text(text, f"线上:{path}", sub, denylist)
        hits.extend(sub)
    return hits, n


def load_local_config(path: Optional[str]) -> Tuple[List[str], List[str]]:
    """本地 denylist：把你自己的真实设备号写在这里，这个文件不要提交。"""
    cand = path or os.path.join(ROOT, ".privacy-local.json")
    if not os.path.isfile(cand):
        return [], []
    try:
        data = json.load(open(cand, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return [], []
    dl = data.get("denylist") or []
    ig = data.get("ignore") or []
    return [str(x) for x in dl], [str(x) for x in ig]


# ---------------------------------------------------------------- 输出
COLOR = {ERROR: "\033[31m", WARN: "\033[33m", INFO: "\033[36m", "OK": "\033[32m"}
RESET = "\033[0m"


def main() -> int:
    ap = argparse.ArgumentParser(description="提交前隐私自查")
    ap.add_argument("--staged", action="store_true", help="只扫暂存区文件")
    ap.add_argument("--history", action="store_true", help="额外扫 git 对象库全部 blob（含悬空）")
    ap.add_argument("--remote", action="store_true", help="额外扫 GitHub 线上已发布文件")
    ap.add_argument("--local", metavar="FILE", help="本地 denylist 配置（默认 .privacy-local.json）")
    ap.add_argument("--no-meta", action="store_true", help="跳过 commit 元数据与图片元数据检查")
    ap.add_argument("--quiet", action="store_true", help="只输出结果摘要")
    args = ap.parse_args()

    denylist, extra_ignore = load_local_config(args.local)
    pats = load_ignore(extra_ignore)

    hits: List[Hit] = []
    scope = []

    if args.staged:
        files = iter_staged_files()
        scope.append(f"暂存区 {len(files)} 个文件")
        hits += scan_files(files, pats, denylist)
    else:
        files = iter_tracked_files()
        scope.append(f"已跟踪文件 {len(files)} 个")
        hits += scan_files(files, pats, denylist)

    if not args.no_meta:
        hits += scan_commit_meta()
        scope.append("commit 元数据 + 图片元数据")

    if args.history:
        hh, stats = scan_history(denylist)
        hits += hh
        scope.append(f"对象库 {stats['blobs']} 个 blob"
                     f"（已发布 {stats['reachable']} / 仅本地 {stats['unreachable']}）")

    if args.remote:
        rh, n = scan_remote(denylist)
        hits += rh
        scope.append(f"线上 {n} 个文本文件")

    errors = [h for h in hits if h.severity == ERROR]
    warns = [h for h in hits if h.severity == WARN]
    infos = [h for h in hits if h.severity == INFO]

    if not args.quiet:
        print("扫描范围：" + "、".join(scope))
        print()
        for sev in (ERROR, WARN, INFO):
            group = [h for h in hits if h.severity == sev]
            if not group:
                continue
            print(f"{COLOR[sev]}{sev}{RESET} —— {len(group)} 处")
            for h in group:
                print(f"  · [{h.rid}] {h.where}")
                print(f"      内容: {h.detail}")
                print(f"      建议: {h.advice}")
            print()

    if errors:
        print(f"{COLOR[ERROR]}✗ 发现 {len(errors)} 处 ERROR 级问题，必须处理后才能提交{RESET}")
        return 1
    if warns:
        print(f"{COLOR[WARN]}⚠ {len(warns)} 处 WARN 级，请人工确认{RESET}")
    else:
        print(f"{COLOR['OK']}✓ 未发现隐私问题{RESET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
