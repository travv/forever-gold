"""luasv: safe SavedVariables parser and deterministic dumper."""

import math
import unittest

import test_support  # noqa: F401  (puts the repo on sys.path)
from fgold import luasv
from fgold.luasv import LuaSVError


def val(src):
    return luasv.loads("X = " + src)["X"]


class TestScalars(unittest.TestCase):
    def test_numbers(self):
        self.assertEqual(val("42"), 42)
        self.assertIsInstance(val("42"), int)
        self.assertEqual(val("-5"), -5)
        self.assertEqual(val("- 5"), -5)
        self.assertEqual(val("- -5"), 5)
        self.assertEqual(val("2.5"), 2.5)
        self.assertEqual(val("-2.5"), -2.5)
        self.assertEqual(val(".5"), 0.5)
        self.assertEqual(val("5."), 5)
        self.assertEqual(val("0xFF"), 255)
        self.assertEqual(val("0x10"), 16)
        self.assertEqual(val("-0x10"), -16)
        self.assertEqual(val("007"), 7)

    def test_exponents_become_ints_when_whole(self):
        self.assertEqual(val("1e+15"), 10 ** 15)
        self.assertIsInstance(val("1e+15"), int)
        self.assertEqual(val("1E3"), 1000)
        self.assertEqual(val("2.5e-3"), 0.0025)
        self.assertEqual(val("3.0"), 3)
        self.assertIsInstance(val("3.0"), int)
        self.assertEqual(val("29.97916603088379"), 29.97916603088379)

    def test_big_integers_stay_exact(self):
        self.assertEqual(val("123456789012345678901234567890"), 123456789012345678901234567890)

    def test_booleans_and_nil(self):
        self.assertIs(val("true"), True)
        self.assertIs(val("false"), False)
        self.assertNotIn("X", luasv.loads("X = nil"))

    def test_string_escapes(self):
        self.assertEqual(val(r'"a\"b"'), 'a"b')
        self.assertEqual(val(r'"a\\b"'), "a\\b")
        self.assertEqual(val(r'"a\nb\tc\rd"'), "a\nb\tc\rd")
        self.assertEqual(val(r'"\065\066\0671"'), "ABC1")
        self.assertEqual(val(r'"\0"'), "\x00")
        self.assertEqual(val(r'"\a\b\f\v"'), "\x07\x08\x0c\x0b")
        self.assertEqual(val(r"'single \'q\' and \"d\"'"), "single 'q' and \"d\"")
        self.assertEqual(val('"line\\\nnext"'), "line\nnext")
        self.assertEqual(val(r'"\q"'), "q")  # Lua 5.1 keeps unknown escapes as the char

    def test_decimal_escapes_build_utf8(self):
        self.assertEqual(val(r'"caf\195\169"'), "café")
        self.assertEqual(val('"café"'), "café")

    def test_long_strings(self):
        self.assertEqual(val("[[abc]]"), "abc")
        self.assertEqual(val("[[\nfirst newline skipped]]"), "first newline skipped")
        self.assertEqual(val("[[a\r\nb]]"), "a\nb")
        self.assertEqual(val("[==[has ]] and ]=] inside]==]"), "has ]] and ]=] inside")
        self.assertEqual(val("[=[\\n stays raw]=]"), "\\n stays raw")

    def test_long_string_scan_data(self):
        data = "2447:37:22x40,23x200;765:3:150x20;" * 5000
        self.assertEqual(val('"' + data + '"'), data)


class TestComments(unittest.TestCase):
    def test_line_and_block_comments(self):
        src = """-- top comment
--[[ block
comment ]]
--[==[ level 2 ]] still comment ]==]
X = { -- trailing
  1, -- [1]
  --[[ inline ]] 2, -- [2]
}
Y = 3 -- end
"""
        d = luasv.loads(src)
        self.assertEqual(d, {"X": [1, 2], "Y": 3})

    def test_comment_between_key_and_equals(self):
        self.assertEqual(val("{ key --[[c]] = 1 }"), {"key": 1})

    def test_triple_dash_is_a_comment(self):
        self.assertEqual(luasv.loads("---5\nX = 1"), {"X": 1})


