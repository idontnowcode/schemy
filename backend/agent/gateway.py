"""EDA Tool Gateway (plan 9.1): everything an agent run can do, and nothing else.

The agent has no shell and no file access. It can only call these Python functions, by name, with arguments
validated by a Pydantic model; each call runs under a time limit; files are written only inside the run's
own ``workspace/``; the board itself is read-only here (the Revision Service, through an approved proposal,
is the only way anything reaches the board). Changes the agent proposes must lie inside the scope the user
approved; that check is code, not instructions.

Allowed EDA tools (plan 9.1): read_project, read_revision, read_symbol_library, validate_ir, propose_ir_patch,
diff_ir, generate_schematic, run_erc, export_netlist, export_svg, compare_netlists. Two control actions end
or pause a step: ``finish`` and ``request_scope_change``.
"""

from __future__ import annotations

import concurrent.futures
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from circuit_core import CircuitIR, validate_circuit
from circuit_core.diff import diff_circuits
from circuit_core.patch import (
    AddComponent,
    Connect,
    Disconnect,
    Move,
    PatchError,
    PatchOp,
    RemoveComponent,
    RenameNet,
    SetNoConnect,
    SetProperty,
    apply_patch,
)
from kicad_adapter import KiCadCli, SchematicGenerationError, SymbolLibrary, SymbolLibraryError, generate_schematic
from kicad_adapter.cli import KiCadCliError
from kicad_adapter.netlist import compare_with_ir, parse_kicad_netlist
from kicad_adapter.schematic import check_against_library

EDA_TOOLS = ("read_project", "read_revision", "read_symbol_library", "validate_ir", "propose_ir_patch", "diff_ir",
             "generate_schematic", "run_erc", "export_netlist", "export_svg", "compare_netlists")
CONTROL = ("finish", "request_scope_change")
SCOPE_OPS = ("set_property", "add_component", "remove_component", "connect", "disconnect", "rename_net",
             "set_no_connect", "move")
_NAME = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
OPS = TypeAdapter(list[PatchOp])


class ToolError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class ScopeItem(BaseModel):
    """One permitted change: an operation and its target (a component id, or a net name for net operations)."""

    model_config = ConfigDict(extra="forbid")
    op: Literal["set_property", "add_component", "remove_component", "connect", "disconnect", "rename_net",
                "set_no_connect", "move"]
    target: str = Field(min_length=1, max_length=64)
    field: str | None = Field(default=None, description="set_property only: the property; empty = any property.")
    why: str | None = Field(default=None, max_length=500)

    def key(self) -> tuple:
        return (self.op, self.target, self.field or None)


def op_in_scope(op, scope: list[ScopeItem]) -> bool:
    """Code-side scope check. A move (layout only, no meaning) is allowed for any part already in the scope."""
    def has(kind: str, target: str, fld: str | None = None) -> bool:
        return any(s.op == kind and s.target == target and (s.field is None or fld is None or s.field == fld)
                   for s in scope)

    if isinstance(op, SetProperty):
        return has("set_property", op.component_id, op.field)
    if isinstance(op, AddComponent):
        return has("add_component", op.component.id)
    if isinstance(op, RemoveComponent):
        return has("remove_component", op.component_id)
    if isinstance(op, Connect):
        return has("connect", op.net)
    if isinstance(op, Disconnect):
        return has("disconnect", op.net)
    if isinstance(op, RenameNet):
        return has("rename_net", op.net)
    if isinstance(op, SetNoConnect):
        return has("set_no_connect", op.component_id)
    if isinstance(op, Move):
        return has("move", op.component_id) or any(s.target == op.component_id for s in scope)
    return False  # replace_circuit and anything else: never by an agent


def describe_op(op) -> str:
    if isinstance(op, AddComponent):
        return f"add_component {op.component.id} ({op.component.reference} {op.component.value})"
    d = op.model_dump(mode="json")
    kind = d.pop("op")
    return kind + " " + " ".join(f"{k}={v}" for k, v in d.items())


# --- tool arguments --------------------------------------------------------------------------------------

class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoArgs(_Args):
    pass


class RevisionArgs(_Args):
    revision: int = Field(ge=1)


class SymbolArgs(_Args):
    symbol_id: str | None = Field(default=None, max_length=200)
    query: str | None = Field(default=None, max_length=200)


class PatchArgs(_Args):
    ops: list[dict[str, Any]] = Field(min_length=1, max_length=60)


class OptionalPatchArgs(_Args):
    ops: list[dict[str, Any]] | None = Field(default=None, max_length=60)


