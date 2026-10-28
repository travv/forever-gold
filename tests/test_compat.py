"""Guards for the Python side: 3.11 syntax, stdlib only, no em-dashes or BOMs.

Scope: the files this part of the repo owns (fgold/, tools/fetch_static.py,
static/, tests/fixtures/handmade/ and the Python unit tests). The repo-wide
red-line checks live in tests/test_redline.py.
"""

import ast
import io
import sys
import tokenize
import unittest
from pathlib import Path

import test_support as ts

OTHER_AGENT_TESTS = {"test_redline.py", "test_api_contract.py"}
EM_DASH = "\u2014"
BOM = "\ufeff"
FSTRING_START = getattr(tokenize, "FSTRING_START", None)   # Python 3.12+ tokenizes f-strings in parts


def _quote(token_text: str) -> str:
    body = token_text.lstrip("rRbBfFuU")
    return body[:3] if body[:3] in ('"""', "'''") else body[:1]


def _clash(outer: str, inner: str) -> bool:
    """Python 3.11 ends an f-string at its first own quote, so nothing inside may reuse it."""
    return bool(inner) and inner[0] == outer[0] and (len(outer) == 1 or len(inner) == 3)


def pep701_fstrings(src: str) -> list:
    """[(line, why)] for f-string syntax that Python 3.11 rejects but 3.12+ (PEP 701) accepts.

    ast.parse(..., feature_version=(3, 11)) does not catch these on 3.12+, and they are SyntaxErrors on
    the 3.11 minimum in design.md. On 3.11 itself the tokenizer keeps an f-string whole (and ast.parse
    rejects these forms anyway), so this returns []."""
    if FSTRING_START is None:
        return []
    hits, stack = [], []   # stack: quotes of the open f-strings, outermost first
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        line = tok.start[0]
        if tok.type == FSTRING_START:
            q = _quote(tok.string)
            if any(_clash(o, q) for o in stack):
                hits.append((line, "nested f-string reuses an enclosing f-string's quote"))
            stack.append(q)
        elif tok.type == tokenize.FSTRING_END:
            stack.pop()
        elif not stack:
            continue
        elif tok.type == tokenize.FSTRING_MIDDLE:
            if len(stack) > 1 and "\\" in tok.string:
                hits.append((line, "backslash inside an f-string replacement field"))
        elif tok.type == tokenize.STRING:
            if any(_clash(o, _quote(tok.string)) for o in stack):
                hits.append((line, "string in an f-string replacement field reuses the f-string's quote"))
            if "\\" in tok.string:
                hits.append((line, "backslash inside an f-string replacement field"))
        elif tok.type == tokenize.COMMENT:
            hits.append((line, "comment inside an f-string replacement field"))
        elif tok.type in (tokenize.NL, tokenize.NEWLINE) and len(stack[-1]) == 1:
            hits.append((line, "line break inside a single-quoted f-string's replacement field"))
    return hits


def all_python_files():
    files = sorted((ts.REPO / "fgold").glob("*.py")) + sorted((ts.REPO / "tools").glob("*.py"))
    return files + sorted((ts.REPO / "tests").glob("*.py"))


def owned_python_files():
    files = sorted((ts.REPO / "fgold").glob("*.py"))
    files.append(ts.REPO / "tools" / "fetch_static.py")
    files += [p for p in sorted((ts.REPO / "tests").glob("test_*.py")) if p.name not in OTHER_AGENT_TESTS]
    return files


def owned_text_files():
    files = owned_python_files()
    files += sorted((ts.REPO / "static").glob("*.json"))
    files += sorted(p for p in (ts.REPO / "tests" / "fixtures" / "handmade").rglob("*") if p.is_file())
    return files


class TestCompat(unittest.TestCase):
    def test_python_311_syntax(self):
        for p in owned_python_files():
            src = p.read_text(encoding="utf-8")
            try:
                ast.parse(src, filename=str(p), feature_version=(3, 11))
            except SyntaxError as exc:
                self.fail(f"{p.name}: not Python 3.11 syntax: {exc}")

    def test_no_pep701_fstrings(self):
        hits = []
        for p in all_python_files():
            for line, why in pep701_fstrings(p.read_text(encoding="utf-8")):
                hits.append(f"{p.relative_to(ts.REPO).as_posix()}:{line}: {why}")
        self.assertEqual(hits, [], "f-string syntax that needs Python 3.12 (design.md says 3.11+)")

    @unittest.skipIf(FSTRING_START is None, "Python 3.11: ast.parse itself rejects these forms")
    def test_pep701_check_catches_what_feature_version_misses(self):
        bad = ['f"{x["a"]}"\n', 'f"{"\\n".join(x)}"\n', 'f"{1 +\n 2}"\n', "f'{x # c\n}'\n", 'f"{f"{x}"}"\n',
               "f\"{f'{\"a\"}'}\"\n", "f'''{f'{x}' + f'''{y}'''}'''\n"]
        for src in bad:
            ast.parse(src, feature_version=(3, 11))   # the old guard: accepted on 3.12+
            self.assertTrue(pep701_fstrings(src), src)
        good = ["f\"{x['a']}\"\n", 'f"a\\nb {x!r:>10}"\n', "f'''{'a'}'''\n", "f\"{f'{x}'}\"\n",
                'f"""{\n x}"""\n', "f'{x:{w}}'\n", "'{\"a\"}'\n"]
        for src in good:
            self.assertEqual(pep701_fstrings(src), [], src)

    def test_no_312_only_apis(self):
        banned = ("itertools.batched", ".is_junction(", "os.path.isjunction", "Path.walk(", "datetime.UTC",
                  "from typing import override", "onexc=")
        for p in owned_python_files():
            if p.name == "test_compat.py":
                continue
            src = p.read_text(encoding="utf-8")
            for b in banned:
                self.assertNotIn(b, src, f"{p.name} uses {b} (Python 3.12+)")

    def test_stdlib_only(self):
        stdlib = set(getattr(sys, "stdlib_module_names", ()))
        if not stdlib:
            self.skipTest("sys.stdlib_module_names needs Python 3.10+")
        allowed = stdlib | {"fgold", "test_support"}
        for p in owned_python_files():
            tree = ast.parse(p.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    names = [node.module]
                else:
                    continue
                for n in names:
                    self.assertIn(n.split(".")[0], allowed, f"{p.name} imports {n}")

    def test_no_em_dash_or_bom(self):
        for p in owned_text_files():
            text = p.read_text(encoding="utf-8")
            self.assertNotIn(EM_DASH, text, f"{p}: em-dash (U+2014)")
            self.assertNotIn(BOM, text, f"{p}: byte order mark")

    def test_lf_line_endings(self):
        for p in owned_text_files():
            self.assertNotIn(b"\r\n", p.read_bytes(), f"{p}: CRLF line endings")


if __name__ == "__main__":
    unittest.main()
