import { useCallback, useEffect, useState } from "react";

import { api, errorText } from "../api";
import { AGENT_FLOW, agentStateLabel, formatTime, providerLabel, scopeText } from "../format";
import type { AgentLimits, AgentRun, AgentScopeItem, AiStatus } from "../types";
import { Chip } from "./Chip";

interface Props {
  projectId: string;
  boardId: string;
  revision: number | null;
  onOpenProposal(id: string): void;
  onChanged(): Promise<unknown>;
}

const ACTIVE = ["PLANNING", "SCOPE_APPROVED", "SANDBOX_EXECUTION", "VALIDATION"];
const FINAL = ["COMMITTED", "REJECTED", "CANCELLED", "FAILED", "STALE"];
const key = (s: AgentScopeItem) => `${s.op}|${s.target}|${s.field ?? ""}`;

/** Bounded AI EDA agent: a dry-run plan, scope approval, sandboxed tool calls, code validation, a proposal. */
export function AgentTab({ projectId, boardId, revision, onOpenProposal, onChanged }: Props) {
  const [runs, setRuns] = useState<AgentRun[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<AgentRun | null>(null);
  const [request, setRequest] = useState("");
  const [limits, setLimits] = useState<AgentLimits | null>(null);
  const [ai, setAi] = useState<AiStatus | null>(null);
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const loadRuns = useCallback(async () => {
    try {
      const r = await api.agentRuns(projectId, boardId);
      setRuns(r.runs);
      return r.runs;
    } catch (e) {
      setMessage(errorText(e));
      return [];
    }
  }, [projectId, boardId]);

  useEffect(() => {
    void loadRuns().then((r) => r[0] && setSelected((cur) => cur ?? r[0].id));
    api.agentDefaults().then((d) => setLimits(d.defaults)).catch(() => setLimits(null));
    api.aiStatus().then(setAi).catch(() => setAi(null));
  }, [loadRuns]);

  const loadDetail = useCallback(async () => {
    if (!selected) return setDetail(null);
    try {
      const run = await api.agentRun(projectId, boardId, selected);
      setDetail(run);
      setChosen((cur) => (cur.size ? cur : new Set([...(run.plan?.scope ?? []), ...run.pending_scope].map(key))));
    } catch (e) {
      setMessage(errorText(e));
    }
  }, [projectId, boardId, selected]);

  useEffect(() => {
    setChosen(new Set());
    void loadDetail();
  }, [loadDetail, revision]);

  // Follow a working run until it waits for the user or ends.
  useEffect(() => {
    if (!detail || !ACTIVE.includes(detail.state)) return;
    const timer = window.setInterval(async () => {
      await loadDetail();
      await loadRuns();
    }, 1200);
    return () => window.clearInterval(timer);
  }, [detail, loadDetail, loadRuns]);

  const aiReady = Boolean(ai?.settings.enabled && ai.key.configured);

  const start = async () => {
    const ok = window.confirm(
      `AI Agent 계획 만들기 (실행하지 않음)\n\n전송할 데이터: 현재 회로(부품·핀·연결)와 요청 글\n받는 곳: ${providerLabel(ai?.settings.provider)} (모델 ${ai?.settings.model})\n\n` +
        "AI는 먼저 계획과 변경 범위만 제안합니다. 범위를 승인하기 전에는 아무것도 실행하지 않고, 결과는 「제안」으로만 만들어집니다. 계속할까요?",
    );
    if (!ok) return;
    setBusy(true);
    setMessage(null);
    try {
      const r = await api.startAgent(projectId, boardId, request.trim(), limits ?? undefined);
      setRequest("");
      setSelected(r.run.id);
      setChosen(new Set());
      await loadRuns();
    } catch (e) {
      setMessage(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const approve = async () => {
    if (!detail) return;
    const offered = [...(detail.plan?.scope ?? []), ...detail.pending_scope];
    const scope = offered.filter((s) => chosen.has(key(s)));
    setBusy(true);
    setMessage(null);
    try {
      await api.approveAgentScope(projectId, boardId, detail.id, scope);
      await loadDetail();
    } catch (e) {
      setMessage(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const cancel = async () => {
    if (!detail) return;
    try {
      setDetail(await api.cancelAgent(projectId, boardId, detail.id));
      await loadRuns();
      await onChanged();
    } catch (e) {
      setMessage(errorText(e));
    }
  };

  return (
    <div className="requirements-layout">
      <div className="panel">
        <div className="card">
          <h3>AI Agent에게 변경 요청</h3>
          <p className="small muted">
            Agent는 허용된 EDA 도구(회로 읽기·검증·patch 제안·회로도 생성·ERC·Netlist 비교)만 쓸 수 있고, 보드를 직접 바꾸지 못합니다. 먼저 계획과
            변경 범위를 제안하고, 승인한 범위 안에서만 격리된 작업 폴더에서 실행한 뒤 결과를 「제안」으로 올립니다.
          </p>
          <textarea
            className="small-textarea"
            aria-label="Agent 요청"
            placeholder="예: R1을 1k로 바꿔 LED 전류를 줄여줘"
            value={request}
            onChange={(e) => setRequest(e.target.value)}
          />
          {limits && (
            <p className="small muted">
              상한: LLM 호출 {limits.max_llm_calls}회 · 토큰 {limits.max_tokens.toLocaleString()} · {limits.max_seconds}초 · 도구 호출 {limits.max_tool_calls}회 · 검증
              실패 시 재시도 {limits.max_retries}회
            </p>
          )}
          <div className="toolbar">
            <button onClick={start} disabled={!aiReady || busy || request.trim().length < 3 || revision === null}>
              계획 만들기 (실행하지 않음)
            </button>
          </div>
          {!aiReady && <p className="small warn-text">설정에서 AI 기능을 켜고 Gemini API 키를 저장하거나 Claude에 로그인하세요.</p>}
          {revision === null && <p className="small muted">먼저 회로를 저장하세요.</p>}
        </div>

        <h3>실행 기록</h3>
        {runs.length === 0 && <p className="muted small">아직 실행이 없습니다.</p>}
        <ul className="template-list" aria-label="Agent 실행 목록">
          {runs.map((r) => {
            const s = agentStateLabel(r.state);
            return (
              <li key={r.id}>
                <button className={`template-item ${r.id === selected ? "active" : ""}`} onClick={() => setSelected(r.id)}>
                  <span className={`chip chip-${s.tone}`}>{s.text}</span> <b>#{Number(r.id)}</b> {r.request}
                  <div className="small muted">
                    revision {r.base_revision ?? "—"} · {formatTime(r.created_at)}
                  </div>
                </button>
              </li>
            );
          })}
        </ul>
        {message && (
          <p className="small warn-text" role="status">
            {message}
          </p>
        )}
      </div>

      {detail && (
        <div className="side">
          <RunDetail
            run={detail}
            chosen={chosen}
            setChosen={setChosen}
            busy={busy}
            onApprove={approve}
            onCancel={cancel}
            onOpenProposal={onOpenProposal}
          />
        </div>
      )}
    </div>
  );
}

function RunDetail({
  run,
  chosen,
  setChosen,
  busy,
  onApprove,
  onCancel,
  onOpenProposal,
}: {
  run: AgentRun;
  chosen: Set<string>;
  setChosen(s: Set<string>): void;
  busy: boolean;
  onApprove(): void;
  onCancel(): void;
  onOpenProposal(id: string): void;
}) {
  const s = agentStateLabel(run.state);
  const reached = new Set(run.history.map((h) => h.to));
  const offered = [...(run.plan?.scope ?? []), ...run.pending_scope];
  const pending = new Set(run.pending_scope.map(key));
  const tools = (run.log ?? []).filter((e) => e.type === "tool");
  const u = run.usage;
  const l = run.limits;

  return (
    <>
      <div className="card">
        <h3>
          #{Number(run.id)} {run.request}
        </h3>
        <div className="toolbar">
          <Chip label="상태" text={s.text} tone={s.tone} />
          <span className="small muted">
            {providerLabel(run.provider)} · {run.model} · 기준 revision {run.base_revision ?? "—"}
          </span>
        </div>
        <div className="state-flow" aria-label="Agent 상태 흐름">
          {AGENT_FLOW.map((st, i) => (
            <span key={st}>
              <span className={`step ${run.state === st ? "current" : reached.has(st) || (st === "PLANNING" && run.state !== "PLANNING") ? "done" : ""}`}>
                {agentStateLabel(st).text}
              </span>
              {i < AGENT_FLOW.length - 1 && <span className="muted"> → </span>}
            </span>
          ))}
          {FINAL.includes(run.state) && run.state !== "COMMITTED" && <span className="step current">{s.text}</span>}
        </div>
        <p className="small muted">
          사용량: LLM {u.llm_calls}/{l.max_llm_calls}회 · 토큰 {u.tokens.toLocaleString()}/{l.max_tokens.toLocaleString()} · 도구 {u.tool_calls}/{l.max_tool_calls}회 ·{" "}
          {u.seconds}/{l.max_seconds}초 · 검증 시도 {run.attempts}
        </p>
        {run.failure && <p className="small warn-text">실패: {run.failure.message}</p>}
        {run.history.length > 0 && <p className="small muted">마지막 변경: {run.history[run.history.length - 1].reason}</p>}
        <div className="toolbar">
          {run.state === "PROPOSED_PLAN" && (
            <button onClick={onApprove} disabled={busy || chosen.size === 0}>
              고른 범위 승인하고 실행
            </button>
          )}
          {run.proposal_id && (
            <button className="ghost" onClick={() => onOpenProposal(run.proposal_id!)}>
              제안 #{Number(run.proposal_id)} 열기
            </button>
          )}
          {!FINAL.includes(run.state) && (
            <button className="ghost" onClick={onCancel}>
              취소
            </button>
          )}
        </div>
      </div>

      {run.plan && (
        <div className="card">
          <h3>계획</h3>
          {run.plan.summary && <p className="small">{run.plan.summary}</p>}
          {run.plan.steps && run.plan.steps.length > 0 && (
            <ol className="small">
              {run.plan.steps.map((x, i) => (
                <li key={i}>{x}</li>
              ))}
            </ol>
          )}
          <h4>변경 범위 {run.state === "PROPOSED_PLAN" ? "— 승인할 항목을 고르세요" : ""}</h4>
          {run.plan.scope_request && <p className="small warn-text">Agent의 범위 추가 요청: {run.plan.scope_request}</p>}
          <table className="table compact">
            <tbody>
              {offered.map((it) => (
                <tr key={key(it)}>
                  <td>
                    <input
                      type="checkbox"
                      aria-label={`범위: ${scopeText(it)}`}
                      disabled={run.state !== "PROPOSED_PLAN"}
                      checked={run.state === "PROPOSED_PLAN" ? chosen.has(key(it)) : run.scope.some((x) => key(x) === key(it))}
                      onChange={(e) => {
                        const next = new Set(chosen);
                        if (e.target.checked) next.add(key(it));
                        else next.delete(key(it));
                        setChosen(next);
                      }}
                    />
                  </td>
                  <td className="small">
                    {scopeText(it)} {pending.has(key(it)) && <span className="chip chip-warn">추가 요청</span>}
                  </td>
                  <td className="small muted">{it.why}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {run.plan.dropped_scope && run.plan.dropped_scope.length > 0 && (
            <p className="small muted">계획에서 뺀 항목(회로에 없는 대상 등): {run.plan.dropped_scope.join("; ")}</p>
          )}
          {run.plan.risks && run.plan.risks.length > 0 && (
            <>
              <h4>위험</h4>
              <ul className="small">
                {run.plan.risks.map((x, i) => (
                  <li key={i}>{x}</li>
                ))}
              </ul>
            </>
          )}
          {run.plan.questions && run.plan.questions.length > 0 && (
            <>
              <h4>질문</h4>
              <ul className="small">
                {run.plan.questions.map((x, i) => (
                  <li key={i}>{x}</li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}

      {tools.length > 0 && (
        <div className="card">
          <h3>도구 호출 기록</h3>
          <table className="table compact">
            <thead>
              <tr>
                <th>#</th>
                <th>도구</th>
                <th>이유</th>
                <th>결과</th>
                <th>초</th>
              </tr>
            </thead>
            <tbody>
              {tools.map((e, i) => (
                <tr key={i}>
                  <td>{e.step}</td>
                  <td className="small">{e.tool}</td>
                  <td className="small">{e.thought}</td>
                  <td className="small">{e.ok ? <span className="ok-text">성공</span> : <span className="warn-text">{e.error?.code}: {e.error?.message}</span>}</td>
                  <td className="small">{e.seconds}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="small muted">전체 기록은 보드 폴더의 agent_runs/{run.id}/log.jsonl에 남습니다 (API 키·토큰은 가림).</p>
        </div>
      )}

      {run.validation && (
        <div className="card">
          <h3>코드 검증</h3>
          <p className="small">
            {run.validation.status === "passed"
              ? "통과 (범위·IR 규칙·라이브러리 핀·회로도 생성·KiCad ERC·Netlist)"
              : run.validation.status === "passed_without_kicad"
                ? `통과 — KiCad 검사는 보류 (${run.validation.kicad})`
                : "실패"}
            {run.validation.erc_errors !== undefined && ` · ERC 오류 ${run.validation.erc_errors}, 경고 ${run.validation.erc_warnings}`}
            {run.validation.netlist_matches_ir !== undefined && ` · Netlist ${run.validation.netlist_matches_ir ? "= IR" : "≠ IR"}`}
          </p>
          {run.validation.problems.length > 0 && (
            <ul className="small warn-text">
              {run.validation.problems.map((x, i) => (
                <li key={i}>{x}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      {run.report && (
        <div className="card">
          <h3>변경 전후 보고</h3>
          {run.summary && <p className="small">{run.summary}</p>}
          {run.report.values.length > 0 && (
            <table className="table compact">
              <thead>
                <tr>
                  <th>부품</th>
                  <th>항목</th>
                  <th>전</th>
                  <th>후</th>
                </tr>
              </thead>
              <tbody>
                {run.report.values.map((v, i) => (
                  <tr key={i}>
                    <td>{v.reference}</td>
                    <td>{v.field}</td>
                    <td>{v.before ?? "—"}</td>
                    <td>{v.after ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <p className="small">
            연결 변경 {run.report.connections.length}개 · 추가 {run.report.components_added.join(", ") || "없음"} · 삭제{" "}
            {run.report.components_removed.join(", ") || "없음"}
          </p>
          {run.report.netlist && !run.report.netlist.error && (
            <p className="small">
              KiCad Netlist 비교 (변경 전 ↔ 후): Net 변경 {Object.keys(run.report.netlist.nets_changed ?? {}).length}개, 값 변경{" "}
              {Object.entries(run.report.netlist.values_changed ?? {})
                .map(([r, v]) => `${r} ${v.before}→${v.after}`)
                .join(", ") || "없음"}
            </p>
          )}
        </div>
      )}
    </>
  );
}
