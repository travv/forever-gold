"""Ingest ForeverGold SavedVariables into the data store (design.md 3, 3.2).

For every root and account it reads ForeverGold.lua.bak and ForeverGold.lua,
waits until the file's mtime has been stable for `stable_secs`, archives each
new version (by sha256) as data/raw/<root>/<account>/<mtime>-<sha8>.lua.gz,
then extracts scans, invoices, inventory, sessions, probes and scanLog.
Running it again on the same files changes nothing. It never writes to a WoW
folder.
"""

from __future__ import annotations

import gzip
import hashlib
import os
import re
import time
from dataclasses import dataclass, field

from . import luasv, paths, util
from .store import HOUSES, Store, market_key, region_code

_ENTRY_RE = re.compile(r"([0-9]+):([0-9]+):([0-9]+x[0-9]+(?:,[0-9]+x[0-9]+)*)")
SCANLOG_KEEP = 5000

SESSION_FIELDS = {
    "id": "str", "char": "str", "realm": "str", "region": "int", "faction": "str", "version": "str",
    "build": "str", "iface": "int", "project": "int", "copperOK": "bool", "planId": "str",
    "loginAt": "int", "priorSessions": "int",
}
INVOICE_TYPES = ("seller", "seller_temp_invoice", "buyer")


def parse_scan_string(s):
    """'2447:37:22x40,23x200;765:3:150x20;' -> ({id: {"n", "ladder"}}, bad_entries)."""
    items = {}
    bad = 0
    if not isinstance(s, str) or not s:
        return items, bad
    for chunk in s.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        m = _ENTRY_RE.fullmatch(chunk)
        if not m:
            bad += 1
            continue
        iid = int(m.group(1))
        auctions = int(m.group(2))
        if iid <= 0:
            bad += 1
            continue
        ladder = {}
        for pt in m.group(3).split(","):
            u, q = pt.split("x")
            u, q = int(u), int(q)
            if q > 0:
                ladder[u] = ladder.get(u, 0) + q
        if not ladder:
            bad += 1
            continue
        cur = items.get(iid)
        if cur is not None:
            for u, q in cur["ladder"]:
                ladder[u] = ladder.get(u, 0) + q
            auctions += cur["n"]
        items[iid] = {"n": auctions, "ladder": [[u, ladder[u]] for u in sorted(ladder)]}
    return items, bad


def format_scan_string(items: dict) -> str:
    """Inverse of parse_scan_string (sorted ids and units). Used by tests."""
    out = []
    for iid in sorted(items):
        e = items[iid]
        pts = ",".join(f"{u}x{q}" for u, q in sorted(e["ladder"]))
        out.append(f"{iid}:{e['n']}:{pts};")
    return "".join(out)


def wait_stable(path, stable_secs: float, max_wait=None, _sleep=time.sleep):
    """(stat, stable). Stable = mtime at least stable_secs old, or unchanged
    (mtime and size) for stable_secs while we watched (handles clock skew on
    network shares). Gives up after max_wait seconds."""
    st = os.stat(path)
    if not stable_secs or stable_secs <= 0:
        return st, True
    if max_wait is None:
        max_wait = max(10.0, stable_secs * 5)
    t0 = time.monotonic()
    sig = (st.st_mtime_ns, st.st_size)
    since = t0
    while True:
        if time.time() - st.st_mtime >= stable_secs:
            return st, True
        now = time.monotonic()
        if now - since >= stable_secs:
            return st, True
        if now - t0 >= max_wait:
            return st, False
        _sleep(min(0.25, stable_secs))
        st = os.stat(path)
        s2 = (st.st_mtime_ns, st.st_size)
        if s2 != sig:
            sig = s2
            since = time.monotonic()


def _coerce(v, kind):
    if kind == "int":
        return util.as_int(v)
    if kind == "str":
        return util.as_str(v, limit=200)
    if kind == "bool":
        return util.as_bool(v)
    return None


INVOICE_CONTENT = ("char", "type", "item", "count", "bid", "deposit", "cut")
EXPIRY_TOLERANCE = 120   # seconds: the same mail seen again computes its expiry to within a second or two


