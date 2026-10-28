"""The addon's API use, checked against cached WoW: Forever API data.

The cache in tests/fixtures/forever_api/ comes from the 'forever' branches of
Ketho/BlizzardInterfaceResources and Gethe/wow-ui-source. Those files are Lua,
but here they are untrusted DATA: they are tokenized as text and never run.

    python3 -m unittest tests.test_api_contract -v
    python3 tests/test_api_contract.py --refresh      # re-download for a new build

--refresh always re-runs itself under `python -I`, downloads into a fresh temp
directory, validates each file by parsing it, and only then copies it into the
cache. A file that fails to download is recorded in manifest.json; the tests
that need it are skipped, not failed.
"""

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ADDON_DIR = REPO / "addon" / "ForeverGold"
CACHE = REPO / "tests" / "fixtures" / "forever_api"
MANIFEST = CACHE / "manifest.json"
STUB = REPO / "tests" / "wow" / "stub.lua"
EMDASH = "\u2014"

DOCS = "Interface/AddOns/Blizzard_APIDocumentationGenerated/"
SOURCES = {
    "ketho": {
        "repo": "Ketho/BlizzardInterfaceResources",
        "branch": "forever",
        "files": {
            "GlobalAPI.lua": "Resources/GlobalAPI.lua",
            "Events.lua": "Resources/Events.lua",
            "Templates.lua": "Resources/Templates.lua",
            "LuaEnum.lua": "Resources/LuaEnum.lua",
            "FrameXML.lua": "Resources/FrameXML.lua",
            "Frames.lua": "Resources/Frames.lua",
            "WidgetAPI.lua": "Resources/WidgetAPI.lua",
        },
    },
    "gethe": {
        "repo": "Gethe/wow-ui-source",
        "branch": "forever",
        "files": {
            "AuctionHouseDocumentation.lua": DOCS + "AuctionHouseDocumentation.lua",
            "ContainerDocumentation.lua": DOCS + "ContainerDocumentation.lua",
            "UIManagerDocumentation.lua": DOCS + "UIManagerDocumentation.lua",
            "BagIndexConstantsDocumentation.lua": DOCS + "BagIndexConstantsDocumentation.lua",
            "Backdrop.lua": "Interface/AddOns/Blizzard_SharedXML/Backdrop.lua",
            "Constants.lua": "Interface/AddOns/Blizzard_FrameXMLBase/Constants.lua",
        },
    },
}
MAX_BYTES = 8 * 1024 * 1024


def cached_path(source, name):
    # Stored with a .txt suffix: data, not code.
    return CACHE / source / (name + ".txt")


