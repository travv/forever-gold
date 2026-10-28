"""Where things live: repo, data store, config, WoW roots and accounts.

Overrides (environment):
    FGOLD_HOME       relocates config.local.json, data/, plan/, reports/ and
                     addon/ForeverGold (tests point this at a temp dir)
    FGOLD_DATA       data directory (same as --data-dir)
    FGOLD_ADDON_DIR  the repo addon folder that `install` links/copies and
                     `plan --apply` also writes PlanData.lua into
    FGOLD_STATIC     static data directory (default <repo>/static)
"""

from __future__ import annotations

import os
import re
import string
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ADDON_NAME = "ForeverGold"
SV_FILE = "ForeverGold.lua"
SV_BAK = "ForeverGold.lua.bak"


def home() -> Path:
    env = os.environ.get("FGOLD_HOME")
    return Path(env).expanduser() if env else REPO_ROOT


def default_data_dir() -> Path:
    env = os.environ.get("FGOLD_DATA")
    return Path(env).expanduser() if env else home() / "data"


def default_config_path() -> Path:
    return home() / "config.local.json"


def example_config_path() -> Path:
    return REPO_ROOT / "config.example.json"


def plan_dir() -> Path:
    return home() / "plan"


def reports_dir() -> Path:
    return home() / "reports"


def addon_src_dir() -> Path:
    env = os.environ.get("FGOLD_ADDON_DIR")
    return Path(env).expanduser() if env else home() / "addon" / ADDON_NAME


def static_dir() -> Path:
    env = os.environ.get("FGOLD_STATIC")
    return Path(env).expanduser() if env else REPO_ROOT / "static"


def is_unc(path) -> bool:
    """\\\\server\\share or //server/share (a remote root: copy, never link)."""
    s = str(path)
    return s.startswith("\\\\") or s.startswith("//")


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slug(text: str) -> str:
    s = _SLUG_RE.sub("-", str(text).lower()).strip("-")
    return s or "root"


_SAFE_CHARS = set(string.ascii_letters + string.digits + " ._-'()&,+#")
_WIN_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}


def safe_part(text: str) -> str:
    """A file-system-safe single path component (Windows rules), %XX-encoded."""
    out = []
    for ch in str(text):
        if ch in _SAFE_CHARS:
            out.append(ch)
        else:
            out.extend("%%%02X" % b for b in ch.encode("utf-8"))
    s = "".join(out)
    if not s:
        return "_"
    if s[-1] in ". ":
        s = s[:-1] + "%%%02X" % ord(s[-1])
    if s in (".", ".."):
        s = s.replace(".", "%2E")
    if s.split(".")[0].upper() in _WIN_RESERVED:
        s = "%%%02X" % ord(s[0]) + s[1:]
    return s


@dataclass
class Root:
    name: str
    path: Path
    raw: str
    remote: bool = False

    @property
    def exists(self) -> bool:
        try:
            return self.path.is_dir()
        except OSError:
            return False

    @property
    def addon_dir(self) -> Path:
        return self.path / "Interface" / "AddOns" / ADDON_NAME

    def accounts(self) -> list:
        """[(account_name, SavedVariables dir)] sorted by name."""
        acct_root = self.path / "WTF" / "Account"
        out = []
        try:
            entries = sorted(acct_root.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            return out
        for d in entries:
            try:
                if d.is_dir() and (d / "SavedVariables").is_dir():
                    out.append((d.name, d / "SavedVariables"))
            except OSError:
                continue
        return out


def name_for_path(path: str, taken: set) -> str:
    p = Path(str(path).replace("\\", "/").rstrip("/"))
    base = p.name.strip("_") or "root"
    if base.lower() == "forever" and p.parent.name:
        base = f"{p.parent.name}-forever"
    name = slug(base)
    cand = name
    i = 2
    while cand in taken:
        cand = f"{name}-{i}"
        i += 1
    taken.add(cand)
    return cand


def resolve_roots(cfg: dict, cli_roots=None) -> list:
    """--wow-root paths (if any) replace the config roots."""
    roots = []
    taken = set()
    if cli_roots:
        for raw in cli_roots:
            name = name_for_path(raw, taken)
            roots.append(Root(name=name, path=Path(raw).expanduser(), raw=str(raw), remote=is_unc(raw)))
        return roots
    for entry in cfg.get("roots") or []:
        raw = str(entry.get("path", ""))
        if entry.get("name"):
            base = slug(entry["name"])
            name, i = base, 2
            while name in taken:
                name, i = f"{base}-{i}", i + 1
            taken.add(name)
        else:
            name = name_for_path(raw, taken)
        roots.append(Root(name=name, path=Path(raw).expanduser(), raw=raw, remote=is_unc(raw)))
    return roots
