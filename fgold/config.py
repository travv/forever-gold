"""config.local.json: load, validate (cause + fix messages), save."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from . import util

DEFAULTS = {
    "roots": [],
    "region": "us",
    "home_house": "Alliance",
    "nest_egg_copper": 5000000,
    "cut": {"faction": 0.05, "neutral": 0.15},
    "post_mode": "match",
    "undercut_copper": 100,
    "professions": {"Herbalism": 1, "Skinning": 1, "Alchemy": 0, "First Aid": 0, "Cooking": 0},
    "watchlist": [],
    "max_rows": 25,
    # Not in config.example.json yet: seconds the SavedVariables mtime must be
    # unchanged before ingest reads it (design.md 3: "stable for 2 s").
    "mtime_stable_seconds": 2,
}

KEY_ORDER = ["roots", "region", "home_house", "nest_egg_copper", "cut", "post_mode", "undercut_copper",
             "professions", "watchlist", "max_rows", "mtime_stable_seconds"]
REGIONS = ("us", "kr", "eu", "tw", "cn")
HOUSES = ("Alliance", "Horde", "Neutral")


class ConfigError(Exception):
    pass


def _check(cfg: dict, where: str) -> list:
    errs = []

    def bad(key, problem):
        errs.append(f"{where}: {key} {problem}")

    roots = cfg.get("roots")
    if not isinstance(roots, list):
        bad("roots", "must be a list of {\"name\", \"path\"} objects")
    else:
        for i, r in enumerate(roots):
            if not isinstance(r, dict) or not isinstance(r.get("path"), str) or not r.get("path"):
                bad(f"roots[{i}]", "needs a \"path\" string")
            elif "name" in r and not isinstance(r["name"], str):
                bad(f"roots[{i}].name", "must be a string")
    if cfg.get("region") not in REGIONS:
        bad("region", f"must be one of {', '.join(REGIONS)} (got {cfg.get('region')!r})")
    if cfg.get("home_house") not in HOUSES:
        bad("home_house", f"must be one of {', '.join(HOUSES)} (got {cfg.get('home_house')!r})")
    nest = cfg.get("nest_egg_copper")
    if util.as_int(nest) is None or isinstance(nest, (bool, str)) or nest < 0:
        bad("nest_egg_copper", f"must be a whole number of copper >= 0 (got {nest!r})")
    cut = cfg.get("cut")
    if not isinstance(cut, dict):
        bad("cut", "must be {\"faction\": 0.05, \"neutral\": 0.15}")
    else:
        for k in ("faction", "neutral"):
            v = cut.get(k)
            if util.as_num(v) is None or not (0 <= v < 1):
                bad(f"cut.{k}", f"must be a fraction between 0 and 1 (got {v!r})")
    if cfg.get("post_mode") not in ("match", "undercut"):
        bad("post_mode", f"must be \"match\" or \"undercut\" (got {cfg.get('post_mode')!r})")
    uc = cfg.get("undercut_copper")
    if util.as_int(uc) is None or isinstance(uc, (bool, str)) or uc < 0:
        bad("undercut_copper", f"must be a whole number of copper >= 0 (got {uc!r})")
    prof = cfg.get("professions")
    if not isinstance(prof, dict):
        bad("professions", "must be an object like {\"Alchemy\": 1}")
    else:
        for k, v in prof.items():
            if util.as_int(v) is None or isinstance(v, (bool, str)) or v < 0:
                bad(f"professions.{k}", f"must be a skill number >= 0 (got {v!r})")
    if not isinstance(cfg.get("watchlist"), list):
        bad("watchlist", "must be a list of item names or ids")
    mr = cfg.get("max_rows")
    if util.as_int(mr) is None or isinstance(mr, (bool, str)) or not (1 <= mr <= 200):
        bad("max_rows", f"must be a whole number from 1 to 200 (got {mr!r})")
    ms = cfg.get("mtime_stable_seconds")
    if util.as_num(ms) is None or ms < 0 or ms > 120:
        bad("mtime_stable_seconds", f"must be a number of seconds from 0 to 120 (got {ms!r})")
    return errs


def load(path, must_exist: bool = False) -> dict:
    """Defaults merged with the file. Raises ConfigError with cause + fix."""
    p = Path(path)
    cfg = copy.deepcopy(DEFAULTS)
    exists = p.is_file()
    if not exists and must_exist:
        raise ConfigError(f"{p}: not found. Fix: run `python -m fgold doctor --write` to create it.")
    if exists:
        try:
            # utf-8-sig: Windows PowerShell 5 (Set-Content -Encoding UTF8) and older Notepad write a BOM
            raw = json.loads(p.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError) as exc:
            raise ConfigError(f"{p}: cannot read ({exc}). Fix: check the file permissions.") from exc
        except ValueError as exc:
            raise ConfigError(f"{p}: not valid JSON ({exc}). Fix: correct the file, or delete it and run "
                              f"`python -m fgold doctor --write`.") from exc
        if not isinstance(raw, dict):
            raise ConfigError(f"{p}: must contain a JSON object. Fix: start from config.example.json.")
        for k, v in raw.items():
            if k == "cut" and isinstance(v, dict):
                merged = dict(cfg["cut"])
                merged.update(v)
                cfg["cut"] = merged
            else:
                cfg[k] = v
    errs = _check(cfg, p.name)
    if errs:
        raise ConfigError("; ".join(errs) + f". Fix: edit {p} (see config.example.json).")
    for k in ("nest_egg_copper", "undercut_copper", "max_rows"):
        cfg[k] = int(cfg[k])
    cfg["professions"] = {str(k): int(v) for k, v in cfg["professions"].items()}
    cfg["cut"] = {"faction": float(cfg["cut"]["faction"]), "neutral": float(cfg["cut"]["neutral"])}
    cfg["_path"] = str(p)
    cfg["_exists"] = exists
    return cfg


def to_file_dict(cfg: dict) -> dict:
    out = {}
    for k in KEY_ORDER:
        if k in cfg:
            out[k] = cfg[k]
    for k, v in cfg.items():
        if k not in out and not k.startswith("_"):
            out[k] = v
    return out


def save(path, cfg: dict) -> None:
    data = to_file_dict(cfg)
    errs = _check({**DEFAULTS, **data}, Path(path).name)
    if errs:
        raise ConfigError("; ".join(errs))
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    util.write_text_atomic(path, text)


def cut_default(cfg: dict, house: str) -> float:
    cut = cfg.get("cut") or {}
    if house == "Neutral":
        return float(cut.get("neutral", 0.15))
    return float(cut.get("faction", 0.05))
