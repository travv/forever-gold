"""End-to-end round trip: SavedVariables -> fgold -> PlanData.lua -> addon -> SavedVariables -> fgold.

Everything is offline and in temp folders. Each fixture (the harness-made one
and the handmade one) is copied into a temp WoW root, the real addon is
copied into a temp FGOLD_HOME, and the real CLI runs as separate processes,
the way the skill runs it: install, ingest, brief, plan --apply. The plan must
land in the root's Interface/AddOns/ForeverGold and re-parse to the applied
plan, and nothing may be written into the repo or the WoW account folders.

When luajit is on PATH, the real addon (tests/wow, stubbed client) then loads
that PlanData.lua: the plan id, the guard rows and the Auctionator buy list
must match the plan. Last, the harness plays one session loop against the
root's SavedVariables (login, Scan, Buy list, Done = /reload, logout), and
fgold must see the game report the applied plan back. Without luajit (for
example on the Windows PC) the addon parts are skipped.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import test_support as ts
from fgold import apply, luasv
from fgold.static import Static

REPO = ts.REPO
ADDON_SRC = REPO / "addon" / "ForeverGold"
RUN_LUA = REPO / "tests" / "wow" / "run.lua"
HARNESS_WTF = REPO / "tests" / "fixtures" / "harness" / "WTF"
HANDMADE_WTF = ts.FIXTURE_ROOT / "WTF"
LUAJIT = shutil.which("luajit")
ACCOUNT = "TEST#1"
EM_DASH = "\u2014"
REPO_WATCH = ("addon", "data", "plan", "reports", "config.local.json", "tests/fixtures/harness",
              "tests/fixtures/handmade")

APPLIED_RE = re.compile(r"^plan (\d{8}-\d{4}-[0-9a-f]{4}): now /reload in game", re.M)
LOGIN_RE = re.compile(r"^ForeverGold: plan (\S+) \(([^)]*) old\): (\d+) buy, (\d+) post, (\d+) craft, (\d+) hold$")
INGEST_RE = re.compile(r"ingest: (\d+) new of (\d+) files?")


def files_state(base: Path, rels) -> dict:
    """{relative path: (size, mtime_ns)} for every file under base/rel (missing = absent)."""
    out = {}
    for rel in rels:
        p = base / rel
        found = [p] if p.is_file() else (sorted(x for x in p.rglob("*") if x.is_file()) if p.is_dir() else [])
        for f in found:
            st = f.stat()
            out[f.relative_to(base).as_posix()] = (st.st_size, st.st_mtime_ns)
    return out


def newest_session(db: dict) -> dict:
    sessions = [s for s in (db.get("sessions") or []) if isinstance(s, dict)]
    return max(sessions, key=lambda s: s.get("loginAt") or 0) if sessions else {}


def gold_to_copper(text: str) -> int:
    """Auctionator's price field is gold ("0.0022" = 22c)."""
    return int(round(float(text) * 10000))


def parse_aucstr(line: str) -> dict:
    """query;cat;minIlvl;maxIlvl;minLvl;maxLvl;minCraft;maxCraft;minPrice;maxPrice;quality;tier;expansion;qty"""
    fields = line.split(";")
    if len(fields) != 14:
        raise AssertionError(f"Auctionator search string has {len(fields)} fields, not 14: {line!r}")
    name = fields[0]
    exact = len(name) >= 2 and name.startswith('"') and name.endswith('"')
    return {"name": name[1:-1] if exact else name, "exact": exact, "maxUnit": gold_to_copper(fields[9]),
            "qty": int(fields[13])}


class Harness:
    """luajit tests/wow/run.lua --plan ... output, split into its line kinds."""

    def __init__(self, proc: subprocess.CompletedProcess):
        self.code = proc.returncode
        self.stdout = proc.stdout
        self.stderr = proc.stderr
        self.lines = proc.stdout.splitlines()

    def one(self, kind: str) -> str:
        found = self.all(kind)
        if len(found) != 1:
            raise AssertionError(f"expected one {kind}: line, got {len(found)}:\n{self.stdout}\n{self.stderr}")
        return found[0]

    def all(self, kind: str) -> list:
        prefix = kind + ": "
        return [line[len(prefix):] for line in self.lines if line.startswith(prefix)]

    def rows(self, kind: str) -> list:
        """BUYROW/POSTROW lines as (id, color, text)."""
        out = []
        for line in self.all(kind):
            iid, color, text = line.split("|", 2)
            out.append((int(iid), color, text))
        return out