def invoice_key(inv: dict, copy: int = 1) -> str:
    """char|type|item|count|bid|deposit|cut|copy.

    Two invoice records are the same mail when their keys match and their expiry times
    (invoice_expiry) are within EXPIRY_TOLERANCE. copy numbers the identical records of one
    inbox walk (same char and seenAt): the addon records k identical mails it sees at once as
    k records, and they are k sales (two buyers at one price), not one mail seen k times."""
    return "|".join(str(inv.get(k)) for k in INVOICE_CONTENT) + f"|{copy}"


def invoice_expiry(inv: dict) -> float:
    """When the mail expires (seenAt + daysLeft days): the same each time the same mail is seen."""
    seen = util.as_num(inv.get("seenAt"), 0) or 0
    days = util.as_num(inv.get("daysLeft"), 0) or 0
    return seen + days * 86400.0


@dataclass
class IngestReport:
    roots: list = field(default_factory=list)
    accounts: list = field(default_factory=list)
    files_seen: int = 0
    files_new: int = 0
    files_unstable: int = 0
    scans_new: int = 0
    invoices_new: int = 0
    sessions_new: int = 0
    sessions_updated: int = 0
    inventory_updated: int = 0
    probes_updated: int = 0
    scanlog_new: int = 0
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    def summary(self) -> str:
        if not self.files_seen:
            return "ingest: no ForeverGold.lua found yet"
        parts = [f"{self.files_new} new of {self.files_seen} file{'' if self.files_seen == 1 else 's'}"]
        for n, one, many in ((self.scans_new, "scan", "scans"), (self.invoices_new, "invoice", "invoices"),
                             (self.sessions_new, "session", "sessions"), (self.inventory_updated, "inventory", "inventories"),
                             (self.probes_updated, "deposit probe", "deposit probes")):
            if n:
                parts.append(f"{n} {one if n == 1 else many}")
        return "ingest: " + ", ".join(parts)


