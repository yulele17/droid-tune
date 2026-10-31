"""可行性探测 —— 本项目的核心，也是和其它 debloat 工具最大的区别。

背景（一次真实踩坑）：
    某台 Redmi Note 13 Pro / 国行 HyperOS 3，按 UAD 官方清单筛出 55 个候选包，
    逐个执行 `pm disable-user --user 0`，结果**只有 7 个成功，48 个被系统拒绝**：
        SecurityException: Cannot disable system packages
    失败率 87%。也就是说这条被全网教程默认的路线，在这台机器上基本不成立。

所以正确的顺序不是"直接冻"，而是**先 probe**：
    取少量候选包，逐个 `disable-user` → 立刻 `enable` 恢复（可逆，无副作用），
    统计"这台 ROM 允许禁用的比例"，据此决定走哪条路线：
        允许比例高  → debloat 冻结（本项目的 debloat 命令）
        允许比例低  → 改走 uiautomator2 模拟点设置开关（推荐配合 android-ad-cleaner）
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, asdict
from typing import List, Optional

from . import __version__
from .adb import Adb, ST_OK, ST_SYSTEM_LOCKED, ST_NOT_INSTALLED, ST_UNKNOWN, normalize_text
from .safety import is_protected, load_keep_patterns

# 兜底探针：多种 ROM 上常见的应用类包，冻几秒再恢复完全无副作用。
# 之所以不用遥测/系统包做探针，是因为它们一旦 toggle 失败或恢复异常影响面大。
BUILTIN_PROBES = [
    "com.google.android.apps.books",
    "com.google.android.videos",
    "com.google.android.music",
    "com.google.android.apps.magazines",
    "com.netflix.partner.activation",
    "com.facebook.services",
    "com.facebook.system",
    "com.miui.video",
    "com.xiaomi.gamecenter",
    "com.miui.player",
]


@dataclass
class ProbeResult:
    package: str
    status: str          # ok / locked / missing / unknown
    restored: bool       # 探测后是否成功恢复原状态
    raw: str = ""


@dataclass
class ProbeReport:
    serial: str
    model: str
    rom: str
    families: List[str]
    created_at: str
    total: int
    ok: int
    locked: int
    missing: int
    unknown: int
    lock_ratio: float     # 被系统锁死的比例（越低越适合 debloat）
    allow_ratio: float    # 允许禁用的比例
    results: List[ProbeResult]
    verdict: str
    advice: str

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)


def pick_probes(installed: set, preferred: Optional[List[str]] = None,
                n: int = 6, patterns: Optional[list] = None) -> List[str]:
    """挑探针包：优先用候选清单（保证本机已装），不足时用内置探针补齐，并剔除保护名单。"""
    pats = patterns if patterns is not None else load_keep_patterns()
    chosen: List[str] = []
    for src in (preferred or [], BUILTIN_PROBES):
        for p in src:
            p = normalize_text(p)
            if p in installed and p not in chosen and not is_protected(p, pats):
                chosen.append(p)
            if len(chosen) >= n:
                return chosen
    return chosen


def probe_package(adb: Adb, package: str) -> ProbeResult:
    """toggle 一次再恢复。这是探测，不是改动 —— 结束时设备应回到 probe 前的状态。"""
    res = adb.set_package_enabled(package, enabled=False)
    restored = False
    if res.status == ST_OK:
        back = adb.set_package_enabled(package, enabled=True)
        restored = back.status == ST_OK
    return ProbeResult(package=package, status=res.status, restored=restored, raw=res.raw[:200])


def run(adb: Adb, installed: set, info, preferred: Optional[List[str]] = None,
        n: int = 6, quiet: bool = False) -> ProbeReport:
    probes = pick_probes(installed, preferred, n)
    if not probes:
        raise RuntimeError("没挑出可用的探针包：本机既没装候选、也没装任何内置探针。")

    if not quiet:
        print(f"探针样本 {len(probes)} 个（逐个 disable → 立刻 enable 恢复，无副作用）：")
        for p in probes:
            print(f"  - {p}")
        print()

    results: List[ProbeResult] = []
    for i, p in enumerate(probes, 1):
        r = probe_package(adb, p)
        results.append(r)
        if not quiet:
            tag = {ST_OK: "可禁用", ST_SYSTEM_LOCKED: "系统锁死", ST_NOT_INSTALLED: "未安装", ST_UNKNOWN: "未知返回"}[r.status]
            warn = "" if r.restored or r.status != ST_OK else "  ⚠ 恢复失败，请手动 enable 还原！"
            print(f"  [{i}/{len(probes)}] {tag:<6} {p}{warn}")

    ok = sum(1 for r in results if r.status == ST_OK)
    locked = sum(1 for r in results if r.status == ST_SYSTEM_LOCKED)
    missing = sum(1 for r in results if r.status == ST_NOT_INSTALLED)
    unknown = sum(1 for r in results if r.status == ST_UNKNOWN)
    counted = ok + locked          # 真正反映 ROM 态度的分母（未安装的不算）
    lock_ratio = (locked / counted) if counted else 1.0
    allow_ratio = (ok / counted) if counted else 0.0

    if allow_ratio >= 0.6:
        verdict = "debloat 路线可行"
        advice = ("这台 ROM 允许禁用大部分目标包。可以直接用 `tune debloat` 做冻结，"
                  "建议先 `--dry-run` 看清单再执行。")
    elif allow_ratio >= 0.2:
        verdict = "debloat 部分可行"
        advice = ("只有一部分包能被禁用。可以跑 `tune debloat`，但预期会有不少 "
                  "`Cannot disable system packages` 报错，属正常；剩余的改用 uiautomator2 "
                  "点设置开关的方式处理（推荐配合 android-ad-cleaner）。")
    else:
        verdict = "debloat 路线基本不可行"
        advice = ("ROM 把目标包锁成了不可禁用系统核心包，`pm disable-user` 对它们无效。"
                  "**不要**指望 debloat 工具解决，改走 uiautomator2 模拟点击设置开关的路线，"
                  "推荐配合 android-ad-cleaner；若只是想要更流畅，直接跑 `tune smooth`。")

    return ProbeReport(
        serial=info.serial, model=info.model, rom=info.rom_display,
        families=info.families, created_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        total=len(results), ok=ok, locked=locked, missing=missing, unknown=unknown,
        lock_ratio=round(lock_ratio, 3), allow_ratio=round(allow_ratio, 3),
        results=results, verdict=verdict, advice=advice,
    )


def save(report: ProbeReport, out_dir: str = "snapshots") -> str:
    os.makedirs(out_dir, exist_ok=True)
    safe_model = "".join(c for c in report.model if c not in " /\\:*?\"<>|") or "unknown"
    path = os.path.join(out_dir, f"probe-{safe_model}-{time.strftime('%Y%m%d-%H%M%S')}.json")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(report.to_json())
    return path


def render(report: ProbeReport) -> str:
    lines = [
        "",
        "—" * 62,
        "可行性探测结论",
        "—" * 62,
        f"设备        : {report.model}（{report.serial}）",
        f"ROM         : {report.rom or '未取到'}  家族: {', '.join(report.families) or '未识别'}",
        f"样本        : 共 {report.total} 个探针包",
        f"结果        : 可禁用 {report.ok} / 系统锁死 {report.locked} / 未安装 {report.missing}",
        f"允许禁用比例: {report.allow_ratio:.0%}   锁死比例: {report.lock_ratio:.0%}",
        "",
        f"结论        : {report.verdict}",
        f"建议        : {report.advice}",
    ]
    not_restored = [r.package for r in report.results if r.status == ST_OK and not r.restored]
    if not_restored:
        lines += ["", "⚠ 以下探针包恢复原状态失败，请手动执行："]
        lines += [f"    adb shell pm enable --user 0 {p}" for p in not_restored]
    lines.append("—" * 62)
    return "\n".join(lines)
