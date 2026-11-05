"""Phase 0 command line.

    aics generate <circuit.json> --out <dir> [--symbol-dir DIR]... [--check]
    aics kicad-info

``--check`` runs ERC, netlist export + IR comparison, and SVG export through
kicad-cli (native, or Docker via AICS_KICAD_DOCKER_IMAGE).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from circuit_core import CircuitIR

from .cli import KiCadCli, KiCadCliError
from .pipeline import run_checks
from .schematic import SchematicGenerationError, generate_schematic
from .symbols import SymbolLibrary, default_symbol_dirs


def _cmd_generate(args: argparse.Namespace) -> int:
    try:
        circuit = CircuitIR.model_validate_json(Path(args.circuit).read_text(encoding="utf-8"))
    except ValidationError as exc:
        print(f"Circuit IR is invalid:\n{exc}", file=sys.stderr)
        return 2

    dirs = [Path(d) for d in args.symbol_dir] or [d for d in default_symbol_dirs() if d.is_dir()]
    if not dirs:
        print("No KiCad symbol directory found. Pass --symbol-dir or set KICAD10_SYMBOL_DIR.",
              file=sys.stderr)
        return 2
    library = SymbolLibrary(dirs)

    try:
        generated = generate_schematic(circuit, library, project_name=args.project_name)
    except SchematicGenerationError as exc:
        print(exc, file=sys.stderr)
        return 1

    out = Path(args.out)
    sch = generated.write(out)
    (out / "symbol_sources.json").write_text(
        json.dumps(generated.symbol_sources, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {sch}")
    for w in generated.warnings:
        print(f"warning [{w.error_code}] {w.message}")

    if not args.check:
        return 0
    cli = KiCadCli.detect()
    if cli is None:
        print("kicad-cli not found: checks NOT run (status: not checked). "
              "Install KiCad 10 or set AICS_KICAD_CLI / AICS_KICAD_DOCKER_IMAGE.", file=sys.stderr)
        return 3
    try:
        report = run_checks(circuit, generated, sch, cli)
    except KiCadCliError as exc:
        print(f"kicad-cli check failed: {exc}", file=sys.stderr)
        return 1

    print(f"kicad-cli: {report.kicad}")
    print(f"ERC: {len(report.erc.errors)} error(s), {len(report.erc.warnings)} warning(s)")
    for v in report.erc.violations:
        print(f"  {v.severity}: [{v.type}] {v.description} {list(v.items)}")
    print(f"netlist vs IR: {report.connectivity.describe()}")
    print(f"svg: {report.svg}")
    print(f"status: {report.status_label()} (not an engineering review)")
    return 0 if report.automated_checks_passed else 1


def _cmd_kicad_info(_: argparse.Namespace) -> int:
    cli = KiCadCli.detect()
    if cli is None:
        print("kicad-cli: not found")
        return 1
    print(f"kicad-cli: {cli.version()} ({cli.describe()})")
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    from circuit_core.rationale import rationale_markdown

    try:
        circuit = CircuitIR.model_validate_json(Path(args.circuit).read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        print(f"cannot read {args.circuit}: {exc}", file=sys.stderr)
        return 2
    requirements = Path(args.requirements).read_text(encoding="utf-8") if args.requirements else None
    text = rationale_markdown(circuit, requirements=requirements) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {args.out}")
    else:
        sys.stdout.write(text)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aics", description="AI Circuit Studio - Phase 0 PoC")
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="Circuit IR JSON -> KiCad schematic")
    gen.add_argument("circuit", help="Circuit IR JSON file")
    gen.add_argument("--out", required=True, help="output directory")
    gen.add_argument("--symbol-dir", action="append", default=[],
                     help="KiCad symbol library directory (repeatable)")
    gen.add_argument("--project-name", default="schematic")
    gen.add_argument("--check", action="store_true", help="run ERC / netlist / SVG via kicad-cli")
    gen.set_defaults(func=_cmd_generate)

    rep = sub.add_parser("report", help="Circuit IR JSON -> rationale report (Markdown)")
    rep.add_argument("circuit", help="Circuit IR JSON file")
    rep.add_argument("--requirements", help="requirements text to quote in the report")
    rep.add_argument("--out", help="write here instead of stdout")
    rep.set_defaults(func=_cmd_report)

    info = sub.add_parser("kicad-info", help="show which kicad-cli would be used")
    info.set_defaults(func=_cmd_kicad_info)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
