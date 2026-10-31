"""命令行入口。"""

from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional

from . import __version__, device as _device
from .adb import Adb, AdbNotFoundError, normalize_text
from . import debloat, probe, smooth, snapshot

SNAPSHOT_DIR = "snapshots"


# ---------------------------------------------------------------- 基础设施

def _fix_windows_encoding() -> None:
    """Windows 中文终端（GBK）下 print 中文会 UnicodeEncodeError，强制用 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


def _make_adb(args) -> Adb:
    return Adb(serial=getattr(args, "serial", None), adb_bin=getattr(args, "adb_bin", None))


def _pick_serial(adb: Adb, serial: Optional[str]) -> str:
    devs = adb.devices()
    if not devs:
        print("没检测到任何设备。请确认：")
        print("  1. 手机已开「USB 调试」，并且**额外**开了「USB 调试（安全设置）」")
        print("  2. 用的是能传数据的数据线（不是纯充电线）")
        print("  3. 手机上点了「允许调试」，且当前已解锁亮屏")
        print("  4. 电脑上没开着手机助手/投屏软件（会抢 adb 连接）")
        print("\n仍不行就重启手机：多次实测显示「USB 调试（安全设置）」打开后被系统拒绝时，重启才生效。")
        sys.exit(2)
    online = [s for s, st in devs if st == "device"]
    if serial:
        if serial not in [s for s, _ in devs]:
            print(f"找不到序列号 {serial}。当前：{[s for s, _ in devs]}")
            sys.exit(2)
        if serial not in online:
            st = dict(devs).get(serial, "")
            print(f"设备 {serial} 状态为 {st}。")
            if st == "unauthorized":
                print("→ 手机上点「允许 USB 调试」并勾「始终允许」即可。")
            sys.exit(2)
        return serial
    if not online:
        print(f"检测到设备但没有可用的：{devs}")
        sys.exit(2)
    if len(online) > 1:
        print("检测到多台设备，请用 -s 指定：")
        for s, st in devs:
            print(f"  {s}  {st}")
        sys.exit(2)
    return online[0]


def _confirm(prompt: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    try:
        ans = input(f"{prompt} [y/N] ").strip().lower()
    except EOFError:
        return False
    return ans in ("y", "yes")


def _require_device(args):
    try:
        adb = _make_adb(args)
    except AdbNotFoundError as e:
        print(str(e))
        sys.exit(2)
    args.serial = _pick_serial(adb, getattr(args, "serial", None))
    adb.serial = args.serial
    return adb, _device.detect(adb, args.serial)


# ---------------------------------------------------------------- 子命令

def cmd_devices(args) -> int:
    adb = _make_adb(args)
    devs = adb.devices()
    if not devs:
        print("（无设备）")
        return 0
    for s, st in devs:
        mark = {"device": "可用", "unauthorized": "未授权（去手机点允许）",
                "offline": "离线", "authorizing": "授权中"}.get(st, st)
        extra = ""
        if st == "device":
            d = _device.detect(adb, s)
            extra = f"  {d.model}  {d.rom_display}"
        print(f"{s:<20} {mark}{extra}")
    return 0


def cmd_detect(args) -> int:
    adb, info = _require_device(args)
    print(_device.describe(info))
    return 0


def cmd_probe(args) -> int:
    adb, info = _require_device(args)
    installed = set(adb.list_packages())
    preferred = None
    if not args.no_uad:
        try:
            from .safety import load_keep_patterns
            plan = debloat.build_plan(installed, info.families, include_advanced=args.include_advanced)
            preferred = plan.packages
        except Exception as e:
            print(f"[提示] 取 UAD 清单失败（{e}），改用内置探针。")

    report = probe.run(adb, installed, info, preferred=preferred, n=args.n)
    print(probe.render(report))
    if args.save:
        path = probe.save(report)
        print(f"\n报告已保存：{path}")
        print("欢迎把它连同机型一起提 PR 到 docs/实测/ —— 这类真实数据比任何文档都有说服力。")
    return 0


def cmd_smooth(args) -> int:
    adb, info = _require_device(args)
    print(f"目标设备：{info.label}")
    print("\n当前设置：")
    anim = smooth.read_animation(adb)
    for k, v in anim.items():
        print(f"  {k:<28} = {v}")
    bg = adb.settings_get("global", smooth.BG_LIMIT_KEY)
    print(f"  {'background_process_limit':<28} = {bg}")

    keys = {"global": list(smooth.ANIM_KEYS) + [smooth.BG_LIMIT_KEY]}
    snap = snapshot.capture(adb, info, keys)
    snap.note = "smooth 调优前的基线"

    planned = []
    if args.scale is not None:
        planned.append(f"三个动画缩放 → {smooth._fmt(args.scale)}")
    if args.bg_limit is not None:
        planned.append(f"后台进程限制 → {args.bg_limit}")

    print("\n将要改动：")
    for p in planned:
        print(f"  - {p}")
    if not planned:
        print("  （无：请用 --scale / --bg-limit 指定）")
        return 0
    if not _confirm("确认执行？", args.yes):
        print("已取消。")
        return 1

    if args.scale is not None:
        changes = smooth.set_animation(adb, args.scale)
        print("\n动画缩放已改：")
        for k, (old, new) in changes.items():
            print(f"  {k}: {old} → {new}")
    if args.bg_limit is not None:
        old, new = smooth.set_background_limit(adb, args.bg_limit)
        print(f"后台限制: {old} → {new}")

    path = snapshot.save(snap)
    print(f"\n基线快照已保存（改前的值）：{path}")
    print("还原命令：python -m tune restore " + os.path.basename(path))
    return 0


def cmd_memory(args) -> int:
    adb, info = _require_device(args)
    print(f"目标设备：{info.label}\n")
    hits = smooth.detect_memory_extension(adb)
    if not hits:
        print("没探测到内存扩展相关的 settings 键（可能在别的存储位置，或该 ROM 无此功能）。")
    else:
        print("探测到以下候选键：")
        for ns, k, v in hits:
            print(f"  [{ns}] {k} = {v}")
    print()
    print("⚠ 本工具**不自动改**这项。实测某些 ROM 读出的值格式异常（如 '256 GB' 而非数字），")
    print("  盲目改写有把内存管理搞乱的风险。")
    print()
    print(smooth.MANUAL_MEMORY_PATH)
    return 0


def cmd_plan(args) -> int:
    adb, info = _require_device(args)
    installed = set(adb.list_packages())
    plan = debloat.build_plan(installed, info.families, include_advanced=args.include_advanced)
    plan.model, plan.rom = info.model, info.rom_display
    print(f"目标设备：{info.label}   ROM：{info.rom_display or '未取到'}")
    print(f"本机已装包：{len(installed)}\n")
    print(debloat.render_plan(plan))
    if args.export:
        md = debloat.export_markdown(plan, info)
        debloat.write_text(args.export, md)
        print(f"\n已导出：{args.export}")
    if plan.packages:
        out = os.path.join(SNAPSHOT_DIR, "freeze_list.txt")
        debloat.write_text(out, "\n".join(plan.packages) + "\n")
        print(f"包名清单（LF 行尾，可直接喂给 pm）：{out}")
    return 0


def cmd_freeze(args) -> int:
    adb, info = _require_device(args)
    src = args.file or os.path.join(SNAPSHOT_DIR, "freeze_list.txt")
    if not os.path.isfile(src):
        print(f"没找到清单文件：{src}\n请先跑 `plan` 生成，或用 --file 指定。")
        return 2
    pkgs = [normalize_text(l) for l in open(src, encoding="utf-8") if l.strip()]
    pkgs = [p for p in pkgs if p and not p.startswith("#")]
    print(f"目标设备：{info.label}")
    print(f"待冻结：{len(pkgs)} 个（来源 {src}）")
    if args.dry_run:
        for p in pkgs:
            print(f"  {p}")
        print("\n（--dry-run，未做任何改动）")
        return 0
    if not _confirm("确认冻结？这些是可 disable-user 的，随时可还原；但照样会有部分被 ROM 拒绝。", args.yes):
        print("已取消。")
        return 1

    outcome = debloat.freeze(adb, pkgs)
    print()
    print("—" * 62)
    print(f"冻结完成：成功 {len(outcome.frozen)} / 被系统锁死 {len(outcome.locked)} "
          f"/ 未安装 {len(outcome.missing)} / 未知 {len(outcome.unknown)}")
    if outcome.lock_ratio > 0.5:
        print()
        print(f"⚠ 锁死比例高达 {outcome.lock_ratio:.0%} —— 这台 ROM 基本不支持 debloat 路线。")
        print("  这不是工具坏了。建议改用 uiautomator2 模拟点设置开关的方式，")
        print("  推荐配合 android-ad-cleaner：https://github.com/rddmwmddr/android-ad-cleaner")
    print("—" * 62)

    snap = snapshot.Snapshot(serial=info.serial, model=info.model, rom=info.rom_display,
                             created_at=__import__("time").strftime("%Y-%m-%d %H:%M:%S"),
                             settings={}, frozen=outcome.frozen, note="freeze")
    path = snapshot.save(snap)
    print(f"已冻结的包记录在快照里，可一键还原：\n  python -m tune restore {os.path.basename(path)}")
    return 0


def cmd_unfreeze(args) -> int:
    adb, info = _require_device(args)
    pkgs: List[str] = []
    if args.file:
        pkgs = [normalize_text(l) for l in open(args.file, encoding="utf-8") if l.strip()]
    elif args.package:
        pkgs = [normalize_text(p) for p in args.package]
    else:
        print("请用 --file 或 --package 指定要还原的包。")
        return 2
    print(f"目标设备：{info.label}   待还原 {len(pkgs)} 个")
    debloat.unfreeze(adb, pkgs)
    return 0


def cmd_snapshot(args) -> int:
    snaps = snapshot.list_snapshots()
    if not snaps:
        print("暂无快照。")
        return 0
    for p in snaps:
        s = snapshot.load(p)
        print(f"{os.path.basename(p)}")
        print(f"    设备 {s.model} / {s.serial}   时间 {s.created_at}")
        print(f"    settings 项 {sum(len(v) for v in s.settings.values())}  已冻结包 {len(s.frozen)}")
    return 0


def cmd_restore(args) -> int:
    adb, info = _require_device(args)
    path = args.file
    if not os.path.isfile(path) and os.path.isfile(os.path.join(SNAPSHOT_DIR, path)):
        path = os.path.join(SNAPSHOT_DIR, path)
    if not os.path.isfile(path):
        print(f"找不到快照：{path}")
        return 2
    snap = snapshot.load(path)
    print(f"快照来自 {snap.model}（{snap.serial}），当前设备是 {info.label}")
    if snap.serial and snap.serial != info.serial and not args.force:
        print("序列号不一致，加 --force 才继续。")
        return 2
    print(f"将还原 {sum(len(v) for v in snap.settings.values())} 项设置、解冻 {len(snap.frozen)} 个包。")
    if not _confirm("确认还原？", args.yes):
        print("已取消。")
        return 1
    for k, v in snapshot.restore_settings(adb, snap).items():
        print(f"  设置 {k}: {v}")
    for pkg, st in snapshot.restore_packages(adb, snap).items():
        print(f"  包 {pkg}: {st}")
    print("\n还原完成。部分设置可能需要重启或重新插拔才完全生效。")
    return 0


def cmd_finish(args) -> int:
    adb, info = _require_device(args)
    old, new = smooth.set_stay_awake(adb, False)
    print(f"插电常亮已还原：{old} → {new}")
    if args.dev_off:
        if _confirm("即将关闭开发者模式，之后 adb 会断开（所有改动会保留）。确认？", args.yes):
            out = adb.shell("settings put global development_settings_enabled 0")
            print(f"开发者模式已尝试关闭：{out or 'ok'}")
            print("请到手机上确认：设置 → 搜「开发者选项」，入口消失即已关（小米系行为）。")
        else:
            print("未关闭开发者模式。")
    else:
        print("\n建议手动关闭开发者模式：设置 → 开发者选项 → 关最上方总开关。")
        print("（不加 --dev-off 就不会自动关，避免误断开。）")
    return 0


# ---------------------------------------------------------------- 入口

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m tune",
        description="droid-tune：不 root 的国产安卓流畅化调优工具（可还原）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="推荐顺序： devices → detect → probe → smooth → plan → freeze → finish",
    )
    p.add_argument("--version", action="version", version=f"droid-tune {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("-s", "--serial", help="设备序列号（只有一台时可省略）")
        sp.add_argument("--adb-bin", dest="adb_bin", help="手动指定 adb 路径")

    sp = sub.add_parser("devices", help="列出已连接设备")
    common(sp)

    sp = sub.add_parser("detect", help="查看设备与 ROM 信息")
    common(sp)

    sp = sub.add_parser("probe", help="★ 可行性探测：这台 ROM 让不让禁用包")
    common(sp)
    sp.add_argument("-n", type=int, default=6, help="探针样本数（默认 6）")
    sp.add_argument("--no-uad", action="store_true", help="不联网取 UAD 清单，只用内置探针")
    sp.add_argument("--include-advanced", action="store_true", help="探针也考虑 UAD 的 Advanced 分级")
    sp.add_argument("--save", action="store_true", help="保存探测报告到 snapshots/")

    sp = sub.add_parser("smooth", help="流畅调优：动画缩放 / 后台限制")
    common(sp)
    sp.add_argument("--scale", type=float, help="动画缩放值，如 0.5；0 为完全关闭动画")
    sp.add_argument("--bg-limit", dest="bg_limit", type=int, help="后台进程数上限，建议 3~4，别设 1")
    sp.add_argument("-y", "--yes", action="store_true", help="跳过确认")

    sp = sub.add_parser("memory", help="探测「内存扩展」开关（只探测，不自动改）")
    common(sp)

    sp = sub.add_parser("plan", help="生成冻结候选清单（只读，不改设备）")
    common(sp)
    sp.add_argument("--include-advanced", action="store_true", help="包含 UAD 的 Advanced 分级")
    sp.add_argument("--export", help="导出 Markdown 清单到指定路径")

    sp = sub.add_parser("freeze", help="执行冻结（可还原）")
    common(sp)
    sp.add_argument("--file", help="包名清单文件（默认 snapshots/freeze_list.txt）")
    sp.add_argument("--dry-run", action="store_true", help="只打印将要冻结的包")
    sp.add_argument("-y", "--yes", action="store_true", help="跳过确认")

    sp = sub.add_parser("unfreeze", help="解冻指定包")
    common(sp)
    sp.add_argument("--file", help="包名清单文件")
    sp.add_argument("--package", nargs="+", help="直接给出包名")

    sp = sub.add_parser("snapshot", help="列出已有快照")
    common(sp)

    sp = sub.add_parser("restore", help="按快照还原")
    common(sp)
    sp.add_argument("file", help="快照文件名或路径")
    sp.add_argument("--force", action="store_true", help="序列号不一致也继续")
    sp.add_argument("-y", "--yes", action="store_true", help="跳过确认")

    sp = sub.add_parser("finish", help="收尾：还原防锁屏，必要时关开发者模式")
    common(sp)
    sp.add_argument("--dev-off", action="store_true", help="同时尝试关闭开发者模式（会断开连接）")
    sp.add_argument("-y", "--yes", action="store_true", help="跳过确认")

    return p


def main(argv: Optional[List[str]] = None) -> int:
    _fix_windows_encoding()
    args = build_parser().parse_args(argv)
    try:
        return {
            "devices": cmd_devices, "detect": cmd_detect, "probe": cmd_probe,
            "smooth": cmd_smooth, "memory": cmd_memory, "plan": cmd_plan,
            "freeze": cmd_freeze, "unfreeze": cmd_unfreeze,
            "snapshot": cmd_snapshot, "restore": cmd_restore, "finish": cmd_finish,
        }[args.cmd](args)
    except AdbNotFoundError as e:
        print(str(e))
        return 2
    except KeyboardInterrupt:
        print("\n已中断。")
        return 130


if __name__ == "__main__":
    sys.exit(main())