# ---------------------------------------------------------------------------
# A tokenizer and table-literal parser for Lua data files. It never executes
# anything: unknown statements are skipped, function bodies are not parsed.
# ---------------------------------------------------------------------------
_TOKEN = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<comment>--\[(?P<eq>=*)\[.*?\](?P=eq)\]|--[^\n]*)
  | (?P<lstr>\[(?P<leq>=*)\[.*?\](?P=leq)\])
  | (?P<str>"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')
  | (?P<num>0[xX][0-9a-fA-F]+|\d+(?:\.\d*)?(?:[eE][+-]?\d+)?|\.\d+)
  | (?P<name>[A-Za-z_][A-Za-z_0-9]*)
  | (?P<op>\.\.\.|\.\.|==|~=|<=|>=|[-+*/%^#<>=(){}\[\];:,.])
    """,
    re.S | re.X,
)
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"', "'": "'", "a": "\a", "b": "\b",
            "f": "\f", "v": "\v", "\n": "\n"}


class Ref(str):
    """A bare identifier used as a value (e.g. `return {GlobalAPI, LuaAPI}`)."""


def _unescape(body):
    out, i = [], 0
    while i < len(body):
        c = body[i]
        if c != "\\":
            out.append(c)
            i += 1
            continue
        nxt = body[i + 1] if i + 1 < len(body) else ""
        if nxt.isdigit():
            m = re.match(r"\d{1,3}", body[i + 1:])
            out.append(chr(int(m.group(0))))
            i += 1 + len(m.group(0))
        else:
            out.append(_ESCAPES.get(nxt, nxt))
            i += 2
    return "".join(out)


def tokenize(text):
    tokens, pos = [], 0
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m:
            raise ValueError("cannot tokenize at offset %d: %r" % (pos, text[pos:pos + 30]))
        pos = m.end()
        kind = m.lastgroup
        if kind in ("ws", "comment", "eq", "leq"):
            continue
        if m.group("ws") is not None or m.group("comment") is not None:
            continue
        if m.group("lstr") is not None:
            raw = m.group("lstr")
            level = raw.index("[", 1)
            tokens.append(("str", raw[level + 1:-(level + 1)].lstrip("\n")))
        elif m.group("str") is not None:
            tokens.append(("str", _unescape(m.group("str")[1:-1])))
        elif m.group("num") is not None:
            tokens.append(("num", m.group("num")))
        elif m.group("name") is not None:
            tokens.append(("name", m.group("name")))
        else:
            tokens.append(("op", m.group("op")))
    return tokens


class _Parser:
    def __init__(self, tokens):
        self.t = tokens
        self.i = 0

    def peek(self, k=0):
        j = self.i + k
        return self.t[j] if j < len(self.t) else ("eof", None)

    def take(self, kind=None, value=None):
        tok = self.peek()
        if (kind and tok[0] != kind) or (value is not None and tok[1] != value):
            raise ValueError("expected %s %s, got %r" % (kind, value, tok))
        self.i += 1
        return tok

    def value(self):
        kind, v = self.peek()
        if kind == "op" and v == "{":
            return self.table()
        if kind == "op" and v == "-":
            self.take()
            return -self.value()
        if kind == "str":
            self.take()
            return v
        if kind == "num":
            self.take()
            if v.lower().startswith("0x"):
                return int(v, 16)
            return int(v) if re.fullmatch(r"\d+", v) else float(v)
        if kind == "name":
            self.take()
            if v == "true":
                return True
            if v == "false":
                return False
            if v == "nil":
                return None
            return Ref(v)
        raise ValueError("unexpected %r in a value" % (self.peek(),))

    def table(self):
        self.take("op", "{")
        out, n = {}, 0
        while not (self.peek()[0] == "op" and self.peek()[1] == "}"):
            kind, v = self.peek()
            if kind == "op" and v == "[":
                self.take()
                key = self.value()
                self.take("op", "]")
                self.take("op", "=")
                out[key] = self.value()
            elif kind == "name" and self.peek(1) == ("op", "="):
                self.take()
                self.take()
                out[v] = self.value()
            else:
                n += 1
                out[n] = self.value()
            if self.peek()[0] == "op" and self.peek()[1] in (",", ";"):
                self.take()
        self.take("op", "}")
        return out

    def skip_balanced(self, open_, close):
        depth = 0
        while self.peek()[0] != "eof":
            kind, v = self.take()
            if kind == "op" and v == open_:
                depth += 1
            elif kind == "op" and v == close:
                depth -= 1
                if depth == 0:
                    return


def parse_lua_data(text):
    """Top-level `[local] Name = <value>` statements of a Lua data file, as a dict."""
    p = _Parser(tokenize(text))
    out = {}
    while p.peek()[0] != "eof":
        kind, v = p.peek()
        if kind == "name" and v == "local":
            p.take()
            continue
        if kind == "name" and p.peek(1) == ("op", "="):
            p.take()
            p.take()
            out[v] = p.value()
            continue
        if kind == "name" and v == "return":
            p.take()
            if p.peek()[0] in ("name", "str", "num") or p.peek() == ("op", "{"):
                p.value()
            continue
        if kind == "op" and v == "(":
            p.skip_balanced("(", ")")
            continue
        p.take()
    return out


def positional(t):
    return [t[i] for i in range(1, len(t) + 1) if i in t] if isinstance(t, dict) else []


def flatten_strings(v):
    if isinstance(v, str):
        yield v
    elif isinstance(v, dict):
        for x in v.values():
            yield from flatten_strings(x)


# ---------------------------------------------------------------------------
# The cache: what each file yields
# ---------------------------------------------------------------------------
def read_cached(source, name):
    path = cached_path(source, name)
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def need(case, source, name):
    text = read_cached(source, name)
    if text is None:
        case.skipTest("forever_api/%s/%s.txt is not cached (download failed or never run): "
                      "python3 tests/test_api_contract.py --refresh" % (source, name))
    return text


_PARSED = {}


def parsed(case, source, name):
    key = (source, name)
    if key not in _PARSED:
        _PARSED[key] = parse_lua_data(need(case, source, name))
    return _PARSED[key]


def global_api(case):
    d = parsed(case, "ketho", "GlobalAPI.lua")
    return set(positional(d["GlobalAPI"])), set(positional(d["LuaAPI"]))


def framexml_functions(case):
    d = parsed(case, "ketho", "FrameXML.lua")
    return set(positional(d.get("FrameXML", {}))) | set(positional(d.get("LoadOnDemand", {})))


def all_events(case):
    return {e for e in flatten_strings(parsed(case, "ketho", "Events.lua")["Events"]) if re.fullmatch(r"[A-Z0-9_]+", e)}


def templates(case):
    return parsed(case, "ketho", "Templates.lua")["Templates"]


def widget_api(case):
    return parsed(case, "ketho", "WidgetAPI.lua")["WidgetAPI"]


def apidoc(case, name):
    d = parsed(case, "gethe", name)
    tables = [v for v in d.values() if isinstance(v, dict) and ("Functions" in v or "Tables" in v)]
    if not tables:
        raise AssertionError("no documentation table in " + name)
    return tables[0]


def doc_functions(doc):
    return {f["Name"]: f for f in positional(doc.get("Functions", {}))}


def doc_events(doc):
    return {e.get("LiteralName") for e in positional(doc.get("Events", {}))}


def doc_tables(doc):
    return {t["Name"]: t for t in positional(doc.get("Tables", {}))}


# ---------------------------------------------------------------------------
# Reading the addon source (also as text)
# ---------------------------------------------------------------------------
def addon_files():
    # PlanData.lua is generated data (fgold renders and re-parses it), not addon code.
    return sorted(p for p in ADDON_DIR.glob("*.lua") if p.name != "PlanData.lua")


_LONG_OPEN = re.compile(r"\[(=*)\[")
_COMMENT_OPEN = re.compile(r"--\[(=*)\[")


def strip_lua(text, keep_strings=True):
    """Remove comments; optionally blank string contents (keeping the quotes)."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c in "\"'":
            j = i + 1
            while j < n and text[j] != c:
                j += 2 if text[j] == "\\" else 1
            body = text[i + 1:j]
            out.append(c + (body if keep_strings else re.sub(r"[^\n]", " ", body)) + c)
            i = j + 1
        elif text.startswith("--", i):
            m = _COMMENT_OPEN.match(text, i)
            if m:
                end = text.find("]" + m.group(1) + "]", i)
                end = n if end < 0 else end + len(m.group(1)) + 2
                out.append(re.sub(r"[^\n]", " ", text[i:end]))
                i = end
            else:
                end = text.find("\n", i)
                end = n if end < 0 else end
                i = end
        elif c == "[" and _LONG_OPEN.match(text, i):
            m = _LONG_OPEN.match(text, i)
            end = text.find("]" + m.group(1) + "]", i)
            end = n if end < 0 else end + len(m.group(1)) + 2
            out.append(text[i:end] if keep_strings else re.sub(r"[^\n]", " ", text[i:end]))
            i = end
        else:
            out.append(c)
            i += 1
    return "".join(out)


def addon_code(keep_strings=True):
    return {p.name: strip_lua(p.read_text(encoding="utf-8"), keep_strings) for p in addon_files()}


LUA_LIBRARIES = {"string", "table", "math", "bit", "coroutine"}
LUA_KEYWORDS = {"and", "break", "do", "else", "elseif", "end", "false", "for", "function", "if", "in",
                "local", "nil", "not", "or", "repeat", "return", "then", "true", "until", "while"}

# Bare globals the addon is known to use; each must exist on Forever.
KNOWN_GLOBALS = ["GetInboxInvoiceInfo", "GetInboxNumItems", "GetInboxHeaderInfo", "GetServerTime",
                 "GetRealmName", "GetCurrentRegion", "GetBuildInfo", "UnitFactionGroup", "UnitName",
                 "GetMoney", "CreateFrame", "debugprofilestop", "print"]

# Global variables (not functions): where each is defined.
GLOBAL_VARIABLES = {
    "BACKPACK_CONTAINER": "gethe Constants.lua",
    "NUM_BAG_SLOTS": "gethe Constants.lua",
    "UIParent": "ketho Frames.lua",
    "DEFAULT_CHAT_FRAME": "FrameXML ChatFrame (always present)",
    "SlashCmdList": "FrameXML ChatFrame (always present)",
    "UISpecialFrames": "FrameXML UIParent.lua (always present)",
    "WOW_PROJECT_ID": "set by the client; reads 1 on Forever (docs/api-notes.md)",
    "Enum": "ketho LuaEnum.lua",
    "Auctionator": "optional dependency",
    "ForeverGoldDB": "our SavedVariables",
    "ForeverGoldPlan": "PlanData.lua (generated)",
    "ForeverGoldPlanDefault": "PlanDefault.lua",
    "SLASH_FOREVERGOLD1": "our slash command",
}


def local_names(code):
    names = set()
    for m in re.finditer(r"\blocal\s+function\s+([A-Za-z_]\w*)", code):
        names.add(m.group(1))
    for m in re.finditer(r"\blocal\s+([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)", code):
        names.update(x.strip() for x in m.group(1).split(","))
    for m in re.finditer(r"\bfunction\b[\w\s.:]*\(([^)]*)\)", code):
        names.update(x.strip() for x in m.group(1).split(",") if x.strip() and x.strip() != "...")
    for m in re.finditer(r"\bfor\s+([\w\s,]+?)\s+in\b", code):
        names.update(x.strip() for x in m.group(1).split(","))
    for m in re.finditer(r"\bfor\s+([A-Za-z_]\w*)\s*=", code):
        names.add(m.group(1))
    return names


def bare_calls(code):
    """Names called as name(...) that are not fields, methods or definitions."""
    calls = set()
    for m in re.finditer(r"(?<![\w.:])([A-Za-z_]\w*)\s*\(", code):
        name = m.group(1)
        before = code[max(0, m.start() - 9):m.start()]
        if name in LUA_KEYWORDS or re.search(r"function\s*$", before):
            continue
        calls.add(name)
    return calls


def global_reads(code):
    """Bare identifiers that are not locals, keywords, fields or table keys."""
    names = set()
    for m in re.finditer(r"(?<![\w.:\"'])([A-Za-z_]\w*)\b(?!\s*=[^=])", code):
        names.add(m.group(1))
    return names


# ---------------------------------------------------------------------------
# --refresh
# ---------------------------------------------------------------------------
def _fetch(url, attempts=3):
    req = urllib.request.Request(url, headers={"User-Agent": "forever-gold-api-check"})
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read(MAX_BYTES + 1)
            break
        except (urllib.error.URLError, OSError):
            if attempt == attempts:
                raise
            time.sleep(2 * attempt)
    if len(data) > MAX_BYTES:
        raise ValueError("larger than %d bytes" % MAX_BYTES)
    return data


def _validate(name, text):
    """Parse a downloaded file as data; raise if it does not look like the expected file."""
    if name in ("Backdrop.lua", "Constants.lua"):
        pattern = r"function BackdropTemplateMixin:SetBackdrop\(" if name == "Backdrop.lua" else r"^BACKPACK_CONTAINER\s*="
        if not re.search(pattern, text, re.M):
            raise ValueError("unexpected content")
        return
    d = parse_lua_data(text)
    if not d or not any(isinstance(v, dict) and len(v) > 0 for v in d.values()):
        raise ValueError("no data tables found")


def refresh(refs):
    tmp = Path(tempfile.mkdtemp(prefix="fg-forever-api-"))
    manifest = {"fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "note": "Untrusted upstream data, parsed as text by tests/test_api_contract.py. "
                        "Refresh: python3 tests/test_api_contract.py --refresh",
                "sources": {}, "files": {}, "failed": []}
    try:
        for source, spec in SOURCES.items():
            ref = refs.get(source) or spec["branch"]
            info = {"repo": spec["repo"], "branch": spec["branch"], "ref": ref}
            try:
                meta = json.loads(_fetch("https://api.github.com/repos/%s/commits/%s" % (spec["repo"], ref)))
                info["commit"] = meta["sha"]
                info["commit_date"] = meta["commit"]["committer"]["date"]
                info["commit_message"] = meta["commit"]["message"].splitlines()[0][:200]
                ref = meta["sha"]
            except (urllib.error.URLError, ValueError, KeyError, OSError) as e:
                info["commit_error"] = str(e)
            manifest["sources"][source] = info
            for name, path in spec["files"].items():
                key = "%s/%s.txt" % (source, name)
                url = "https://raw.githubusercontent.com/%s/%s/%s" % (spec["repo"], ref, path)
                entry = {"url": url, "path": path}
                try:
                    raw = _fetch(url)
                    (tmp / source).mkdir(parents=True, exist_ok=True)
                    (tmp / source / name).write_bytes(raw)
                    text = raw.decode("utf-8").replace("\r\n", "\n")
                    _validate(name, text)
                    dashes = text.count(EMDASH)
                    text = text.replace(EMDASH, "-")
                    entry.update(status="ok", bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
                                 emdash_replaced=dashes)
                    dest = cached_path(source, name)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with open(dest, "w", encoding="utf-8", newline="\n") as f:
                        f.write(text)
                except (urllib.error.URLError, ValueError, UnicodeDecodeError, OSError) as e:
                    entry.update(status="failed", error=str(e), kept_previous=cached_path(source, name).is_file())
                    manifest["failed"].append(key)
                manifest["files"][key] = entry
                print("%-48s %s" % (key, entry["status"] if entry["status"] == "ok" else "FAILED: " + entry["error"]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    with open(MANIFEST, "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
        f.write("\n")
    for source, info in manifest["sources"].items():
        print("%s: %s %s" % (source, info.get("commit", "?")[:12], info.get("commit_message", info.get("commit_error", ""))))
    if manifest["failed"]:
        print("failed (those tests will skip): " + ", ".join(manifest["failed"]))
        return 1
    return 0


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------
class ParserSelfTest(unittest.TestCase):
    def test_parses_the_shapes_the_cache_uses(self):
        d = parse_lua_data('local T = {\n\t["!a-b"] = {type = "Frame", intrinsic = true},\n\tX = { A = 0x1, B = -3, C = 1.5 },\n'
                           '\t"pos", -- comment\n}\nN = 2\nreturn T\nAPIDocumentation:AddDocumentationTable(T);\n')
        self.assertEqual(d["T"]["!a-b"], {"type": "Frame", "intrinsic": True})
        self.assertEqual(d["T"]["X"], {"A": 1, "B": -3, "C": 1.5})
        self.assertEqual(d["T"][1], "pos")
        self.assertEqual(d["N"], 2)

    def test_strip_lua(self):
        code = strip_lua('local a = "x -- y" -- tail\n--[[ block\nPostItem ]] b = 1', keep_strings=False)
        self.assertNotIn("tail", code)
        self.assertNotIn("PostItem", code)
        self.assertIn('"      "', code)


class LanguageContract(unittest.TestCase):
    """Lua 5.1 as the client runs it: no os/io, no goto, no // and no bit operators."""

    def test_lua51_only(self):
        problems = []
        for name, code in addon_code(keep_strings=False).items():
            for lineno, line in enumerate(code.splitlines(), 1):
                for pattern, why in ((r"\bos\.", "os library"), (r"\bio\.", "io library"), (r"\bgoto\b", "goto"),
                                     (r"//", "floor division"), (r"&|<<|>>", "bit operator"),
                                     (r"\|", "bit operator"), (r"~(?!=)", "bit operator"),
                                     (r"::\w+::", "label"),
                                     (r"\b(require|dofile|loadfile)\b", "not in the client")):
                    if re.search(pattern, line):
                        problems.append("%s:%d %s: %s" % (name, lineno, why, line.strip()))
        self.assertEqual(problems, [])

    def test_toc_lists_every_file_in_order(self):
        toc = (ADDON_DIR / "ForeverGold.toc").read_text(encoding="utf-8").splitlines()
        self.assertEqual(toc[0], "## Interface: 16001")
        files = [line.strip() for line in toc if line.strip() and not line.startswith("#")]
        self.assertEqual(files, ["PlanDefault.lua", "PlanData.lua", "Core.lua", "Scan.lua", "Ledger.lua",
                                 "Bridge.lua", "UI.lua"])
        for f in addon_files():
            self.assertIn(f.name, files)
            self.assertTrue(f.read_text(encoding="utf-8").startswith("local ADDON, ns = ...\n"),
                            f.name + " must start with the shared namespace line")

    def test_every_register_event_is_a_literal(self):
        for name, code in addon_code().items():
            for m in re.finditer(r"\bRegisterEvent\s*\(([^)]*)\)", code):
                self.assertRegex(m.group(1).strip(), r'^"[A-Z0-9_]+"$', "%s: RegisterEvent(%s)" % (name, m.group(1)))


class ForeverApiContract(unittest.TestCase):
    def test_manifest_reports_the_cache(self):
        if not MANIFEST.is_file():
            self.skipTest("no forever_api cache yet: python3 tests/test_api_contract.py --refresh")
        m = json.loads(MANIFEST.read_text(encoding="utf-8"))
        for key in m.get("failed", []):
            print("forever_api: %s failed to download, its checks skip" % key, file=sys.stderr)
        for source in SOURCES:
            self.assertIn(source, m["sources"])

    def test_c_namespace_functions_exist(self):
        api, _ = global_api(self)
        missing = []
        for name, code in addon_code(keep_strings=False).items():
            for ref in sorted(set(re.findall(r"\bC_[A-Za-z]+\.[A-Za-z_]\w*", code))):
                if ref not in api:
                    missing.append("%s: %s" % (name, ref))
        self.assertEqual(missing, [], "C_ functions not in Forever's GlobalAPI.lua")

    def test_auction_house_calls_match_the_documentation(self):
        funcs = doc_functions(apidoc(self, "AuctionHouseDocumentation.lua"))
        used = set()
        for code in addon_code(keep_strings=False).values():
            used.update(re.findall(r"\bC_AuctionHouse\.([A-Za-z_]\w*)", code))
        self.assertTrue(used, "the addon uses C_AuctionHouse")
        self.assertEqual(sorted(used - set(funcs)), [])
        self.assertNotIn("Arguments", funcs["ReplicateItems"])
        self.assertEqual([a["Name"] for a in positional(funcs["CalculateCommodityDeposit"]["Arguments"])],
                         ["itemID", "duration", "quantity"])
        self.assertEqual([r["Type"] for r in positional(funcs["SupportsCopperValues"]["Returns"])], ["bool"])

    def test_replicate_item_info_returns_match_the_addon(self):
        funcs = doc_functions(apidoc(self, "AuctionHouseDocumentation.lua"))
        documented = [r["Name"] for r in positional(funcs["GetReplicateItemInfo"]["Returns"])]
        self.assertEqual(len(documented), 18)
        code = addon_code(keep_strings=False)["Scan.lua"]
        m = re.search(r"local\s+([\w\s,]+?)\s*=\s*get\(i\)", code)
        self.assertIsNotNone(m, "Scan.lua unpacks GetReplicateItemInfo into named locals")
        names = [x.strip() for x in m.group(1).split(",")]
        self.assertEqual(names, documented, "GetReplicateItemInfo returns changed: update Scan.lua")
        # the signature helper reads count, buyoutPrice and itemID by position
        sig = re.search(r"local\s+([\w\s,]+?)\s*=\s*get\(i\)\s*\n\s*parts", code)
        self.assertIsNotNone(sig)
        pos = [x.strip() for x in sig.group(1).split(",")]
        for want in ("count", "buyoutPrice", "itemID"):
            self.assertEqual(pos.index(want), documented.index(want), want + " position in signature()")

    def test_events_exist(self):
        events = all_events(self)
        ah_events = doc_events(apidoc(self, "AuctionHouseDocumentation.lua"))
        used = set()
        for code in addon_code().values():
            used.update(re.findall(r'\bRegisterEvent\s*\(\s*"([A-Z0-9_]+)"\s*\)', code))
        self.assertGreaterEqual(len(used), 10)
        self.assertEqual(sorted(used - events), [], "events not in Forever's Events.lua")
        for e in used:
            if e.startswith(("AUCTION_HOUSE", "REPLICATE")):
                self.assertIn(e, ah_events)

    def test_frame_types_templates_and_scripts_exist(self):
        tmpl = templates(self)
        widgets = widget_api(self)
        handlers = set()
        for spec in widgets.values():
            handlers.update(positional(spec.get("handlers", {})))
        for name, code in addon_code().items():
            for m in re.finditer(r'CreateFrame\(\s*"(\w+)"(?:\s*,\s*([^,)]+))?(?:\s*,\s*([^,)]+))?(?:\s*,\s*"([^"]+)")?', code):
                self.assertIn(m.group(1), widgets, "%s: frame type %s" % (name, m.group(1)))
                for t in re.split(r"[,\s]+", m.group(4) or ""):
                    if t:
                        self.assertIn(t, tmpl, "%s: template %s" % (name, t))
            for script in re.findall(r'SetScript\(\s*"(\w+)"', code):
                self.assertIn(script, handlers, "%s: script handler %s" % (name, script))
            for font in re.findall(r'CreateFontString\(\s*[^,)]*,\s*"\w+"\s*,\s*"(\w+)"', code):
                self.assertEqual(tmpl.get(font, {}).get("type"), "Font", "%s: font object %s" % (name, font))

    def test_widget_methods_exist(self):
        widgets = widget_api(self)
        methods = set()
        for spec in widgets.values():
            methods.update(positional(spec.get("methods", {})))
        backdrop = set(re.findall(r"function BackdropTemplateMixin:(\w+)\(", need(self, "gethe", "Backdrop.lua")))
        self.assertIn("SetBackdrop", backdrop)
        missing = []
        for name, code in addon_code(keep_strings=False).items():
            for meth in sorted(set(re.findall(r":([A-Z]\w*)\s*\(", code))):
                if meth not in methods and meth not in backdrop:
                    missing.append("%s: :%s()" % (name, meth))
        self.assertEqual(missing, [], "widget methods not in Forever's WidgetAPI.lua / BackdropTemplateMixin")

    def test_bare_globals_exist(self):
        api, lua = global_api(self)
        fxml = framexml_functions(self)
        known = api | lua | fxml
        for g in KNOWN_GLOBALS:
            self.assertIn(g, known, "known global %s is not on Forever" % g)
        problems = []
        for name, code in addon_code(keep_strings=False).items():
            defined = local_names(code)
            for call in sorted(bare_calls(code) - defined):
                if call not in known:
                    problems.append("%s: %s()" % (name, call))
        self.assertEqual(problems, [], "called globals missing from GlobalAPI/LuaAPI/FrameXML")

    def test_lua_library_members_exist(self):
        _, lua = global_api(self)
        missing = []
        for name, code in addon_code(keep_strings=False).items():
            for lib, member in sorted(set(re.findall(r"\b(string|table|math|bit|coroutine)\.(\w+)", code))):
                if lib + "." + member not in lua:
                    missing.append("%s: %s.%s" % (name, lib, member))
        self.assertEqual(missing, [])

    def test_global_variables_are_known(self):
        api, lua = global_api(self)
        known = api | lua | framexml_functions(self)
        constants = need(self, "gethe", "Constants.lua")
        frames = set(positional(parsed(self, "ketho", "Frames.lua")["Frames"]))
        for var, where in GLOBAL_VARIABLES.items():
            if where.startswith("gethe Constants"):
                self.assertRegex(constants, r"(?m)^%s\s*=" % var)
            if where.startswith("ketho Frames"):
                self.assertIn(var, frames)
        unknown = []
        for name, code in addon_code(keep_strings=False).items():
            defined = local_names(code) | LUA_KEYWORDS | LUA_LIBRARIES | {"ADDON", "ns", "self"}
            for ident in sorted(global_reads(code) - defined):
                if ident in known or ident in GLOBAL_VARIABLES or ident.startswith("C_"):
                    continue
                unknown.append("%s: %s" % (name, ident))
        self.assertEqual(unknown, [], "global reads that are not Forever API (add a local or check the name)")

    def test_bag_index_names_used_for_bags_and_bank(self):
        enum = parsed(self, "ketho", "LuaEnum.lua")["Enum"]
        bag = enum["BagIndex"]
        self.assertEqual(bag["Backpack"], 0)
        bank = [k for k in bag if k == "Bank" or re.fullmatch(r"(BankBag|CharacterBankTab)_\d+", k)]
        self.assertTrue(bank, "Ledger.lua finds bank containers by these names; none exist")
        doc = doc_tables(apidoc(self, "BagIndexConstantsDocumentation.lua"))
        doc_names = {f["Name"]: f["EnumValue"] for f in positional(doc["BagIndex"]["Fields"])}
        self.assertEqual(doc_names, bag, "Ketho LuaEnum and the API docs agree on BagIndex")
        self.assertEqual(parsed(self, "ketho", "LuaEnum.lua")["Constants"]["InventoryConstants"]["NumBagSlots"], 4)

    def test_container_item_info_fields(self):
        doc = apidoc(self, "ContainerDocumentation.lua")
        funcs = doc_functions(doc)
        for f in ("GetContainerNumSlots", "GetContainerItemInfo"):
            self.assertIn(f, funcs)
        fields = {x["Name"] for x in positional(doc_tables(doc)["ContainerItemInfo"]["Fields"])}
        ledger = addon_code(keep_strings=False)["Ledger.lua"]
        for used in set(re.findall(r"\binfo\.(\w+)", ledger)):
            self.assertIn(used, fields, "ContainerItemInfo." + used)
        self.assertIn("BAG_UPDATE_DELAYED", doc_events(doc))

    def test_reload_exists(self):
        funcs = doc_functions(apidoc(self, "UIManagerDocumentation.lua"))
        self.assertIn("Reload", funcs)

    def test_stub_matches_forever(self):
        """The harness stub must not drift from the real API."""
        stub = STUB.read_text(encoding="utf-8")
        api, _ = global_api(self)
        events = all_events(self)
        tmpl = templates(self)
        block = re.search(r"local BAG_INDEX = \{(.*?)\n\}", stub, re.S).group(1)
        stub_bag = {k: int(v) for k, v in re.findall(r"(\w+)\s*=\s*(-?\d+)", block)}
        self.assertEqual(stub_bag, parsed(self, "ketho", "LuaEnum.lua")["Enum"]["BagIndex"])
        known = re.search(r"local KNOWN_EVENTS = \{\}\nfor _, e in ipairs\(\{(.*?)\}\)", stub, re.S).group(1)
        self.assertEqual(sorted(set(re.findall(r'"([A-Z0-9_]+)"', known)) - events), [])
        ktmpl = re.search(r"local KNOWN_TEMPLATES = \{(.*?)\}", stub, re.S).group(1)
        self.assertEqual(sorted(set(re.findall(r"(\w+)\s*=\s*true", ktmpl)) - set(tmpl)), [])
        traps = re.search(r"local TRAP_NAMESPACED = \{(.*?)\n\}", stub, re.S).group(1)
        for ns, names in re.findall(r"(C_\w+)\s*=\s*\{([^}]*)\}", traps):
            for n in re.findall(r'"(\w+)"', names):
                self.assertIn(ns + "." + n, api, "stub trap for a function Forever does not have")
        self.assertIn('"1.60.1", "70634", "Oct 2026", 16001', stub)


def main(argv):
    if "--refresh" in argv:
        if not sys.flags.isolated:
            # Re-run isolated: no PYTHON* env vars, no script dir or cwd on sys.path.
            return subprocess.call([sys.executable, "-I", str(Path(__file__).resolve())] + argv)
        refs = {}
        for i, a in enumerate(argv):
            if a in ("--ketho-ref", "--gethe-ref") and i + 1 < len(argv):
                refs[a[2:7]] = argv[i + 1]
        return refresh(refs)
    unittest.main(argv=[sys.argv[0]] + argv, verbosity=2)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