class _Accumulator:
    def __init__(self, store: Store, region_default: str):
        self.store = store
        self.region_default = region_default
        self.ledger_index = {}   # invoice_key -> [expiry, ...] of the mails already in the ledger
        for r in store.ledger():
            if isinstance(r, dict):
                key = invoice_key(r, util.as_int(r.get("copy"), 1) or 1)
                self.ledger_index.setdefault(key, []).append(invoice_expiry(r))
        self.new_invoices = []
        self.sessions = {s["id"]: s for s in store.sessions() if isinstance(s, dict) and s.get("id")}
        self.sessions_dirty = False
        self.inventory = store.inventory()
        self.inventory_dirty = False
        self.probes = store.probes()
        self.probes_dirty = False
        self.scanlog_keys = {self._scanlog_key(e) for e in self.probes["scanLog"] if isinstance(e, dict)}
        self.scan_index = store.scan_index()
        self.index_dirty = False

    @staticmethod
    def _scanlog_key(e):
        return "|".join(str(e.get(k)) for k in ("root", "account", "reqAt", "gotAt", "result", "rows", "by"))

    # ---- per file ---------------------------------------------------------
    def extract(self, db: dict, root: str, account: str, mtime: int, rep: IngestReport) -> dict:
        info = {"sessions": 0, "lastScanAt": util.as_int(db.get("lastScanAt"), 0) or 0}
        char_realm = {}
        sessions = util.as_list(db.get("sessions"))
        info["sessions"] = len(sessions)
        for s in sessions:
            if isinstance(s, dict):
                rec = self._session(s, root, account, mtime, rep)
                if rec and rec.get("char") and rec.get("realm"):
                    char_realm[rec["char"]] = rec["realm"]
        for scan in util.as_list(db.get("scans")):
            if isinstance(scan, dict):
                self._scan(scan, root, account, rep)
        walk_copies = {}   # (content, seenAt) -> identical records so far in this file
        for inv in util.as_list(db.get("invoices")):
            if isinstance(inv, dict):
                self._invoice(inv, root, account, rep, walk_copies)
        for char, inv in util.as_dict(db.get("inventory")).items():
            if isinstance(char, str) and isinstance(inv, dict):
                self._inventory(char, inv, root, account, rep)
        probes = util.as_dict(db.get("probes"))
        for iid, pr in util.as_dict(probes.get("deposit")).items():
            if util.item_id(iid) and isinstance(pr, (dict, list)):
                self._deposit(util.item_id(iid), util.as_dict(pr), root, account, rep)
        for e in util.as_list(db.get("scanLog")):
            if isinstance(e, dict):
                self._scanlog(e, root, account, rep)
        return info

    def _session(self, s, root, account, mtime, rep):
        rec = {}
        for k, kind in SESSION_FIELDS.items():
            v = _coerce(s.get(k), kind)
            if v is not None:
                rec[k] = v
        if not rec.get("id"):
            if rec.get("loginAt") and rec.get("char"):
                rec["id"] = f"{rec['loginAt']}-{rec['char'].split('-')[0]}"
            else:
                return None
        rec["root"] = root
        rec["account"] = account
        rec["srcMtime"] = mtime
        cur = self.sessions.get(rec["id"])
        if cur is None:
            self.sessions[rec["id"]] = rec
            rep.sessions_new += 1
            self.sessions_dirty = True
            return rec
        if mtime >= (cur.get("srcMtime") or 0):
            merged = dict(cur)
            merged.update(rec)
        else:
            merged = dict(rec)
            merged.update(cur)
        if merged != cur:
            self.sessions[rec["id"]] = merged
            rep.sessions_updated += 1
            self.sessions_dirty = True
        return merged

    def _scan(self, scan, root, account, rep):
        at = util.as_int(scan.get("at"))
        data = scan.get("data")
        if not at or at <= 0 or not isinstance(data, str):
            rep.warnings.append(f"{root}/{account}: skipped a scan without 'at' or 'data'")
            return
        char = util.as_str(scan.get("char"), "", 200)
        realm = util.as_str(scan.get("realm")) or (char.split("-", 1)[1] if "-" in char else "unknown")
        house = scan.get("house") if scan.get("house") in HOUSES else "Neutral"
        region = region_code(scan.get("region"), self.region_default)
        market = market_key(region, realm, house)
        path = self.store.scan_path(market, at)
        by = scan.get("by") if scan.get("by") in ("fg", "other") else "other"
        rows = util.as_int(scan.get("rows"), 0) or 0
        if not path.exists():
            items, bad = parse_scan_string(data)
            own_items, bad_own = parse_scan_string(scan.get("ownData") or "")
            if bad or bad_own:
                rep.warnings.append(f"{root}/{account}: scan {at} had {bad + bad_own} malformed entries (skipped them)")
            rec = {
                "at": at,
                "market": market,
                "by": by,
                "char": char,
                "house": house,
                "rows": rows,
                "priced": util.as_int(scan.get("priced"), 0) or 0,
                "bidOnly": util.as_int(scan.get("bidOnly"), 0) or 0,
                "own": util.as_int(scan.get("own"), 0) or 0,
                "reqAt": util.as_int(scan.get("reqAt"), 0) or 0,
                "packMs": util.as_int(scan.get("packMs"), 0) or 0,
                "items": {str(k): v for k, v in items.items()},
                "own_items": {str(k): v for k, v in own_items.items()},
                "root": root,
                "account": account,
            }
            util.write_json_gz(path, rec)
            rep.scans_new += 1
        idx = self.scan_index.setdefault(market, {})
        if str(at) not in idx:
            idx[str(at)] = {"char": char, "house": house, "by": by, "rows": rows}
            self.index_dirty = True

    def _invoice(self, inv, root, account, rep, walk_copies):
        typ = inv.get("type")
        if typ not in INVOICE_TYPES:
            return
        rec = {
            "seenAt": util.as_int(inv.get("seenAt"), 0) or 0,
            "char": util.as_str(inv.get("char"), "", 200),
            "daysLeft": util.as_num(inv.get("daysLeft"), 0) or 0,
            "type": typ,
            "item": util.as_str(inv.get("item"), "", 200),
            "player": util.as_str(inv.get("player"), None, 200),
            "bid": util.as_int(inv.get("bid"), 0) or 0,
            "buyout": util.as_int(inv.get("buyout"), 0) or 0,
            "deposit": util.as_int(inv.get("deposit"), 0) or 0,
            "cut": util.as_int(inv.get("cut"), 0) or 0,
            "delay": util.as_int(inv.get("delay"), 0) or 0,
            "etaH": util.as_int(inv.get("etaH"), 0) or 0,
            "etaM": util.as_int(inv.get("etaM"), 0) or 0,
            "count": util.as_int(inv.get("count"), 0) or 0,
            "commodity": bool(inv.get("commodity")) if isinstance(inv.get("commodity"), bool) else None,
        }
        if isinstance(inv.get("house"), str) and inv["house"] in HOUSES:
            rec["house"] = inv["house"]
        rec["root"] = root
        rec["account"] = account
        walk = (invoice_key(rec, 0), rec["seenAt"])
        copy = walk_copies[walk] = walk_copies.get(walk, 0) + 1
        key = invoice_key(rec, copy)
        expiry = invoice_expiry(rec)
        known = self.ledger_index.setdefault(key, [])
        if any(abs(expiry - e) <= EXPIRY_TOLERANCE for e in known):
            return   # this mail is already in the ledger (seen in another session or file version)
        rec["copy"] = copy
        rec["key"] = key
        known.append(expiry)
        self.new_invoices.append(rec)
        rep.invoices_new += 1

    def _inventory(self, char, inv, root, account, rep):
        def counts(v):
            out = {}
            for k, c in util.as_dict(v).items():
                iid, n = util.item_id(k), util.as_int(c)
                if iid and n and n > 0:
                    out[str(iid)] = out.get(str(iid), 0) + n
            return out

        at = util.as_int(inv.get("at"), 0) or 0
        bank_at = util.as_int(inv.get("bankAt"), 0) or 0
        cur = self.inventory.get(char) or {}
        changed = False
        if not cur or at > (cur.get("at") or 0):
            cur = dict(cur)
            cur.update({"at": at, "money": util.as_int(inv.get("money"), 0) or 0, "bags": counts(inv.get("bags")),
                        "root": root, "account": account})
            cur.setdefault("bank", {})
            cur.setdefault("bankAt", 0)
            changed = True
        if bank_at > (cur.get("bankAt") or 0):
            cur = dict(cur)
            cur["bank"] = counts(inv.get("bank"))
            cur["bankAt"] = bank_at
            changed = True
        if changed:
            self.inventory[char] = cur
            self.inventory_dirty = True
            rep.inventory_updated += 1

    def _deposit(self, iid, pr, root, account, rep):
        at = util.as_int(pr.get("at"), 0) or 0
        rec = {"qty": util.as_int(pr.get("qty"), 1) or 1, "at": at, "root": root, "account": account}
        for d in (1, 2, 3):
            v = util.as_int(pr.get(d, pr.get(str(d))))
            if v is not None and v >= 0:
                rec[str(d)] = v
        if not any(str(d) in rec for d in (1, 2, 3)):
            return
        if isinstance(pr.get("house"), str) and pr["house"] in HOUSES:
            rec["house"] = pr["house"]
        cur = self.probes["deposit"].get(str(iid))
        if cur is None or at > (cur.get("at") or 0):
            self.probes["deposit"][str(iid)] = rec
            self.probes_dirty = True
            rep.probes_updated += 1

    def _scanlog(self, e, root, account, rep):
        rec = {
            "reqAt": util.as_int(e.get("reqAt"), 0) or 0,
            "gotAt": util.as_int(e.get("gotAt"), 0) or 0,
            "result": e.get("result") if e.get("result") in ("ok", "dropped", "closed", "noreply", "other") else "other",
            "rows": util.as_int(e.get("rows"), 0) or 0,
            "by": e.get("by") if e.get("by") in ("fg", "other") else "other",
            "root": root,
            "account": account,
        }
        key = self._scanlog_key(rec)
        if key in self.scanlog_keys:
            return
        self.scanlog_keys.add(key)
        self.probes["scanLog"].append(rec)
        self.probes_dirty = True
        rep.scanlog_new += 1

    # ---- write ------------------------------------------------------------
    def flush(self):
        if self.new_invoices:
            self.new_invoices.sort(key=lambda r: (r["seenAt"], r["key"]))
            self.store.dir.mkdir(parents=True, exist_ok=True)
            with open(self.store.ledger_path, "a", encoding="utf-8", newline="\n") as f:
                f.write(util.jsonl_text(self.new_invoices))
            self.new_invoices = []
        if self.sessions_dirty:
            recs = sorted(self.sessions.values(), key=lambda s: (s.get("loginAt") or 0, s["id"]))
            util.write_text_atomic(self.store.sessions_path, util.jsonl_text(recs))
            self.sessions_dirty = False
        if self.inventory_dirty:
            util.write_json(self.store.inventory_path, self.inventory)
            self.inventory_dirty = False
        if self.probes_dirty:
            log = sorted(self.probes["scanLog"], key=lambda e: (e.get("reqAt") or e.get("gotAt") or 0, self._scanlog_key(e)))
            self.probes["scanLog"] = log[-SCANLOG_KEEP:]
            util.write_json(self.store.probes_path, self.probes)
            self.probes_dirty = False
        if self.index_dirty:
            util.write_json(self.store.scan_index_path, self.scan_index)
            self.index_dirty = False


