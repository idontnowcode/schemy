"""Read symbol definitions and pin geometry from installed KiCad libraries.

Pin numbers, names and positions always come from the real ``.kicad_sym``
file; nothing about a symbol is assumed. The parsed symbol is also what gets
embedded into the generated schematic's ``lib_symbols`` cache.

Derived symbols (``extends``) are flattened onto their parent, as KiCad does in a schematic.

PoC limits (reported as errors, never silently ignored):
- multi-unit symbols are not supported;
- only the KiCad 6+ single-file ``.kicad_sym`` library format is read
  (KiCad 10 ``.kicad_symdir`` directories are not).
"""

from __future__ import annotations

import os
import platform
import re
import shutil
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from . import sexpr
from .sexpr import child, children, head, unquote


class SymbolLibraryError(LookupError):
    pass


@dataclass(frozen=True)
class LibPin:
    number: str
    name: str
    electrical_type: str
    x: float
    """Connection point in library coordinates (mm, Y axis points up)."""
    y: float
    angle: int
    """Direction from the connection point towards the symbol body (degrees, CCW)."""
    length: float
    unit: int
    body_style: int


@dataclass(frozen=True)
class LibField:
    name: str
    value: str
    x: float
    y: float
    angle: float
    hidden: bool
    effects: list | None


@dataclass(frozen=True)
class LibSymbol:
    lib_id: str
    node: list
    pins: tuple[LibPin, ...]
    fields: tuple[LibField, ...]
    source_file: Path

    def pin(self, number: str) -> LibPin | None:
        for p in self.pins:
            if p.number == number:
                return p
        return None

    def field(self, name: str) -> LibField | None:
        for f in self.fields:
            if f.name == name:
                return f
        return None

    def embedded_node(self) -> list:
        """Copy of the definition named ``<nickname>:<symbol>`` for ``lib_symbols``."""
        node = sexpr.deepcopy(self.node)
        node[1] = sexpr.q(self.lib_id)
        return node


_UNIT_RE = re.compile(r"^(?P<base>.+)_(?P<unit>\d+)_(?P<style>\d+)$")


def _parse_symbol(lib_id: str, node: list, source: Path) -> LibSymbol:
    name = unquote(node[1])
    if child(node, "extends") is not None:
        raise SymbolLibraryError(
            f"{lib_id}: derived symbols ('extends') are not supported by the PoC generator"
        )

    pins: list[LibPin] = []
    units: set[int] = set()
    for sub in children(node, "symbol"):
        m = _UNIT_RE.match(unquote(sub[1]))
        if not m or m.group("base") != name:
            raise SymbolLibraryError(f"{lib_id}: unexpected sub-symbol name {sub[1]}")
        unit, style = int(m.group("unit")), int(m.group("style"))
        if unit:
            units.add(unit)
        for pin in children(sub, "pin"):
            at = child(pin, "at")
            length = child(pin, "length")
            pins.append(
                LibPin(
                    number=unquote(child(pin, "number")[1]),
                    name=unquote(child(pin, "name")[1]),
                    electrical_type=pin[1],
                    x=float(at[1]),
                    y=float(at[2]),
                    angle=int(float(at[3])) % 360 if len(at) > 3 else 0,
                    length=float(length[1]) if length else 0.0,
                    unit=unit,
                    body_style=style,
                )
            )
    if len(units) > 1:
        raise SymbolLibraryError(f"{lib_id}: multi-unit symbols are not supported by the PoC generator")

    fields: list[LibField] = []
    for prop in children(node, "property"):
        at = child(prop, "at")
        hide = child(prop, "hide")
        fields.append(
            LibField(
                name=unquote(prop[1]),
                value=unquote(prop[2]),
                x=float(at[1]),
                y=float(at[2]),
                angle=float(at[3]) if len(at) > 3 else 0.0,
                hidden=bool(hide and hide[1:] == ["yes"]),
                effects=child(prop, "effects"),
            )
        )
    # De Morgan alternates (body style 2) duplicate the normal pins; keep style 1.
    pins = [p for p in pins if p.body_style in (0, 1)]
    return LibSymbol(lib_id, node, tuple(pins), tuple(fields), source)


def _flatten(table: dict[str, list], name: str, path: Path, depth: int = 0) -> list:
    """A derived symbol (``extends``) as one self-contained definition, the way KiCad stores it in a
    schematic's symbol cache: the parent's graphics and pins under the derived name, with the derived
    symbol's own properties and flags taking precedence."""
    node = table[name]
    ext = child(node, "extends")
    if ext is None:
        return node
    parent_name = unquote(ext[1])
    if depth > 4 or parent_name not in table:
        raise SymbolLibraryError(f"{path.name}:{name}: parent symbol '{parent_name}' not found")
    parent = sexpr.deepcopy(_flatten(table, parent_name, path, depth + 1))
    own_props = {unquote(p[1]): p for p in children(node, "property")}
    own_flags = {head(n): n for n in node[2:]
                 if isinstance(n, list) and head(n) not in ("property", "extends", "symbol")}
    flat: list = ["symbol", sexpr.q(name)]
    used_props: set[str] = set()
    for item in parent[2:]:
        kind = head(item) if isinstance(item, list) else None
        if kind == "property":
            pname = unquote(item[1])
            flat.append(sexpr.deepcopy(own_props.get(pname, item)))
            used_props.add(pname)
        elif kind == "symbol":
            sub = sexpr.deepcopy(item)
            m = _UNIT_RE.match(unquote(sub[1]))
            if m:
                sub[1] = sexpr.q(f"{name}_{m.group('unit')}_{m.group('style')}")
            flat.append(sub)
        elif kind in own_flags:
            flat.append(sexpr.deepcopy(own_flags[kind]))
        else:
            flat.append(sexpr.deepcopy(item))
    # Properties only the derived symbol has go after the parent's, before the unit sub-symbols.
    extra = [sexpr.deepcopy(p) for n, p in own_props.items() if n not in used_props]
    if extra:
        first_sub = next((i for i, it in enumerate(flat) if isinstance(it, list) and head(it) == "symbol"), len(flat))
        flat[first_sub:first_sub] = extra
    return flat