class TestTables(unittest.TestCase):
    def test_list_vs_dict(self):
        self.assertEqual(val('{ "a", "b", "c" }'), ["a", "b", "c"])
        self.assertEqual(val('{ [1] = "a", [2] = "b" }'), ["a", "b"])
        self.assertEqual(val('{ [2] = "b", [1] = "a" }'), ["a", "b"])
        self.assertEqual(val('{ [1] = "a", [3] = "c" }'), {1: "a", 3: "c"})
        self.assertEqual(val('{ [0] = "z", [1] = "a" }'), {0: "z", 1: "a"})
        self.assertEqual(val('{ "p", key = "v" }'), {1: "p", "key": "v"})
        self.assertEqual(val("{}"), {})
        self.assertEqual(val("{ [2447] = 37, [765] = 12 }"), {2447: 37, 765: 12})

    def test_nested_and_separators(self):
        self.assertEqual(val("{ {1, 2}; { k = { } }, }"), [[1, 2], {"k": {}}])
        self.assertEqual(val('{ ["a b"] = 1, ["1"] = 2 }'), {"a b": 1, "1": 2})

    def test_nil_values(self):
        self.assertEqual(val("{ a = 1, b = nil }"), {"a": 1})
        self.assertEqual(val("{ nil, 2 }"), {2: 2})
        self.assertEqual(val("{ 1, nil }"), [1])

    def test_positional_wins_over_explicit_index(self):
        self.assertEqual(val('{ [1] = "x", "y" }'), ["y"])

    def test_float_keys_normalize(self):
        self.assertEqual(val('{ [1.0] = "a", [2] = "b" }'), ["a", "b"])
        self.assertEqual(val('{ [1.5] = "a" }'), {1.5: "a"})

    def test_mixed_deposit_probe_shape(self):
        v = val("{ 100, -- [1]\n 100, -- [2]\n 240, -- [3]\n qty = 20, at = 5 }")
        self.assertEqual(v, {1: 100, 2: 100, 3: 240, "qty": 20, "at": 5})

    def test_wow_style_file(self):
        src = '\nForeverGoldDB = {\n\t["schema"] = 1,\n\t["sessions"] = {\n\t\t{\n\t\t\t["id"] = "1-A",\n\t\t}, -- [1]\n\t},\n}\n'
        self.assertEqual(luasv.loads(src), {"ForeverGoldDB": {"schema": 1, "sessions": [{"id": "1-A"}]}})

    def test_bom_crlf_and_bytes(self):
        src = "\ufeffX = {\r\n  1,\r\n}\r\nY = 'z'\r\n".encode("utf-8")
        self.assertEqual(luasv.loads(src), {"X": [1], "Y": "z"})

    def test_multiple_statements(self):
        self.assertEqual(luasv.loads("A = 1; B = { 2 } C = 'c'"), {"A": 1, "B": [2], "C": "c"})

    def test_handmade_fixture_parses(self):
        db = test_support.fixture_db()
        self.assertEqual(db["schema"], 1)
        self.assertEqual(len(db["sessions"]), 2)
        self.assertEqual(db["inventory"]["Travv-Forever"]["bags"][2447], 37)
        self.assertEqual(db["probes"]["deposit"][785], {1: 100, 2: 100, 3: 240, "qty": 20, "at": 1793927340,
                                                       "house": "Alliance"})


class TestErrorsAndSafety(unittest.TestCase):
    def assertBad(self, src, fragment=None):
        with self.assertRaises(LuaSVError) as cm:
            luasv.loads(src)
        if fragment:
            self.assertIn(fragment, str(cm.exception))
        return cm.exception

    def test_never_executes_code(self):
        self.assertBad("X = os.exit()", "unexpected name 'os'")
        self.assertBad("X = function() end", "unexpected name 'function'")
        self.assertBad("X = { f = loadstring('x') }", "unexpected name 'loadstring'")
        self.assertBad("os.exit()")
        self.assertBad("local X = 1")
        self.assertBad("X = 1 + 2")
        self.assertBad("X = a .. b")

    def test_syntax_errors_have_positions(self):
        e = self.assertBad("X = {\n  1,\n  2 3,\n}", "expected ',' or '}'")
        self.assertEqual(e.line, 3)
        self.assertBad('X = "abc', "unfinished string")
        self.assertBad('X = "a\nb"', "unfinished string")
        self.assertBad("X = [[abc", "unfinished long string")
        self.assertBad("X = --[[ never closed", "unfinished long comment")
        self.assertBad("X = {", "unfinished table")
        self.assertBad("X = 3abc", "malformed number")
        self.assertBad("X = 1.2.3", "malformed number")
        self.assertBad('X = "\\300"', "escape sequence too large")
        self.assertBad("X = -'a'", "unary minus")
        self.assertBad("X = {[true] = 1}", "boolean table keys")
        self.assertBad("X = {[nil] = 1}", "nil")
        self.assertBad("X = {[{}] = 1}")
        self.assertBad("X", "expected '='")
        self.assertBad("X == 1")
        self.assertBad("X = { end = 1 }", "reserved word")

    def test_unicode_digits_are_not_numbers(self):
        self.assertBad("X = \u0663")
        self.assertEqual(val('"\\\u0663"'), "\u0663")

    def test_depth_limit(self):
        deep = "X = " + "{" * (luasv.MAX_DEPTH + 5) + "}" * (luasv.MAX_DEPTH + 5)
        self.assertBad(deep, "nested deeper")
        ok = "X = " + "{" * 50 + "}" * 50
        luasv.loads(ok)


