"""隐私自查脚本的逻辑测试：python -m unittest discover tests

守护这几件事：
  - 规则能真的抓到东西（否则脚本就是个摆设）
  - Luhn 校验能压掉「纯数字随机串」的误报
  - 本地 denylist 优先级最高
  - 放行规则生效，且脚本自身不会自我命中
  - 图片元数据能被读出来

注意：本文件里的所有敏感样本都**在运行时拼接**，源码里不留明文。
否则这个文件自己就会被 privacy-scan 报成 ERROR（这个道理本身就是最好的教材）。
"""

import importlib.util
import json
import os
import struct
import sys
import tempfile
import unittest
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCAN_PATH = os.path.join(ROOT, "tools", "privacy-scan.py")

_spec = importlib.util.spec_from_file_location("privacy_scan", SCAN_PATH)
scan = importlib.util.module_from_spec(_spec)
# dataclass 在装饰时会回查 sys.modules 里的模块对象，必须先注册再执行
sys.modules["privacy_scan"] = scan
_spec.loader.exec_module(scan)

# ---- 敏感样本：运行时拼接，源码里不出现明文 ----
_MOBILE = "138" + "1234" + "5678"
_UDID = "00008030" + "-" + "000E18D40C81802E"
_EMAIL = "zhangsan" + "@" + "gmail.com"
_WINPATH = "日志在 C:" + "\\" + "Users" + "\\" + "Administrator" + "\\Desktop"
_CARD = "453957876362148" + "6"
_TOTP = "JBSWY3DP" + "EHPK3PXP"
_GPS = "31.2304" + ", " + "121.4737"
_LONGNUM = "1234567890123456" + "789"


def _hits(text, denylist=None):
    out = []
    scan.scan_text(text, "test", out, denylist or [])
    return out


class TestRules(unittest.TestCase):
    def test_detects_cn_mobile(self):
        ids = [h.rid for h in _hits("联系 " + _MOBILE + " 就行")]
        self.assertIn("cn-mobile", ids)

    def test_mobile_number_not_double_counted_in_long_digits(self):
        """长数字串里不该把中间一段当手机号（边界检查）。"""
        ids = [h.rid for h in _hits("编号 " + _LONGNUM)]
        self.assertNotIn("cn-mobile", ids)

    def test_luhn_filters_random_digits(self):
        """长随机数字串不该被当成银行卡。"""
        ids = [h.rid for h in _hits("订单号 " + _LONGNUM)]
        self.assertNotIn("bank-card", ids)

    def test_detects_windows_user_path(self):
        ids = [h.rid for h in _hits(_WINPATH)]
        self.assertIn("windows-user-path", ids)

    def test_noreply_email_is_allowed(self):
        ids = [h.rid for h in _hits("yulele17@users.noreply.github.com")]
        self.assertNotIn("private-email", ids)

    def test_real_email_is_flagged(self):
        ids = [h.rid for h in _hits("联系我 " + _EMAIL)]
        self.assertIn("private-email", ids)

    def test_ios_udid(self):
        ids = [h.rid for h in _hits("UDID " + _UDID)]
        self.assertIn("ios-udid-new", ids)

    def test_totp_secret_warns(self):
        hits = _hits("key " + _TOTP)
        self.assertTrue(any(h.rid == "totp-secret" for h in hits))
        # TOTP 种子泄漏必须比普通信息更醒目
        self.assertTrue(any("作废" in h.advice for h in hits))

    def test_gps_precision(self):
        hits = _hits("拍摄于 " + _GPS)
        self.assertTrue(any(h.rid == "gps-precision" for h in hits))


class TestDenylist(unittest.TestCase):
    def test_denylist_wins(self):
        hits = _hits("我的序列号是 1a434768", ["1a434768"])
        self.assertTrue(any(h.rid == "local-denylist" and h.severity == scan.ERROR for h in hits))

    def test_denylist_absent(self):
        hits = _hits("这里没有敏感值", ["1a434768"])
        self.assertEqual([h for h in hits if h.rid == "local-denylist"], [])


