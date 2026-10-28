"""util: money formatting/rounding, coercion of untrusted values, json helpers."""

import tempfile
import unittest
from pathlib import Path

import test_support  # noqa: F401
from fgold import util


class TestMoney(unittest.TestCase):
    def test_fmt_money(self):
        cases = {0: "0c", 5: "5c", 150: "1s 50c", 10000: "1g", 10050: "1g 50c", 1234567: "123g 45s 67c",
                 -250: "-2s 50c", 12345678900: "1,234,567g 89s"}
        for c, s in cases.items():
            self.assertEqual(util.fmt_money(c), s)
        self.assertEqual(util.fmt_money("junk"), "?")

    def test_money_round_is_half_up(self):
        self.assertEqual([util.money_round(x) for x in (0.5, 1.5, 2.5, 2.49, -0.5)], [1, 2, 3, 2, 0])

    def test_ceil_silver(self):
        self.assertEqual([util.ceil_silver(x) for x in (-5, 0, 1, 99, 100, 101, 34, 250)],
                         [0, 0, 100, 100, 100, 200, 100, 300])


class TestCoercion(unittest.TestCase):
    def test_as_int(self):
        self.assertEqual([util.as_int(v) for v in (5, 5.0, "12", " -7 ", "--5", "1e3", 5.5, True, None, [1])],
                         [5, 5, 12, -7, None, None, None, None, None, None])
        self.assertEqual(util.as_int("x", 0), 0)

    def test_item_id(self):
        self.assertEqual([util.item_id(v) for v in (2447, "2447", 2447.0, 0, -1, "abc", True)],
                         [2447, 2447, 2447, None, None, None, None])

    def test_as_list_and_dict(self):
        self.assertEqual(util.as_list([1, 2]), [1, 2])
        self.assertEqual(util.as_list({2: "b", 1: "a", "x": "y"}), ["a", "b"])
        self.assertEqual(util.as_list("nope"), [])
        self.assertEqual(util.as_dict({1: "a"}), {1: "a"})
        self.assertEqual(util.as_dict(["a", "b"]), {1: "a", 2: "b"})
        self.assertEqual(util.as_dict(None), {})

    def test_as_str_num_bool(self):
        self.assertEqual(util.as_str(5), "5")
        self.assertIsNone(util.as_str(True))
        self.assertEqual(util.as_str("x" * 900)[:3], "xxx")
        self.assertEqual(len(util.as_str("x" * 900)), 500)
        self.assertIsNone(util.as_num(float("nan")))
        self.assertEqual(util.as_num(2.5), 2.5)
        self.assertIsNone(util.as_bool(1))
        self.assertIs(util.as_bool(False), False)


class TestFiles(unittest.TestCase):
    def test_json_gz_jsonl_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            util.write_json_gz(d / "a" / "x.json.gz", {"k": [1, 2], "é": "ü"})
            self.assertEqual(util.read_json_gz(d / "a" / "x.json.gz"), {"k": [1, 2], "é": "ü"})
            first = (d / "a" / "x.json.gz").read_bytes()
            util.write_json_gz(d / "a" / "x.json.gz", {"é": "ü", "k": [1, 2]})
            self.assertEqual((d / "a" / "x.json.gz").read_bytes(), first, "deterministic gzip")
            (d / "l.jsonl").write_text(util.jsonl_text([{"a": 1}, {"b": 2}]) + "not json\n\n", encoding="utf-8")
            self.assertEqual(util.read_jsonl(d / "l.jsonl"), [{"a": 1}, {"b": 2}])
            self.assertEqual(util.read_jsonl(d / "missing.jsonl"), [])
            self.assertIsNone(util.read_json(d / "missing.json"))
            util.write_json(d / "j.json", {"b": 1, "a": 2})
            self.assertEqual((d / "j.json").read_text(encoding="utf-8"), '{\n  "a": 2,\n  "b": 1\n}\n')


if __name__ == "__main__":
    unittest.main()