def ingest(roots, data_dir, stable_secs: float = 2.0, max_wait=None, region_default: str = "us", now=None) -> IngestReport:
    store = Store(data_dir)
    rep = IngestReport()
    state = store.state()
    seen = state["seen"]
    acc = _Accumulator(store, region_default)
    for root in roots:
        rep.roots.append(root.name)
        if not root.exists:
            rep.warnings.append(f"root {root.name}: folder not found: {root.raw}. Fix: run `python -m fgold doctor` "
                                f"and correct the roots in config.local.json")
            continue
        for account, svdir in root.accounts():
            rep.accounts.append(f"{root.name}/{account}")
            for fname in (paths.SV_BAK, paths.SV_FILE):
                path = svdir / fname
                if not path.is_file():
                    continue
                rep.files_seen += 1
                try:
                    st, stable = wait_stable(path, stable_secs, max_wait)
                    if not stable:
                        rep.files_unstable += 1
                        rep.warnings.append(f"{root.name}/{account}/{fname}: still being written, skipped. Fix: wait for "
                                            f"the game to finish saving (/reload or log out), then run ingest again")
                        continue
                    data = path.read_bytes()
                except OSError as exc:
                    rep.errors.append(f"{root.name}/{account}/{fname}: cannot read ({exc})")
                    continue
                sha = hashlib.sha256(data).hexdigest()
                if sha in seen:
                    continue
                mtime = int(st.st_mtime)
                archive = store.raw_dir / paths.safe_part(root.name) / paths.safe_part(account) / f"{mtime}-{sha[:8]}.lua.gz"
                if not archive.exists():
                    archive.parent.mkdir(parents=True, exist_ok=True)
                    tmp = archive.with_name(archive.name + ".tmp")
                    with open(tmp, "wb") as f:
                        f.write(gzip.compress(data, mtime=0))
                    os.replace(tmp, archive)
                entry = {"root": root.name, "account": account, "file": fname, "mtime": mtime, "size": len(data),
                         "archive": archive.relative_to(store.dir).as_posix(), "ingestedAt": int(now or time.time())}
                rep.files_new += 1
                try:
                    parsed = luasv.loads(data)
                except luasv.LuaSVError as exc:
                    entry["error"] = str(exc)
                    seen[sha] = entry
                    rep.errors.append(f"{root.name}/{account}/{fname}: not readable as SavedVariables ({exc}); archived "
                                      f"as {entry['archive']}")
                    continue
                db = parsed.get("ForeverGoldDB")
                if not isinstance(db, dict):
                    entry["error"] = "no ForeverGoldDB table"
                    seen[sha] = entry
                    rep.errors.append(f"{root.name}/{account}/{fname}: no ForeverGoldDB table in the file")
                    continue
                info = acc.extract(db, root.name, account, mtime, rep)
                seen[sha] = entry
                akey = f"{root.name}/{account}"
                cur = state["accounts"].get(akey) or {}
                if fname == paths.SV_FILE and mtime >= (cur.get("mtime") or 0):
                    state["accounts"][akey] = {"mtime": mtime, "sha": sha, "sessions": info["sessions"],
                                               "lastScanAt": info["lastScanAt"]}
    acc.flush()
    state["last_ingest"] = int(now or time.time())
    store.save_state(state)
    return rep
