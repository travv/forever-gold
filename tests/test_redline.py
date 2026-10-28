"""The red line: Claude thinks, Travis clicks (docs/design.md section 6, CLAUDE.md).

1. The addon source never names a protected AH/mail/craft/input/chat function,
   never builds a function name at runtime, and PlanData.lua (which the TOC
   loads as code) is a single data assignment.
2. Nothing in the repo automates input, keeps a character online or reads
   game memory or packets.
3. The /forever-gold skill and .claude/settings.json pre-approve only the fgold
   commands and the two files the flow edits, and deny edits in WoW folders.
4. No em-dash (U+2014) in addon/, fgold/ or tests/, nor in the rest of the
   repo's text.

Markdown is prose: CLAUDE.md, the skill and the README have to *name* the banned
tools to forbid them, so in .md files only code (fenced blocks and inline code
spans, the commands Travis copies) is scanned. docs/design.md and
docs/api-notes.md are the spec and the API facts and list the banned calls, so
they are not scanned at all.
"""

import json
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
ADDON = REPO / "addon"
DESIGN = REPO / "docs" / "design.md"
API_NOTES = REPO / "docs" / "api-notes.md"
PLAN_DATA = ADDON / "ForeverGold" / "PlanData.lua"
SKILL = REPO / ".claude" / "skills" / "forever-gold" / "SKILL.md"
SETTINGS = REPO / ".claude" / "settings.json"
SELF = Path(__file__).resolve()
EMDASH = "\u2014"

# design.md section 6, in order. test_matches_design_doc keeps this in sync.
BANNED_ADDON_TOKENS = [
    "PostItem", "PostCommodity", "ConfirmPostItem", "ConfirmPostCommodity", "PlaceBid",
    "StartCommoditiesPurchase", "ConfirmCommoditiesPurchase", "CancelAuction", "CancelSell",
    "SendMail", "TakeInboxItem", "TakeInboxMoney", "AutoLootMailItem", "DeleteInboxItem",
    "CraftRecipe", "DoTradeSkill", "PickupContainerItem", "UseContainerItem", "RunMacro",
    "RunMacroText", "RunBinding", "SecureActionButton", "SetOverrideBinding", ":Click(",
    "SendChatMessage", "AcceptTrade", "SendWhisper", "SendMessage",
]

# Ways around the token list: building a function name at runtime or running code strings.
ADDON_EVASION = [
    (r"\b_G\b", "_G lookup"), (r"\bgetglobal\b", "getglobal"), (r"\bsetglobal\b", "setglobal"),
    (r"\bloadstring\b", "loadstring"), (r"\bRunScript\b", "RunScript"), (r"\bsetfenv\b", "setfenv"),
    (r"\bgetfenv\b", "getfenv"), (r"\bsecure(call|callfunction|executerange)\b", "securecall / securecallfunction"),
    (r"\bhooksecurefunc\b", "hooksecurefunc"),
    # C_AuctionHouse["Post" .. "Commodity"](...): a name built inside an index, or a call of one
    (r"\[[^\]]*\.\.", "concatenation inside an index"), (r"\]\s*\(", "call of a computed index"),
    (r"\b(_G|C_\w+)\s*\[", "indexing _G or a C_ namespace"),
]

INPUT_AUTOMATION = [
    ("pyautogui", r"pyautogui"),
    ("pynput", r"pynput"),
    ("AutoHotkey", r"autohotkey|\.ahk\b"),
    ("osascript", r"osascript"),
    ("cliclick", r"cliclick"),
    ("keybd_event", r"keybd_event"),
    ("SendInput", r"sendinput"),
    ("anti-afk", r"anti[-_ ]?afk"),
    # relatives: other input drivers, memory and packet readers, computer use
    ("AppleScript keystroke", r"system events|\bkeystroke\s+\"|\bkey code\s+\d"),
    ("xdotool", r"xdotool"),
    ("SendKeys", r"\bsendkeys\b"),
    ("pywinauto", r"pywinauto"),
    ("AutoIt", r"\bautoit"),
    ("mouse_event", r"\bmouse_event\b"),
    ("SetCursorPos", r"\bsetcursorpos\b"),
    ("user32", r"\buser32\b"),
    ("keyboard/mouse module", r"^\s*(import|from)\s+(keyboard|mouse)\b"),
    ("process memory", r"readprocessmemory|writeprocessmemory|\bpymem\b|\bfrida\b"),
    ("packet capture", r"\bscapy\b|\bpyshark\b|\bpcapy\b|\bwindivert\b"),
    ("computer use", r"\bcomputer_20\d{6}\b"),
    # more input drivers: window messages (win32 context, so C_Club.SendMessage in the API cache is not
    # a hit), DirectInput, Quartz events, AppleScript from Objective-C, Linux input devices, Node
    ("win32 window messages", r"win32(api|gui)\.(post|send)message|\bwm_(keydown|keyup|char|lbuttondown|rbuttondown)\b"),
    ("pydirectinput", r"pydirectinput"),
    ("Quartz events", r"\bcgevent(post|create)"),
    ("NSAppleScript", r"nsapplescript"),
    ("Linux input devices", r"\b(uinput|evdev|ydotool)\b"),
    ("robotjs", r"robotjs"),
]
BANNED_EXTENSIONS = {".ahk", ".scpt", ".applescript", ".au3"}

