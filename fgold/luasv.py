"""Safe reader for WoW SavedVariables files and a deterministic Lua writer.

Nothing here executes Lua. The text is tokenized and parsed as *data*:

* statements ``Name = <value>`` (optionally separated by ``;``)
* tables with ``["k"] = v``, ``[1] = v``, ``name = v`` and bare positional values
* strings in double or single quotes with the Lua 5.1 escapes
  (``\\" \\\\ \\n \\t \\r \\a \\b \\f \\v \\ddd`` and backslash-newline), and long
  strings ``[[...]]`` / ``[==[...]==]``
* numbers: integers, decimals, exponents (``1e+15``), hex (``0xFF``), unary minus
* ``true``, ``false``, ``nil``; comments ``--`` and ``--[[ ]]``

Lua has one number type, so a number whose value is a whole number becomes a
Python ``int`` (``1e+15`` -> ``1000000000000000``, ``3.0`` -> ``3``); anything
else is a ``float``. A table whose keys are exactly ``1..n`` becomes a list,
any other non-empty table a dict, and an empty table ``{}`` an empty dict.

``dump(name, value)`` writes ``name = <value>`` with sorted keys, two-space
indentation and ints as ints, so the same data always gives the same text.
``loads(dump(name, v))[name] == canonical(v)`` for any dumpable ``v``.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

__all__ = ["LuaSVError", "loads", "load", "dump", "dumps", "canonical", "is_identifier", "find_assignment"]

MAX_DEPTH = 200

LUA_KEYWORDS = frozenset(
    "and break do else elseif end false for function if in local nil not or "
    "repeat return then true until while".split()
)

_WS_RE = re.compile(r"[ \t\r\n\f\v]+")
_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NUM_RE = re.compile(r"0[xX][0-9a-fA-F]+|(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
_FAST_DQ = re.compile(r'"([^"\\\n\r]*)"')
_FAST_SQ = re.compile(r"'([^'\\\n\r]*)'")
_NEWLINES_RE = re.compile(r"\r\n|\n\r|\r|\n")
_IDENT_RE = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]*\Z")
_DIGITS = "0123456789"
_SIMPLE_ESCAPES = {
    "a": 7, "b": 8, "f": 12, "n": 10, "r": 13, "t": 9, "v": 11,
    "\\": 92, '"': 34, "'": 39,
}
_MAX_EXACT_FLOAT = 2 ** 53


class LuaSVError(ValueError):
    """The text is not a SavedVariables data file we can read."""

    def __init__(self, message: str, line: int | None = None, col: int | None = None):
        self.line = line
        self.col = col
        where = f" (line {line}, col {col})" if line is not None else ""
        super().__init__(message + where)


def is_identifier(s) -> bool:
    return isinstance(s, str) and bool(_IDENT_RE.match(s)) and s not in LUA_KEYWORDS


def _number(token: str):
    if token[:2] in ("0x", "0X"):
        return int(token, 16)
    if "." not in token and "e" not in token and "E" not in token:
        return int(token)
    x = float(token)
    return _normalize_float(x)


def _normalize_float(x: float):
    if math.isfinite(x) and x == math.floor(x) and abs(x) < 2 ** 63:
        return int(x)
    return x


class _Parser:
    def __init__(self, text: str):
        self.t = text
        self.n = len(text)
        self.pos = 0

    # ---- errors and positions -------------------------------------------
    def error(self, msg: str, pos: int | None = None):
        p = self.pos if pos is None else pos
        line = self.t.count("\n", 0, p) + 1
        col = p - (self.t.rfind("\n", 0, p) + 1) + 1
        raise LuaSVError(msg, line, col)

    # ---- whitespace and comments ----------------------------------------
    def skip(self) -> None:
        t, n = self.t, self.n
        while self.pos < n:
            c = t[self.pos]
            if c in " \t\r\n\f\v":
                self.pos = _WS_RE.match(t, self.pos).end()
                continue
            if c == "-" and t.startswith("--", self.pos):
                start = self.pos
                self.pos += 2
                if self.pos < n and t[self.pos] == "[":
                    level = self._long_level(self.pos)
                    if level is not None:
                        self._read_long(level, start, what="comment")
                        continue
                nl = t.find("\n", self.pos)
                self.pos = n if nl < 0 else nl + 1
                continue
            break

    def _long_level(self, pos: int):
        t = self.t
        j = pos + 1
        while j < self.n and t[j] == "=":
            j += 1
        if j < self.n and t[j] == "[":
            return j - pos - 1
        return None

    def _read_long(self, level: int, start: int, what: str = "string") -> str:
        """self.pos is at the opening '['. Returns the content, newlines normalized."""
        open_len = level + 2
        body_start = self.pos + open_len
        close = "]" + "=" * level + "]"
        end = self.t.find(close, body_start)
        if end < 0:
            self.error(f"unfinished long {what}", start)
        body = self.t[body_start:end]
        self.pos = end + len(close)
        if body.startswith("\r\n") or body.startswith("\n\r"):
            body = body[2:]
        elif body.startswith("\n") or body.startswith("\r"):
            body = body[1:]
        if "\r" in body:
            body = _NEWLINES_RE.sub("\n", body)
        return body

    # ---- values -----------------------------------------------------------
    def value(self, depth: int):
        self.skip()
        if self.pos >= self.n:
            self.error("unexpected end of file, expected a value")
        c = self.t[self.pos]
        if c == "{":
            return self.table(depth + 1)
        if c == '"' or c == "'":
            return self.string(c)
        if c == "[":
            level = self._long_level(self.pos)
            if level is None:
                self.error("unexpected '['")
            return self._read_long(level, self.pos)
        if c == "-":
            start = self.pos
            self.pos += 1
            self.skip()
            if self.pos < self.n and self.t[self.pos] in _DIGITS + ".-":
                v = self.value(depth)
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    self.error("unary minus needs a number", start)
                return _normalize_float(-v) if isinstance(v, float) else -v
            self.error("unary minus needs a number", start)
        if c in _DIGITS or c == ".":
            return self.number()
        m = _NAME_RE.match(self.t, self.pos)
        if m:
            word = m.group(0)
            if word == "true":
                self.pos = m.end()
                return True
            if word == "false":
                self.pos = m.end()
                return False
            if word == "nil":
                self.pos = m.end()
                return None
            self.error(f"unexpected name {word!r}: only data (tables, strings, numbers, booleans) is allowed")
        self.error(f"unexpected character {c!r}")

    def number(self):
        m = _NUM_RE.match(self.t, self.pos)
        if not m:
            self.error("malformed number")
        end = m.end()
        if end < self.n and (self.t[end].isalnum() or self.t[end] == "_" or self.t[end] == "."):
            self.error("malformed number")
        self.pos = end
        try:
            return _number(m.group(0))
        except (ValueError, OverflowError):
            self.error("malformed number", m.start())

    def string(self, quote: str) -> str:
        fast = (_FAST_DQ if quote == '"' else _FAST_SQ).match(self.t, self.pos)
        if fast:
            self.pos = fast.end()
            return fast.group(1)
        start = self.pos
        t, n = self.t, self.n
        i = self.pos + 1
        buf = bytearray()
        seg = i
        while True:
            if i >= n:
                self.error("unfinished string", start)
            c = t[i]
            if c == quote:
                buf += t[seg:i].encode("utf-8", "replace")
                self.pos = i + 1
                return buf.decode("utf-8", errors="replace")
            if c == "\n" or c == "\r":
                self.error("unfinished string (newline inside quotes)", start)
            if c != "\\":
                i += 1
                continue
            buf += t[seg:i].encode("utf-8", "replace")
            i += 1
            if i >= n:
                self.error("unfinished string", start)
            e = t[i]
            if e in _SIMPLE_ESCAPES:
                buf.append(_SIMPLE_ESCAPES[e])
                i += 1
            elif e == "\n" or e == "\r":
                buf.append(10)
                i += 1
                if i < n and t[i] in "\r\n" and t[i] != e:
                    i += 1
            elif e in _DIGITS:
                j = i
                while j < n and j < i + 3 and t[j] in _DIGITS:
                    j += 1
                code = int(t[i:j])
                if code > 255:
                    self.error("escape sequence too large", i - 1)
                buf.append(code)
                i = j
            else:
                # Lua 5.1 keeps an unknown escaped character as itself.
                buf += e.encode("utf-8", "replace")
                i += 1
            seg = i

    def key_value(self, depth: int):
        """A table key inside [ ]: string, number or boolean."""
        k = self.value(depth)
        if k is None:
            self.error("table key is nil")
        if isinstance(k, bool):
            self.error("boolean table keys are not supported")
        if isinstance(k, float) and math.isnan(k):
            self.error("table key is NaN")
        if isinstance(k, (dict, list)):
            self.error("table keys must be strings or numbers")
        return k

    def table(self, depth: int):
        if depth > MAX_DEPTH:
            self.error(f"tables nested deeper than {MAX_DEPTH}")
        t = self.t
        self.pos += 1  # '{'
        hashpart = {}
        positional = []
        while True:
            self.skip()
            if self.pos >= self.n:
                self.error("unfinished table (missing '}')")
            c = t[self.pos]
            if c == "}":
                self.pos += 1
                break
            if c == "[" and self._long_level(self.pos) is None:
                self.pos += 1
                key = self.key_value(depth)
                self.skip()
                if not t.startswith("]", self.pos):
                    self.error("expected ']' after table key")
                self.pos += 1
                self.skip()
                if not t.startswith("=", self.pos):
                    self.error("expected '=' after ']'")
                self.pos += 1
                val = self.value(depth)
                if val is None:
                    hashpart.pop(key, None)
                else:
                    hashpart[key] = val
            else:
                m = _NAME_RE.match(t, self.pos)
                is_field = False
                if m and m.group(0) not in ("true", "false", "nil"):
                    save = self.pos
                    self.pos = m.end()
                    self.skip()
                    if self.pos < self.n and t[self.pos] == "=" and not t.startswith("==", self.pos):
                        is_field = True
                    else:
                        self.pos = save
                if is_field:
                    key = m.group(0)
                    if key in LUA_KEYWORDS:
                        self.error(f"reserved word {key!r} used as a table key", m.start())
                    self.pos += 1  # '='
                    val = self.value(depth)
                    if val is None:
                        hashpart.pop(key, None)
                    else:
                        hashpart[key] = val
                else:
                    positional.append(self.value(depth))
            self.skip()
            if self.pos >= self.n:
                self.error("unfinished table (missing '}')")
            c = t[self.pos]
            if c == "," or c == ";":
                self.pos += 1
                continue
            if c == "}":
                self.pos += 1
                break
            self.error("expected ',' or '}' in table")
        result = hashpart
        for i, v in enumerate(positional, 1):
            if v is None:
                result.pop(i, None)
            else:
                result[i] = v
        return _shape(result)

    def chunk(self) -> dict:
        out = {}
        while True:
            self.skip()
            if self.pos >= self.n:
                return out
            if self.t[self.pos] == ";":
                self.pos += 1
                continue
            m = _NAME_RE.match(self.t, self.pos)
            if not m or m.group(0) in LUA_KEYWORDS:
                self.error("expected 'Name = value'")
            name = m.group(0)
            self.pos = m.end()
            self.skip()
            if not self.t.startswith("=", self.pos) or self.t.startswith("==", self.pos):
                self.error(f"expected '=' after {name!r}")
            self.pos += 1
            val = self.value(0)
            if val is None:
                out.pop(name, None)
            else:
                out[name] = val


def _shape(d: dict):
    """Keys exactly 1..n -> list; everything else stays a dict ({} stays {})."""
    n = len(d)
    if n == 0:
        return d
    for k in d:
        if type(k) is not int or k < 1 or k > n:
            return d
    return [d[i] for i in range(1, n + 1)]


def loads(text) -> dict:
    """Parse SavedVariables text (str or bytes). Returns {name: value}."""
    if isinstance(text, (bytes, bytearray)):
        text = bytes(text).decode("utf-8", errors="replace")
    if not isinstance(text, str):
        raise TypeError("loads() needs str or bytes")
    if text.startswith("\ufeff"):
        text = text[1:]
    return _Parser(text).chunk()


def load(path) -> dict:
    return loads(Path(path).read_bytes())


def find_assignment(text, name: str):
    """The value of a line-start ``name = <data>`` in a file that may also hold
    code (e.g. ``local ADDON, ns = ...``). Only that value is parsed, as data."""
    if isinstance(text, (bytes, bytearray)):
        text = bytes(text).decode("utf-8", errors="replace")
    if text.startswith("\ufeff"):
        text = text[1:]
    m = re.compile(r"(?m)^[ \t]*" + re.escape(name) + r"[ \t]*=(?!=)").search(text)
    if not m:
        raise LuaSVError(f"no '{name} = ...' assignment found")
    p = _Parser(text)
    p.pos = m.end()
    return p.value(0)


# ---- writer -----------------------------------------------------------------

def _quote(s: str) -> str:
    out = ['"']
    for ch in s:
        o = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif o < 32 or o == 127:
            out.append("\\%03d" % o)
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _num(v) -> str:
    if isinstance(v, bool):
        raise TypeError("bool is not a number")
    if isinstance(v, int):
        return str(v)
    if not math.isfinite(v):
        raise ValueError(f"cannot write non-finite number {v!r}")
    if v == math.floor(v) and abs(v) <= _MAX_EXACT_FLOAT:
        return str(int(v))
    return repr(float(v))


def _key_sort(k):
    if isinstance(k, (int, float)) and not isinstance(k, bool):
        return (0, k, "")
    return (1, 0, str(k))


def _key(k) -> str:
    if isinstance(k, bool) or k is None:
        raise TypeError(f"unsupported table key {k!r}")
    if isinstance(k, str):
        return k if is_identifier(k) else "[" + _quote(k) + "]"
    if isinstance(k, (int, float)):
        return "[" + _num(k) + "]"
    raise TypeError(f"unsupported table key type {type(k).__name__}")


def _dump(v, level: int) -> str:
    if v is None:
        return "nil"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, (int, float)):
        return _num(v)
    if isinstance(v, str):
        return _quote(v)
    pad = "  " * (level + 1)
    end = "  " * level
    if isinstance(v, (list, tuple)):
        if not v:
            return "{}"
        parts = []
        for item in v:
            if item is None:
                raise ValueError("cannot write nil inside a list (Lua lists have no holes)")
            parts.append(pad + _dump(item, level + 1) + ",\n")
        return "{\n" + "".join(parts) + end + "}"
    if isinstance(v, dict):
        keys = [k for k in v if v[k] is not None]
        if not keys:
            return "{}"
        try:
            keys.sort(key=_key_sort)
        except TypeError as exc:
            raise TypeError(f"table keys cannot be sorted: {exc}") from exc
        parts = [pad + _key(k) + " = " + _dump(v[k], level + 1) + ",\n" for k in keys]
        return "{\n" + "".join(parts) + end + "}"
    raise TypeError(f"cannot write {type(v).__name__} to Lua")


def dumps(value, level: int = 0) -> str:
    """Lua source for a single value."""
    return _dump(value, level)


def dump(name: str, value) -> str:
    """``name = <value>`` plus a trailing newline, deterministic."""
    if not is_identifier(name):
        raise ValueError(f"{name!r} is not a valid Lua global name")
    return f"{name} = {_dump(value, 0)}\n"


def canonical(v):
    """What ``loads(dump(...))`` gives back for v (lists/dicts/ints normalized)."""
    if isinstance(v, bool) or v is None or isinstance(v, str):
        return v
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return _normalize_float(v)
    if isinstance(v, (list, tuple)):
        return _shape({i: canonical(x) for i, x in enumerate(v, 1)})
    if isinstance(v, dict):
        d = {}
        for k, x in v.items():
            if x is None:
                continue
            if isinstance(k, float):
                k = _normalize_float(k)
            d[k] = canonical(x)
        return _shape(d)
    raise TypeError(f"cannot canonicalize {type(v).__name__}")
