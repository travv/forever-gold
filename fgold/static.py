"""Static game data from static/*.json (written by tools/fetch_static.py)."""

from __future__ import annotations

import json
from pathlib import Path

from . import paths, util

PROFESSION_LINES = {
    "alchemy": 171,
    "first aid": 129,
    "cooking": 185,
    "blacksmithing": 164,
    "tailoring": 197,
    "leatherworking": 165,
    "engineering": 202,
}
LINE_NAMES = {v: k.title() for k, v in PROFESSION_LINES.items()}
DEFAULT_HOUSES = {
    "Alliance": {"cut": 0.05, "deposit": 0.05},
    "Horde": {"cut": 0.05, "deposit": 0.05},
    "Neutral": {"cut": 0.15, "deposit": 0.25},
}


def _int_keys(d) -> dict:
    out = {}
    if not isinstance(d, dict):
        return out
    for k, v in d.items():
        i = util.item_id(k)
        if i is not None:
            out[i] = v
    return out


class Static:
    def __init__(self, items=None, recipes=None, houses=None, vendor_caps=None, vendor_reagents=None,
                 meta=None, directory=None, missing=None):
        self.items = items or {}
        self.recipes = recipes or {}
        self.houses = houses or dict(DEFAULT_HOUSES)
        self.vendor_caps = vendor_caps or {}
        self.vendor_reagents = vendor_reagents or {}
        self.meta = meta or {}
        self.directory = directory
        self.missing = list(missing or [])
        self._by_name = None

    # ---- loading --------------------------------------------------------
    @classmethod
    def load(cls, directory=None) -> "Static":
        d = Path(directory) if directory else paths.static_dir()
        missing = []

        def read(name, default):
            p = d / name
            if not p.is_file():
                missing.append(name)
                return default
            try:
                return json.loads(p.read_text(encoding="utf-8-sig"))   # a BOM is not an error
            except (OSError, ValueError, UnicodeDecodeError):
                missing.append(name)
                return default

        items = {}
        for k, v in _int_keys(read("items.json", {})).items():
            if isinstance(v, dict) and isinstance(v.get("name"), str):
                items[k] = {
                    "name": v["name"],
                    "sell": util.as_int(v.get("sell"), 0) or 0,
                    "buy": util.as_num(v.get("buy"), 0) or 0,
                    "stack": util.as_int(v.get("stack"), 1) or 1,
                    "q": util.as_int(v.get("q"), 1),
                }
        recipes = {}
        for k, v in _int_keys(read("recipes.json", {})).items():
            if not isinstance(v, dict):
                continue
            regs = []
            for pair in v.get("reagents") or []:
                if isinstance(pair, list) and len(pair) == 2:
                    rid, cnt = util.item_id(pair[0]), util.as_int(pair[1])
                    if rid and cnt and cnt > 0:
                        regs.append((rid, cnt))
            out = util.item_id(v.get("out"))
            if regs and out:
                recipes[k] = {
                    "skill_line": util.as_int(v.get("skill_line"), 0),
                    "skill": util.as_int(v.get("skill"), 0) or 0,
                    "reagents": regs,
                    "out": out,
                    "out_count": max(1, util.as_int(v.get("out_count"), 1) or 1),
                }
        houses = dict(DEFAULT_HOUSES)
        raw_h = read("houses.json", {})
        if isinstance(raw_h, dict):
            for h in DEFAULT_HOUSES:
                v = raw_h.get(h)
                if isinstance(v, dict) and util.as_num(v.get("cut")) is not None and util.as_num(v.get("deposit")) is not None:
                    houses[h] = {"cut": float(v["cut"]), "deposit": float(v["deposit"])}
        caps = {}
        for k, v in _int_keys(read("vendor_caps.json", {})).items():
            if isinstance(v, dict) and util.as_int(v.get("cap")):
                caps[k] = {"name": str(v.get("name", "")), "cap": util.as_int(v.get("cap")),
                           "verified": bool(v.get("verified", False))}
        reagents = {}
        for k, v in _int_keys(read("vendor_reagents.json", {})).items():
            reagents[k] = {"name": str(v.get("name", "")) if isinstance(v, dict) else str(v)}
        meta = read("meta.json", {})
        if "meta.json" in missing:
            missing.remove("meta.json")
        return cls(items, recipes, houses, caps, reagents, meta if isinstance(meta, dict) else {}, d, missing)

    # ---- lookups --------------------------------------------------------
    @property
    def fallback(self) -> bool:
        return bool(self.meta.get("fallback"))

    def item(self, iid):
        return self.items.get(iid)

    def name(self, iid) -> str:
        it = self.items.get(iid)
        return it["name"] if it else f"item {iid}"

    def sell(self, iid) -> int:
        it = self.items.get(iid)
        return int(it["sell"]) if it else 0

    def quality(self, iid):
        it = self.items.get(iid)
        return it["q"] if it else None

    def stack(self, iid) -> int:
        it = self.items.get(iid)
        return int(it["stack"]) if it else 1

    def is_vendor_reagent(self, iid) -> bool:
        return iid in self.vendor_reagents

    def vendor_buy(self, iid):
        """Per-unit vendor price, only for reagents NPC vendors actually sell."""
        if iid not in self.vendor_reagents:
            return None
        it = self.items.get(iid)
        if not it or not it["buy"]:
            return None
        return it["buy"]

    def cap(self, iid):
        c = self.vendor_caps.get(iid)
        return c["cap"] if c else None

    def house_rates(self, house: str) -> dict:
        return self.houses.get(house) or DEFAULT_HOUSES.get(house) or DEFAULT_HOUSES["Alliance"]

    def _names(self) -> dict:
        if self._by_name is None:
            idx = {}
            for iid, it in self.items.items():
                idx.setdefault(it["name"].lower(), []).append(iid)
            for v in idx.values():
                v.sort()
            self._by_name = idx
        return self._by_name

    def id_for_name(self, name: str):
        ids = self._names().get(str(name).strip().lower())
        return ids[0] if ids else None

    def find(self, query: str, limit: int = 10) -> list:
        """Item ids for a name: exact match, else prefix, else substring."""
        q = str(query).strip().lower()
        if not q:
            return []
        idx = self._names()
        if q in idx:
            return list(idx[q])
        pref = sorted((n, i) for n, ids in idx.items() if n.startswith(q) for i in ids)
        if pref:
            return [i for _, i in pref[:limit]]
        sub = sorted((n, i) for n, ids in idx.items() if q in n for i in ids)
        return [i for _, i in sub[:limit]]

    def recipes_for(self, line: int, max_skill: int) -> list:
        """[(spellID, recipe)] on a skill line that need <= max_skill."""
        return sorted((sid, r) for sid, r in self.recipes.items()
                      if r["skill_line"] == line and r["skill"] <= max_skill)


def profession_line(name: str):
    return PROFESSION_LINES.get(str(name).strip().lower())