class NameArgs(_Args):
    name: str = Field(default="candidate", description="File name stem inside the run workspace.")


class FinishArgs(_Args):
    summary: str = Field(min_length=1, max_length=4000)


class ScopeRequestArgs(_Args):
    items: list[ScopeItem] = Field(min_length=1, max_length=30)
    why: str = Field(min_length=1, max_length=2000)


TOOL_SPECS: dict[str, tuple[type[_Args], str, float]] = {
    "read_project": (NoArgs, "기준 revision의 회로: 부품 id·기호·값·심볼·핀별 Net, Net 목록", 20),
    "read_revision": (RevisionArgs, "과거 revision의 회로를 읽기 전용으로 봄", 20),
    "read_symbol_library": (SymbolArgs, "KiCad 심볼의 핀 표(symbol_id) 또는 심볼 검색(query). 설정된 라이브러리만", 60),
    "validate_ir": (OptionalPatchArgs, "ops(없으면 지금 제안한 변경)를 기준 회로에 적용해 IR 규칙과 라이브러리 핀 검사", 60),
    "propose_ir_patch": (PatchArgs, "변경(Circuit IR patch 연산 목록)을 제안. 승인된 범위 밖이면 거부됨", 60),
    "diff_ir": (NoArgs, "기준 회로와 제안한 변경 뒤 회로의 차이(코드 계산)", 20),
    "generate_schematic": (NameArgs, "제안한 변경 뒤 회로로 KiCad 회로도를 실행 workspace 안에 생성", 120),
    "run_erc": (NameArgs, "생성한 회로도에 KiCad ERC 실행", 180),
    "export_netlist": (NameArgs, "생성한 회로도의 Netlist를 workspace에 내보냄", 180),
    "export_svg": (NameArgs, "생성한 회로도의 SVG를 workspace에 내보냄", 180),
    "compare_netlists": (NameArgs, "내보낸 Netlist를 제안 회로(IR)와, 기준 회로와 비교", 60),
    "finish": (FinishArgs, "변경과 확인이 끝났을 때 요약을 남기고 종료 (그 뒤 코드가 다시 검증)", 5),
    "request_scope_change": (ScopeRequestArgs, "범위 밖 변경이 꼭 필요할 때 요청. 실행이 멈추고 사용자가 다시 승인", 5),
}


def tool_catalog() -> str:
    lines = []
    for name, (model, text, _) in TOOL_SPECS.items():
        props = model.model_json_schema().get("properties", {})
        args = ", ".join(f"{k}: {v.get('type') or 'object'}" for k, v in props.items()) or "인자 없음"
        lines.append(f"- {name}({args}): {text}")
    return "\n".join(lines)


# --- the gateway --------------------------------------------------------------------------------------------

@dataclass
class RunContext:
    project_id: str
    base: CircuitIR | None
    base_revision: int | None
    scope: list[ScopeItem]
    workspace: Path
    library: Callable[[], SymbolLibrary | None]
    kicad: Callable[[], KiCadCli | None]
    read_revision: Callable[[int], CircuitIR]
    search_symbols: Callable[[str], list[dict]] | None = None
    candidate_ops: list = field(default_factory=list)
    generated: dict[str, Path] = field(default_factory=dict)
    netlists: dict[str, Path] = field(default_factory=dict)


