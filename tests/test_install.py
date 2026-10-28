"""install: symlink / junction / copy, UNC copies, PlanData.lua from PlanDefault."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import test_support as ts
from fgold import install, luasv
from fgold.paths import Root


class InstallCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="fgold-install-")
        self.tmp = Path(self._tmp.name)
        self.src = ts.write_addon_src(self.tmp / "repo" / "addon" / "ForeverGold")

    def tearDown(self):
        self._tmp.cleanup()

    def root(self, name="wow", remote=False, create=True) -> Root:
        p = self.tmp / name / "_forever_"
        if create:
            (p / "WTF").mkdir(parents=True)
        return Root(name, p, str(p), remote=remote)

    def plan_in(self, folder: Path) -> dict:
        return luasv.load(folder / "PlanData.lua")["ForeverGoldPlan"]


class TestLocalInstall(InstallCase):
    @unittest.skipUnless(ts.has_symlink_support(), "needs symlinks")
    def test_symlink_on_macos_and_linux(self):
        r = self.root()
        o = install.install_root(r, self.src, platform="darwin")
        self.assertEqual(o.status, "linked")
        self.assertTrue(r.addon_dir.is_symlink())
        self.assertTrue(os.path.samefile(r.addon_dir, self.src))
        self.assertTrue(o.plan_created)
        plan = self.plan_in(self.src)
        self.assertEqual((plan["schema"], plan["id"]), (1, "none"))
        self.assertEqual(plan["headline"], "No plan yet: run /forever-gold")
        again = install.install_root(r, self.src, platform="darwin")
        self.assertEqual(again.status, "already")
        self.assertFalse(again.plan_created)

    @unittest.skipUnless(ts.has_symlink_support(), "needs symlinks")
    def test_existing_plan_data_is_not_overwritten(self):
        (self.src / "PlanData.lua").write_text("ForeverGoldPlan = { schema = 1, id = \"mine\" }\n", encoding="utf-8")
        install.install_root(self.root(), self.src, platform="linux")
        self.assertEqual(self.plan_in(self.src)["id"], "mine")

    @unittest.skipUnless(ts.has_symlink_support(), "needs symlinks")
    def test_windows_junction_is_made_without_a_shell(self):
        # cmd /c mklink /J re-parses the paths ('&' splits the command, '^' is eaten, %VAR% expands),
        # so the junction is made directly (_winapi.CreateJunction); a symlink stands in for it here.
        r = self.root()
        seen = []

        def create(source, target):
            seen.append((source, target))
            os.symlink(source, target, target_is_directory=True)

        no_shell = AssertionError("install must not start a shell")
        with mock.patch("subprocess.run", side_effect=no_shell), mock.patch("subprocess.Popen", side_effect=no_shell):
            o = install.install_root(r, self.src, platform="win32", create_junction=create)
        self.assertEqual(seen, [(str(self.src), str(r.addon_dir))], "CreateJunction(source, target)")
        self.assertEqual(o.status, "junction")
        self.assertTrue((self.src / "PlanData.lua").is_file())

    def test_windows_junction_failure_falls_back_to_copy(self):
        r = self.root()

        def create(source, target):
            raise OSError("Access is denied.")

        o = install.install_root(r, self.src, platform="win32", create_junction=create)
        self.assertEqual(o.status, "copied")
        self.assertIn("Access is denied", o.detail)
        self.assertTrue((r.addon_dir / "ForeverGold.toc").is_file())
        self.assertTrue((r.addon_dir / "PlanData.lua").is_file())

    def test_without_winapi_the_addon_is_copied(self):
        r = self.root()
        with mock.patch("fgold.install._winapi_junction", return_value=None):
            o = install.install_root(r, self.src, platform="win32")
        self.assertEqual(o.status, "copied")
        self.assertIn("_winapi", o.detail)

    @unittest.skipUnless(ts.has_symlink_support(), "needs symlinks")
    def test_link_to_the_wrong_folder_is_removed_and_the_addon_copied(self):
        # The old `cmd /c mklink /J <target> C:\Users\Travis\R&D\...` made a junction to C:\Users\Travis\R,
        # failed on the rest, and the copy fallback then wrote the addon through that junction.
        r = self.root()
        wrong = self.tmp / "R"
        wrong.mkdir()
        (wrong / "keep.txt").write_text("not yours", encoding="utf-8")

        def half_done(source, target):
            os.symlink(wrong, target, target_is_directory=True)
            raise OSError("'D' is not recognized as an internal or external command")

        o = install.install_root(r, self.src, platform="win32", create_junction=half_done)
        self.assertEqual(o.status, "copied")
        self.assertIn("pointed at", o.detail)
        self.assertFalse(r.addon_dir.is_symlink(), "the wrong link is gone")
        self.assertTrue((r.addon_dir / "ForeverGold.toc").is_file())
        self.assertEqual([p.name for p in wrong.iterdir()], ["keep.txt"], "nothing written into the other folder")
        # a link that reports success but points elsewhere (a '^' in the path) is caught the same way
        r2 = self.root("wow2")
        o2 = install.install_root(r2, self.src, platform="win32",
                                  create_junction=lambda s, t: os.symlink(wrong, t, target_is_directory=True))
        self.assertEqual(o2.status, "copied")
        self.assertEqual([p.name for p in wrong.iterdir()], ["keep.txt"])
        real_symlink = os.symlink
        with mock.patch("fgold.install.os.symlink", side_effect=lambda s, t, target_is_directory: real_symlink(
                wrong, t, target_is_directory=True)):
            o3 = install.install_root(self.root("mac"), self.src, platform="darwin")
        self.assertEqual(o3.status, "copied", "symlinks are checked too")

    def test_copy_fallback_when_symlink_fails(self):
        r = self.root()
        with mock.patch("fgold.install.os.symlink", side_effect=OSError("symlinks disabled")):
            o = install.install_root(r, self.src, platform="linux")
        self.assertEqual(o.status, "copied")
        self.assertIn("link failed", o.detail)
        self.assertFalse(r.addon_dir.is_symlink())
        self.assertEqual(self.plan_in(r.addon_dir)["id"], "none")

    def test_symlinks_are_not_tried_on_windows(self):
        r = self.root()

        def refuse(source, target):
            raise OSError("no")

        with mock.patch("fgold.install.os.symlink") as sym:
            install.install_root(r, self.src, platform="win32", create_junction=refuse)
        sym.assert_not_called()


class TestRemoteAndExisting(InstallCase):
    def test_unc_root_is_always_copied(self):
        r = self.root(remote=True)
        with mock.patch("fgold.install.os.symlink") as sym:
            o = install.install_root(r, self.src, platform="win32", create_junction=lambda *a: self.fail("no junction"))
        sym.assert_not_called()
        self.assertEqual(o.status, "copied")
        self.assertTrue(r.addon_dir.is_dir())
        self.assertFalse(r.addon_dir.is_symlink())
        self.assertEqual(sorted(p.name for p in r.addon_dir.iterdir()),
                         ["Core.lua", "ForeverGold.toc", "PlanData.lua", "PlanDefault.lua"])

    def test_existing_copy_is_updated_and_plan_kept(self):
        r = self.root()
        r.addon_dir.mkdir(parents=True)
        (r.addon_dir / "Core.lua").write_text("-- old\n", encoding="utf-8")
        (r.addon_dir / "PlanData.lua").write_text("ForeverGoldPlan = { schema = 1, id = \"applied\" }\n", encoding="utf-8")
        (self.src / "PlanData.lua").write_text("ForeverGoldPlan = { schema = 1, id = \"repo\" }\n", encoding="utf-8")
        o = install.install_root(r, self.src, platform="linux")
        self.assertEqual(o.status, "updated")
        self.assertEqual((r.addon_dir / "Core.lua").read_text(encoding="utf-8"), "local ADDON, ns = ...\n")
        self.assertEqual(self.plan_in(r.addon_dir)["id"], "applied")

    @unittest.skipUnless(ts.has_symlink_support(), "needs symlinks")
    def test_link_to_somewhere_else_is_an_error(self):
        r = self.root()
        other = self.tmp / "other"
        other.mkdir()
        (r.addon_dir.parent).mkdir(parents=True)
        os.symlink(other, r.addon_dir, target_is_directory=True)
        o = install.install_root(r, self.src, platform="linux")
        self.assertEqual(o.status, "error")
        self.assertIn("link to somewhere else", o.detail)

    def test_missing_root(self):
        o = install.install_root(self.root(create=False), self.src, platform="linux")
        self.assertEqual(o.status, "error")
        self.assertIn("not found", o.detail)


class TestInstallCommand(InstallCase):
    def test_missing_source(self):
        rep = install.install([self.root()], self.tmp / "nope")
        self.assertFalse(rep.ok)
        self.assertIn("addon source not found", rep.errors[0])

    def test_root_filter_and_no_roots(self):
        a, b = self.root("pc"), self.root("vm", remote=True)
        rep = install.install([a, b], self.src, only="vm")
        self.assertEqual([o.root for o in rep.outcomes], ["vm"])
        rep = install.install([a, b], self.src, only="nas")
        self.assertIn("no root named 'nas'", rep.errors[0])
        rep = install.install([], self.src)
        self.assertIn("doctor --write", rep.errors[0])


class TestDefaultPlan(InstallCase):
    def test_from_plan_default_content(self):
        text = install.default_plan_text(self.src)
        self.assertTrue(text.startswith("-- Generated by fgold."))
        plan = luasv.loads(text)["ForeverGoldPlan"]
        default = luasv.find_assignment(ts.PLAN_DEFAULT_LUA, "ForeverGoldPlanDefault")
        self.assertEqual(plan, luasv.canonical(default))

    def test_builtin_when_plan_default_is_missing_or_bad(self):
        (self.src / "PlanDefault.lua").unlink()
        plan = luasv.loads(install.default_plan_text(self.src))["ForeverGoldPlan"]
        self.assertEqual(plan["id"], "none")
        (self.src / "PlanDefault.lua").write_text("ForeverGoldPlanDefault = setmetatable({}, x)\n", encoding="utf-8")
        plan = luasv.loads(install.default_plan_text(self.src))["ForeverGoldPlan"]
        self.assertEqual(plan["id"], "none")
        self.assertEqual(plan["budget"], {"spendCap": 0, "principal": 0})


if __name__ == "__main__":
    unittest.main()