# Local outputs (gitignored) and the spec and API facts (they must name the banned calls) are not scanned.
TOP_SKIP = {".git", "data", "reports"}
ANY_SKIP = {"__pycache__", ".venv", "venv", "node_modules"}
SKIP_PATHS = {DESIGN, API_NOTES, REPO / "tests" / "out", REPO / "plan"}
FGOLD_BASH = re.compile(r"^Bash\((python|py -3|python3) -m fgold( \*|:\*)\)$")
SKILL_EDITS = {"Edit(plan/draft.json)", "Edit(reports/*-brief.md)"}
WOW_FOLDER_DENY = ["Edit(//**/_forever_/**)", "Edit(//**/Interface/AddOns/**)", "Edit(**/PlanData.lua)"]


def iter_files(root, skip_paths=SKIP_PATHS):
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        parts = path.relative_to(REPO).parts
        if parts[0] in TOP_SKIP or any(part in ANY_SKIP for part in parts):
            continue
        if any(path == p or p in path.parents for p in skip_paths):
            continue
        yield path


def read_text(path):
    data = path.read_bytes()
    if b"\0" in data[:4096]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def markdown_code(text):
    """Only the code of a Markdown file: fenced blocks, and the inline code spans of prose lines
    (other text blanked, line numbering kept)."""
    out, fence = [], None
    for line in text.splitlines():
        m = re.match(r"\s*(```|~~~)", line)
        if m:
            fence = None if fence == m.group(1) else (fence or m.group(1))
            out.append("")
        elif fence:
            out.append(line)
        else:
            out.append("  ".join(re.findall(r"`([^`]+)`", line)))
    return "\n".join(out)


def plan_data_problem(text):
    """None if PlanData.lua is one `ForeverGoldPlan = { ... }` data assignment, else why not.

    The TOC loads it as code, so a call appended to it would run; luasv parses data only and
    rejects any call, name or operator."""
    from fgold import luasv
    try:
        parsed = luasv.loads(text)
    except luasv.LuaSVError as exc:
        return f"not data only ({exc})"
    if list(parsed) != ["ForeverGoldPlan"] or not isinstance(parsed["ForeverGoldPlan"], dict):
        return f"must assign exactly ForeverGoldPlan = {{...}} (assigns {sorted(parsed)})"
    return None


def allowed_tools(skill_text):
    """The allowed-tools rules of a SKILL.md frontmatter (YAML list or space/comma-separated string)."""
    m = re.match(r"---\n(.*?)\n---", skill_text, re.S)
    lines = m.group(1).splitlines() if m else []
    for i, line in enumerate(lines):
        km = re.match(r"allowed-tools:\s*(.*)$", line)
        if not km:
            continue
        if km.group(1).strip():
            return re.findall(r"[A-Za-z]+(?:\([^)]*\))?", km.group(1))
        items = []
        for nxt in lines[i + 1:]:
            im = re.match(r"\s+-\s+(.+?)\s*$", nxt)
            if not im:
                break
            items.append(im.group(1).strip("'\""))
        return items
    return []


def rel(path):
    return path.relative_to(REPO).as_posix()


