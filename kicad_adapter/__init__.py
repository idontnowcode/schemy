"""Circuit IR -> KiCad: schematic generation and kicad-cli checks."""

from .cli import DockerRunner, KiCadCli, KiCadCliError, NativeRunner
from .erc import ErcReport, parse_erc_report
from .netlist import ConnectivityDiff, Netlist, compare_with_ir, parse_kicad_netlist
from .pipeline import CheckReport, run_checks
from .schematic import (
    SCHEMATIC_FILE_VERSION,
    GeneratedSchematic,
    SchematicGenerationError,
    check_against_library,
    generate_schematic,
    project_file,
)
from .symbols import LibPin, LibSymbol, SymbolLibrary, SymbolLibraryError

__all__ = [
    "SCHEMATIC_FILE_VERSION",
    "CheckReport",
    "ConnectivityDiff",
    "DockerRunner",
    "ErcReport",
    "GeneratedSchematic",
    "KiCadCli",
    "KiCadCliError",
    "LibPin",
    "LibSymbol",
    "NativeRunner",
    "Netlist",
    "SchematicGenerationError",
    "SymbolLibrary",
    "SymbolLibraryError",
    "check_against_library",
    "compare_with_ir",
    "generate_schematic",
    "parse_erc_report",
    "parse_kicad_netlist",
    "project_file",
    "run_checks",
]
