"""预装冻结（disable-user，可还原）—— 以及最重要的"提前自知能动性不足"。

分工关系：
  - `probe` 负责回答"这台 ROM 让不让禁"；
  - `plan` 负责算出"名单上有哪些、哪些被保护名单拦下"；
  - `freeze` 才真正动手，并且每一行结果都要如实暴露 `locked`（被 ROM 拒绝）。

关于失败：**冻失败不代表工具坏了**，那正是这台 ROM 的真实态度。
所以 freeze 的返回值刻意保留三类结果（成功/被锁/未装），而不是合并成一个"失败"。
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .adb import Adb, ST_OK, ST_SYSTEM_LOCKED, ST_NOT_INSTALLED, ST_UNKNOWN, normalize_text
from .safety import filter_safe, load_keep_patterns
from .uad import Entry, candidates, load as load_uad


@dataclass
class Plan:
    model: str
    rom: str
    freeze: List[Entry] = field(default_factory=list)
    protected: Dict[str, str] = field(default_factory=dict)

    @property
    def packages(self) -> List[str]:
        return [e.package for e in self.freeze]


@dataclass
class FreezeOutcome:
    total: int
    frozen: List[str] = field(default_factory=list)
    locked: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    unknown: List[str] = field(default_factory=list)

    @property
    def lock_ratio(self) -> float:
        counted = len(self.frozen) + len(self.locked)
        return (len(self.locked) / counted) if counted else 0.0


def build_plan(installed: set, families: List[str], local_list: Optional[str] = None,
               include_advanced: bool = False, extra_patterns: Optional[list] = None) -> Plan:
    """交叉比对 UAD 清单与实机包列表，产出候选与被保护名单拦下的部分。

    include_advanced 默认 False，这是刻意的：
      UAD 的 Recommended ≈ 遥测/广告/无代码 overlay，冻掉基本无感；
      Advanced 里混着音效、相册编辑器这类**真有日常功能**的东西，
      属于"可选"档，要额外人工确认，不进默认清单。
    """
    data = load_uad(local_list)
    cands = candidates(data, installed, families, include_advanced)
    pats = extra_patterns if extra_patterns is not None else load_keep_patterns()
    pkgs = [e.package for e in cands]
    safe, blocked = filter_safe(pkgs, pats)
    safe_set = set(safe)
    return Plan(
        model="", rom="",
        freeze=[e for e in cands if e.package in safe_set],
        protected=dict(blocked),
    )


def freeze(adb: Adb, packages: List[str], quiet: bool = False) -> FreezeOutcome:
    """逐个冻结。可重入：已禁用的包直接跳过并记录为成功，重跑不会出错。"""
    out = FreezeOutcome(total=len(packages))
    disabled_now = set(adb.list_packages("-d"))
    for i, pkg in enumerate(packages, 1):
        pkg = normalize_text(pkg)
        if pkg in disabled_now:
            out.frozen.append(pkg)
            tag = "已禁用"
        else:
            res = adb.set_package_enabled(pkg, enabled=False)
            if res.status == ST_OK:
                out.frozen.append(pkg)
                tag = "已冻结"
            elif res.status == ST_SYSTEM_LOCKED:
                out.locked.append(pkg)
                tag = "系统锁死"
            elif res.status == ST_NOT_INSTALLED:
                out.missing.append(pkg)
                tag = "未安装"
            else:
                out.unknown.append(pkg)
                tag = "未知返回"
        if not quiet:
            print(f"  [{i}/{len(packages)}] {tag:<6} {pkg}")
    return out


def unfreeze(adb: Adb, packages: List[str], quiet: bool = False) -> Dict[str, str]:
    res_map: Dict[str, str] = {}
    for pkg in packages:
        pkg = normalize_text(pkg)
        r = adb.set_package_enabled(pkg, enabled=True)
        res_map[pkg] = "enabled" if r.status == ST_OK else r.raw[:120]
        if not quiet:
            print(f"  {('已还原' if r.status == ST_OK else '失败'):<6} {pkg}")
    return res_map


def render_plan(plan: Plan) -> str:
    lines = [
        f"候选包       : 建议冻结 {len(plan.freeze)} 个",
        f"被保护拦截   : {len(plan.protected)} 个（冻了会掉功能，已自动排除）",
        "",
    ]
    if plan.protected:
        lines.append("被保护的包：")
        for pkg, why in sorted(plan.protected.items()):
            lines.append(f"  - {pkg}  （{why}）")
        lines.append("")
    lines.append("建议冻结清单：")
    for e in plan.freeze:
        lines.append(f"  [{e.removal[0]}] {e.package}")
        if e.description:
            lines.append(f"        {e.description[:80]}")
    return "\n".join(lines)


def export_markdown(plan: Plan, info) -> str:
    """导出给人看的清单文件（可以自己逐条勾，也可以贴进 issue 讨论）。"""
    L = [
        "# 预装冻结候选清单",
        "",
        f"- 设备：{info.model}（{info.device_code or info.serial}）",
        f"- ROM ：{info.rom_display or '未取到'}",
        f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        f"共筛出 **{len(plan.freeze)}** 个建议冻结项，另有 **{len(plan.protected)}** 个命中保护名单已排除。",
        "",
        "冻结命令：`adb shell pm disable-user --user 0 <包名>`，还原：`adb shell pm enable --user 0 <包名>`。",
        "",
        "## 建议冻结",
        "",
        "| 包名 | UAD 分级 | 说明 |",
        "|---|---|---|",
    ]
    for e in plan.freeze:
        L.append(f"| `{e.package}` | {e.removal} | {e.description.replace('|', '/')[:90]} |")
    if plan.protected:
        L += ["", "## 已保护（不冻）", "", "| 包名 | 原因 |", "|---|---|"]
        for pkg, why in sorted(plan.protected.items()):
            L.append(f"| `{pkg}` | {why} |")
    return "\n".join(L) + "\n"


def write_text(path: str, text: str) -> str:
    """统一用 LF 落盘。**不要用默认值**，Windows 下默认会写成 CRLF，
    包名尾巴带上 \\r 后交给 pm 会全部判成 Unknown package（本项目踩过的头号坑）。"""
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path
