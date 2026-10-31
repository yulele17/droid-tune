"""流畅调优（不 root、可还原）—— 属于"边际收益明显、风险极低"的那档。

三条调整：
  1. 动画缩放       → 最立竿见影的"跟手感"
  2. 后台进程限制   → 省电、减少后台常驻
  3. 内存扩展       → 只探测、不建议自动改（见 detect_memory_extension 的说明）

反面清单（网上流传很广但这里**不做**，原因写在 NOT_RECOMMENDED）：
关闭 MIUI 优化、强制 GPU 渲染、停用 HW 叠加层 —— 收益不明显，副作用反而常见于日常。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .adb import Adb

ANIM_KEYS = (
    "window_animation_scale",
    "transition_animation_scale",
    "animator_duration_scale",
)
BG_LIMIT_KEY = "background_process_limit"
STAY_AWAKE_KEY = "stay_on_while_plugged_in"

NOT_RECOMMENDED = (
    ("关闭 MIUI 优化", "容易让状态栏、通知、权限弹窗出现异常，得不偿失"),
    ("强制 GPU 渲染", "新机型收益微乎其微，可能更耗电、偶发闪退"),
    ("停用 HW 叠加层", "同上，且部分机型直接黑屏"),
    ("禁用系统更新", "能防后台偷更，但会漏掉安全补丁，一般不建议"),
)


def read_animation(adb: Adb) -> Dict[str, str]:
    return {k: adb.settings_get("global", k) for k in ANIM_KEYS}


def set_animation(adb: Adb, scale: float) -> Dict[str, Tuple[str, str]]:
    """设置三个动画缩放，返回 {key: (旧值, 新值)}。"""
    changes: Dict[str, Tuple[str, str]] = {}
    val = _fmt(scale)
    for k in ANIM_KEYS:
        old = adb.settings_get("global", k)
        adb.settings_put("global", k, val)
        changes[k] = (old, adb.settings_get("global", k))
    return changes


def set_background_limit(adb: Adb, limit: int) -> Tuple[str, str]:
    old = adb.settings_get("global", BG_LIMIT_KEY)
    adb.settings_put("global", BG_LIMIT_KEY, str(limit))
    return (old, adb.settings_get("global", BG_LIMIT_KEY))


def clear_background_limit(adb: Adb) -> None:
    adb.shell(f"settings delete global {BG_LIMIT_KEY}")


def set_stay_awake(adb: Adb, on: bool) -> Tuple[str, str]:
    """跑长任务前打开防锁屏（插电时常亮），任务结束要还原原始值。"""
    old = adb.settings_get("global", STAY_AWAKE_KEY)
    adb.settings_put("global", STAY_AWAKE_KEY, "7" if on else "0")
    return (old, adb.settings_get("global", STAY_AWAKE_KEY))


def detect_memory_extension(adb: Adb) -> List[Tuple[str, str, str]]:
    """探测"内存扩展/虚拟内存"开关。

    刻意**不自动改**：实测在 HyperOS 上读到的候选值形如 `miui_memory_size=256 GB`
    ——格式明显不是标准数字 GB，盲目改写有把内存管理搞乱的风险。
    这里只把候选 key 摆出来，把最终决定权交给人（附手动路径）。
    """
    pats = ("memory", "swap", "extend", "zram", "miui_memory", "ram")
    hits: List[Tuple[str, str, str]] = []
    for ns in ("global", "system", "secure"):
        try:
            for k, v in adb.settings_list(ns).items():
                kl = k.lower()
                if any(p in kl for p in pats):
                    hits.append((ns, k, v))
        except Exception:
            continue
    return hits


MANUAL_MEMORY_PATH = (
    "手动关闭路径：设置 → 省电与电池（或「更多设置」）→ 内存扩展 → 关。\n"
    "各 ROM 入口名可能不同，直接在设置里搜「内存扩展」最快。"
)


def _fmt(v: float) -> str:
    """统一成一位小数，避免 0.5 变成 '0.5' 之外的歧义写法。"""
    return ("%g" % float(v))
