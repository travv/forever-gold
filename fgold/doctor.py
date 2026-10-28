"""doctor: find WoW: Forever installs, accounts and addon state (design.md 3)."""

from __future__ import annotations

import os
import string
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import config as config_mod
from . import ct, paths
from .install import is_link, same_dir

FOREVER_DIR = "_forever_"


def candidate_wow_dirs(platform: str = None, env=None, home: Path = None) -> list:
    """Folders that may hold a 'World of Warcraft' install (they may not exist)."""
    platform = platform or sys.platform
    env = os.environ if env is None else env
    home = Path(home) if home else Path.home()
    out = []
    if platform.startswith("win"):
        for var in ("ProgramFiles(x86)", "ProgramFiles", "ProgramW6432"):
            v = env.get(var)
            if v:
                out.append(Path(v) / "World of Warcraft")
        pub = env.get("PUBLIC")
        if pub:
            out.append(Path(pub) / "Games" / "World of Warcraft")
        for drive in string.ascii_uppercase[2:12]:  # C: .. L:
            base = Path(f"{drive}:/")
            for sub in ("World of Warcraft", "Games/World of Warcraft", "Program Files (x86)/World of Warcraft",
                        "Program Files/World of Warcraft", "Battle.net/World of Warcraft",
                        "Blizzard/World of Warcraft", "Games/Blizzard/World of Warcraft"):
                out.append(base / sub)
    elif platform == "darwin":
        out += [Path("/Applications/World of Warcraft"), home / "Applications" / "World of Warcraft"]
        try:
            vols = sorted(Path("/Volumes").iterdir())
        except OSError:
            vols = []
        for v in vols:
            out += [v / "World of Warcraft", v / "Applications" / "World of Warcraft", v / "Games" / "World of Warcraft"]
    else:
        out += [home / "Games" / "world-of-warcraft" / "drive_c" / "Program Files (x86)" / "World of Warcraft",
                home / ".wine" / "drive_c" / "Program Files (x86)" / "World of Warcraft",
                home / "Games" / "battlenet" / "drive_c" / "Program Files (x86)" / "World of Warcraft"]
    seen, uniq = set(), []
    for p in out:
        key = str(p).replace("\\", "/").lower()
        if key not in seen:
            seen.add(key)
            uniq.append(p)
    return uniq


def find_forever_roots(platform: str = None, env=None, home: Path = None) -> list:
    found = []
    for wow in candidate_wow_dirs(platform, env, home):
        p = wow / FOREVER_DIR
        try:
            if p.is_dir():
                found.append(p)
        except OSError:
            continue
    return found


@dataclass
class RootCheck:
    name: str
    raw: str
    exists: bool
    addon: str                       # linked | copy | missing | broken
    accounts: list = field(default_factory=list)   # [(account, has_sv, size, mtime)]


def check_root(root, addon_src: Path) -> RootCheck:
    rc = RootCheck(root.name, root.raw, root.exists, "missing")
    if not rc.exists:
        return rc
    d = root.addon_dir
    if is_link(d):
        rc.addon = "linked" if same_dir(d, addon_src) else "broken"
    elif d.is_dir():
        rc.addon = "copy"
    for acct, svdir in root.accounts():
        sv = svdir / paths.SV_FILE
        try:
            st = sv.stat()
            rc.accounts.append((acct, True, st.st_size, st.st_mtime))
        except OSError:
            rc.accounts.append((acct, False, 0, 0))
    return rc


def merged_roots(cfg_roots: list, found: list, platform: str = None) -> list:
    """Config roots plus newly found paths, named pc/mac/linux (-2, -3...)."""
    platform = platform or sys.platform
    base = "pc" if platform.startswith("win") else "mac" if platform == "darwin" else "linux"
    out = [dict(r) for r in cfg_roots]
    have = {os.path.normcase(os.path.normpath(str(r.get("path", "")))) for r in out}
    names = {r.get("name") for r in out}
    for p in found:
        key = os.path.normcase(os.path.normpath(str(p)))
        if key in have:
            continue
        name = base
        i = 2
        while name in names:
            name = f"{base}-{i}"
            i += 1
        names.add(name)
        have.add(key)
        out.append({"name": name, "path": Path(p).as_posix()})
    return out