class RoundTrip:
    """One fixture's round trip. Subclasses set the class attributes below."""

    wtf: Path = None              # the fixture's WTF folder
    now: int = 0                  # FGOLD_NOW for the forward leg (after the fixture's last logout)
    static_dir: Path = ts.FIXTURE_STATIC
    config: dict = ts.TEST_CONFIG
    expect_rows: bool = True      # the plan must have at least one buy or post row

    # ---- set up: the forward leg runs once per fixture --------------------------------
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix="fgold-e2e-")
        cls.world = Path(cls._tmp.name)
        cls.root = cls.world / "wow" / "_forever_"
        cls.home = cls.world / "home"
        cls.data = cls.home / "data"
        shutil.copytree(cls.wtf, cls.root / "WTF")
        shutil.copytree(ADDON_SRC, cls.home / "addon" / "ForeverGold",
                        ignore=shutil.ignore_patterns("PlanData.lua", ".*"))
        (cls.home / "config.local.json").write_text(json.dumps(cls.config), encoding="utf-8", newline="\n")
        cls.sv_path = cls.root / "WTF" / "Account" / ACCOUNT / "SavedVariables" / "ForeverGold.lua"
        cls.fixture_db = luasv.load(cls.sv_path)["ForeverGoldDB"]
        copper = newest_session(cls.fixture_db).get("copperOK")
        cls.copper_flag = "true" if copper is True else "false" if copper is False else "missing"
        cls.addon_dir = cls.root / "Interface" / "AddOns" / "ForeverGold"
        cls.plan_path = cls.addon_dir / "PlanData.lua"

        cls.repo_before = repo_before = files_state(REPO, REPO_WATCH)
        cls.install = cls.fgold("install")
        cls.default_plan_text = cls.plan_path.read_text(encoding="utf-8") if cls.plan_path.is_file() else None
        wtf_before = files_state(cls.root, ["WTF"])
        cls.ingest = cls.fgold("ingest")
        cls.ingest_again = cls.fgold("ingest")
        cls.brief = cls.fgold("brief")
        cls.apply = cls.fgold("plan", "--apply")
        cls.wtf_changed = wtf_before != files_state(cls.root, ["WTF"])
        cls.repo_changed = sorted(set(repo_before.items()) ^ set(files_state(REPO, REPO_WATCH).items()))
        m = APPLIED_RE.search(cls.apply.stdout)
        cls.plan_id = m.group(1) if m else None

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    @classmethod
    def fgold(cls, *argv, now=None, home=None, data=None, root=None) -> subprocess.CompletedProcess:
        """python -m fgold <argv> --data-dir <temp> --wow-root <temp root>, run from the repo root."""
        env = {k: v for k, v in os.environ.items() if not k.startswith("FGOLD_")}
        env.update({"FGOLD_HOME": str(home or cls.home), "FGOLD_STATIC": str(cls.static_dir),
                    "FGOLD_NOW": str(now or cls.now), "FGOLD_STABLE_SECS": "0", "PYTHONIOENCODING": "utf-8"})
        cmd = [sys.executable, "-m", "fgold", *argv, "--data-dir", str(data or cls.data),
               "--wow-root", str(root or cls.root)]
        return subprocess.run(cmd, cwd=str(REPO), env=env, capture_output=True, text=True, encoding="utf-8",
                              timeout=180)

    @classmethod
    def harness(cls, plan_path: Path, *extra) -> Harness:
        cmd = [LUAJIT, str(RUN_LUA), "--plan", str(plan_path), "--copper-ok", cls.copper_flag, *extra]
        proc = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True, encoding="utf-8", timeout=180)
        return Harness(proc)

    def need_luajit(self):
        if not LUAJIT:
            self.skipTest("luajit is not on PATH: the addon half of the round trip is skipped")

    def applied_plan(self) -> dict:
        return luasv.load(self.plan_path)[apply.GLOBAL]

    def draft(self) -> dict:
        return json.loads((self.home / "plan" / "draft.json").read_text(encoding="utf-8"))

    # ---- (a) fgold: SavedVariables in, PlanData.lua out ---------------------------------
    def test_a1_cli_steps_succeed(self):
        for name in ("install", "ingest", "ingest_again", "brief", "apply"):
            proc = getattr(self, name)
            self.assertEqual(proc.returncode, 0, f"{name} failed:\n{proc.stdout}\n{proc.stderr}")
            self.assertTrue(proc.stdout.startswith(("Next: ", "plan ")), f"{name}: first line is the next action")
        files = int(INGEST_RE.search(self.ingest.stdout).group(1))
        self.assertGreaterEqual(files, 1, self.ingest.stdout)
        self.assertEqual(INGEST_RE.search(self.ingest_again.stdout).group(1), "0", "ingest is idempotent")
        self.assertIsNotNone(self.plan_id, self.apply.stdout)

    def test_a2_plan_lands_in_the_root_and_reparses(self):
        self.assertTrue(self.plan_path.is_file(), f"no PlanData.lua in {self.addon_dir}")
        text = self.plan_path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith(apply.HEADER))
        plan = self.applied_plan()
        self.assertEqual(plan["id"], self.plan_id)
        self.assertEqual(plan["schema"], 1)
        self.assertEqual(plan["generatedAt"], self.now)
        self.assertEqual(sorted(plan), sorted(apply.TOP_KEYS), "only the keys of design.md section 2")
        applied = json.loads((self.home / "plan" / "applied.json").read_text(encoding="utf-8"))
        apply.validate(applied, static=Static.load(self.static_dir), max_rows=self.config["max_rows"])
        applied["ref"] = {int(k): v for k, v in applied["ref"].items()}
        self.assertEqual(plan, luasv.canonical(applied), "PlanData.lua re-parses to the applied plan")
        draft = self.draft()
        for key in ("buy", "craft", "post", "hold", "vendor", "headline", "notes", "steps", "budget",
                    "basedOnScan", "lastScanAt", "expectPriorSessions", "settings"):
            self.assertEqual(plan[key], luasv.canonical(draft[key]), f"{key}: the plan is the draft as applied")
        self.assertEqual(plan["basedOnScan"]["market"], ts.MARKET)
        self.assertEqual(plan["expectPriorSessions"], len(self.fixture_db["sessions"]))
        if self.expect_rows:
            self.assertTrue(plan["buy"] or plan["post"], "the fixture should give buy or post rows")
        self.assertNotIn(EM_DASH, text)

    def test_a3_brief_and_draft_written_to_the_temp_home(self):
        reports = sorted((self.home / "reports").glob("*-brief.md"))
        self.assertEqual(len(reports), 1, "brief creates reports/ and writes one report")
        text = reports[0].read_text(encoding="utf-8")
        self.assertEqual(text.splitlines()[0], self.brief.stdout.splitlines()[0], "first line = next action")
        self.assertIn(f"report: {reports[0]}", self.brief.stdout)
        self.assertTrue(text.rstrip().endswith("## Reads"), "## Reads is left empty for Claude")
        self.assertNotIn(EM_DASH, text)
        self.assertNotIn(EM_DASH, (self.home / "plan" / "draft.json").read_text(encoding="utf-8"))

    def test_a4_nothing_written_into_the_repo_or_account_folders(self):
        self.assertEqual(self.repo_changed, [], "the e2e run must not touch the repo's addon, data, plan or reports")
        self.assertFalse(self.wtf_changed, "ingest, brief and apply never write into the WoW account folders")
        self.assertTrue((self.root / "Interface" / "AddOns").is_dir())

    def test_d1_repo_untouched_after_the_addon_runs(self):
        """Runs last (unittest sorts by name): the luajit runs must not write into the repo either."""
        changed = sorted(set(self.repo_before.items()) ^ set(files_state(REPO, REPO_WATCH).items()))
        self.assertEqual(changed, [], "nothing in the repo's addon, data, plan, reports or fixtures may change")

    # ---- (b) the addon loads the plan Python wrote ---------------------------------------
    def test_b1_addon_loads_the_default_plan_from_install(self):
        self.need_luajit()
        self.assertIsNotNone(self.default_plan_text, "install creates PlanData.lua")
        path = self.world / "default-PlanData.lua"
        path.write_text(self.default_plan_text, encoding="utf-8", newline="\n")
        h = self.harness(path)
        self.assertEqual(h.code, 0, h.stdout + h.stderr)
        self.assertEqual(h.one("PLANID"), "none")
        self.assertEqual(h.one("PLANSRC"), "data", "install's PlanData.lua is a valid plan, not a fallback")
        self.assertEqual(h.one("LOGIN"), "ForeverGold: plan none (n/a old): 0 buy, 0 post, 0 craft, 0 hold")
        self.assertEqual(h.all("BUYROW") + h.all("POSTROW") + h.all("AUCSTR") + h.all("PLANERR"), [])

    def test_b2_addon_loads_the_applied_plan(self):
        self.need_luajit()
        plan = self.applied_plan()
        h = self.harness(self.plan_path)
        self.assertEqual(h.code, 0, h.stdout + h.stderr)
        self.assertEqual(h.one("RESULT"), "ok", h.stderr)
        self.assertEqual(h.all("PLANERR"), [])
        self.assertEqual(h.one("PLANID"), self.plan_id)
        self.assertEqual(h.one("PLANSRC"), "data")
        m = LOGIN_RE.match(h.one("LOGIN"))
        self.assertIsNotNone(m, h.one("LOGIN"))
        self.assertEqual(m.group(1), self.plan_id)
        self.assertEqual(m.group(2), "10m")
        self.assertEqual([int(x) for x in m.group(3, 4, 5, 6)],
                         [len(plan[k]) for k in ("buy", "post", "craft", "hold")], "login line counts")

        buys, posts = h.rows("BUYROW"), h.rows("POSTROW")
        if self.expect_rows:
            self.assertTrue(buys or posts, "at least one BUYROW or POSTROW line")
        self.assertEqual([r[0] for r in buys], [r["id"] for r in plan["buy"]], "one guard row per buy row, in order")
        self.assertEqual([r[0] for r in posts], [r["id"] for r in plan["post"]], "one guard row per post row")
        for iid, color, text in buys + posts:
            self.assertIn(color, ("green", "red"), f"{iid}: guards are colored once the scan is in")
        for (iid, _, text), row in zip(buys, plan["buy"]):
            self.assertTrue(text.startswith(f"{row['name']} x{row['qty']}"), text)
        for (iid, _, text), row in zip(posts, plan["post"]):
            self.assertTrue(text.startswith(f"{row['name']} x{row['qty']}: "), text)

        aucs = [parse_aucstr(s) for s in h.all("AUCSTR")]
        self.assertEqual(len(aucs), len(plan["buy"]), "one Auctionator search per buy row")
        for got, row in zip(aucs, plan["buy"]):
            self.assertEqual(got, {"name": row["name"], "exact": True, "maxUnit": row["maxUnit"], "qty": row["qty"]},
                             f"Auctionator search for buy row {row['id']}")

    # ---- (c) the game reports the plan back -------------------------------------------------
    def test_c1_session_loop_reports_the_plan_back(self):
        self.need_luajit()
        world = Path(tempfile.mkdtemp(prefix="fgold-e2e-loop-", dir=self.world))
        root, home = world / "wow" / "_forever_", world / "home"
        shutil.copytree(self.root, root, symlinks=True)   # the installed addon link comes along
        shutil.copytree(self.home, home, symlinks=True)
        data = home / "data"
        sv = root / "WTF" / "Account" / ACCOUNT / "SavedVariables" / "ForeverGold.lua"
        h = self.harness(self.plan_path, "--sv", str(sv))
        self.assertEqual(h.code, 0, h.stdout + h.stderr)
        self.assertEqual(h.one("RESULT"), "ok", h.stderr)
        self.assertEqual(h.one("SVOUT"), str(sv))
        self.assertTrue(h.one("RELOAD").startswith(f"ForeverGold: plan {self.plan_id} "), "Done reloads the plan")
        self.assertTrue(sv.with_name(sv.name + ".bak").is_file(), "the client keeps a .bak")

        db = luasv.load(sv)["ForeverGoldDB"]
        expect = len(self.fixture_db["sessions"])
        new = db["sessions"][expect:]
        self.assertEqual([s["planId"] for s in new], [self.plan_id] * 2, "login and the Done reload")
        self.assertEqual([s["priorSessions"] for s in new], [expect, expect + 1], "read-back worked")
        self.assertEqual(db["scans"][-1]["by"], "fg", "the harness scanned")

        later = self.now + 3600
        ing = self.fgold("ingest", now=later, home=home, data=data, root=root)
        self.assertEqual(ing.returncode, 0, ing.stdout + ing.stderr)
        self.assertEqual(INGEST_RE.search(ing.stdout).group(1), "2", "the reload save (.bak) and the logout save")
        status = self.fgold("status", now=later, home=home, data=data, root=root)
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertIn(f"game reported {self.plan_id} (matches)", status.stdout)
        first = status.stdout.splitlines()[0]
        self.assertNotIn("/reload", first)
        self.assertNotIn("restart", first)

        brief = self.fgold("brief", "--no-ingest", now=later, home=home, data=data, root=root)
        self.assertEqual(brief.returncode, 0, brief.stdout + brief.stderr)
        report = Path(re.search(r"^report: (.+)$", brief.stdout, re.M).group(1)).read_text(encoding="utf-8")
        self.assertIn(f"first login on plan {self.plan_id}: priorSessions {expect}, plan expected {expect} or more: OK",
                      report, "the Done reload adds a session; read-back is judged on the first login")
        self.assertNotIn("MISMATCH", report)
        self.assertNotIn("NOT READ BACK", report)
        draft2 = json.loads((home / "plan" / "draft.json").read_text(encoding="utf-8"))
        self.assertEqual(draft2["lastScanAt"], db["scans"][-1]["at"], "the next plan knows tonight's scan")
        self.assertEqual(draft2["expectPriorSessions"], expect + 2)