class Gateway:
    def __init__(self, ctx: RunContext, timeout_scale: float = 1.0):
        self.ctx = ctx
        self.timeout_scale = timeout_scale

    # -- entry point --
    def call(self, name: str, raw_args: Any) -> dict:
        if name not in EDA_TOOLS and name not in CONTROL:
            raise ToolError("TOOL_NOT_ALLOWED", f"'{name}'은(는) 허용된 도구가 아닙니다. 허용: {', '.join(EDA_TOOLS)}")
        model, _, timeout = TOOL_SPECS[name]
        try:
            args = model.model_validate(raw_args if raw_args is not None else {})
        except ValidationError as exc:
            err = exc.errors()[0]
            raise ToolError("BAD_ARGS", f"{name}: 인자 오류 {'.'.join(str(x) for x in err['loc'])}: {err['msg']}") from exc
        if name in CONTROL:
            return args.model_dump(mode="json")
        fn = getattr(self, "_" + name)
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"tool-{name}")
        future = pool.submit(fn, args)
        try:
            return future.result(timeout=timeout * self.timeout_scale)
        except concurrent.futures.TimeoutError as exc:
            raise ToolError("TOOL_TIMEOUT", f"{name}: {timeout * self.timeout_scale:.0f}초 안에 끝나지 않았습니다.") from exc
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    # -- helpers --
    def _path(self, *parts: str) -> Path:
        """A path inside the run workspace; anything else is refused."""
        for p in parts:
            if not _NAME.fullmatch(p):
                raise ToolError("PATH_REFUSED", f"'{p}': workspace 안의 이름(영문·숫자·_-)만 쓸 수 있습니다.")
        root = self.ctx.workspace.resolve()
        path = root.joinpath(*parts).resolve()
        if root not in path.parents and path != root:
            raise ToolError("PATH_REFUSED", "workspace 밖의 경로입니다.")
        return path

    def _candidate(self, ops: list | None = None) -> CircuitIR:
        ops = self.ctx.candidate_ops if ops is None else ops
        if self.ctx.base is None and not ops:
            raise ToolError("NO_CIRCUIT", "보드에 회로가 없습니다.")
        try:
            return apply_patch(self.ctx.base, ops)
        except PatchError as exc:
            raise ToolError("PATCH_FAILED", f"변경을 적용할 수 없습니다: {exc}") from exc

    def _parse(self, raw: list[dict]) -> list:
        try:
            return OPS.validate_python(raw)
        except ValidationError as exc:
            err = exc.errors()[0]
            raise ToolError("BAD_PATCH", f"patch 연산 오류 {'.'.join(str(x) for x in err['loc'])}: {err['msg']}") from exc

    # -- tools --
    def _read_project(self, _: NoArgs) -> dict:
        c = self.ctx.base
        if c is None:
            return {"revision": None, "components": [], "nets": []}
        nets = {(n.component_id, n.pin): net.name for net in c.nets for n in net.nodes}
        return {
            "revision": self.ctx.base_revision,
            "components": [{"id": x.id, "reference": x.reference, "value": x.value, "symbol_id": x.symbol_id,
                            "footprint_id": x.footprint_id, "origin": x.origin,
                            "pins": [{"number": p.number, "name": p.name, "net": nets.get((x.id, p.number)),
                                      "no_connect": p.no_connect} for p in x.pins]} for x in c.components],
            "nets": [{"name": n.name, "nodes": len(n.nodes)} for n in c.nets],
        }

    def _read_revision(self, args: RevisionArgs) -> dict:
        try:
            c = self.ctx.read_revision(args.revision)
        except Exception as exc:  # noqa: BLE001 - reported to the agent
            raise ToolError("NOT_FOUND", f"revision {args.revision}을(를) 읽지 못했습니다.") from exc
        return {"revision": args.revision, "components": [{"id": x.id, "reference": x.reference, "value": x.value}
                                                          for x in c.components],
                "nets": [{"name": n.name, "pins": [f"{n2.component_id}.{n2.pin}" for n2 in n.nodes]} for n in c.nets]}

    def _read_symbol_library(self, args: SymbolArgs) -> dict:
        lib = self.ctx.library()
        if lib is None:
            raise ToolError("NO_LIBRARY", "KiCad 심볼 라이브러리가 설정되어 있지 않습니다.")
        if args.symbol_id:
            try:
                sym = lib.get(args.symbol_id)
            except SymbolLibraryError as exc:
                raise ToolError("NOT_FOUND", str(exc)) from exc
            return {"symbol_id": sym.lib_id, "units": sym.unit_count,
                    "pins": [{"number": p.number, "name": p.name, "type": p.electrical_type} for p in sym.pins]}
        if args.query and self.ctx.search_symbols:
            return {"query": args.query, "matches": self.ctx.search_symbols(args.query)[:10]}
        raise ToolError("BAD_ARGS", "symbol_id 또는 query 중 하나를 주세요.")

    def _validate_ir(self, args: OptionalPatchArgs) -> dict:
        ops = self._parse(args.ops) if args.ops is not None else None
        candidate = self._candidate(ops)
        issues = [{"code": i.error_code, "severity": i.severity.value, "message": i.message}
                  for i in validate_circuit(candidate).issues]
        lib = self.ctx.library()
        if lib is not None:
            _, lib_issues = check_against_library(candidate, lib)
            issues += [{"code": i.error_code, "severity": i.severity.value, "message": i.message} for i in lib_issues]
        return {"ok": not any(i["severity"] == "error" for i in issues), "issues": issues[:40]}

    def _propose_ir_patch(self, args: PatchArgs) -> dict:
        ops = self._parse(args.ops)
        outside = [describe_op(op) for op in ops if not op_in_scope(op, self.ctx.scope)]
        if outside:
            raise ToolError("SCOPE_EXCEEDED", "승인된 범위 밖의 변경: " + "; ".join(outside) +
                            ". 꼭 필요하면 request_scope_change로 요청하세요.")
        candidate = self._candidate(ops)
        self.ctx.candidate_ops = ops
        self.ctx.generated.clear()
        self.ctx.netlists.clear()
        diff = diff_circuits(self.ctx.base, candidate)
        return {"accepted": len(ops), "summary": diff["summary"]}

    def _diff_ir(self, _: NoArgs) -> dict:
        if not self.ctx.candidate_ops:
            raise ToolError("NO_PATCH", "아직 제안한 변경이 없습니다 (propose_ir_patch 먼저).")
        diff = diff_circuits(self.ctx.base, self._candidate())
        return {k: diff[k] for k in ("summary", "components_added", "components_removed", "properties_changed",
                                     "connections", "nets_added", "nets_removed")}

    def _generate_schematic(self, args: NameArgs) -> dict:
        folder = self._path(args.name)
        lib = self.ctx.library()
        if lib is None:
            raise ToolError("NO_LIBRARY", "KiCad 심볼 라이브러리가 설정되어 있지 않습니다.")
        try:
            generated = generate_schematic(self._candidate(), lib, project_name=args.name)
        except SchematicGenerationError as exc:
            raise ToolError("GENERATION_FAILED", "회로도를 만들 수 없습니다: " +
                            "; ".join(f"[{i.error_code}] {i.message}" for i in exc.issues[:10])) from exc
        sch = generated.write(folder)
        self.ctx.generated[args.name] = sch
        return {"files": sorted(f.name for f in folder.iterdir()), "power_flags": generated.power_flags,
                "warnings": [w.error_code for w in generated.warnings]}

    def _cli(self) -> KiCadCli:
        cli = self.ctx.kicad()
        if cli is None:
            raise ToolError("KICAD_UNAVAILABLE", "kicad-cli를 찾지 못했습니다. KiCad 검사는 보류됩니다.")
        return cli

    def _sch(self, name: str) -> Path:
        self._path(name)
        if name not in self.ctx.generated:
            raise ToolError("NOT_GENERATED", f"'{name}' 회로도를 먼저 generate_schematic으로 만드세요.")
        return self.ctx.generated[name]

    def _run_erc(self, args: NameArgs) -> dict:
        sch, cli = self._sch(args.name), self._cli()
        try:
            report = cli.erc(sch, self._path(args.name, "erc").with_suffix(".json"))
        except KiCadCliError as exc:
            raise ToolError("KICAD_FAILED", str(exc)) from exc
        return {"errors": len(report.errors), "warnings": len(report.warnings),
                "violations": report.summary()["violations"][:20]}

    def _export_netlist(self, args: NameArgs) -> dict:
        sch, cli = self._sch(args.name), self._cli()
        out = self._path(args.name, "netlist").with_suffix(".net")
        try:
            text = cli.export_netlist(sch, out)
        except KiCadCliError as exc:
            raise ToolError("KICAD_FAILED", str(exc)) from exc
        self.ctx.netlists[args.name] = out
        netlist = parse_kicad_netlist(text)
        return {"file": out.name, "components": len(netlist.components), "nets": len(netlist.nets)}

    def _export_svg(self, args: NameArgs) -> dict:
        sch, cli = self._sch(args.name), self._cli()
        try:
            cli.export_svg(sch, self._path(args.name))
        except KiCadCliError as exc:
            raise ToolError("KICAD_FAILED", str(exc)) from exc
        return {"files": sorted(f.name for f in self._path(args.name).glob("*.svg"))}

    def _compare_netlists(self, args: NameArgs) -> dict:
        if args.name not in self.ctx.netlists:
            raise ToolError("NOT_EXPORTED", f"'{args.name}' Netlist를 먼저 export_netlist로 내보내세요.")
        netlist = parse_kicad_netlist(self.ctx.netlists[args.name].read_text(encoding="utf-8"))
        candidate = self._candidate()
        vs_ir = compare_with_ir(candidate, netlist)
        out: dict = {"matches_candidate_ir": vs_ir.ok, "detail": None if vs_ir.ok else vs_ir.describe()[:2000]}
        if self.ctx.base is not None:
            vs_base = compare_with_ir(self.ctx.base, netlist)
            out["differs_from_base"] = not vs_base.ok
            out["changes_from_base"] = None if vs_base.ok else vs_base.describe()[:2000]
        return out


def run_timed(fn: Callable[[], dict]) -> tuple[dict | None, ToolError | None, float]:
    start = time.monotonic()
    try:
        return fn(), None, time.monotonic() - start
    except ToolError as exc:
        return None, exc, time.monotonic() - start
