"""doctor: candidate install folders, root checks, --write."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import test_support as ts
from fgold import doctor
from fgold.paths import Root


class DoctorCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="fgold-doctor-")
        self.tmp = Path(self._tmp.name)
        self.src = ts.write_addon_src(self.tmp / "repo" / "addon" / "ForeverGold")
        self.empty_home = self.tmp / "home"
        self.empty_home.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def fixture_root(self, name="pc") -> Root:
        p = self.tmp / name / "World of Warcraft" / "_forever_"
        shutil.copytree(ts.FIXTURE_ROOT / "WTF", p / "WTF")
        return Root(name, p, str(p))


class TestCandidates(DoctorCase):
    def test_windows_locations(self):
        env = {"ProgramFiles(x86)": "C:\\Program Files (x86)", "ProgramFiles": "C:\\Program Files"}
        dirs = [str(p).replace("\\", "/") for p in doctor.candidate_wow_dirs("win32", env=env, home=self.empty_home)]
        self.assertEqual(dirs[0], "C:/Program Files (x86)/World of Warcraft")
        self.assertIn("C:/Program Files/World of Warcraft", dirs)
        self.assertIn("C:/Games/World of Warcraft", dirs)
        self.assertIn("D:/World of Warcraft", dirs)
        self.assertIn("D:/Games/World of Warcraft", dirs)
        self.assertEqual(len(dirs), len({d.lower() for d in dirs}), "no duplicates")

    def test_macos_locations(self):
        dirs = [str(p) for p in doctor.candidate_wow_dirs("darwin", env={}, home=self.empty_home)]
        self.assertEqual(dirs[0], "/Applications/World of Warcraft")
        self.assertIn(str(self.empty_home / "Applications" / "World of Warcraft"), dirs)

    def test_finds_forever_folders(self):
        target = self.empty_home / "Games" / "world-of-warcraft" / "drive_c" / "Program Files (x86)" / "World of Warcraft"
        (target / "_forever_").mkdir(parents=True)
        (target / "_retail_").mkdir()
        found = doctor.find_forever_roots("linux", env={}, home=self.empty_home)
        self.assertEqual(found, [target / "_forever_"])


class TestChecks(DoctorCase):
    def test_check_root_lists_accounts(self):
        r = self.fixture_root()
        c = doctor.check_root(r, self.src)
        self.assertEqual(c.addon, "missing")
        self.assertEqual([a[0] for a in c.accounts], ["TEST#1"])
        self.assertTrue(c.accounts[0][1])
        (r.path / "WTF" / "Account" / "EMPTY#2" / "SavedVariables").mkdir(parents=True)
        c = doctor.check_root(r, self.src)
        self.assertEqual([(a[0], a[1]) for a in c.accounts], [("EMPTY#2", False), ("TEST#1", True)])
        r.addon_dir.mkdir(parents=True)
        self.assertEqual(doctor.check_root(r, self.src).addon, "copy")

    @unittest.skipUnless(ts.has_symlink_support(), "needs symlinks")
    def test_check_root_sees_links(self):
        r = self.fixture_root()
        r.addon_dir.parent.mkdir(parents=True)
        os.symlink(self.src, r.addon_dir, target_is_directory=True)
        self.assertEqual(doctor.check_root(r, self.src).addon, "linked")
        other = self.tmp / "other"
        other.mkdir()
        self.assertEqual(doctor.check_root(r, other).addon, "broken")

    def test_merged_roots_names(self):
        cfg_roots = [{"name": "pc", "path": "C:/WoW/_forever_"}]
        found = [Path("C:/WoW/_forever_"), Path("D:/Games/World of Warcraft/_forever_")]
        merged = doctor.merged_roots(cfg_roots, found, platform="win32")
        self.assertEqual([r["name"] for r in merged], ["pc", "pc-2"])
        merged = doctor.merged_roots([], [Path("/Applications/World of Warcraft/_forever_")], platform="darwin")
        self.assertEqual(merged, [{"name": "mac", "path": "/Applications/World of Warcraft/_forever_"}])


class TestRun(DoctorCase):
    def test_run_and_write(self):
        r = self.fixture_root()
        cfg_path = self.tmp / "config.local.json"
        lines, code = doctor.run(dict(ts.TEST_CONFIG), [r], self.src, ts.load_static(), True, cfg_path,
                                 self.tmp / "data", platform="linux", env={}, home=self.empty_home, now=ts.NOW)
        self.assertEqual(code, 0)
        self.assertEqual(lines[0], "Next: run `python -m fgold install`, then fully restart WoW")
        text = "\n".join(lines)
        self.assertIn("account TEST#1: ForeverGold.lua", text)
        self.assertIn("NOT INSTALLED", text)
        self.assertIn("Python ", text)
        saved = json.loads(cfg_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["roots"], [{"name": "pc", "path": str(r.path)}])
        self.assertEqual(list(saved)[:2], ["roots", "region"])
        self.assertNotIn("_path", saved)

    def test_nothing_found(self):
        lines, code = doctor.run(dict(ts.TEST_CONFIG), [], self.src, ts.load_static(), False, self.tmp / "c.json",
                                 self.tmp / "data", platform="linux", env={}, home=self.empty_home)
        self.assertEqual(code, 1)
        self.assertTrue(lines[0].startswith("Next: no WoW: Forever install found"))

    def test_found_but_not_saved(self):
        target = self.empty_home / "Games" / "world-of-warcraft" / "drive_c" / "Program Files (x86)" / "World of Warcraft"
        (target / "_forever_").mkdir(parents=True)
        lines, code = doctor.run(dict(ts.TEST_CONFIG), [], self.src, ts.load_static(), False, self.tmp / "c.json",
                                 self.tmp / "data", platform="linux", env={}, home=self.empty_home)
        self.assertEqual(lines[0], "Next: run `python -m fgold doctor --write` to save the 1 install(s) found")
        self.assertIn("found (not in config)", "\n".join(lines))


if __name__ == "__main__":
    unittest.main()