class TestHarnessFixture(RoundTrip, unittest.TestCase):
    """tests/fixtures/harness: written by the addon in the harness (two sessions, fg + other scans)."""

    wtf = HARNESS_WTF
    now = 1793932200          # Thu Nov 5, 8:30 PM CT, after the fixture's logout


class TestHandmadeFixture(RoundTrip, unittest.TestCase):
    """tests/fixtures/handmade: written to design.md by hand (copperOK false, two characters, buy rows)."""

    wtf = HANDMADE_WTF
    now = ts.NOW


class TestHandmadeRealStatic(RoundTrip, unittest.TestCase):
    """Smoke run on the real static/ data with every crafting profession on: the planner, the
    validator and the addon must agree on whatever the full data produces."""

    wtf = HANDMADE_WTF
    now = ts.NOW
    static_dir = REPO / "static"
    config = dict(ts.TEST_CONFIG, nest_egg_copper=100000,
                  professions={"Alchemy": 300, "First Aid": 300, "Cooking": 300, "Blacksmithing": 300,
                               "Tailoring": 300, "Leatherworking": 300, "Engineering": 300})
    expect_rows = False       # depends on the downloaded data; consistency is still checked

    @classmethod
    def setUpClass(cls):
        if not (cls.static_dir / "items.json").is_file():
            raise unittest.SkipTest("static/ has not been fetched (python -I tools/fetch_static.py)")
        super().setUpClass()


if __name__ == "__main__":
    unittest.main()
