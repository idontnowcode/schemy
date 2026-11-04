"""A searchable index of the installed KiCad symbol libraries.

The full KiCad 10 library is about 220 MB in 223 files, too much to parse on every request.
This module scans the files once with light-weight patterns (no full S-expression parse), keeps
name, description, keywords, footprint, datasheet link, unit and pin counts per symbol, and caches
the result keyed by file size and modification time. Exact pin data always comes from
:class:`kicad_adapter.symbols.SymbolLibrary` once a symbol is chosen.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

_BLOCK = re.compile(r'^\t\(symbol "((?:[^"\\]|\\.)*)"', re.M)
_PROP = re.compile(r'^\t\t\(property "(Value|Description|ki_keywords|Footprint|Datasheet)" "((?:[^"\\]|\\.)*)"', re.M)
_EXTENDS = re.compile(r'^\t\t\(extends "((?:[^"\\]|\\.)*)"', re.M)
_POWER = re.compile(r"^\t\t\(power\b", re.M)
_UNIT = re.compile(r'^\t\t\(symbol "(?:[^"\\]|\\.)*_(\d+)_(\d+)"', re.M)
_PIN = re.compile(r"^\t\t\t\(pin ", re.M)
CACHE_VERSION = 1


@dataclass
class IndexedSymbol:
    lib_id: str
    description: str
    keywords: str
    footprint: str
    datasheet: str
    units: int
    pins: int
    power: bool
    extends: str | None


def _unquote(s: str) -> str:
    return s.replace('\\"', '"').replace("\\\\", "\\")


def scan_file(path: Path) -> list[IndexedSymbol]:
    text = path.read_text(encoding="utf-8")
    lib = path.stem
    starts = [(m.start(), _unquote(m.group(1))) for m in _BLOCK.finditer(text)]
    raw: dict[str, dict] = {}
    for i, (pos, name) in enumerate(starts):
        block = text[pos: starts[i + 1][0] if i + 1 < len(starts) else len(text)]
        props = {k: _unquote(v) for k, v in _PROP.findall(block)}
        ext = _EXTENDS.search(block)
        raw[name] = {
            "props": props, "extends": _unquote(ext.group(1)) if ext else None, "power": bool(_POWER.search(block)),
            "units": len({u for u, _ in _UNIT.findall(block) if u != "0"}) or 1, "pins": len(_PIN.findall(block)),
        }
    out = []
    for name, r in raw.items():
        base = raw.get(r["extends"]) if r["extends"] else None
        props = {**(base["props"] if base else {}), **r["props"]}
        out.append(IndexedSymbol(
            lib_id=f"{lib}:{name}", description=props.get("Description", ""), keywords=props.get("ki_keywords", ""),
            footprint=props.get("Footprint", ""), datasheet=props.get("Datasheet", ""),
            units=base["units"] if base else r["units"], pins=base["pins"] if base else r["pins"],
            power=r["power"] or bool(base and base["power"]), extends=r["extends"]))
    return out


def _norm(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", s.upper())


def _wildcard(name: str) -> re.Pattern | None:
    """KiCad names stand for a family with lower-case 'x' ('STM32F103C8Tx'): match any one character."""
    if "x" not in name:
        return None
    parts = ["[A-Z0-9]" if ch == "x" else re.escape(ch.upper()) for ch in name if ch.isalnum()]
    return re.compile("^" + "".join(parts) + "$")


class SymbolIndex:
    def __init__(self, symbols: list[IndexedSymbol]):
        self.symbols = symbols
        self._by_id = {s.lib_id: s for s in symbols}
        self._names = [(s, _norm(s.lib_id.split(":", 1)[1]), _wildcard(s.lib_id.split(":", 1)[1])) for s in symbols]

    @classmethod
    def build(cls, search_dirs: list[Path], cache: Path | None = None) -> "SymbolIndex":
        files = sorted({p.resolve(): p for d in search_dirs if Path(d).is_dir()
                        for p in Path(d).glob("*.kicad_sym")}.values(), key=lambda p: (p.stem, str(p)))
        cached: dict = {}
        if cache and cache.is_file():
            try:
                data = json.loads(cache.read_text(encoding="utf-8"))
                if data.get("version") == CACHE_VERSION:
                    cached = data.get("files", {})
            except (ValueError, OSError):
                cached = {}
        fresh: dict = {}
        symbols: list[IndexedSymbol] = []
        seen_libs: set[str] = set()
        for f in files:
            if f.stem in seen_libs:  # the first directory wins, as in SymbolLibrary
                continue
            seen_libs.add(f.stem)
            st = f.stat()
            key = str(f.resolve())
            entry = cached.get(key)
            if entry and entry["size"] == st.st_size and entry["mtime"] == st.st_mtime:
                items = [IndexedSymbol(**s) for s in entry["symbols"]]
            else:
                items = scan_file(f)
            fresh[key] = {"size": st.st_size, "mtime": st.st_mtime, "symbols": [asdict(s) for s in items]}
            symbols += items
        if cache is not None and fresh != cached:
            cache.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache.with_suffix(".tmp")
            tmp.write_text(json.dumps({"version": CACHE_VERSION, "files": fresh}), encoding="utf-8")
            tmp.replace(cache)
        return cls(symbols)

    def get(self, lib_id: str) -> IndexedSymbol | None:
        return self._by_id.get(lib_id)

    def search(self, terms: list[str], *, limit: int = 8,
               single_unit_only: bool = True) -> list[tuple[int, IndexedSymbol]]:
        """Rank symbols for a part: an exact or wildcard name match of a part number first
        ('SN65HVD230DR' finds 'SN65HVD230', 'STM32F103C8T6' finds 'STM32F103C8Tx'), then keyword overlap."""
        queries = [t for t in terms if t and t.strip()]
        norm_queries = [_norm(q) for q in queries if len(_norm(q)) >= 3]
        words = {w for q in queries for w in re.findall(r"[a-z0-9.+-]{2,}", q.lower())}
        scored = []
        for s, n, wild in self._names:
            if s.power or (single_unit_only and s.units > 1):
                continue
            name = s.lib_id.split(":", 1)[1]
            score = 0
            for q in norm_queries:
                if q == n:
                    score = max(score, 100)
                elif wild is not None and wild.match(q):
                    score = max(score, 95)
                elif len(n) >= 4 and q.startswith(n):
                    score = max(score, 70 + min(len(n), 20))
                elif len(q) >= 5 and n.startswith(q):
                    score = max(score, 55)
            if words:
                hay = f"{s.keywords} {s.description} {name}".lower()
                score += 4 * sum(1 for w in words if w in hay)
            if score >= 8:
                scored.append((score, s))
        scored.sort(key=lambda x: (-x[0], x[1].lib_id))
        return scored[:limit]
