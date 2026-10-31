"""设备识别 —— 决定后面所有策略能不能套用的前提。

为什么要把 ROM 版本单独拎出来：debloat 的可行性高度依赖 ROM，而不是安卓大版本。
同样是 Android 14+，国行 HyperOS 3 会把绝大多数自家预装锁成"不可禁用系统包"，
而某些海外版/类原生 ROM 允许直接 disable。踩过一次坑就明白该先看这里。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from .adb import Adb, normalize_text

# 包名前缀 → ROM 家族。用于后面选"该套用哪份清单 / 该给什么风险提示"
FAMILY_PREFIXES = {
    "xiaomi": ("com.miui.", "com.xiaomi.", "com.mi.", "com.mipay.", "com.mig."),
    "huawei": ("com.huawei.", "com.hicloud.", "com.android.hwmirage"),
    "honor": ("com.hihonor.", "com.honor."),
    "oppo": ("com.coloros.", "com.oppo.", "com.heytap."),
    "vivo": ("com.vivo.", "com.bbk.", "com.iqoo."),
    "oneplus": ("com.oneplus.", "com.oplus."),
    "samsung": ("com.samsung.", "com.sec."),
    "google": ("com.google.",),
}


@dataclass
class DeviceInfo:
    serial: str
    manufacturer: str = ""
    brand: str = ""
    model: str = ""            # 市场名，如 "Redmi Note 13 Pro"
    device_code: str = ""      # 内部代号，如 2312DRA50C
    android_version: str = ""
    sdk: str = ""
    rom_display: str = ""      # 如 OS3.0.306.0.WNRCNXM
    fingerprint: str = ""
    families: list = field(default_factory=list)

    @property
    def label(self) -> str:
        parts = [p for p in (self.brand, self.model, self.device_code) if p]
        return " ".join(parts) or self.serial


def detect(adb: Adb, serial: Optional[str] = None) -> DeviceInfo:
    serial = serial or adb.serial or ""
    info = DeviceInfo(serial=serial)
    info.manufacturer = adb.getprop("ro.product.manufacturer")
    info.brand = adb.getprop("ro.product.brand")
    info.model = adb.getprop("ro.product.model")
    info.device_code = adb.getprop("ro.product.device")
    info.android_version = adb.getprop("ro.build.version.release")
    info.sdk = adb.getprop("ro.build.version.sdk")
    info.fingerprint = adb.getprop("ro.build.fingerprint")

    # ROM 展示版本：各厂 key 不统一，逐个试
    rom_keys = (
        "ro.build.version.incremental",       # 小米/多数厂商：递增版本号
        "ro.mi.os.version.name",              # HyperOS/Pengpai OS 名
        "ro.system.build.version.incremental",
        "ro.build.display.id",                # 有些机型才是完整展示串
    )
    versions = []
    for k in rom_keys:
        v = adb.getprop(k)
        if v and v not in versions:
            versions.append(v)
    info.rom_display = versions[0] if versions else ""

    families = set()
    low_all = (info.fingerprint + "|" + info.brand + "|" + info.manufacturer + "|" + info.model).lower()
    if re.search(r"hyperos|miui|xiaomi|redmi|poco", low_all):
        families.add("xiaomi")
    for token, fam in (
        ("huawei", "huawei"), ("honor", "honor"),
        ("oppo", "oppo"), ("coloros", "oppo"), ("realme", "oppo"),
        ("vivo", "vivo"), ("iqoo", "vivo"),
        ("oneplus", "oneplus"), ("samsung", "samsung"),
    ):
        if token in low_all:
            families.add(fam)
    info.families = sorted(families)
    return info


def family_from_package(package: str) -> Optional[str]:
    """按包名前缀反推厂商，用于给 UAD 清单里的条目归类。"""
    pkg = normalize_text(package)
    for fam, prefixes in FAMILY_PREFIXES.items():
        for p in prefixes:
            if pkg.startswith(p):
                return fam
    return None


def describe(info: DeviceInfo) -> str:
    lines = [
        f"序列号     : {info.serial}",
        f"型号       : {info.model}（{info.device_code}）",
        f"厂商       : {info.manufacturer} / {info.brand}",
        f"安卓版本   : {info.android_version}（SDK {info.sdk}）",
        f"ROM 版本   : {info.rom_display or '（未取到）'}",
        f"ROM 家族   : {', '.join(info.families) if info.families else '未识别（多半是类原生）'}",
    ]
    return "\n".join(lines)
