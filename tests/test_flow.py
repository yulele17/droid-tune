"""无真机跑逻辑测试：python -m unittest discover tests

重点守护这几类东西（都是踩过坑、又极易被后续改动悄悄破坏的）：
  - pm 返回的四种语义能不能被正确分类
  - 快照能不能完整往返还原
  - 清单文件落盘必须是 LF（CRLF 会让整批包名报 Unknown package）
  - probe 的判定阈值与建议文案
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.fake_adb import FakeAdb  # noqa: E402
from tune import debloat, probe, smooth, snapshot  # noqa: E402
from tune import device as device_mod  # noqa: E402


def fake_info():
    adb = FakeAdb([])
    return device_mod.DeviceInfo(serial="TEST0001", model="Test Phone", families=["xiaomi"], rom_display="TEST.1")


class TestPmClassification(unittest.TestCase):
    def setUp(self):
        self.adb = FakeAdb(
            packages=["com.a.freezable", "com.b.locked", "com.c.plain"],
            locked={"com.b.locked"},
        )

    def test_freezable_and_locked(self):
        out = debloat.freeze(self.adb, ["com.a.freezable", "com.b.locked", "com.nope.notinstalled"], quiet=True)
        self.assertIn("com.a.freezable", out.frozen)
        self.assertIn("com.b.locked", out.locked)
        self.assertIn("com.nope.notinstalled", out.missing)

    def test_lock_ratio(self):
        out = debloat.freeze(self.adb, ["com.a.freezable", "com.b.locked"], quiet=True)
        self.assertAlmostEqual(out.lock_ratio, 0.5)

    def test_reentrant_freeze(self):
        """重跑不应出错，已禁用的包仍算成功。"""
        debloat.freeze(self.adb, ["com.a.freezable"], quiet=True)
        again = debloat.freeze(self.adb, ["com.a.freezable"], quiet=True)
        self.assertEqual(again.frozen, ["com.a.freezable"])
        self.assertEqual(again.locked, [])

    def test_unfreeze_restores(self):
        debloat.freeze(self.adb, ["com.a.freezable"], quiet=True)
        debloat.unfreeze(self.adb, ["com.a.freezable"], quiet=True)
        self.assertNotIn("com.a.freezable", self.adb.list_packages("-d"))


class TestSmooth(unittest.TestCase):
    def setUp(self):
        self.adb = FakeAdb([], props={})

    def test_animation_scale(self):
        changes = smooth.set_animation(self.adb, 0.5)
        self.assertEqual(len(changes), 3)
        for k, (old, new) in changes.items():
            self.assertEqual(new, "0.5")
        self.assertEqual(self.adb.settings_get("global", "window_animation_scale"), "0.5")

    def test_background_limit(self):
        smooth.set_background_limit(self.adb, 3)
        self.assertEqual(self.adb.settings_get("global", smooth.BG_LIMIT_KEY), "3")
        smooth.clear_background_limit(self.adb)
        self.assertEqual(self.adb.settings_get("global", smooth.BG_LIMIT_KEY), "null")

    def test_stay_awake_toggle(self):
        smooth.set_stay_awake(self.adb, True)
        self.assertEqual(self.adb.settings_get("global", smooth.STAY_AWAKE_KEY), "7")
        smooth.set_stay_awake(self.adb, False)
        self.assertEqual(self.adb.settings_get("global", smooth.STAY_AWAKE_KEY), "0")

    def test_memory_probe_does_not_write(self):
        self.adb.global_settings["miui_memory_size"] = "256 GB"
        hits = smooth.detect_memory_extension(self.adb)
        self.assertTrue(any(k == "miui_memory_size" for _ns, k, _v in hits))
        self.assertEqual(self.adb.global_settings["miui_memory_size"], "256 GB")


class TestSnapshot(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.adb = FakeAdb(["com.a.freezable"], locked=set())

    def test_roundtrip(self):
        before = {"global": {"window_animation_scale": "1.0"}}
        snap = snapshot.Snapshot(serial="TEST0001", model="Test Phone", rom="TEST.1",
                                 created_at="2026-01-01 00:00:00",
                                 settings={"global": {"window_animation_scale": "1.0"}},
                                 frozen=["com.a.freezable"])
        path = os.path.join(self.tmp, "s.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write(snap.to_json())

        # 把设备改成别的样子
        smooth.set_animation(self.adb, 0.5)
        debloat.freeze(self.adb, ["com.a.freezable"], quiet=True)

        loaded = snapshot.load(path)
        snapshot.restore_settings(self.adb, loaded)
        snapshot.restore_packages(self.adb, loaded)

        self.assertEqual(self.adb.settings_get("global", "window_animation_scale"), "1.0")
        self.assertNotIn("com.a.freezable", self.adb.list_packages("-d"))


class TestLineEndings(unittest.TestCase):
    def test_write_text_is_lf(self):
        """CRLF 会让整批包名被判 Unknown package，这条防回归。"""
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "list.txt")
        debloat.write_text(path, "com.a\ncom.b\n")
        with open(path, "rb") as f:
            raw = f.read()
        self.assertNotIn(b"\r\n", raw)
        self.assertTrue(raw.endswith(b"\n"))


class TestProbe(unittest.TestCase):
    def test_pick_probes_skips_protected(self):
        installed = {"com.miui.packageinstaller", "com.miui.analytics"}
        picked = probe.pick_probes(installed, preferred=["com.miui.packageinstaller", "com.miui.analytics"], n=5)
        self.assertNotIn("com.miui.packageinstaller", picked)
        self.assertIn("com.miui.analytics", picked)

    def test_verdict_when_mostly_locked(self):
        pkgs = [f"com.x.p{i}" for i in range(10)]
        adb = FakeAdb(pkgs, locked={p for p in pkgs[1:]})
        rep = probe.run(adb, set(pkgs), fake_info(), preferred=pkgs, n=10, quiet=True)
        self.assertEqual(rep.ok, 1)
        self.assertEqual(rep.locked, 9)
        self.assertEqual(rep.verdict, "debloat 路线基本不可行")
        self.assertIn("不要", rep.advice)

    def test_probe_restores_state(self):
        """探测完必须回到原状 —— 这是 probe 无副作用的前提。"""
        pkgs = ["com.a.one", "com.b.two"]
        adb = FakeAdb(pkgs, locked=set())
        probe.run(adb, set(pkgs), fake_info(), preferred=pkgs, n=2, quiet=True)
        self.assertEqual(adb.list_packages("-d"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
