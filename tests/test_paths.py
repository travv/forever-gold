"""paths: root resolution and naming, UNC detection, environment overrides."""

import os
import unittest
from pathlib import Path
from unittest import mock

import test_support as ts
from fgold import paths


class TestRoots(unittest.TestCase):
    def test_cli_roots_replace_config_roots(self):
        cfg = {"roots": [{"name": "pc", "path": "C:/WoW/_forever_"}]}
        roots = paths.resolve_roots(cfg, ["/games/World of Warcraft/_forever_", "/vm/World of Warcraft/_forever_"])
        self.assertEqual([r.name for r in roots], ["world-of-warcraft-forever", "world-of-warcraft-forever-2"])
        roots = paths.resolve_roots(cfg, [str(ts.FIXTURE_ROOT)])
        self.assertEqual(roots[0].name, "handmade")

    def test_config_roots(self):
        cfg = {"roots": [{"name": "pc", "path": "C:/Program Files (x86)/World of Warcraft/_forever_"},
                         {"name": "vm", "path": "//UNRAID-VM/forever"}, {"name": "PC", "path": "D:/x/_forever_"},
                         {"path": "E:/Games/World of Warcraft/_forever_"}]}
        roots = paths.resolve_roots(cfg)
        self.assertEqual([r.name for r in roots], ["pc", "vm", "pc-2", "world-of-warcraft-forever"])
        self.assertEqual([r.remote for r in roots], [False, True, False, False])
        self.assertEqual(roots[1].raw, "//UNRAID-VM/forever")
        self.assertFalse(roots[0].exists)

    def test_unc(self):
        self.assertTrue(paths.is_unc("//UNRAID-VM/forever"))
        self.assertTrue(paths.is_unc("\\\\UNRAID-VM\\forever"))
        self.assertFalse(paths.is_unc("C:/WoW"))
        self.assertFalse(paths.is_unc("/Applications/World of Warcraft/_forever_"))

    def test_accounts_need_a_saved_variables_folder(self):
        root = paths.Root("t", ts.FIXTURE_ROOT, str(ts.FIXTURE_ROOT))
        self.assertEqual([a for a, _ in root.accounts()], ["TEST#1"])
        self.assertEqual(root.addon_dir, ts.FIXTURE_ROOT / "Interface" / "AddOns" / "ForeverGold")
        self.assertEqual(paths.Root("x", Path("/nope"), "/nope").accounts(), [])


class TestOverrides(unittest.TestCase):
    def test_defaults_live_in_the_repo(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            for k in ("FGOLD_HOME", "FGOLD_DATA", "FGOLD_ADDON_DIR", "FGOLD_STATIC"):
                os.environ.pop(k, None)
            self.assertEqual(paths.default_data_dir(), paths.REPO_ROOT / "data")
            self.assertEqual(paths.default_config_path(), paths.REPO_ROOT / "config.local.json")
            self.assertEqual(paths.plan_dir(), paths.REPO_ROOT / "plan")
            self.assertEqual(paths.reports_dir(), paths.REPO_ROOT / "reports")
            self.assertEqual(paths.addon_src_dir(), paths.REPO_ROOT / "addon" / "ForeverGold")
            self.assertEqual(paths.static_dir(), paths.REPO_ROOT / "static")

    def test_env_overrides(self):
        env = {"FGOLD_HOME": "/tmp/h", "FGOLD_DATA": "/tmp/d", "FGOLD_ADDON_DIR": "/tmp/a", "FGOLD_STATIC": "/tmp/s"}
        with mock.patch.dict(os.environ, env):
            self.assertEqual(paths.default_data_dir(), Path("/tmp/d"))
            self.assertEqual(paths.default_config_path(), Path("/tmp/h/config.local.json"))
            self.assertEqual(paths.plan_dir(), Path("/tmp/h/plan"))
            self.assertEqual(paths.reports_dir(), Path("/tmp/h/reports"))
            self.assertEqual(paths.addon_src_dir(), Path("/tmp/a"))
            self.assertEqual(paths.static_dir(), Path("/tmp/s"))
        with mock.patch.dict(os.environ, {"FGOLD_HOME": "/tmp/h"}):
            os.environ.pop("FGOLD_DATA", None)
            os.environ.pop("FGOLD_ADDON_DIR", None)
            self.assertEqual(paths.default_data_dir(), Path("/tmp/h/data"))
            self.assertEqual(paths.addon_src_dir(), Path("/tmp/h/addon/ForeverGold"))


if __name__ == "__main__":
    unittest.main()