def windows_kicad_roots() -> list[Path]:
    """KiCad 10 install folders on Windows: all-users (Program Files) and per-user (AppData) installs."""
    roots = []
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramW6432"),
                 os.path.join(os.environ["LOCALAPPDATA"], "Programs") if os.environ.get("LOCALAPPDATA") else None):
        if base:
            root = Path(base) / "KiCad" / "10.0"
            if root not in roots:
                roots.append(root)
    return roots


def default_symbol_dirs() -> list[Path]:
    """Candidate KiCad 10 symbol directories for this machine (may not exist)."""
    dirs: list[Path] = []
    for var in ("KICAD10_SYMBOL_DIR", "KICAD_SYMBOL_DIR"):
        if os.environ.get(var):
            dirs.append(Path(os.environ[var]))
    # Next to the kicad-cli the app uses: <KiCad>/bin/kicad-cli(.exe) -> <KiCad>/share/kicad/symbols.
    for exe in (os.environ.get("AICS_KICAD_CLI"), shutil.which("kicad-cli")):
        if exe:
            dirs.append(Path(exe).resolve().parent.parent / "share" / "kicad" / "symbols")
    system = platform.system()
    if system == "Windows":
        for root in windows_kicad_roots():
            dirs.append(root / "share" / "kicad" / "symbols")
    elif system == "Darwin":
        dirs.append(Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols"))
    else:
        dirs += [Path("/usr/share/kicad/symbols"), Path("/usr/local/share/kicad/symbols")]
    return dirs


class SymbolLibrary:
    """Looks up ``<nickname>:<symbol>`` in ``<dir>/<nickname>.kicad_sym``."""

    def __init__(self, search_dirs: Iterable[Path | str]):
        self.search_dirs: Sequence[Path] = [Path(d) for d in search_dirs]
        self._files: dict[str, tuple[Path, dict[str, list], list]] = {}
        self._symbols: dict[str, LibSymbol] = {}

    @classmethod
    def default(cls) -> "SymbolLibrary":
        return cls([d for d in default_symbol_dirs() if d.is_dir()])

    def _load_file(self, nickname: str) -> tuple[Path, dict[str, list], list]:
        if nickname not in self._files:
            for d in self.search_dirs:
                path = d / f"{nickname}.kicad_sym"
                if path.is_file():
                    root = sexpr.parse(path.read_text(encoding="utf-8"))
                    if head(root) != "kicad_symbol_lib":
                        raise SymbolLibraryError(f"{path} is not a KiCad symbol library")
                    table = {unquote(s[1]): s for s in children(root, "symbol")}
                    header = [n for n in root[1:] if head(n) in ("version", "generator_version")]
                    self._files[nickname] = (path, table, header)
                    break
            else:
                searched = ", ".join(str(d) for d in self.search_dirs) or "(none)"
                raise SymbolLibraryError(
                    f"symbol library '{nickname}.kicad_sym' not found; searched: {searched}"
                )
        return self._files[nickname]

    def get(self, lib_id: str) -> LibSymbol:
        if lib_id not in self._symbols:
            nickname, _, name = lib_id.partition(":")
            if not nickname or not name:
                raise SymbolLibraryError(f"invalid lib_id '{lib_id}'")
            path, table, _ = self._load_file(nickname)
            if name not in table:
                raise SymbolLibraryError(f"symbol '{name}' not found in {path}")
            self._symbols[lib_id] = _parse_symbol(lib_id, _flatten(table, name, path), path)
        return self._symbols[lib_id]

    def properties(self, lib_id: str) -> dict[str, str]:
        """Field values of a symbol as written in the library (also works for derived symbols,
        which :meth:`get` refuses). Raises :class:`SymbolLibraryError` if the symbol is unknown."""
        nickname, _, name = lib_id.partition(":")
        if not nickname or not name:
            raise SymbolLibraryError(f"invalid lib_id '{lib_id}'")
        path, table, _ = self._load_file(nickname)
        if name not in table:
            raise SymbolLibraryError(f"symbol '{name}' not found in {path}")
        return {unquote(prop[1]): unquote(prop[2]) for prop in children(table[name], "property")}

    def datasheet_url(self, lib_id: str) -> str | None:
        """The symbol's ``Datasheet`` field if it is an http(s) link, else None ("~", empty, local path)."""
        try:
            value = self.properties(lib_id).get("Datasheet", "").strip()
        except SymbolLibraryError:
            return None
        return value if re.match(r"^https?://\S+$", value) else None

    def provenance(self, nickname: str) -> dict[str, str]:
        """Path and format version of a library file, for recording where a symbol came from."""
        path, _, header = self._load_file(nickname)
        info = {"path": str(path)}
        for node in header:
            info[node[0]] = unquote(node[1])
        return info
