"""Fetch WoW: Forever static game data from wago.tools and write static/*.json.

Usage (from the repo root):
    python tools/fetch_static.py                   # default build 1.60.1.70634
    python tools/fetch_static.py --build latest    # newest wowf build on wago.tools
    python tools/fetch_static.py --from-dir DIR    # re-use CSVs downloaded earlier

Downloads are untrusted data: they land in a fresh temp directory, are parsed
as CSV only (never executed), and every number is converted with int()/float()
inside a guard. Stdlib only, so it also runs as `python -I tools/fetch_static.py`.

Writes (see docs/design.md 3.3):
    static/items.json            {"<id>": {"name", "sell", "buy", "stack", "q"}}
    static/recipes.json          {"<spellID>": {"skill_line", "skill", "reagents", "out", "out_count"}}
    static/houses.json           {"Alliance"|"Horde"|"Neutral": {"cut", "deposit"}}
    static/vendor_caps.json      {"<id>": {"name", "cap", "verified"}}
    static/vendor_reagents.json  {"<id>": {"name"}}
    static/meta.json             build, source, counts, unresolved names
If wago.tools is unreachable and no static files exist yet, a small fallback
set is written and meta.json says "fallback": true.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

PRODUCT = "wowf"
DEFAULT_BUILD = "1.60.1.70634"
BUILDS_URL = "https://wago.tools/api/builds"
CSV_URL = "https://wago.tools/db2/{table}/csv?build={build}"
TABLES = ("ItemSparse", "SpellReagents", "SpellEffect", "SkillLineAbility", "AuctionHouse")
MAX_DOWNLOAD = 200 * 1024 * 1024  # bytes per file; ItemSparse is about 5 MB
USER_AGENT = "forever-gold-fetch-static/0.1 (personal project)"

# Skill lines with recipes we price (design.md 3.3).
SKILL_LINES = {
    171: "Alchemy",
    129: "First Aid",
    185: "Cooking",
    164: "Blacksmithing",
    197: "Tailoring",
    165: "Leatherworking",
    202: "Engineering",
}
EFFECT_CREATE_ITEM = 24

# Black Market vendor caps in copper. Community-reported, never verified in game.
VENDOR_CAPS = {
    "Black Lotus": 500000,
    "Arcane Crystal": 500000,
    "Pristine Leather": 50000,
    "Frilled Lichen": 50000,
    "Pyrite": 50000,
}

# Reagents that NPC vendors sell (vanilla-era list; re-check in game).
VENDOR_REAGENTS = [
    "Empty Vial", "Leaded Vial", "Crystal Vial", "Imbued Vial",
    "Coarse Thread", "Fine Thread", "Silken Thread", "Heavy Silken Thread", "Rune Thread",
    "Mild Spices", "Hot Spices", "Soothing Spices", "Refreshing Spring Water",
    "Bleach", "Salt", "Weak Flux", "Strong Flux", "Copper Rod",
]

ALLIANCE_FACTIONS = {1, 3, 4, 115}
HORDE_FACTIONS = {2, 5, 6, 116}
NEUTRAL_FACTIONS = {369}

VERSION_RE = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,7}$")

# Used only when wago.tools is unreachable and static/ is empty. Vanilla-era
# vendor values (copper) for the items the tests and fixtures use.
FALLBACK_ITEMS = {
    118: ("Minor Healing Potion", 5, 20, 5, 1),
    159: ("Refreshing Spring Water", 1, 5, 20, 1),
    765: ("Silverleaf", 10, 40, 20, 1),
    785: ("Mageroyal", 20, 80, 20, 1),
    858: ("Lesser Healing Potion", 25, 100, 5, 1),
    2318: ("Light Leather", 15, 60, 20, 1),
    2319: ("Medium Leather", 50, 200, 20, 1),
    2320: ("Coarse Thread", 2, 10, 20, 1),
    2447: ("Peacebloom", 10, 40, 20, 1),
    2449: ("Earthroot", 20, 80, 20, 1),
    2450: ("Briarthorn", 25, 100, 20, 1),
    2453: ("Bruiseweed", 25, 100, 20, 1),
    2454: ("Elixir of Minor Strength", 20, 80, 20, 1),
    2589: ("Linen Cloth", 13, 55, 20, 1),
    2592: ("Wool Cloth", 33, 135, 20, 1),
    2678: ("Mild Spices", 2, 2, 20, 1),
    2770: ("Copper Ore", 5, 20, 20, 1),
    2835: ("Rough Stone", 2, 8, 20, 1),
    2840: ("Copper Bar", 10, 40, 20, 1),
    3371: ("Empty Vial", 1, 4, 20, 1),
    13468: ("Black Lotus", 1000, 4000, 20, 2),
}
FALLBACK_RECIPES = {   # skill = the yellow rank (TrivialSkillLineRankLow) in build 70634
    1244431: (129, 55, [[3371, 1], [2678, 1], [2447, 1]], 118, 1),
    1244432: (129, 85, [[3371, 1], [2678, 2], [2450, 1]], 858, 1),
    2329: (171, 55, [[2449, 1], [765, 1], [3371, 1]], 2454, 1),
}


class FetchError(Exception):
    """A download or parse problem, with a human-readable cause."""


def log(msg: str) -> None:
    print(msg, flush=True)


def http_get(url: str, dest: Path, timeout: float = 120.0) -> int:
    """Download url to dest with a size cap. Returns bytes written."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/csv,application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed https host)
            status = getattr(resp, "status", 200)
            if status != 200:
                raise FetchError(f"{url}: HTTP {status}")
            total = 0
            with dest.open("wb") as out:
                while True:
                    chunk = resp.read(1 << 16)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_DOWNLOAD:
                        raise FetchError(f"{url}: bigger than {MAX_DOWNLOAD} bytes, refusing")
                    out.write(chunk)
            return total
    except urllib.error.HTTPError as exc:
        raise FetchError(f"{url}: HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise FetchError(f"{url}: {exc}") from exc


def resolve_build(build: str, workdir: Path) -> str:
    if build != "latest":
        if not VERSION_RE.match(build):
            raise FetchError(f"--build {build!r} is not a version like 1.60.1.70634 or 'latest'")
        return build
    dest = workdir / "builds.json"
    http_get(BUILDS_URL, dest, timeout=60)
    try:
        data = json.loads(dest.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise FetchError(f"{BUILDS_URL}: not JSON ({exc})") from exc
    return pick_latest(data)


def pick_latest(data) -> str:
    """Newest wowf version from the parsed /api/builds JSON (untrusted)."""
    builds = data.get(PRODUCT) if isinstance(data, dict) else None
    if not isinstance(builds, list) or not builds:
        raise FetchError(f"{BUILDS_URL}: no builds listed for product {PRODUCT!r}")
    best = None
    for b in builds:
        if not isinstance(b, dict):
            continue
        ver = b.get("version")
        if not isinstance(ver, str) or not VERSION_RE.match(ver):
            continue
        key = (str(b.get("created_at", "")), tuple(int(p) for p in ver.split(".")))
        if best is None or key > best[0]:
            best = (key, ver)
    if best is None:
        raise FetchError(f"{BUILDS_URL}: no usable version strings for {PRODUCT!r}")
    return best[1]


def download_tables(build: str, workdir: Path) -> dict:
    paths = {}
    for table in TABLES:
        url = CSV_URL.format(table=table, build=build)
        dest = workdir / f"{table}.csv"
        n = http_get(url, dest)
        log(f"  downloaded {table}: {n:,} bytes")
        paths[table] = dest
    return paths


def read_csv(path: Path, table: str, required: list) -> list:
    try:
        with path.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            header = reader.fieldnames or []
            missing = [c for c in required if c not in header]
            if missing:
                raise FetchError(f"{table}: missing columns {missing}; columns are {header}")
            return list(reader)
    except UnicodeDecodeError as exc:
        raise FetchError(f"{table}: not UTF-8 CSV ({exc})") from exc
    except csv.Error as exc:
        raise FetchError(f"{table}: bad CSV ({exc})") from exc


def to_int(value, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        try:
            return int(float(str(value).strip()))
        except (TypeError, ValueError):
            return default


def to_float(value, default: float = 0.0) -> float:
    try:
        x = float(str(value).strip())
    except (TypeError, ValueError):
        return default
    if x != x or x in (float("inf"), float("-inf")):
        return default
    return x


def clean_name(value) -> str:
    s = str(value or "")
    s = "".join(ch for ch in s if ch >= " " and ch != "\x7f")
    return s.strip()[:200]


def build_items(rows: list) -> dict:
    items = {}
    for r in rows:
        iid = to_int(r.get("ID"))
        name = clean_name(r.get("Display_lang"))
        if iid <= 0 or not name:
            continue
        buy_total = max(0, to_int(r.get("BuyPrice")))
        per = max(1, to_int(r.get("VendorStackCount"), 1))
        buy = buy_total / per
        buy = int(buy) if float(buy).is_integer() else round(buy, 4)
        items[iid] = {
            "name": name,
            "sell": max(0, to_int(r.get("SellPrice"))),
            "buy": buy,
            "stack": max(1, to_int(r.get("Stackable"), 1)),
            "q": max(0, to_int(r.get("OverallQualityID"))),
        }
    return items


def recipe_skill(row: dict) -> int:
    """Profession rank at which a recipe is surely craftable: max(MinSkillLineRank, TrivialSkillLineRankLow).

    Build 70634 has MinSkillLineRank 1 on 1874 of 1890 recipes, so it gates nothing (Major Healing Potion
    and Flask of the Titans "needed" skill 1). TrivialSkillLineRankLow is the rank where the recipe turns
    yellow (Linen Bandage 30, Major Healing Potion 290, Flask of the Titans 315); a recipe can only be
    learned at or below it, so planning at that rank never picks a recipe Travis cannot make. It can miss
    a recipe in its orange band, which costs a craft, not gold. CSVs without the column fall back to
    MinSkillLineRank."""
    return max(0, to_int(row.get("MinSkillLineRank")), to_int(row.get("TrivialSkillLineRankLow")))


def build_recipes(sla_rows: list, reagent_rows: list, effect_rows: list, items: dict, base_col: str):
    """Join SkillLineAbility + SpellReagents + SpellEffect(24) into recipes."""
    lines = {}
    for r in sla_rows:
        sl = to_int(r.get("SkillLine"))
        if sl not in SKILL_LINES:
            continue
        spell = to_int(r.get("Spell"))
        if spell <= 0:
            continue
        row_id = to_int(r.get("ID"))
        cur = lines.get(spell)
        if cur is None or row_id < cur[0]:
            lines[spell] = (row_id, sl, recipe_skill(r))
    reagents = {}
    for r in reagent_rows:
        spell = to_int(r.get("SpellID"))
        if spell not in lines:
            continue
        merged = {}
        order = []
        for i in range(8):
            it = to_int(r.get(f"Reagent_{i}"))
            cnt = to_int(r.get(f"ReagentCount_{i}"))
            if it > 0 and cnt > 0:
                if it not in merged:
                    order.append(it)
                    merged[it] = 0
                merged[it] += cnt
        if order:
            reagents[spell] = [[it, merged[it]] for it in order]
    creates = {}
    for r in effect_rows:
        spell = to_int(r.get("SpellID"))
        if spell not in lines or to_int(r.get("Effect")) != EFFECT_CREATE_ITEM:
            continue
        item = to_int(r.get("EffectItemType"))
        if item <= 0:
            continue
        idx = to_int(r.get("EffectIndex"))
        count = max(1, int(round(to_float(r.get(base_col), 1.0))))
        cur = creates.get(spell)
        if cur is None or idx < cur[0]:
            creates[spell] = (idx, item, count)
    recipes = {}
    stats = Counter()
    for spell, (_, sl, skill) in sorted(lines.items()):
        regs = reagents.get(spell)
        made = creates.get(spell)
        if not regs or not made:
            stats["orphan"] += 1
            continue
        if made[1] not in items or any(it not in items for it, _ in regs):
            stats["unresolved_item"] += 1
            continue
        recipes[spell] = {
            "skill_line": sl,
            "skill": skill,
            "reagents": regs,
            "out": made[1],
            "out_count": made[2],
        }
        stats["kept"] += 1
    return recipes, stats


def house_of(row: dict) -> str | None:
    name = clean_name(row.get("Name_lang")).lower()
    fac = to_int(row.get("FactionID"))
    cut = to_int(row.get("ConsignmentRate"))
    if fac in NEUTRAL_FACTIONS or "blackwater" in name or "booty bay" in name or "gadgetzan" in name:
        return "Neutral"
    if fac in HORDE_FACTIONS or any(w in name for w in ("horde", "undercity", "thunder bluff", "orgrimmar")):
        return "Horde"
    if fac in ALLIANCE_FACTIONS or any(w in name for w in ("alliance", "stormwind", "darnassus", "ironforge")):
        return "Alliance"
    if cut >= 10:
        return "Neutral"
    return None


def build_houses(rows: list):
    rates = {"Alliance": Counter(), "Horde": Counter(), "Neutral": Counter()}
    skipped = []
    for r in rows:
        h = house_of(r)
        if h is None:
            skipped.append(clean_name(r.get("Name_lang")) or str(r.get("ID")))
            continue
        rates[h][(to_int(r.get("ConsignmentRate")), to_int(r.get("DepositRate")))] += 1
    houses = {}
    notes = []
    for h, counter in rates.items():
        if not counter:
            raise FetchError(f"AuctionHouse: no rows map to the {h} house")
        (cut, dep), _ = counter.most_common(1)[0]
        if len(counter) > 1:
            notes.append(f"{h}: several rate pairs {dict(counter)}, using the most common")
        houses[h] = {"cut": round(cut / 100.0, 4), "deposit": round(dep / 100.0, 4)}
    return houses, skipped, notes


def resolve_names(names, items: dict):
    by_name = {}
    for iid, it in items.items():
        by_name.setdefault(it["name"].lower(), []).append(iid)
    found = {}
    unresolved = []
    ambiguous = []
    for n in names:
        ids = sorted(by_name.get(n.lower(), []))
        if not ids:
            unresolved.append(n)
            continue
        if len(ids) > 1:
            ambiguous.append(f"{n}: {ids}")
            stackable = [i for i in ids if items[i]["stack"] > 1]
            ids = stackable or ids
        found[n] = ids[0]
    return found, unresolved, ambiguous


def write_json(path: Path, data, one_entry_per_line: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if one_entry_per_line and isinstance(data, dict):
        keys = sorted(data, key=lambda k: (0, int(k)) if str(k).isdigit() else (1, str(k)))
        parts = []
        for k in keys:
            parts.append(json.dumps(str(k)) + ": " + json.dumps(data[k], ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        text = "{\n" + ",\n".join(parts) + "\n}\n" if parts else "{}\n"
    else:
        text = json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def generate(csv_paths: dict, out_dir: Path, build: str, source: str) -> dict:
    item_rows = read_csv(csv_paths["ItemSparse"], "ItemSparse",
                         ["ID", "Display_lang", "SellPrice", "BuyPrice", "VendorStackCount", "Stackable", "OverallQualityID"])
    items = build_items(item_rows)
    del item_rows
    sla_rows = read_csv(csv_paths["SkillLineAbility"], "SkillLineAbility", ["ID", "SkillLine", "Spell", "MinSkillLineRank"])
    skill_col = ("max(MinSkillLineRank, TrivialSkillLineRankLow)"
                 if sla_rows and "TrivialSkillLineRankLow" in sla_rows[0] else "MinSkillLineRank")
    reagent_cols = ["SpellID"] + [f"Reagent_{i}" for i in range(8)] + [f"ReagentCount_{i}" for i in range(8)]
    reagent_rows = read_csv(csv_paths["SpellReagents"], "SpellReagents", reagent_cols)
    with csv_paths["SpellEffect"].open(encoding="utf-8", newline="") as f:
        header = next(csv.reader(f), [])
    if "EffectBasePoints" in header:
        base_col = "EffectBasePoints"
    elif "EffectBasePointsF" in header:
        base_col = "EffectBasePointsF"
    else:
        raise FetchError(f"SpellEffect: no EffectBasePoints or EffectBasePointsF column; columns are {header}")
    effect_rows = read_csv(csv_paths["SpellEffect"], "SpellEffect", ["SpellID", "Effect", "EffectItemType", "EffectIndex", base_col])
    effect_rows = [r for r in effect_rows if to_int(r.get("Effect")) == EFFECT_CREATE_ITEM]
    recipes, rstats = build_recipes(sla_rows, reagent_rows, effect_rows, items, base_col)
    ah_rows = read_csv(csv_paths["AuctionHouse"], "AuctionHouse", ["ID", "Name_lang", "FactionID", "DepositRate", "ConsignmentRate"])
    houses, ah_skipped, ah_notes = build_houses(ah_rows)

    cap_ids, cap_unresolved, cap_ambiguous = resolve_names(list(VENDOR_CAPS), items)
    vendor_caps = {}
    for name, iid in cap_ids.items():
        vendor_caps[iid] = {"name": items[iid]["name"], "cap": VENDOR_CAPS[name], "verified": False}
    reg_ids, reg_unresolved, reg_ambiguous = resolve_names(VENDOR_REAGENTS, items)
    vendor_reagents = {iid: {"name": items[iid]["name"]} for iid in reg_ids.values()}

    write_json(out_dir / "items.json", items, one_entry_per_line=True)
    write_json(out_dir / "recipes.json", recipes, one_entry_per_line=True)
    write_json(out_dir / "houses.json", houses)
    write_json(out_dir / "vendor_caps.json", {str(k): v for k, v in vendor_caps.items()})
    write_json(out_dir / "vendor_reagents.json", {str(k): v for k, v in vendor_reagents.items()})
    meta = {
        "product": PRODUCT,
        "build": build,
        "source": source,
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "fallback": False,
        "columns": {"create_count": base_col, "skill": skill_col},
        "counts": {
            "items": len(items),
            "recipes": len(recipes),
            "recipes_orphan_dropped": rstats.get("orphan", 0),
            "recipes_unresolved_item_dropped": rstats.get("unresolved_item", 0),
            "vendor_caps": len(vendor_caps),
            "vendor_reagents": len(vendor_reagents),
        },
        "unresolved": sorted(cap_unresolved + reg_unresolved),
        "ambiguous": sorted(cap_ambiguous + reg_ambiguous),
        "notes": ah_notes + ([f"AuctionHouse rows not mapped to a house: {ah_skipped}"] if ah_skipped else []),
    }
    write_json(out_dir / "meta.json", meta)
    return meta


def write_fallback(out_dir: Path, reason: str) -> dict:
    items = {iid: {"name": n, "sell": s, "buy": b, "stack": st, "q": q} for iid, (n, s, b, st, q) in FALLBACK_ITEMS.items()}
    recipes = {sid: {"skill_line": sl, "skill": sk, "reagents": regs, "out": out, "out_count": oc}
               for sid, (sl, sk, regs, out, oc) in FALLBACK_RECIPES.items()}
    houses = {"Alliance": {"cut": 0.05, "deposit": 0.05}, "Horde": {"cut": 0.05, "deposit": 0.05},
              "Neutral": {"cut": 0.15, "deposit": 0.25}}
    vendor_caps = {"13468": {"name": "Black Lotus", "cap": 500000, "verified": False}}
    vendor_reagents = {"3371": {"name": "Empty Vial"}, "2678": {"name": "Mild Spices"}, "2320": {"name": "Coarse Thread"},
                       "159": {"name": "Refreshing Spring Water"}}
    write_json(out_dir / "items.json", items, one_entry_per_line=True)
    write_json(out_dir / "recipes.json", recipes, one_entry_per_line=True)
    write_json(out_dir / "houses.json", houses)
    write_json(out_dir / "vendor_caps.json", vendor_caps)
    write_json(out_dir / "vendor_reagents.json", vendor_reagents)
    meta = {
        "product": PRODUCT,
        "build": None,
        "source": "built-in fallback (wago.tools unreachable)",
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "fallback": True,
        "fallback_reason": reason,
        "counts": {"items": len(items), "recipes": len(recipes)},
        "unresolved": [],
        "ambiguous": [],
        "notes": ["FALLBACK DATA: run python tools/fetch_static.py when wago.tools is reachable"],
    }
    write_json(out_dir / "meta.json", meta)
    return meta


def main(argv=None) -> int:
    repo = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description="Fetch Forever static data from wago.tools into static/*.json")
    ap.add_argument("--build", default=DEFAULT_BUILD, help=f"wowf build, default {DEFAULT_BUILD}; 'latest' asks {BUILDS_URL}")
    ap.add_argument("--out", default=str(repo / "static"), help="output directory (default: <repo>/static)")
    ap.add_argument("--from-dir", help="use CSVs already downloaded into this directory (<Table>.csv)")
    ap.add_argument("--keep", action="store_true", help="keep the temp download directory")
    ap.add_argument("--no-fallback", action="store_true", help="never write the built-in fallback set")
    args = ap.parse_args(argv)
    out_dir = Path(args.out)

    if args.from_dir:
        src = Path(args.from_dir)
        csv_paths = {t: src / f"{t}.csv" for t in TABLES}
        missing = [str(p) for p in csv_paths.values() if not p.is_file()]
        if missing:
            print(f"error: missing CSVs: {missing}", file=sys.stderr)
            return 2
        build = args.build if args.build != "latest" else "unknown"
        meta = generate(csv_paths, out_dir, build, f"local CSVs in {src}")
    else:
        workdir = Path(tempfile.mkdtemp(prefix="fgold-static-"))
        try:
            try:
                build = resolve_build(args.build, workdir)
                log(f"build {build} (product {PRODUCT}); downloading into {workdir}")
                csv_paths = download_tables(build, workdir)
            except FetchError as exc:
                print(f"error: could not download from wago.tools: {exc}", file=sys.stderr)
                existing = (out_dir / "items.json").is_file()
                if args.no_fallback or existing:
                    if existing:
                        print("static/ already has data; leaving it unchanged.", file=sys.stderr)
                    return 3
                meta = write_fallback(out_dir, str(exc))
                print(f"wrote FALLBACK static data to {out_dir} (meta.json fallback=true). Re-run when online.", file=sys.stderr)
                return 4
            meta = generate(csv_paths, out_dir, build, CSV_URL.format(table="<table>", build=build))
        except FetchError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 5
        finally:
            if args.keep:
                log(f"kept downloads in {workdir}")
            else:
                shutil.rmtree(workdir, ignore_errors=True)

    c = meta["counts"]
    log(f"wrote {out_dir}: {c['items']:,} items, {c['recipes']:,} recipes "
        f"({c.get('recipes_orphan_dropped', 0)} orphans and {c.get('recipes_unresolved_item_dropped', 0)} "
        f"with unknown items dropped), {c.get('vendor_caps', 0)} vendor caps, {c.get('vendor_reagents', 0)} vendor reagents")
    if meta["unresolved"]:
        log(f"names not found in ItemSparse: {', '.join(meta['unresolved'])}")
    if meta["ambiguous"]:
        log(f"ambiguous names (picked the stackable/lowest id): {'; '.join(meta['ambiguous'])}")
    for n in meta.get("notes", []):
        log(f"note: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
