"""Command line: python -m fgold <command> (design.md 3).

Global options (before or after the command):
    --data-dir PATH   data store (default <repo>/data, env FGOLD_DATA)
    --config PATH     config file (default <repo>/config.local.json)
    --wow-root PATH   WoW root, repeatable; replaces the config roots
Test hooks (environment): FGOLD_HOME, FGOLD_ADDON_DIR, FGOLD_STATIC (see
paths.py), FGOLD_NOW (fixed clock, unix seconds), FGOLD_STABLE_SECS
(mtime-stable wait for ingest).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from . import __version__
from . import apply as apply_mod
from . import config as config_mod
from . import ct, doctor, ingest, install, paths, planner, probes, render, util
from . import market as mk
from .static import Static
from .store import Store, split_market

GENERATED_DRAFT = "draft.orig.json"   # the draft as brief wrote it; plan --apply checks edits against it


class CliError(Exception):
    """A problem with a cause and a fix, printed without a traceback."""

    def __init__(self, message: str, code: int = 1):
        self.code = code
        super().__init__(message)


def _now() -> int:
    env = os.environ.get("FGOLD_NOW")
    if env:
        v = util.as_int(env)
        if v is not None:
            return v
    return int(time.time())


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--data-dir", metavar="PATH", default=argparse.SUPPRESS,
                        help="data store (default <repo>/data, env FGOLD_DATA)")
    common.add_argument("--config", metavar="PATH", default=argparse.SUPPRESS,
                        help="config file (default <repo>/config.local.json)")
    common.add_argument("--wow-root", metavar="PATH", action="append", default=argparse.SUPPRESS,
                        help="WoW root folder (the _forever_ folder); repeatable; replaces config roots")
    p = argparse.ArgumentParser(prog="python -m fgold", parents=[common],
                                description="Claude-assisted gold making for WoW: Forever. Claude thinks, you click.")
    p.add_argument("--version", action="version", version=f"fgold {__version__}")
    sub = p.add_subparsers(dest="command", metavar="command")
    d = sub.add_parser("doctor", parents=[common], help="find WoW installs, accounts, addon state")
    d.add_argument("--write", action="store_true", help="save the roots found to config.local.json")
    i = sub.add_parser("install", parents=[common], help="link/copy the addon into each WoW root")
    i.add_argument("--root", metavar="NAME", help="only this root (by name)")
    sub.add_parser("ingest", parents=[common], help="read SavedVariables into the data store")
    b = sub.add_parser("brief", parents=[common], help="ingest + plan: writes the brief and plan/draft.json")
    b.add_argument("--no-ingest", action="store_true", help="use the data store as it is")
    b.add_argument("--market", metavar="KEY", help="market key region|realm|house (default: newest home market)")
    pl = sub.add_parser("plan", parents=[common], help="validate the draft; --apply writes PlanData.lua")
    pl.add_argument("--apply", action="store_true", help="write PlanData.lua to every root and the repo")
    pl.add_argument("--draft", metavar="PATH", help="draft file (default plan/draft.json)")
    sub.add_parser("status", parents=[common], help="data health")
    sub.add_parser("probes", parents=[common], help="launch-night measurements")
    pr = sub.add_parser("price", parents=[common], help="price lookup by name or item id")
    pr.add_argument("item", nargs="+", help="item name or id")
    return p


class Env:
    def __init__(self, args):
        self.args = args
        self.now = _now()
        self.config_path = Path(getattr(args, "config", None) or paths.default_config_path())
        self.data_dir = Path(getattr(args, "data_dir", None) or paths.default_data_dir())
        self._cfg = None
        self._static = None

    @property
    def cfg(self) -> dict:
        if self._cfg is None:
            try:
                self._cfg = config_mod.load(self.config_path)
            except config_mod.ConfigError as exc:
                raise CliError(f"config: {exc}", code=2) from exc
        return self._cfg

    @property
    def roots(self) -> list:
        return paths.resolve_roots(self.cfg, getattr(self.args, "wow_root", None))

    @property
    def static(self) -> Static:
        if self._static is None:
            self._static = Static.load()
        return self._static

    @property
    def store(self) -> Store:
        return Store(self.data_dir)

    def stable_secs(self) -> float:
        env = os.environ.get("FGOLD_STABLE_SECS")
        if env is not None:
            try:
                return max(0.0, float(env))
            except ValueError:
                pass
        return float(self.cfg.get("mtime_stable_seconds", 2))


def out(line: str = "") -> None:
    print(line, flush=True)


def err(line: str) -> None:
    print(line, file=sys.stderr, flush=True)


# ---- commands ---------------------------------------------------------------------

def cmd_doctor(env: Env) -> int:
    broken = False
    try:
        cfg = env.cfg
    except CliError as exc:
        err(f"error: {exc}")
        cfg = dict(config_mod.DEFAULTS)
        broken = True
    # Never save over a config we could not read: --write would replace the
    # hand-set nest egg and professions with the defaults.
    write = env.args.write and not broken
    roots = paths.resolve_roots(cfg, getattr(env.args, "wow_root", None))
    lines, code = doctor.run(cfg, roots, paths.addon_src_dir(), env.static, write, env.config_path,
                             env.data_dir, now=env.now)
    if broken and env.args.write:
        lines[0] = (f"Next: fix {env.config_path.name} (error above), then run `python -m fgold doctor --write` "
                    f"again. Not saved: it would have replaced your settings with the defaults")
        code = 2
    for line in lines:
        out(line)
    return code


def cmd_install(env: Env) -> int:
    rep = install.install(env.roots, paths.addon_src_dir(), only=env.args.root)
    fresh = [o for o in rep.outcomes if o.status in ("linked", "junction", "copied")]
    if rep.errors and not rep.outcomes:
        out(f"Next: fix the problem below, then run `python -m fgold install` again")
    elif fresh:
        out("Next: fully QUIT and restart WoW (new addon files need a full restart, not /reload)")
    elif rep.errors:
        out("Next: fix the failed root(s) below, then run `python -m fgold install` again")
    else:
        out("Next: run `python -m fgold brief` (the addon is installed everywhere)")
    for o in rep.outcomes:
        extra = "; created PlanData.lua from PlanDefault" if o.plan_created else ""
        out(f"{o.root}: {o.status}: {o.detail}{extra}")
    for e in rep.errors:
        err(f"error: {e}")
    if fresh:
        out(install.RESTART)
    return 1 if rep.errors else 0


def _run_ingest(env: Env):
    return ingest.ingest(env.roots, env.data_dir, stable_secs=env.stable_secs(), region_default=env.cfg["region"],
                         now=env.now)


def cmd_ingest(env: Env) -> int:
    rep = _run_ingest(env)
    if not env.roots:
        out("Next: run `python -m fgold doctor --write` (no WoW roots configured)")
    elif rep.errors:
        out("Next: see the errors below (cause + fix)")
    elif not rep.files_seen:
        out("Next: log in once with the addon installed, then /reload so the game writes ForeverGold.lua")
    else:
        out("Next: run `python -m fgold brief`")
    out(rep.summary())
    for w in rep.warnings:
        out(f"warning: {w}")
    for e in rep.errors:
        err(f"error: {e}")
    return 1 if rep.errors else 0


def choose_market(store: Store, cfg: dict, wanted=None):
    markets = store.markets()
    if wanted:
        try:
            split_market(wanted)
        except ValueError as exc:
            raise CliError(f"--market {wanted!r}: {exc}. Fix: use a key like us|Forever|Alliance", code=2) from exc
        if wanted not in markets:
            raise CliError(f"no scans for market {wanted!r}. Markets with scans: {', '.join(markets) or 'none'}", code=1)
        return wanted
    if not markets:
        return None

    def newest(m):
        return store.scan_counts(m)[2] or 0

    pref = [m for m in markets if split_market(m)[0] == cfg["region"] and split_market(m)[2] == cfg["home_house"]]
    return max(pref or markets, key=newest)


def plan_inputs(env: Env, market) -> planner.PlanInputs:
    store = env.store
    scans, days = [], None
    found = store.scan_paths(market) if market else []
    if found:
        # Only the scans the math reads (history and movers look 7 days back from the newest);
        # the scan-day count comes from the file names, so old scans are never loaded.
        scans = store.load_scans(market, since=found[-1][0] - mk.HISTORY_WINDOW)
        days = mk.days_of(at for at, _ in found)
    last_scan_at = 0
    for m in store.markets():
        last_scan_at = max(last_scan_at, store.scan_counts(m)[2] or 0)
    return planner.PlanInputs(
        cfg=env.cfg, static=env.static, market=market, scans=scans, inventory=store.inventory(),
        sessions=store.sessions(), ledger=store.ledger(), probes=store.probes(), state=store.state(),
        now=env.now, char_houses=probes.char_houses(store), last_scan_at=last_scan_at, scan_days=days)


def cmd_brief(env: Env) -> int:
    rep = None
    if not env.args.no_ingest:
        rep = _run_ingest(env)
    store = env.store
    market = choose_market(store, env.cfg, env.args.market)
    plan, info = planner.build_plan(plan_inputs(env, market))
    roots = env.roots
    root_rows = [(r.name, r.raw, r.exists, r.exists and r.addon_dir.is_dir()) for r in roots]
    markets = []
    for m in store.markets():
        n, first, last = store.scan_counts(m)
        markets.append((m, n, first, last))
    applied = store.state().get("applied") or {}
    warnings = []
    if env.static.missing:
        warnings.append(f"static data missing ({', '.join(env.static.missing)}): run `python tools/fetch_static.py`")
    bctx = render.BriefContext(plan=plan, info=info, static=env.static, now=env.now, roots=root_rows,
                               sessions=store.sessions(), ingest=rep, markets=markets, applied=applied,
                               warnings=warnings)
    text = render.render_brief(bctx)
    report = paths.reports_dir() / f"{ct.stamp_ct(env.now)}-brief.md"
    util.write_text_atomic(report, text)
    draft = paths.plan_dir() / "draft.json"
    util.write_json(paths.plan_dir() / GENERATED_DRAFT, plan)
    util.write_json(draft, plan)
    out(render.next_action(bctx))
    out(f"report: {report}")
    out(f"draft: {draft}")
    for i, line in enumerate(render.summary_lines(bctx), 1):
        out(f"{i}. {line}")
    if rep is not None:
        for e in rep.errors:
            err(f"ingest error: {e}")
    return 0


def _load_draft(path: Path, what: str = "draft") -> dict:
    if not path.is_file():
        raise CliError(f"no {what} at {path}. Fix: run `python -m fgold brief` first")
    try:
        # utf-8-sig: a BOM (PowerShell 5, older Notepad) is not an error
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise CliError(f"{path} is not valid JSON ({exc}). Fix: correct the edit, or re-run `python -m fgold brief`")


def _plan_gold(store: Store, sessions: list, market: str) -> int:
    """Gold of the account that pays for the buys, as the planner chose it (0 without inventory)."""
    inv = store.inventory()
    try:
        return planner.spend_basis(inv, sessions, market or None)["gold"]
    except ValueError:
        return planner.spend_basis(inv, sessions, None)["gold"]


def _market_ref(store: Store, bos) -> dict:
    """ref as brief builds it, from the stored scan basedOnScan names ({} when there is none)."""
    if not isinstance(bos, dict):
        return {}
    market, at = bos.get("market"), util.as_int(bos.get("at"))
    if not isinstance(market, str) or not market or not at:
        return {}
    try:
        path = store.scan_path(market, at)
        if not path.is_file():
            return {}
        return apply_mod.ref_from_scan(util.read_json_gz(path))
    except (OSError, ValueError, EOFError):
        return {}


def cmd_plan(env: Env) -> int:
    draft_path = Path(env.args.draft) if env.args.draft else paths.plan_dir() / "draft.json"
    draft = _load_draft(draft_path)
    generated = _load_draft(paths.plan_dir() / GENERATED_DRAFT, "generated draft (brief writes it next to draft.json)")
    store = env.store
    sessions = store.sessions()
    copper_ok = planner.newest_copper_ok(sessions)
    bos = draft.get("basedOnScan") if isinstance(draft, dict) else None
    market = bos.get("market") if isinstance(bos, dict) and isinstance(bos.get("market"), str) else None
    gold = _plan_gold(store, sessions, market or None)
    try:
        res = apply_mod.apply_draft(draft, env.roots, paths.addon_src_dir(), static=env.static,
                                    max_rows=int(env.cfg.get("max_rows", 25)), copper_ok=copper_ok, gold=gold,
                                    nest=int(env.cfg["nest_egg_copper"]), market_ref=_market_ref(store, bos),
                                    generated=generated, now=env.now, dry_run=not env.args.apply)
    except apply_mod.PlanError as exc:
        out(f"Next: fix {len(exc.problems)} problem(s) in {draft_path}, then run `python -m fgold plan --apply` again")
        for i, p in enumerate(exc.problems, 1):
            err(f"{i}. {p}")
        return 1
    plan = res.plan
    counts = ", ".join(f"{len(plan[k])} {k}" for k in ("buy", "craft", "post", "hold", "vendor"))
    if not env.args.apply:
        out(f"Next: run `python -m fgold plan --apply` (draft is valid: {counts})")
        for w in res.warnings:
            out(f"warning: {w}")
        return 0
    if res.reached:
        out(f"plan {plan['id']}: now /reload in game ({counts})")
    else:
        out(f"Next: run `python -m fgold install` (plan {plan['id']} was not written to any WoW root)")
    for label, path in res.written:
        out(f"wrote {label}: {path}")
    for label, why in res.skipped:
        out(f"skipped {label}: {why}")
    for w in res.warnings:
        out(f"warning: {w}")
    if not res.reached:
        # No client can load this plan, so it is not "applied": status keeps judging the last plan
        # that reached a root instead of prescribing a restart for an id no game has.
        return 1
    applied_json = paths.plan_dir() / "applied.json"
    util.write_json(applied_json, apply_mod.plan_to_json(plan))
    state = store.state()
    expect = {k: max(0, min(20, util.as_int(v.get("sessions"), 0) or 0))
              for k, v in (state.get("accounts") or {}).items() if isinstance(v, dict)}
    state["applied"] = {"id": plan["id"], "at": env.now, "draft": str(draft_path),
                        "expectPriorSessions": plan.get("expectPriorSessions"), "expect": expect,
                        "roots": list(res.reached),
                        "skipped": {label: why for label, why in res.skipped if label != "repo"},
                        "targets": [str(p) for _, p in res.written]}
    store.save_state(state)
    return 0


def cmd_status(env: Env) -> int:
    for line in probes.status_report(env.store, env.cfg, env.static, env.now, env.roots):
        out(line)
    return 0


def cmd_probes(env: Env) -> int:
    for line in probes.probes_report(env.store, env.cfg, env.static, env.now):
        out(line)
    return 0


def cmd_price(env: Env) -> int:
    lines, code = probes.price_report(env.store, env.static, " ".join(env.args.item), env.now)
    for line in lines:
        out(line)
    return code


COMMANDS = {
    "doctor": cmd_doctor,
    "install": cmd_install,
    "ingest": cmd_ingest,
    "brief": cmd_brief,
    "plan": cmd_plan,
    "status": cmd_status,
    "probes": cmd_probes,
    "price": cmd_price,
}


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2
    env = Env(args)
    try:
        return COMMANDS[args.command](env)
    except CliError as exc:
        err(f"error: {exc}")
        return exc.code
    except KeyboardInterrupt:
        err("interrupted")
        return 130