class TestDump(unittest.TestCase):
    def test_deterministic_sorted_output(self):
        a = {"b": 1, "a": 2, 10: "x", 2: "y", "c": [3, 1]}
        b = {"c": [3, 1], 2: "y", "a": 2, 10: "x", "b": 1}
        self.assertEqual(luasv.dump("T", a), luasv.dump("T", b))
        text = luasv.dump("T", a)
        self.assertTrue(text.startswith("T = {\n  [2] = \"y\",\n  [10] = \"x\",\n  a = 2,\n"))
        self.assertTrue(text.endswith("}\n"))

    def test_escapes(self):
        s = 'q"b\\s\nn\r\t\x01\x7fé'
        text = luasv.dump("S", s)
        self.assertEqual(text, 'S = "q\\"b\\\\s\\nn\\r\\t\\001\\127é"\n')
        self.assertEqual(luasv.loads(text)["S"], s)

    def test_ints_as_ints(self):
        self.assertEqual(luasv.dumps(5), "5")
        self.assertEqual(luasv.dumps(5.0), "5")
        self.assertEqual(luasv.dumps(1e15), "1000000000000000")
        self.assertEqual(luasv.dumps(0.05), "0.05")
        self.assertEqual(luasv.dumps(True), "true")
        self.assertEqual(luasv.dumps(-7), "-7")

    def test_keys(self):
        text = luasv.dump("T", {"and": 1, "ok_key": 2, "two words": 3, "9lives": 4, 2447: 5, 1.5: 6})
        self.assertIn('["and"] = 1', text)
        self.assertIn("ok_key = 2", text)
        self.assertIn('["two words"] = 3', text)
        self.assertIn('["9lives"] = 4', text)
        self.assertIn("[2447] = 5", text)
        self.assertIn("[1.5] = 6", text)

    def test_rejects_unwritable(self):
        with self.assertRaises(ValueError):
            luasv.dump("T", [1, None, 2])
        with self.assertRaises(ValueError):
            luasv.dump("T", math.nan)
        with self.assertRaises(ValueError):
            luasv.dump("T", math.inf)
        with self.assertRaises(TypeError):
            luasv.dump("T", {True: 1})
        with self.assertRaises(TypeError):
            luasv.dump("T", object())
        with self.assertRaises(ValueError):
            luasv.dump("not a name", 1)
        with self.assertRaises(ValueError):
            luasv.dump("end", 1)

    def test_none_values_in_dicts_are_skipped(self):
        self.assertEqual(luasv.loads(luasv.dump("T", {"a": None, "b": 1}))["T"], {"b": 1})

    def test_round_trip(self):
        cases = [
            {"schema": 1, "id": "20261105-0412-ab12", "ref": {2447: {"min": 16, "mkt": 17, "q": 2400}},
             "buy": [{"id": 2447, "name": "Peacebloom", "qty": 60, "maxUnit": 18, "why": "a \"q\"\nb", "flip": False}],
             "notes": [], "budget": {"spendCap": 250000}, "f": 0.25, "neg": -3},
            [1, 2, [3, [4, {}]]],
            {1: "a", 3: "c"},
            {"1": "string key stays a string"},
            {"nested": {"deeper": {"deepest": ["x" * 3000]}}},
            [],
            {},
            {"k": (1, 2)},
            {"big": 2 ** 60, "x": 1e300, "tiny": 1e-300},
        ]
        for v in cases:
            text = luasv.dump("V", v)
            self.assertEqual(luasv.loads(text)["V"], luasv.canonical(v), text)
            self.assertEqual(luasv.dump("V", luasv.loads(text)["V"]), text)

    def test_fixture_round_trip(self):
        db = test_support.fixture_db()
        text = luasv.dump("ForeverGoldDB", db)
        self.assertEqual(luasv.loads(text)["ForeverGoldDB"], luasv.canonical(db))


class TestFindAssignment(unittest.TestCase):
    def test_finds_table_after_code_line(self):
        src = "local ADDON, ns = ...\nForeverGoldPlanDefault = { schema = 1, id = \"none\" }\nns.x = 1\n"
        self.assertEqual(luasv.find_assignment(src, "ForeverGoldPlanDefault"), {"schema": 1, "id": "none"})

    def test_missing(self):
        with self.assertRaises(LuaSVError):
            luasv.find_assignment("X = 1", "ForeverGoldPlanDefault")


if __name__ == "__main__":
    unittest.main()
