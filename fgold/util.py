"""Small shared helpers: money, safe coercion of untrusted values, atomic files."""

from __future__ import annotations

import gzip
import json
import math
import os
import re
import stat
import time
from pathlib import Path

COPPER_PER_SILVER = 100
COPPER_PER_GOLD = 10000
_INT_RE = re.compile(r"\A-?[0-9]{1,30}\Z")


# ---- money --------------------------------------------------------------------

def money_round(x) -> int:
    """Round half up to whole copper (Python's round() is banker's rounding)."""
    return int(math.floor(float(x) + 0.5))


def ceil_silver(c: int) -> int:
    """Round copper up to a whole silver (Blizzard's SanitizeAuctionHousePrice)."""
    c = int(c)
    if c <= 0:
        return 0
    return ((c + COPPER_PER_SILVER - 1) // COPPER_PER_SILVER) * COPPER_PER_SILVER


def fmt_money(c) -> str:
    """12345 -> '1g 23s 45c'; zero parts are left out; 0 -> '0c'."""
    try:
        c = money_round(c)
    except (TypeError, ValueError, OverflowError):
        return "?"
    sign = "-" if c < 0 else ""
    c = abs(c)
    g, rest = divmod(c, COPPER_PER_GOLD)
    s, cc = divmod(rest, COPPER_PER_SILVER)
    parts = []
    if g:
        parts.append(f"{g:,}g")
    if s:
        parts.append(f"{s}s")
    if cc or not parts:
        parts.append(f"{cc}c")
    return sign + " ".join(parts)


def fmt_pct(x: float, digits: int = 0) -> str:
    return f"{x * 100:.{digits}f}%"


# ---- untrusted value coercion --------------------------------------------------

def as_int(v, default=None):
    """int for ints and whole floats; default for anything else (bools too)."""
    if isinstance(v, bool):
        return default
    if isinstance(v, int):
        return v
    if isinstance(v, float) and math.isfinite(v) and v == math.floor(v):
        return int(v)
    if isinstance(v, str) and _INT_RE.match(v.strip()):
        return int(v.strip())
    return default


def as_num(v, default=None):
    if isinstance(v, bool):
        return default
    if isinstance(v, (int, float)) and math.isfinite(v):
        return v
    return default


def as_str(v, default=None, limit: int = 500):
    if isinstance(v, str):
        return v[:limit]
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return str(v)[:limit]
    return default


def as_bool(v, default=None):
    return v if isinstance(v, bool) else default


def as_list(v) -> list:
    """Lua list from luasv: list stays, dict with int keys -> values in key order."""
    if isinstance(v, list):
        return v
    if isinstance(v, dict):
        keys = [k for k in v if isinstance(k, int) and not isinstance(k, bool)]
        return [v[k] for k in sorted(keys)]
    return []


def as_dict(v) -> dict:
    """Lua map from luasv: dict stays, list [a, b] -> {1: a, 2: b}."""
    if isinstance(v, dict):
        return v
    if isinstance(v, list):
        return {i: x for i, x in enumerate(v, 1)}
    return {}


def item_id(v):
    """Positive int item id from int, whole float or digit string; else None."""
    i = as_int(v)
    if i is None or i <= 0:
        return None
    return i


# ---- files ----------------------------------------------------------------------

def is_link(p) -> bool:
    """Symlink, or (Windows) a directory junction / mount point."""
    p = Path(p)
    try:
        if p.is_symlink():
            return True
    except OSError:
        return False
    if os.name == "nt":
        try:
            st = os.lstat(p)
        except OSError:
            return False
        tag = getattr(st, "st_reparse_tag", 0)
        return bool(tag) and tag == getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003)
    return False


def same_dir(a, b) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def read_json(path, default=None):
    p = Path(path)
    if not p.is_file():
        return default
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_text_atomic(path, text: str, retries: int = 8, delay: float = 0.25) -> None:
    """Write via temp file + os.replace; retry PermissionError (Windows locks)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    data = text.encode("utf-8")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    replace_with_retry(tmp, p, retries=retries, delay=delay)


def replace_with_retry(src, dst, retries: int = 8, delay: float = 0.25, _replace=None) -> None:
    rep = _replace or os.replace
    last = None
    for attempt in range(max(1, retries)):
        try:
            rep(src, dst)
            return
        except PermissionError as exc:  # file open in another program (Windows)
            last = exc
            time.sleep(delay * (attempt + 1))
    try:
        os.unlink(src)
    except OSError:
        pass
    raise last


def write_json(path, data, indent=2) -> None:
    text = json.dumps(data, ensure_ascii=False, indent=indent, sort_keys=True) + "\n"
    write_text_atomic(path, text)


def write_json_gz(path, data) -> None:
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    with open(tmp, "wb") as f:
        f.write(gzip.compress(raw, mtime=0))
    replace_with_retry(tmp, p)


def read_json_gz(path):
    with gzip.open(path, "rb") as f:
        return json.loads(f.read().decode("utf-8"))


def read_jsonl(path) -> list:
    p = Path(path)
    if not p.is_file():
        return []
    out = []
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def jsonl_text(records) -> str:
    return "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in records)