def run(cfg: dict, roots: list, addon_src: Path, static, write: bool, config_path: Path, data_dir: Path,
        platform: str = None, env=None, home: Path = None, now=None):
    """(lines, exit_code). First line is the next action."""
    now = now or time.time()
    found = find_forever_roots(platform, env, home)
    checks = [check_root(r, addon_src) for r in roots]
    lines = []
    lines.append(f"Python {sys.version.split()[0]} ({sys.executable}) on {sys.platform}")
    lines.append(f"repo: {paths.REPO_ROOT}")
    lines.append(f"config: {config_path} ({'exists' if Path(config_path).is_file() else 'missing'})")
    lines.append(f"data: {data_dir}")
    lines.append(f"addon source: {addon_src} ({'ok' if Path(addon_src).is_dir() else 'MISSING'})")
    meta = static.meta or {}
    if static.missing:
        lines.append(f"static: MISSING {', '.join(static.missing)}. Fix: python tools/fetch_static.py")
    else:
        lines.append(f"static: build {meta.get('build', '?')}{' (FALLBACK)' if meta.get('fallback') else ''}, "
                     f"{len(static.items):,} items, {len(static.recipes):,} recipes")
    lines.append("WoW roots:" if checks else "WoW roots: none configured")
    for c in checks:
        lines.append(f"  {c.name}: {c.raw} [{'found' if c.exists else 'NOT FOUND'}]")
        if not c.exists:
            continue
        addon = {"linked": "linked to this repo", "copy": "installed (copy)",
                 "missing": "NOT INSTALLED (run python -m fgold install)",
                 "broken": "link points somewhere else (delete it, then python -m fgold install)"}[c.addon]
        lines.append(f"    addon: {addon}")
        if not c.accounts:
            lines.append("    accounts: none yet (log in once to create WTF/Account)")
        for acct, has, size, mtime in c.accounts:
            if has:
                lines.append(f"    account {acct}: ForeverGold.lua {size / 1024:.1f} KB, saved {ct.age_ago(now - mtime)}")
            else:
                lines.append(f"    account {acct}: no ForeverGold.lua yet")
    known = {os.path.normcase(os.path.normpath(str(r.path))) for r in roots}
    extra = [p for p in found if os.path.normcase(os.path.normpath(str(p))) not in known]
    for p in extra:
        lines.append(f"  found (not in config): {p}")
    code = 0
    if write:
        cfg_roots = cfg.get("roots") or []
        if roots and not cfg_roots:
            cfg_roots = [{"name": r.name, "path": r.raw} for r in roots]
        new_roots = merged_roots(cfg_roots, found, platform)
        newcfg = {k: v for k, v in cfg.items() if not k.startswith("_")}
        newcfg["roots"] = new_roots
        try:
            config_mod.save(config_path, newcfg)
            lines.append(f"saved {config_path} with {len(new_roots)} root(s)")
        except (config_mod.ConfigError, OSError) as exc:
            lines.append(f"error: could not save {config_path}: {exc}")
            code = 1
    if not roots and not found:
        nxt = ("Next: no WoW: Forever install found. Fix: add {\"name\": \"pc\", \"path\": \".../World of Warcraft/_forever_\"} "
               "to config.local.json roots, or pass --wow-root PATH")
        code = code or 1
    elif extra and not write:
        nxt = f"Next: run `python -m fgold doctor --write` to save the {len(extra)} install(s) found"
    elif any(c.exists and c.addon in ("missing", "broken") for c in checks) or (write and extra):
        nxt = "Next: run `python -m fgold install`, then fully restart WoW"
    elif any(not c.exists for c in checks):
        nxt = "Next: fix the root path(s) marked NOT FOUND in config.local.json"
    else:
        nxt = "Next: all set: run `python -m fgold brief` (or /forever-gold)"
    return [nxt] + lines, code
