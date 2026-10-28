"""config: defaults, validation messages (cause + fix), save/load."""

import json
import tempfile
import unittest
from pathlib import Path

import test_support as ts
from fgold import config
from fgold.config import ConfigError


class TestConfig(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="fgold-config-")
        self.tmp = Path(self._tmp.name)
        self.path = self.tmp / "config.local.json"

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, data):
        self.path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")

    def test_missing_file_gives_defaults(self):
        cfg = config.load(self.path)
        self.assertFalse(cfg["_exists"])
        self.assertEqual(cfg["roots"], [])
        self.assertEqual(cfg["nest_egg_copper"], 5000000)
        self.assertEqual(cfg["cut"], {"faction": 0.05, "neutral": 0.15})
        self.assertEqual(cfg["max_rows"], 25)
        with self.assertRaises(ConfigError) as cm:
            config.load(self.path, must_exist=True)
        self.assertIn("doctor --write", str(cm.exception))

    def test_example_config_is_valid(self):
        cfg = config.load(ts.REPO / "config.example.json")
        self.assertEqual([r["name"] for r in cfg["roots"]], ["pc", "vm"])
        self.assertEqual(cfg["home_house"], "Alliance")

    def test_bad_json(self):
        self.write("{ nope")
        with self.assertRaises(ConfigError) as cm:
            config.load(self.path)
        self.assertIn("not valid JSON", str(cm.exception))
        self.assertIn("Fix:", str(cm.exception))
        self.write("[1, 2]")
        with self.assertRaises(ConfigError):
            config.load(self.path)

    def test_a_byte_order_mark_is_accepted(self):
        # PowerShell 5 `Set-Content -Encoding UTF8` and older Notepad write EF BB BF; the error used to
        # advise deleting the file, losing the hand-set nest egg and professions.
        self.path.write_bytes(b"\xef\xbb\xbf" + json.dumps({"nest_egg_copper": 7000000,
                                                             "professions": {"Alchemy": 120}}).encode("utf-8"))
        cfg = config.load(self.path)
        self.assertEqual((cfg["nest_egg_copper"], cfg["professions"]), (7000000, {"Alchemy": 120}))

    def test_each_bad_value_is_named(self):
        self.write({"region": "xx", "cut": {"faction": 1.5}, "post_mode": "cheap", "max_rows": 0,
                    "nest_egg_copper": "5", "roots": [{"name": "pc"}], "professions": {"Alchemy": -1},
                    "home_house": "Goblin", "undercut_copper": 1.5, "watchlist": "x", "mtime_stable_seconds": -1})
        with self.assertRaises(ConfigError) as cm:
            config.load(self.path)
        msg = str(cm.exception)
        for frag in ("region must be one of", "cut.faction must be a fraction", "post_mode must be",
                     "max_rows must be", "nest_egg_copper must be", "roots[0] needs a \"path\"",
                     "professions.Alchemy", "home_house must be", "undercut_copper", "watchlist", "mtime_stable_seconds"):
            self.assertIn(frag, msg)
        self.assertIn("Fix: edit", msg)

    def test_partial_cut_and_whole_floats(self):
        self.write({"cut": {"neutral": 0.2}, "nest_egg_copper": 5000000.0, "professions": {"Alchemy": 150.0}})
        cfg = config.load(self.path)
        self.assertEqual(cfg["cut"], {"faction": 0.05, "neutral": 0.2})
        self.assertIsInstance(cfg["nest_egg_copper"], int)
        self.assertEqual(cfg["professions"], {"Alchemy": 150})
        self.assertEqual(config.cut_default(cfg, "Neutral"), 0.2)
        self.assertEqual(config.cut_default(cfg, "Horde"), 0.05)

    def test_save_and_load(self):
        cfg = config.load(self.path)
        cfg["roots"] = [{"name": "pc", "path": "C:/WoW/_forever_"}]
        cfg["max_rows"] = 10
        config.save(self.path, cfg)
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(list(raw)[:3], ["roots", "region", "home_house"])
        self.assertNotIn("_path", raw)
        again = config.load(self.path)
        self.assertEqual(again["max_rows"], 10)
        self.assertEqual(again["roots"], cfg["roots"])
        with self.assertRaises(ConfigError):
            config.save(self.path, dict(cfg, region="mars"))


if __name__ == "__main__":
    unittest.main()
