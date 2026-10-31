"""安全护栏 —— 决定哪些包绝对不许动。

原则：**宁可少冻十个，不可冻错一个。**
UAD 官方的 Recommended/Advanced/Unsafe 分级是社区经验，落到具体 ROM 上经常不准：
被标 Recommended 的包里混着会让人脸解锁失效、云同步断开、甚至国行 bootloop 的东西。
所以本模块在官方分级之上再叠加一层实测保护名单，且保护优先级最高。
"""

from __future__ import annotations

import fnmatch
import os
from typing import List, Optional, Tuple

from .adb import normalize_text

DEFAULT_KEEP_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "keep-critical.txt",
)


def load_keep_patterns(path: Optional[str] = None) -> List[Tuple[str, str]]:
    """读取保护名单，返回 [(pattern, reason)]。不存在则返回空表而不是抛错。

    行格式：``com.example.foo   # 冻结后的具体症状``
    ``#`` 前是包名/通配符，后是原因说明，两者都会被返回。

    读取时统一 strip()，因此 Windows 上被 git 检出成 CRLF 的文件也能正确解析。
    """
    path = path or DEFAULT_KEEP_FILE
    out: List[Tuple[str, str]] = []
    if not os.path.isfile(path):
        return out
    with open(path, encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw or raw.startswith("#"):
                continue
            pattern, _, reason = raw.partition("#")
            pattern = pattern.strip()
            if not pattern:
                continue
            out.append((pattern, reason.strip()))
    return out


def is_protected(package: str, patterns: Optional[List[Tuple[str, str]]] = None) -> Optional[str]:
    """命中保护名单返回命中说明（用作提示符），没命中返回 None。"""
    pkg = normalize_text(package)
    for pat, reason in (patterns if patterns is not None else load_keep_patterns()):
        if fnmatch.fnmatch(pkg, pat):
            if reason:
                return f"命中保护规则 {pat}（{reason}）"
            return f"命中保护规则 {pat}"
    return None


def filter_safe(packages: List[str], patterns: Optional[List[Tuple[str, str]]] = None) -> Tuple[List[str], List[Tuple[str, str]]]:
    """把一串包名分成 (可冻, 被保护的)。"""
    pats = patterns if patterns is not None else load_keep_patterns()
    safe, blocked = [], []
    for p in packages:
        hit = is_protected(p, pats)
        (blocked.append((p, hit or "")) if hit else safe.append(p))
    return safe, blocked


def assert_no_uninstall():
    """一次性提醒：本项目**不提供** uninstall 能力。

    理由：`pm uninstall -k --user 0` 在国行 ROM 上大概率同样被拒，即便成功也难以恢复
    （部分 ROM 恢复只能靠整机还原或 OTA 重刷），收益远小于风险。要禁用就用 disable-user。
    """
