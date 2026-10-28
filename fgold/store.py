"""The data store under data/ (design.md 3.2, ingest.py layout).

    data/raw/<root>/<account>/<mtime>-<sha8>.lua.gz   every SavedVariables version seen
    data/scans/<region>/<realm>/<house>/<at>.json.gz  one file per scan
    data/scans/index.json                             {market: {"<at>": {char, house, by, rows}}}, so
                                                      reports never load every scan to read who scanned
    data/ledger.jsonl                                 invoices, deduped
    data/sessions.jsonl                               sessions, one per id
    data/inventory.json                               latest per character
    data/probes.json                                  deposit probes + scanLog
    data/state.json                                   seen shas, last ingest, applied plan

The market key is "<region>|<realm>|<house>". '|' is not allowed in Windows
file names, so the three parts become nested folders (each part %XX-encoded
when it holds characters Windows rejects).
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote

from . import paths, util

REGIONS = {1: "us", 2: "kr", 3: "eu", 4: "tw", 5: "cn"}
HOUSES = ("Alliance", "Horde", "Neutral")


def region_code(region, default: str = "us") -> str:
    i = util.as_int(region)
    if i is None:
        s = util.as_str(region)
        return s.lower() if s else default
    return REGIONS.get(i, str(i))


def market_key(region: str, realm: str, house: str) -> str:
    def clean(s):
        return str(s).replace("|", "/").strip() or "unknown"
    return f"{clean(region)}|{clean(realm)}|{clean(house)}"


def split_market(key: str):
    parts = str(key).split("|")
    if len(parts) != 3:
        raise ValueError(f"market key {key!r} is not 'region|realm|house'")
    return parts[0], parts[1], parts[2]


def house_of_market(key: str) -> str:
    try:
        return split_market(key)[2]
    except ValueError:
        return "Neutral"


class Store:
    def __init__(self, data_dir):
        self.dir = Path(data_dir)
        self._scan_cache = {}

    # ---- paths --------------------------------------------------------------
    @property
    def raw_dir(self) -> Path:
        return self.dir / "raw"

    @property
    def scans_dir(self) -> Path:
        return self.dir / "scans"

    @property
    def scan_index_path(self) -> Path:
        return self.scans_dir / "index.json"

    @property
    def ledger_path(self) -> Path:
        return self.dir / "ledger.jsonl"

    @property
    def sessions_path(self) -> Path:
        return self.dir / "sessions.jsonl"

    @property
    def inventory_path(self) -> Path:
        return self.dir / "inventory.json"

    @property
    def probes_path(self) -> Path:
        return self.dir / "probes.json"

    @property
    def state_path(self) -> Path:
        return self.dir / "state.json"

    def market_dir(self, market: str) -> Path:
        region, realm, house = split_market(market)
        return self.scans_dir / paths.safe_part(region) / paths.safe_part(realm) / paths.safe_part(house)

    def scan_path(self, market: str, at: int) -> Path:
        return self.market_dir(market) / f"{int(at)}.json.gz"

    # ---- scans --------------------------------------------------------------
    def markets(self) -> list:
        out = []
        base = self.scans_dir
        if not base.is_dir():
            return out
        for r in sorted(p for p in base.iterdir() if p.is_dir()):
            for m in sorted(p for p in r.iterdir() if p.is_dir()):
                for h in sorted(p for p in m.iterdir() if p.is_dir()):
                    if any(h.glob("*.json.gz")):
                        out.append(market_key(unquote(r.name), unquote(m.name), unquote(h.name)))
        return out

    def scan_paths(self, market: str) -> list:
        d = self.market_dir(market)
        if not d.is_dir():
            return []
        found = []
        for p in d.glob("*.json.gz"):
            stem = p.name[: -len(".json.gz")]
            at = util.as_int(stem)
            if at is not None:
                found.append((at, p))
        found.sort()
        return found

    def load_scans(self, market: str, since=None) -> list:
        out = []
        for at, p in self.scan_paths(market):
            if since is not None and at < since:
                continue
            key = str(p)
            if key not in self._scan_cache:
                try:
                    self._scan_cache[key] = util.read_json_gz(p)
                except (OSError, ValueError, EOFError):
                    continue
            out.append(self._scan_cache[key])
        return out

    def scan_index(self) -> dict:
        """{market: {"<at>": {"char", "house", "by", "rows"}}} as ingest wrote it (older stores may miss scans)."""
        try:
            d = util.read_json(self.scan_index_path, {})
        except (OSError, ValueError):
            d = {}
        return d if isinstance(d, dict) else {}

    def newest_scan(self, market: str):
        found = self.scan_paths(market)
        if not found:
            return None
        scans = self.load_scans(market, since=found[-1][0])
        return scans[-1] if scans else None

    def scan_counts(self, market: str):
        found = self.scan_paths(market)
        return len(found), (found[0][0] if found else None), (found[-1][0] if found else None)

    # ---- other files ----------------------------------------------------------
    def ledger(self) -> list:
        return util.read_jsonl(self.ledger_path)

    def sessions(self) -> list:
        recs = util.read_jsonl(self.sessions_path)
        recs.sort(key=lambda s: (util.as_int(s.get("loginAt"), 0) or 0, str(s.get("id", ""))))
        return recs

    def inventory(self) -> dict:
        d = util.read_json(self.inventory_path, {})
        return d if isinstance(d, dict) else {}

    def probes(self) -> dict:
        d = util.read_json(self.probes_path, {})
        if not isinstance(d, dict):
            d = {}
        d.setdefault("deposit", {})
        d.setdefault("scanLog", [])
        return d

    def state(self) -> dict:
        try:
            d = util.read_json(self.state_path, {})
        except ValueError:
            d = {}
        if not isinstance(d, dict):
            d = {}
        d.setdefault("version", 1)
        d.setdefault("seen", {})
        d.setdefault("accounts", {})
        return d

    def save_state(self, state: dict) -> None:
        util.write_json(self.state_path, state)

    def newest_session(self):
        s = self.sessions()
        return s[-1] if s else None
