"""拉取并解析 UAD-ng 官方 debloat 清单。

清单来源：Universal-Debloater-Alliance/universal-android-debloater-next-generation
文件：resources/assets/uad_lists.json（约 5000+ 条，按包名索引）

为什么自带多通道回退：实测在部分网络下
  - raw.githubusercontent.com 直接 403 / curl exit 56；
  - git clone 又可能因 TLS 后端（schannel）报错中断。
所以按 urllib → curl → codeload 打包 → git 浅克隆 → 本地缓存 的顺序逐级 fallback，
任何一条通了就行，最后都落到一个本地 JSON 缓存，避免每次联网。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from typing import Dict, List, Optional

REPO = "Universal-Debloater-Alliance/universal-android-debloater-next-generation"
INNER = os.path.join("resources", "assets", "uad_lists.json")

RAW_URL = f"https://raw.githubusercontent.com/{REPO}/main/resources/assets/uad_lists.json"
TGZ_URL = f"https://codeload.github.com/{REPO}/tar.gz/refs/heads/main"
GIT_URL = f"https://github.com/{REPO}.git"

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".cache")
CACHE_FILE = os.path.join(CACHE_DIR, "uad_lists.json")

SAFE_REMOVALS = ("Recommended", "Advanced")
UNSAFE = ("Unsafe", "Expert")


@dataclass
class Entry:
    package: str
    removal: str
    list_name: str
    description: str


def _save_cache(text: str) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _try_urllib(url: str, timeout: int = 30) -> Optional[str]:
    req = urllib.request.Request(url, headers={"User-Agent": "droid-tune"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", errors="replace")
        json.loads(raw)  # 必须是合法 JSON 才认
        return raw
    except Exception:
        return None


def _try_curl(url: str, timeout: int = 90) -> Optional[str]:
    if not shutil.which("curl"):
        return None
    try:
        p = subprocess.run(["curl", "-sSL", "--max-time", str(timeout), url],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
        raw = p.stdout.decode("utf-8", errors="replace")
        json.loads(raw)
        return raw
    except Exception:
        return None


def _try_tarball(timeout: int = 180) -> Optional[str]:
    """整仓打包下载，再从 tar 里抠出那个 JSON。raw 被墙时这条最管用。"""
    if not shutil.which("curl"):
        return None
    tmp = tempfile.mkdtemp(prefix="uad-")
    tgz = os.path.join(tmp, "repo.tar.gz")
    try:
        subprocess.run(["curl", "-sSL", "--max-time", str(timeout), "-o", tgz, TGZ_URL],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
        if not os.path.isfile(tgz) or os.path.getsize(tgz) < 1024:
            return None
        import tarfile
        with tarfile.open(tgz) as tf:
            target = None
            for m in tf.getmembers():
                if m.name.endswith(INNER.replace("\\", "/")):
                    target = m
                    break
            if target is None:
                return None
            f = tf.extractfile(target)
            raw = f.read().decode("utf-8", errors="replace")
        json.loads(raw)
        return raw
    except Exception:
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _try_git_clone(timeout: int = 300) -> Optional[str]:
    """最后兜底：浅克隆整仓。Windows 上注意 TLS 后端，优先用 openssl。"""
    if not shutil.which("git"):
        return None
    tmp = tempfile.mkdtemp(prefix="uad-git-")
    dest = os.path.join(tmp, "repo")
    try:
        for extra in (["-c", "http.sslBackend=openssl"], []):
            subprocess.run(["git", *extra, "clone", "--depth", "1", GIT_URL, dest],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           stdin=subprocess.DEVNULL, timeout=timeout)
            cand = os.path.join(dest, *INNER.split(os.sep)) if os.sep in INNER else os.path.join(dest, INNER)
            cand = os.path.join(dest, "resources", "assets", "uad_lists.json")
            if os.path.isfile(cand):
                with open(cand, encoding="utf-8") as fh:
                    raw = fh.read()
                json.loads(raw)
                return raw
    except Exception:
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return None


def load(local_path: Optional[str] = None, refresh: bool = False) -> Dict[str, dict]:
    """取到 UAD 清单 dict。优先级：本地指定 > 缓存（非强制刷新）> 各条网络通道 > 报错。"""
    if local_path:
        with open(local_path, encoding="utf-8") as f:
            return json.load(f)
    if os.path.isfile(CACHE_FILE) and not refresh:
        with open(CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)

    channels = (
        ("raw.githubusercontent", lambda: _try_urllib(RAW_URL)),
        ("curl raw", lambda: _try_curl(RAW_URL)),
        ("codeload 打包", _try_tarball),
        ("git 浅克隆", _try_git_clone),
    )
    errors = []
    for name, fn in channels:
        raw = fn()
        if raw:
            _save_cache(raw)
            print(f"[清单] 通过「{name}」获取成功，已缓存到 {os.path.relpath(CACHE_FILE)}")
            return json.loads(raw)
        errors.append(name)
    raise RuntimeError(
        "四种通道都没拉到 UAD 清单：" + "、".join(errors) + "\n"
        "可以手动下载后放到 .cache/uad_lists.json，或用 --list-local 指定路径：\n"
        f"  {RAW_URL}"
    )


def candidates(data: Dict[str, dict], installed: set, families: Optional[List[str]] = None,
               include_advanced: bool = True) -> List[Entry]:
    """交叉比对：清单里点名 && 这台机器真装了 && 属于目标厂商 && 不是 Unsafe。"""
    from .device import family_from_package
    fams = set(families or [])
    out: List[Entry] = []
    for pkg, meta in data.items():
        if pkg not in installed:
            continue
        if not isinstance(meta, dict):
            continue
        removal = meta.get("removal", "")
        if removal not in SAFE_REMOVALS:
            continue
        if not include_advanced and removal == "Advanced":
            continue
        if fams and (family_from_package(pkg) not in fams):
            continue
        out.append(Entry(
            package=pkg,
            removal=removal,
            list_name=meta.get("list", ""),
            description=(meta.get("description") or "").replace("\n", " ").strip(),
        ))
    return sorted(out, key=lambda e: (e.removal, e.package))
