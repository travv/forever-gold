"""install: put the addon into <root>/Interface/AddOns/ForeverGold (design.md 3).

Local root: a directory junction on Windows, a symlink on macOS/Linux, a copy
if linking fails. Remote root (UNC path): always a copy. PlanData.lua is
created from PlanDefault.lua's content when it is missing. New addon files are
only seen after a FULL client restart.

The junction is the one `mklink /J` makes, created with CPython's
_winapi.CreateJunction instead of `cmd /c mklink`: cmd.exe re-parses its
command line, so a path with `&` would end the command and link elsewhere,
`^` is eaten as an escape and `%VAR%` expands even inside quotes. Whatever
made it, a new link must resolve to the repo addon folder; a link that points
anywhere else is removed (the link only) and the addon is copied instead.
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import luasv, util
from .apply import HEADER, GLOBAL

DEFAULT_PLAN = {
    "schema": 1,
    "id": "none",
    "generatedAt": 0,
    "basedOnScan": {"at": 0, "market": "", "rows": 0},
    "lastScanAt": 0,
    "expectPriorSessions": 0,
    "settings": {"postMode": "match", "undercut": 100},
    "headline": "No plan yet: run /forever-gold",
    "notes": [],
    "steps": [],
    "budget": {"spendCap": 0, "principal": 0},
    "buy": [],
    "craft": [],
    "post": [],
    "hold": [],
    "vendor": [],
    "ref": {},
}
RESTART = ("First install: fully QUIT and restart WoW (a /reload does not see new addon files). "
           "Then check the AddOns list at character select.")
COPY_IGNORE = ("PlanData.lua", ".DS_Store", "Thumbs.db", "*.tmp")


@dataclass
class InstallOutcome:
    root: str
    status: str            # linked | junction | copied | updated | already | error
    detail: str = ""
    plan_created: bool = False


@dataclass
class InstallReport:
    outcomes: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.outcomes) and not self.errors


is_link = util.is_link
same_dir = util.same_dir


def remove_link(p: Path) -> None:
    """Remove a symlink or junction itself, never the folder it points to."""
    try:
        os.unlink(p)        # symlinks; on Windows also directory symlinks and junctions
    except OSError:
        os.rmdir(p)         # a junction on Pythons whose unlink refuses it (removes the link only)


def default_plan_text(addon_src: Path) -> str:
    """PlanData.lua content made from PlanDefault.lua (falls back to the built-in default)."""
    plan = None
    p = Path(addon_src) / "PlanDefault.lua"
    if p.is_file():
        try:
            v = luasv.find_assignment(p.read_bytes(), "ForeverGoldPlanDefault")
            if isinstance(v, dict) and v.get("schema") == 1:
                plan = v
        except (luasv.LuaSVError, OSError):
            plan = None
    if plan is None:
        plan = DEFAULT_PLAN
    return HEADER + luasv.dump(GLOBAL, plan)


def ensure_plan_data(folder: Path, addon_src: Path) -> bool:
    target = Path(folder) / "PlanData.lua"
    if target.exists():
        return False
    from . import util
    util.write_text_atomic(target, default_plan_text(addon_src))
    return True


def _winapi_junction():
    try:
        import _winapi  # CPython stdlib on Windows
        return _winapi.CreateJunction
    except (ImportError, AttributeError):
        return None


def make_junction(target: Path, source: Path, create=None) -> None:
    """Windows directory junction at target pointing at source (no admin rights, no shell).

    create(source, target) defaults to _winapi.CreateJunction; tests pass a stand-in."""
    create = create or _winapi_junction()
    if create is None:
        raise OSError("this Python has no _winapi.CreateJunction (use the python.org or winget CPython)")
    create(str(source), str(target))


def copy_addon(source: Path, target: Path) -> None:
    shutil.copytree(source, target, dirs_exist_ok=True, ignore=shutil.ignore_patterns(*COPY_IGNORE))


def _link_text(p: Path) -> str:
    try:
        return os.readlink(p)
    except (OSError, ValueError):
        return "somewhere else"


def install_root(root, addon_src: Path, platform: str = None, create_junction=None) -> InstallOutcome:
    platform = platform or sys.platform
    src = Path(addon_src)
    if not root.exists:
        return InstallOutcome(root.name, "error", f"WoW root not found: {root.raw}. Fix: correct the path in "
                                                  f"config.local.json or run `python -m fgold doctor --write`")
    target = root.addon_dir
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return InstallOutcome(root.name, "error", f"cannot create {target.parent} ({exc}). Fix: check permissions "
                                                  f"(Program Files may need the folder created once by hand)")
    if is_link(target) or target.exists():
        if same_dir(target, src):
            created = ensure_plan_data(src, src)
            return InstallOutcome(root.name, "already", f"{target} already points at {src}", created)
        if is_link(target):
            return InstallOutcome(root.name, "error", f"{target} is a link to somewhere else. Fix: delete that "
                                                      f"link (not the folder it points to) and run install again")
        try:
            copy_addon(src, target)
        except (OSError, shutil.Error) as exc:
            return InstallOutcome(root.name, "error", f"updating the copy at {target} failed ({exc})")
        created = ensure_plan_data(target, src)
        return InstallOutcome(root.name, "updated", f"updated the copy at {target} (re-run install after addon "
                                                    f"changes)", created)
    if root.remote:
        try:
            copy_addon(src, target)
        except (OSError, shutil.Error) as exc:
            return InstallOutcome(root.name, "error", f"copy to {target} failed ({exc})")
        created = ensure_plan_data(target, src)
        return InstallOutcome(root.name, "copied", f"copied to remote root {target} (re-run install after addon "
                                                   f"changes; plan --apply writes PlanData.lua there)", created)
    kind = "junction" if platform.startswith("win") else "linked"
    link_err = None
    try:
        if kind == "junction":
            make_junction(target, src, create=create_junction)
        else:
            os.symlink(src, target, target_is_directory=True)
    except (OSError, NotImplementedError, ValueError) as exc:
        link_err = exc
    # Judge the result by where the link points, not by the call's return: never write through
    # a link that resolves anywhere but the repo addon folder.
    if is_link(target):
        if same_dir(target, src):
            created = ensure_plan_data(src, src)
            return InstallOutcome(root.name, kind, f"{target} -> {src}", created)
        wrong = _link_text(target)
        try:
            remove_link(target)
        except OSError as exc:
            return InstallOutcome(root.name, "error", f"{target} was linked to {wrong}, not {src}, and removing that "
                                                      f"link failed ({exc}). Fix: delete the link {target} (not the "
                                                      f"folder it points to), then run install again")
        link_err = OSError(f"the new link pointed at {wrong}, not {src}, so it was removed")
    elif link_err is None:
        link_err = OSError(f"no link appeared at {target}")
    try:
        copy_addon(src, target)
    except (OSError, shutil.Error) as exc:
        return InstallOutcome(root.name, "error", f"link failed ({link_err}) and copy failed ({exc})")
    created = ensure_plan_data(target, src)
    return InstallOutcome(root.name, "copied", f"link failed ({link_err}); copied to {target} instead "
                                               f"(re-run install after addon changes)", created)


def install(roots, addon_src, only=None, platform=None, create_junction=None) -> InstallReport:
    rep = InstallReport()
    src = Path(addon_src)
    if not src.is_dir() or not any(src.glob("*.toc")):
        rep.errors.append(f"addon source not found at {src} (no .toc file). Fix: run from a full checkout of "
                          f"forever-gold (git pull), or set FGOLD_ADDON_DIR")
        return rep
    chosen = [r for r in roots if only is None or r.name == only]
    if only is not None and not chosen:
        rep.errors.append(f"no root named {only!r} (roots: {', '.join(r.name for r in roots) or 'none'}). "
                          f"Fix: python -m fgold doctor")
        return rep
    if not chosen:
        rep.errors.append("no WoW roots configured. Fix: python -m fgold doctor --write (or pass --wow-root PATH)")
        return rep
    for root in chosen:
        o = install_root(root, src, platform=platform, create_junction=create_junction)
        rep.outcomes.append(o)
        if o.status == "error":
            rep.errors.append(f"{o.root}: {o.detail}")
    return rep