class AddonRedLine(unittest.TestCase):
    def addon_sources(self):
        files = []
        for path in iter_files(ADDON):
            # PlanData.lua is generated data whose "why" texts are prose; test_plan_data_is_data_only
            # checks that it is nothing but data instead.
            if path.name == "PlanData.lua":
                continue
            text = read_text(path)
            if text is not None:
                files.append((path, text))
        return files

    def test_addon_exists(self):
        names = {p.name for p, _ in self.addon_sources()}
        for f in ("ForeverGold.toc", "Core.lua", "Scan.lua", "Ledger.lua", "Bridge.lua", "UI.lua", "PlanDefault.lua"):
            self.assertIn(f, names)

    def test_no_protected_or_input_calls_in_addon(self):
        hits = []
        for path, text in self.addon_sources():
            for lineno, line in enumerate(text.splitlines(), 1):
                for token in BANNED_ADDON_TOKENS:
                    if token in line:
                        hits.append("%s:%d: %s" % (rel(path), lineno, token))
        self.assertEqual(hits, [], "banned tokens in the addon (design.md section 6)")

    def test_no_dynamic_global_lookups_in_addon(self):
        hits = []
        for path, text in self.addon_sources():
            for lineno, line in enumerate(text.splitlines(), 1):
                for pattern, why in ADDON_EVASION:
                    if re.search(pattern, line):
                        hits.append("%s:%d: %s" % (rel(path), lineno, why))
        self.assertEqual(hits, [], "the token check is only meaningful without dynamic lookups")

    def test_plan_data_is_data_only(self):
        if not PLAN_DATA.is_file():
            self.skipTest("no addon/ForeverGold/PlanData.lua here (fgold install creates it)")
        self.assertIsNone(plan_data_problem(PLAN_DATA.read_bytes()), "PlanData.lua is loaded as code by the TOC")

    def test_plan_data_check_rejects_code(self):
        from fgold import apply, install
        good = install.default_plan_text(ADDON / "ForeverGold")
        self.assertIsNone(plan_data_problem(good))
        for tail in ("\nC_AuctionHouse.PostCommodity(1, 2, 3)\n", "\nlocal f = C_AuctionHouse['Post' .. 'Commodity']\n",
                     "\nOther = 1\n", "\nForeverGoldPlan.x = PlaceBid\n"):
            self.assertIsNotNone(plan_data_problem(good + tail), tail)
        self.assertIsNotNone(plan_data_problem(apply.HEADER + "ForeverGoldPlan = 5\n"))

    def test_evasion_patterns(self):
        for line in ("C_AuctionHouse['Post' .. 'Commodity'](a, b)", "local f = _G[name]", "ns[k](x)",
                     "securecallfunction(f)", "secureexecuterange(t, f)", "C_Club.SendMessage(1, 2, 'hi')",
                     "C_BattleNet.SendWhisper(id, 'hi')"):
            hit = any(re.search(p, line) for p, _ in ADDON_EVASION) or any(t in line for t in BANNED_ADDON_TOKENS)
            self.assertTrue(hit, line)

    def test_matches_design_doc(self):
        if not DESIGN.is_file():
            self.skipTest("docs/design.md not found")
        text = DESIGN.read_text(encoding="utf-8")
        section = text.split("## 6.", 1)[1]
        listed = section.split("must not contain any of:", 1)[1].split(". The repo", 1)[0]
        self.assertEqual(re.findall(r"`([^`]+)`", listed), BANNED_ADDON_TOKENS,
                         "design.md section 6 changed: update BANNED_ADDON_TOKENS")


class RepoRedLine(unittest.TestCase):
    def test_no_input_automation_in_repo(self):
        hits = []
        for path in iter_files(REPO):
            if path == SELF:
                continue
            if path.suffix.lower() in BANNED_EXTENSIONS:
                hits.append("%s: %s file" % (rel(path), path.suffix))
                continue
            text = read_text(path)
            if text is None:
                continue
            if path.suffix.lower() == ".md":
                text = markdown_code(text)
            for lineno, line in enumerate(text.splitlines(), 1):
                for label, pattern in INPUT_AUTOMATION:
                    if re.search(pattern, line, re.I):
                        hits.append("%s:%d: %s" % (rel(path), lineno, label))
        self.assertEqual(hits, [], "input-automation markers (CLAUDE.md red lines)")

    def test_markdown_code_blocks_are_scanned(self):
        sample = ("Never use pyautogui.\n```\npython -m fgold brief\n```\n~~~sh\nosascript -e x\n~~~\n"
                  "Do: `ydotool key 28` then `py -3 -m fgold ingest`\n")
        code = markdown_code(sample)
        self.assertNotIn("pyautogui", code, "prose may name a banned tool")
        self.assertIn("osascript", code)
        self.assertIn("ydotool", code, "inline code is a command Travis copies")
        self.assertEqual(len(code.split("\n")), len(sample.splitlines()))

    def test_docs_are_scanned_except_the_spec_and_api_notes(self):
        scanned = {p for p in iter_files(REPO) if p.parent == REPO / "docs"}
        self.assertIn(REPO / "docs" / "launch-checklist.md", scanned)
        self.assertNotIn(DESIGN, scanned)
        self.assertNotIn(API_NOTES, scanned)

    def test_readme_second_account_rules_name_the_account_risks(self):
        # README is the manual Travis follows; the VM caveat and the gold-buying ban were only in
        # docs/ideas.md (research) and in the Claude-facing CLAUDE.md / SKILL.md.
        text = (REPO / "README.md").read_text(encoding="utf-8")
        rules = text.split("**Rules for the second account:**", 1)[1].split("\n## ", 1)[0]
        for pattern in (r"VM caveat \(unverified\)", r"[Nn]o Blizzard policy on VMs", r"[Nn]ever buy gold",
                        r"GDKP", r"gold from strangers", r"Economy Holds"):
            self.assertRegex(rules, pattern)


