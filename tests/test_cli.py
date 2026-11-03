"""kicad-cli runner behaviour that does not need KiCad installed."""

import shutil
import subprocess
from pathlib import Path

import pytest

from kicad_adapter import DockerRunner, KiCadCli, KiCadCliError, NativeRunner
from kicad_adapter.__main__ import main


class FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def test_detect_returns_none_without_kicad(monkeypatch):
    for var in ("AICS_KICAD_CLI", "AICS_KICAD_DOCKER_IMAGE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    monkeypatch.setattr("kicad_adapter.cli._windows_default", lambda: None)
    assert KiCadCli.detect() is None


def test_detect_prefers_explicit_executable(monkeypatch):
    monkeypatch.setenv("AICS_KICAD_CLI", r"C:\KiCad\bin\kicad-cli.exe")
    cli = KiCadCli.detect()
    assert isinstance(cli.runner, NativeRunner)


def test_native_runner_passes_an_argument_list(monkeypatch, tmp_path):
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"], seen["kwargs"] = argv, kwargs
        return FakeProc(stdout="10.0.8\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    sch = tmp_path / "a b.kicad_sch"   # space must survive as one argument
    NativeRunner("kicad-cli").run(["sch", "upgrade", sch], timeout=5)
    assert seen["argv"] == ["kicad-cli", "sch", "upgrade", str(sch)]
    assert "shell" not in seen["kwargs"]


def test_docker_runner_maps_paths_into_work(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: seen.setdefault("argv", argv) and FakeProc())
    out = tmp_path / "outputs"
    out.mkdir()
    sch = out / "schematic.kicad_sch"
    sch.write_text("x")
    DockerRunner("kicad/kicad:10.0.8").run(["sch", "erc", "--output", out / "erc.json", sch], timeout=5)
    argv = seen["argv"]
    assert argv[:5] == ["docker", "run", "--rm", "--network", "none"]
    assert f"{out.resolve()}:/work" in argv
    assert argv[-6:] == ["kicad-cli", "sch", "erc", "--output", "/work/erc.json", "/work/schematic.kicad_sch"]


def test_failed_command_raises_with_stderr(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: FakeProc(2, "", "boom"))
    with pytest.raises(KiCadCliError, match="boom"):
        KiCadCli(NativeRunner("kicad-cli")).version()


def test_generate_command_without_kicad_reports_not_checked(monkeypatch, tmp_path, capsys):
    from .conftest import LED_CIRCUIT, SYMBOL_DIR

    monkeypatch.setattr(KiCadCli, "detect", classmethod(lambda cls: None))
    code = main(["generate", str(LED_CIRCUIT), "--out", str(tmp_path),
                 "--symbol-dir", str(SYMBOL_DIR), "--check"])
    assert code == 3
    assert (tmp_path / "schematic.kicad_sch").is_file()
    assert "checks NOT run" in capsys.readouterr().err
    assert not (tmp_path / "check_report.json").exists()


def test_generate_command_rejects_invalid_ir(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text('{"project_id": "x", "components": []}')
    assert main(["generate", str(bad), "--out", str(tmp_path / "o"), "--symbol-dir", "."]) == 2
    assert "Circuit IR is invalid" in capsys.readouterr().err
    assert not Path(tmp_path / "o").exists()
