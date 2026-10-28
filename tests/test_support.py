"""Shared helpers for the fgold unit tests (no tests in here).

Everything runs offline in temp dirs: FGOLD_HOME, FGOLD_STATIC, FGOLD_NOW and
FGOLD_STABLE_SECS are set per test so nothing is written into the repo.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from fgold import luasv  # noqa: E402
from fgold.static import Static  # noqa: E402

FIXTURE_ROOT = REPO / "tests" / "fixtures" / "handmade"
FIXTURE_ACCOUNT = "TEST#1"
FIXTURE_SV = FIXTURE_ROOT / "WTF" / "Account" / FIXTURE_ACCOUNT / "SavedVariables" / "ForeverGold.lua"
FIXTURE_STATIC = FIXTURE_ROOT / "static"
NOW = 1793928600            # Thu Nov 5 2026, 7:30 PM CST (20 min after the fixture scan)
SCAN_AT = 1793927400
MARKET = "us|Forever|Alliance"

TEST_CONFIG = {
    "roots": [],
    "region": "us",
    "home_house": "Alliance",
    "nest_egg_copper": 5000000,
    "cut": {"faction": 0.05, "neutral": 0.15},
    "post_mode": "match",
    "undercut_copper": 100,
    "professions": {"Herbalism": 1, "First Aid": 100, "Alchemy": 0},
    "watchlist": [],
    "max_rows": 25,
    "mtime_stable_seconds": 0,
}

PLAN_DEFAULT_LUA = """local ADDON, ns = ...
-- Default plan used when PlanData.lua is missing or invalid.
ForeverGoldPlanDefault = {
  schema = 1,
  id = "none",
  generatedAt = 0,
  basedOnScan = { at = 0, market = "", rows = 0 },
  lastScanAt = 0,
  expectPriorSessions = 0,
  settings = { postMode = "match", undercut = 100 },
  headline = "No plan yet: run /forever-gold",
  notes = {},
  steps = {},
  budget = { spendCap = 0, principal = 0 },
  buy = {}, craft = {}, post = {}, hold = {}, vendor = {},
  ref = {},
}
"""


def load_static() -> Static:
    return Static.load(FIXTURE_STATIC)


def fixture_db() -> dict:
    return luasv.load(FIXTURE_SV)["ForeverGoldDB"]


def write_addon_src(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "ForeverGold.toc").write_text("## Interface: 16001\n## Title: ForeverGold\n## SavedVariables: ForeverGoldDB\n"
                                            "PlanDefault.lua\nPlanData.lua\nCore.lua\n", encoding="utf-8", newline="\n")
    (folder / "PlanDefault.lua").write_text(PLAN_DEFAULT_LUA, encoding="utf-8", newline="\n")
    (folder / "Core.lua").write_text("local ADDON, ns = ...\n", encoding="utf-8", newline="\n")
    return folder


def make_root(base: Path, accounts: dict, files=("ForeverGold.lua",)) -> Path:
    """A fake _forever_ root: accounts = {name: sv_text or {filename: text}}."""
    for acct, content in accounts.items():
        sv = base / "WTF" / "Account" / acct / "SavedVariables"
        sv.mkdir(parents=True, exist_ok=True)
        if isinstance(content, dict):
            for fname, text in content.items():
                (sv / fname).write_text(text, encoding="utf-8", newline="\n")
        else:
            for fname in files:
                (sv / fname).write_text(content, encoding="utf-8", newline="\n")
    return base


def sv_text(db: dict) -> str:
    return luasv.dump("ForeverGoldDB", db)


def snapshot(folder: Path) -> dict:
    """{relative path: bytes} of every file under folder (for 'never writes' checks)."""
    out = {}
    for p in sorted(Path(folder).rglob("*")):
        if p.is_file() and not p.is_symlink():
            out[p.relative_to(folder).as_posix()] = p.read_bytes()
    return out


class TempHomeCase(unittest.TestCase):
    """A test case with an isolated FGOLD_HOME, config, fake addon and a copy of the fixture root."""

    with_addon = True
    config = TEST_CONFIG

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="fgold-test-")
        self.tmp = Path(self._tmp.name)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.config_path = self.home / "config.local.json"
        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")
        self.addon_src = self.home / "addon" / "ForeverGold"
        if self.with_addon:
            write_addon_src(self.addon_src)
        self.root = self.tmp / "wow" / "_forever_"
        shutil.copytree(FIXTURE_ROOT / "WTF", self.root / "WTF")
        self.data_dir = self.home / "data"
        env = {
            "FGOLD_HOME": str(self.home),
            "FGOLD_STATIC": str(FIXTURE_STATIC),
            "FGOLD_NOW": str(NOW),
            "FGOLD_STABLE_SECS": "0",
        }
        self._env = mock.patch.dict(os.environ, env)
        self._env.start()
        for k in ("FGOLD_DATA", "FGOLD_ADDON_DIR"):
            os.environ.pop(k, None)

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def run_cli(self, *argv, roots=None):
        """Run fgold.cli.main in-process. Returns (exit code, stdout, stderr)."""
        from fgold import cli

        args = list(argv)
        for r in (roots if roots is not None else [self.root]):
            args += ["--wow-root", str(r)]
        so, se = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(so), contextlib.redirect_stderr(se):
            code = cli.main(args)
        return code, so.getvalue(), se.getvalue()


def has_symlink_support() -> bool:
    with tempfile.TemporaryDirectory() as d:
        try:
            os.symlink(d, os.path.join(d, "link"), target_is_directory=True)
            return True
        except (OSError, NotImplementedError, AttributeError):
            return False