class ClaudePermissions(unittest.TestCase):
    """The skill's allowed-tools skip the permission prompt for the whole /forever-gold turn, and
    workspace trust does not gate them. A bare Bash, Write or Edit there let Claude run any command
    or write any file (SavedVariables, another addon's folder, a hand-written PlanData.lua) without
    Travis seeing a prompt, which made the fgold-only allow list in .claude/settings.json moot."""

    def test_skill_pre_approves_only_fgold_and_its_two_files(self):
        rules = allowed_tools(SKILL.read_text(encoding="utf-8"))
        self.assertTrue(rules, "allowed-tools not found in SKILL.md")
        broad = [r for r in rules if not (FGOLD_BASH.match(r) or r == "Read" or r in SKILL_EDITS)]
        self.assertEqual(broad, [], "only fgold commands, Read and the draft / brief edits may skip the prompt")

    def test_settings_allow_only_fgold_and_deny_edits_in_wow_folders(self):
        perms = json.loads(SETTINGS.read_text(encoding="utf-8"))["permissions"]
        self.assertTrue(perms["allow"])
        self.assertEqual([r for r in perms["allow"] if not FGOLD_BASH.match(r)], [])
        for rule in WOW_FOLDER_DENY:
            self.assertIn(rule, perms.get("deny", []), "deny rules hold even when allowed-tools grants Edit")

    def test_allowed_tools_parser(self):
        self.assertEqual(allowed_tools("---\nname: x\nallowed-tools: Bash(git add *) Read, Edit\n---\nbody\n"),
                         ["Bash(git add *)", "Read", "Edit"])
        self.assertEqual(allowed_tools("---\nname: x\nallowed-tools:\n  - Bash\n  - Write\nwhen: y\n---\n"),
                         ["Bash", "Write"])
        self.assertFalse(FGOLD_BASH.match("Bash"))
        self.assertFalse(FGOLD_BASH.match("Bash(python -m fgold * && curl x)"))
        self.assertTrue(FGOLD_BASH.match("Bash(py -3 -m fgold *)"))


class NoEmDash(unittest.TestCase):
    OUTPUTS = {REPO / "tests" / "out", REPO / "plan"}

    def scan(self, roots):
        hits = []
        for root in roots:
            if not root.exists():
                continue
            for path in iter_files(root, self.OUTPUTS) if root.is_dir() else [root]:
                text = read_text(path)
                if text is None:
                    continue
                for lineno, line in enumerate(text.splitlines(), 1):
                    if EMDASH in line:
                        hits.append("%s:%d" % (rel(path), lineno))
        return hits

    def test_no_emdash_in_code(self):
        hits = self.scan([REPO / "addon", REPO / "fgold", REPO / "tests"])
        self.assertEqual(hits, [], "em-dash (U+2014) found; use a colon, comma or parentheses")

    def test_no_emdash_anywhere_else(self):
        # design.md principle 6: anywhere in the repo, docs included.
        roots = [p for p in REPO.iterdir() if p.name not in {"addon", "fgold", "tests"} | TOP_SKIP | ANY_SKIP]
        hits = self.scan(roots)
        self.assertEqual(hits, [], "em-dash (U+2014) found; use a colon, comma or parentheses")


if __name__ == "__main__":
    unittest.main(verbosity=2)
