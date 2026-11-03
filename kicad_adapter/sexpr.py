"""Minimal S-expression reader/writer for KiCad files.

A node is either a ``list`` of nodes or an atom ``str``. Atoms keep their raw
token text, so numbers and quoted strings round-trip exactly; a quoted string
atom includes its surrounding quotes. Use :func:`q` to build one and
:func:`unquote` to read one.

The writer follows KiCad's layout closely enough to diff against files saved by
KiCad (tab indentation, a list with sub-lists breaks one child per line, runs
of ``(xy ...)`` share a line), but KiCad does not depend on formatting.
"""

from __future__ import annotations

import copy
import math
import re
from collections.abc import Iterator

Node = list | str

_TOKEN = re.compile(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()"]+', re.S)


class SExprError(ValueError):
    pass


def parse(text: str) -> list:
    """Parse a document containing exactly one top-level list."""
    stack: list[list] = []
    result: list | None = None
    for m in _TOKEN.finditer(text):
        tok = m.group()
        if tok == "(":
            stack.append([])
        elif tok == ")":
            if not stack:
                raise SExprError(f"unbalanced ')' at offset {m.start()}")
            node = stack.pop()
            if stack:
                stack[-1].append(node)
            elif result is None:
                result = node
            else:
                raise SExprError("more than one top-level expression")
        else:
            if not stack:
                raise SExprError(f"atom outside of a list at offset {m.start()}")
            stack[-1].append(tok)
    if stack:
        raise SExprError("unbalanced '(' at end of input")
    if result is None:
        raise SExprError("empty document")
    return result


# --- atoms ---------------------------------------------------------------

def q(value: str) -> str:
    """Build a quoted string atom."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def unquote(atom: str) -> str:
    """Return the text of an atom, removing quotes and escapes if quoted."""
    if len(atom) >= 2 and atom[0] == '"' and atom[-1] == '"':
        out, i, body = [], 0, atom[1:-1]
        while i < len(body):
            ch = body[i]
            if ch == "\\" and i + 1 < len(body):
                nxt = body[i + 1]
                out.append("\n" if nxt == "n" else nxt)
                i += 2
            else:
                out.append(ch)
                i += 1
        return "".join(out)
    return atom


def num(value: float) -> str:
    """Format a millimetre value the way KiCad does (<= 4 decimals, no trailing zeros)."""
    if not math.isfinite(value):
        raise SExprError(f"non-finite number {value!r}")
    text = f"{round(value, 4):.4f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


# --- tree helpers ----------------------------------------------------------

def head(node: Node) -> str | None:
    if isinstance(node, list) and node and isinstance(node[0], str):
        return node[0]
    return None


def children(node: list, name: str) -> Iterator[list]:
    for child in node[1:]:
        if head(child) == name:
            yield child


def child(node: list, name: str) -> list | None:
    return next(children(node, name), None)


def deepcopy(node: Node) -> Node:
    return copy.deepcopy(node)


# --- writer -----------------------------------------------------------------

def dumps(node: Node) -> str:
    """Serialise a node; a document ends with a newline."""
    out: list[str] = []
    _write(node, 0, out)
    return "".join(out) + "\n"


def _write(node: Node, depth: int, out: list[str]) -> None:
    if isinstance(node, str):
        out.append(node)
        return
    atoms_end = 0
    while atoms_end < len(node) and isinstance(node[atoms_end], str):
        atoms_end += 1
    out.append("(" + " ".join(node[:atoms_end]))
    rest = node[atoms_end:]
    if not rest:
        out.append(")")
        return
    indent = "\t" * (depth + 1)
    i = 0
    while i < len(rest):
        item = rest[i]
        out.append("\n" + indent)
        if head(item) == "xy":
            # KiCad keeps a run of points on one line: (pts (xy a b) (xy c d))
            run = []
            while i < len(rest) and head(rest[i]) == "xy":
                run.append("(" + " ".join(rest[i]) + ")")
                i += 1
            out.append(" ".join(run))
            continue
        _write(item, depth + 1, out)
        i += 1
    out.append("\n" + "\t" * depth + ")")
