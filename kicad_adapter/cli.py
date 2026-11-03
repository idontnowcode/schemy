"""Run ``kicad-cli`` (ERC, netlist, SVG, upgrade) as a subprocess.

Commands are always passed as argument lists - never through a shell string
(plan section 15, rule 5). Two runners exist:

- ``NativeRunner``: an installed ``kicad-cli`` (the normal Windows setup).
- ``DockerRunner``: the official ``kicad/kicad`` image, for Linux dev/CI
  machines without a KiCad install. The directory holding the files is
  bind-mounted at ``/work`` and the container has no network.

Detection order: ``AICS_KICAD_CLI`` (path to an executable), ``kicad-cli`` on
PATH, the default Windows install path, then ``AICS_KICAD_DOCKER_IMAGE``.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .erc import ErcReport, parse_erc_report

DEFAULT_TIMEOUT_S = 180


class KiCadCliError(RuntimeError):
    def __init__(self, message: str, result: "CliResult | None" = None):
        self.result = result
        detail = ""
        if result is not None:
            detail = f"\n  command: {' '.join(result.args)}\n  exit: {result.returncode}"
            if result.stderr.strip():
                detail += f"\n  stderr: {result.stderr.strip()[:2000]}"
        super().__init__(message + detail)


@dataclass(frozen=True)
class CliResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class NativeRunner:
    def __init__(self, executable: str | Path):
        self.executable = str(executable)

    def describe(self) -> str:
        return f"native: {self.executable}"

    def run(self, args: list[str | Path], timeout: float) -> CliResult:
        argv = [self.executable, *(str(a) for a in args)]
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
        return CliResult(tuple(argv), proc.returncode, proc.stdout, proc.stderr)


class DockerRunner:
    def __init__(self, image: str, docker: str = "docker"):
        self.image = image
        self.docker = docker

    def describe(self) -> str:
        return f"docker: {self.image}"

    def run(self, args: list[str | Path], timeout: float) -> CliResult:
        paths = [Path(a).resolve() for a in args if isinstance(a, Path)]
        if not paths:
            mount_root = None
        else:
            mount_root = Path(os.path.commonpath([p if p.is_dir() else p.parent for p in paths]))

        def to_container(a: str | Path) -> str:
            if not isinstance(a, Path):
                return a
            rel = Path(a).resolve().relative_to(mount_root)
            return str(PurePosixPath("/work", *rel.parts))

        argv = [self.docker, "run", "--rm", "--network", "none"]
        if hasattr(os, "getuid"):
            argv += ["--user", f"{os.getuid()}:{os.getgid()}", "--env", "HOME=/home/kicad"]
        if mount_root is not None:
            argv += ["--volume", f"{mount_root}:/work", "--workdir", "/work"]
        argv += [self.image, "kicad-cli", *(to_container(a) for a in args)]
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
        return CliResult(tuple(argv), proc.returncode, proc.stdout, proc.stderr)


def _windows_default() -> Path | None:
    if platform.system() != "Windows":
        return None
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    candidate = Path(pf) / "KiCad" / "10.0" / "bin" / "kicad-cli.exe"
    return candidate if candidate.is_file() else None


class KiCadCli:
    def __init__(self, runner: NativeRunner | DockerRunner, timeout: float = DEFAULT_TIMEOUT_S):
        self.runner = runner
        self.timeout = timeout

    @classmethod
    def detect(cls) -> "KiCadCli | None":
        if exe := os.environ.get("AICS_KICAD_CLI"):
            return cls(NativeRunner(exe))
        if exe := shutil.which("kicad-cli"):
            return cls(NativeRunner(exe))
        if exe := _windows_default():
            return cls(NativeRunner(exe))
        image = os.environ.get("AICS_KICAD_DOCKER_IMAGE")
        if image and shutil.which("docker"):
            return cls(DockerRunner(image))
        return None

    def describe(self) -> str:
        return self.runner.describe()

    def _run(self, args: list[str | Path], *, ok_codes: tuple[int, ...] = (0,)) -> CliResult:
        try:
            result = self.runner.run(args, self.timeout)
        except FileNotFoundError as exc:
            raise KiCadCliError(f"cannot start kicad-cli ({self.describe()}): {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise KiCadCliError(f"kicad-cli timed out after {self.timeout}s") from exc
        if result.returncode not in ok_codes:
            raise KiCadCliError("kicad-cli failed", result)
        return result

    def version(self) -> str:
        return self._run(["version"]).stdout.strip()

    def erc(self, schematic: Path, output: Path) -> ErcReport:
        """Run ERC with all severities and return the parsed JSON report."""
        self._run(["sch", "erc", "--format", "json", "--severity-all",
                   "--units", "mm", "--output", output, schematic])
        if not output.is_file():
            raise KiCadCliError(f"ERC did not write {output}")
        return parse_erc_report(output.read_text(encoding="utf-8"))

    def export_netlist(self, schematic: Path, output: Path) -> str:
        self._run(["sch", "export", "netlist", "--format", "kicadsexpr", "--output", output, schematic])
        if not output.is_file():
            raise KiCadCliError(f"netlist export did not write {output}")
        return output.read_text(encoding="utf-8")

    def export_svg(self, schematic: Path, output_dir: Path) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        self._run(["sch", "export", "svg", "--exclude-drawing-sheet", "--output", output_dir, schematic])
        svg = output_dir / f"{schematic.stem}.svg"
        if not svg.is_file():
            raise KiCadCliError(f"SVG export did not write {svg}")
        return svg

    def upgrade(self, schematic: Path) -> None:
        """Load and re-save the schematic in KiCad's own writer (round-trip check)."""
        self._run(["sch", "upgrade", "--force", schematic])
