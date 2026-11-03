"""Copy selected symbols out of a KiCad symbol library into a small library file.

Used to build the hermetic test fixtures in tests/fixtures/kicad_symbols from
an installed KiCad (see the README in that folder).

    python scripts/extract_symbol_subset.py <full Device.kicad_sym> <out.kicad_sym> R LED
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kicad_adapter import sexpr  # noqa: E402
from kicad_adapter.sexpr import children, head, unquote  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    src, dst, names = Path(argv[0]), Path(argv[1]), argv[2:]
    root = sexpr.parse(src.read_text(encoding="utf-8"))
    table = {unquote(s[1]): s for s in children(root, "symbol")}
    missing = [n for n in names if n not in table]
    if missing:
        print(f"not found in {src}: {', '.join(missing)}", file=sys.stderr)
        return 1
    out = [root[0]] + [n for n in root[1:] if head(n) != "symbol"] + [table[n] for n in names]
    dst.write_text(sexpr.dumps(out), encoding="utf-8", newline="\n")
    print(f"wrote {dst} ({', '.join(names)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