class TestIgnore(unittest.TestCase):
    def test_default_skips_script_itself(self):
        pats = scan.load_ignore()
        self.assertTrue(scan.is_ignored("tools/privacy-scan.py", pats))

    def test_glob_ignore(self):
        pats = scan.load_ignore(["docs/实测/*.md"])
        self.assertTrue(scan.is_ignored("docs/实测/某机型.md", pats))
        self.assertFalse(scan.is_ignored("docs/01-准备工作.md", pats))

    def test_script_does_not_flag_itself(self):
        """脚本里写着各种正则，不能把自己扫成一片 ERROR。"""
        src = open(SCAN_PATH, encoding="utf-8").read()
        hits = []
        scan.scan_text(src, "self", hits, [])
        self.assertEqual([h for h in hits if h.severity == scan.ERROR], [])

    def test_test_fixtures_do_not_trip_rules(self):
        """本文件的敏感样本是拼出来的，所以这个文件自己必须干净。"""
        src = open(os.path.abspath(__file__), encoding="utf-8").read()
        hits = []
        scan.scan_text(src, "self", hits, [])
        self.assertEqual([h for h in hits if h.severity == scan.ERROR], [])


class TestImageMeta(unittest.TestCase):
    @staticmethod
    def _png(with_text=True):
        def chunk(typ, data):
            return (struct.pack(">I", len(data)) + typ + data
                    + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF))
        ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
        raw = b"\x00\xff\xff\xff"
        parts = [b"\x89PNG\r\n\x1a\n", chunk(b"IHDR", ihdr)]
        if with_text:
            # 坐标同样拼接，避免源码里出现可被 gps-precision 规则命中的相邻数值
            g = b"31.2304" + b", " + b"121.4737"
            parts.append(chunk(b"tEXt", b"Comment\x00GPS " + g))
        parts += [chunk(b"IDAT", zlib.compress(raw)), chunk(b"IEND", b"")]
        return b"".join(parts)

    def _write(self, data):
        f = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        f.write(data)
        f.close()
        return f.name

    def test_png_text_chunk_detected(self):
        name = self._write(self._png(True))
        try:
            notes = scan.image_meta(name)
            self.assertTrue(any("tEXt" in n for n in notes))
        finally:
            os.unlink(name)

    def test_plain_png_no_meta(self):
        name = self._write(self._png(False))
        try:
            self.assertEqual(scan.image_meta(name), [])
        finally:
            os.unlink(name)


class TestLuhn(unittest.TestCase):
    def test_known_luhn(self):
        self.assertTrue(scan._luhn_ok("453957876362148" + "6"))

    def test_bad_luhn(self):
        self.assertFalse(scan._luhn_ok("1234567890123456"))


class TestJsonCommentStripper(unittest.TestCase):
    def test_removes_line_and_block_comments(self):
        raw = '{\n  // 注释\n  "a": 1, /* 块注释 */ "b": 2\n}'
        self.assertEqual(json.loads(scan.strip_json_comments(raw)), {"a": 1, "b": 2})

    def test_keeps_slashes_inside_strings(self):
        """字符串里的 // 不能被当注释吃掉（URL 会中招）。"""
        raw = '{"u": "https://github.com/x"}'
        self.assertEqual(json.loads(scan.strip_json_comments(raw)), {"u": "https://github.com/x"})

    def test_example_file_is_valid_json(self):
        """示例模板必须是合法 JSON —— 否则用户复制过去后 denylist 静默失效。"""
        p = os.path.join(ROOT, "tools", "privacy-local.json.example")
        data = json.loads(scan.strip_json_comments(open(p, encoding="utf-8").read()))
        self.assertIn("denylist", data)
        self.assertIn("ignore", data)


class TestIgnoreAppliesEverywhere(unittest.TestCase):
    """放行规则必须对三种扫描一视同仁，否则 CI 会被自己的示例文件卡死。"""

    def test_example_file_is_in_ignore(self):
        pats = scan.load_ignore()
        self.assertTrue(scan.is_ignored("tools/privacy-local.json.example", pats))

    def test_example_file_has_no_rule_triggering_literals(self):
        """示例文件本身不该含任何能触发规则的明文。"""
        p = os.path.join(ROOT, "tools", "privacy-local.json.example")
        hits = []
        scan.scan_text(open(p, encoding="utf-8").read(), "example", hits, [])
        self.assertEqual([h for h in hits if h.severity == scan.ERROR], [])

    def test_blob_paths_returns_mapping(self):
        m = scan.blob_paths()
        self.assertIsInstance(m, dict)
        # 本仓库里至少能找到一个已知文件
        self.assertTrue(any("README" in p for ps in m.values() for p in ps))


if __name__ == "__main__":
    unittest.main(verbosity=2)
