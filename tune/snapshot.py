"""快照与还原 —— 所有改动的前置保险，也是最后的后悔药。

设计原则：**任何会改变设备的命令，都必须先有快照。**
快照是纯文本 JSON，里面同时记录了：
  - 改之前的 settings 值（按 namespace 分桶）
  - 实际冻结成功的包名
  - 因为命中保护名单而没动的包名（留证据，方便复盘）
这样 `restore` 不需要联网、不需要原始清单，离线也能一键回退。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Dict, List

from .adb import Adb, ST_OK


@dataclass
class Snapshot:
    serial: str
    model: str
    rom: str
    created_at: str
    settings: Dict[str, Dict[str, str]] = field(default_factory=dict)
    frozen: List[str] = field(default_factory=list)
    protected: Dict[str, str] = field(default_factory=dict)
    note: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)


def _safe_name(model: str) -> str:
    cleaned = "".join(c for c in model if c not in " /\\:*?\"<>|")
    return cleaned or "unknown"


def capture(adb: Adb, info, keys: Dict[str, List[str]]) -> Snapshot:
    """读取当前 settings 值作为基线。keys 形如 {"global": [...], "system": [...]}"""
    settings: Dict[str, List[str]] = {}
    for ns, ks in keys.items():
        bucket = {}
        for k in ks:
            bucket[k] = adb.settings_get(ns, k)
        settings[ns] = bucket
    return Snapshot(
        serial=info.serial, model=info.model, rom=info.rom_display,
        created_at=time.strftime("%Y-%m-%d %H:%M:%S"), settings=settings,
    )


def save(snap: Snapshot, out_dir: str = "snapshots") -> str:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"snapshot-{_safe_name(snap.model)}-{time.strftime('%Y%m%d-%H%M%S')}.json")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(snap.to_json())
    return path


def load(path: str) -> Snapshot:
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    return Snapshot(
        serial=d.get("serial", ""), model=d.get("model", ""), rom=d.get("rom", ""),
        created_at=d.get("created_at", ""), settings=d.get("settings", {}),
        frozen=d.get("frozen", []), protected=d.get("protected", {}), note=d.get("note", ""),
    )


def list_snapshots(out_dir: str = "snapshots") -> List[str]:
    if not os.path.isdir(out_dir):
        return []
    return sorted(
        os.path.join(out_dir, f) for f in os.listdir(out_dir)
        if f.startswith("snapshot-") and f.endswith(".json")
    )


def restore_settings(adb: Adb, snap: Snapshot) -> Dict[str, str]:
    """还原快照里记录的 settings 值。特殊的 null 表示"原本未设置"，用 delete 处理。"""
    results: Dict[str, str] = {}
    for ns, bucket in snap.settings.items():
        for k, v in bucket.items():
            if v in ("null", ""):
                adb.shell(f"settings delete {ns} {k}")
                results[f"{ns}.{k}"] = "deleted"
            else:
                adb.settings_put(ns, k, v)
                results[f"{ns}.{k}"] = f"restored={adb.settings_get(ns, k)}"
    return results


def restore_packages(adb: Adb, snap: Snapshot) -> Dict[str, str]:
    """解冻快照里记录的所有包。"""
    results: Dict[str, str] = {}
    for pkg in snap.frozen:
        res = adb.set_package_enabled(pkg, enabled=True)
        results[pkg] = "enabled" if res.status == ST_OK else f"failed: {res.raw[:120]}"
    return results
