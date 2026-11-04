import { useCallback, useEffect, useRef, useState } from "react";

import { api, errorText, proposalFileText, waitForJob } from "../api";
import {
  automatedLabel,
  cropSvg,
  diffFieldLabel,
  diffSummaryText,
  formatTime,
  proposalSourceLabel,
  proposalStatusLabel,
  ROW_STATUS,
} from "../format";
import type { AiStatus, BoardReview, CircuitIR, Job, Proposal } from "../types";
import { Chip } from "./Chip";
import { IssueList } from "./IssueList";

interface Props {
  projectId: string;
  boardId: string;
  revision: number | null;
  circuit: CircuitIR | null;
  focusId?: string | null;
  onChanged(): Promise<unknown>;
}

const FIELDS = [
  ["value", "값"],
  ["footprint_id", "풋프린트"],
  ["manufacturer", "제조사"],
  ["manufacturer_part_number", "제조사 품번"],
] as const;

export function ProposalsTab({ projectId, boardId, revision, circuit, focusId, onChanged }: Props) {
  const [list, setList] = useState<Proposal[] | null>(null);
  const [selected, setSelected] = useState<string | null>(focusId ?? null);
  const [detail, setDetail] = useState<Proposal | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [ai, setAi] = useState<AiStatus | null>(null);
  const [boardReview, setBoardReview] = useState<BoardReview | null>(null);
  const aiReady = Boolean(ai?.settings.enabled && ai.key.configured);

  useEffect(() => {
    api.aiStatus().then(setAi).catch(() => setAi(null));
    api.boardReview(projectId, boardId).then((r) => setBoardReview(r.review)).catch(() => setBoardReview(null));
  }, [projectId, boardId]);

  const load = useCallback(async () => {
    try {
      const items = await api.proposals(projectId, boardId);
      setList(items);
      setSelected((cur) => cur ?? items.find((p) => ["ready", "checking", "invalid"].includes(p.status))?.id ?? items[0]?.id ?? null);
    } catch (e) {
      setMessage(errorText(e));
    }
  }, [projectId, boardId]);

  useEffect(() => {
    void load();
  }, [load, revision]);

  useEffect(() => {
    if (focusId) setSelected(focusId);
  }, [focusId]);

  useEffect(() => {
    if (!selected) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    api
      .proposal(projectId, boardId, selected)
      .then((p) => !cancelled && setDetail(p))
      .catch((e) => !cancelled && setMessage(errorText(e)));
    return () => {
      cancelled = true;
    };
  }, [projectId, boardId, selected, list]);

  // While a proposal is being checked, refresh until the check is done.
  useEffect(() => {
    if (!list?.some((p) => p.status === "checking")) return;
    const t = setTimeout(() => void load(), 1500);
    return () => clearTimeout(t);
  }, [list, load]);

  const run = async (label: string, fn: () => Promise<void>) => {
    setMessage(null);
    setBusy(label);
    try {
      await fn();
    } catch (e) {
      setMessage(errorText(e));
    } finally {
      setBusy(null);
    }
  };

  const approve = (p: Proposal) =>
    run("승인하는 중…", async () => {
      const passed = p.evidence?.automated_check?.status === "passed";
      const text = passed
        ? `제안 #${Number(p.id)} "${p.title}"을(를) 승인할까요?\n새 revision으로 저장하고 KiCad 회로도를 다시 생성합니다.`
        : `제안 #${Number(p.id)}은(는) 자동 검사를 통과하지 못했거나 검사하지 않았습니다.\n그래도 승인해 새 revision으로 저장할까요?`;
      if (!window.confirm(text)) return;
      const r = await api.approveProposal(projectId, boardId, p.id, !passed);
      setMessage(`revision ${r.revision.revision}으로 저장했습니다. 회로도를 다시 생성합니다…`);
      await onChanged();
      await load();
      if (r.job) {
        const job = await waitForJob(r.job.id);
        setMessage(
          job.status === "succeeded"
            ? `revision ${r.revision.revision}으로 저장하고 회로도를 다시 생성했습니다.`
            : `revision ${r.revision.revision}으로 저장했습니다. 회로도 생성은 실패했습니다: ${job.error?.message ?? ""}`,
        );
        await onChanged();
      }
    });

  const reject = (p: Proposal) =>
    run("거부하는 중…", async () => {
      const note = window.prompt(`제안 #${Number(p.id)}을(를) 거부합니다. 이유(선택):`, "");
      if (note === null) return;
      await api.rejectProposal(projectId, boardId, p.id, note || undefined);
      setMessage("거부했습니다. 회로는 바뀌지 않았습니다.");
      await load();
    });

  const jobError = (job: Job) => (job.error ? `[${job.error.error_code}] ${job.error.message}` : "실패했습니다.");

  const reviewProposal = (p: Proposal) =>
    run("AI가 제안을 검토하는 중…", async () => {
      if (!window.confirm(`AI 검토\n\n전송할 데이터: 제안된 회로(부품·연결), 요구사항, 검사 결과, 데이터시트 요약\n받는 곳: Google Gemini API (모델 ${ai?.settings.model})\n\n계속할까요?`)) return;
      const job = await waitForJob((await api.aiReview(projectId, boardId, p.id)).id, undefined, 1500);
      if (job.status === "failed") return setMessage(jobError(job));
      setMessage(`검토 의견 ${job.result.review.findings.length}건을 받았습니다.`);
      await load();
    });

  const revise = (p: Proposal | null) =>
    run("AI가 검토 의견을 반영해 수정안을 만드는 중… (1~3분)", async () => {
      const extra = window.prompt("검토 의견을 반영한 수정안을 만듭니다. 추가로 요청할 내용(선택):", "");
      if (extra === null) return;
      const job = await waitForJob((await api.aiRevise(projectId, boardId, p?.id, extra)).id, undefined, 1500);
      if (job.status === "failed") return setMessage(jobError(job));
      setMessage(`수정안을 제안 #${Number(job.result.proposal_id)}(으)로 만들었습니다.`);
      setSelected(job.result.proposal_id);
      await load();
    });

  const reviewBoard = () =>
    run("AI가 현재 회로를 검토하는 중…", async () => {
      if (!window.confirm(`현재 회로 AI 검토\n\n전송할 데이터: 저장된 회로(부품·연결), 요구사항, 검사 결과, 데이터시트 요약\n받는 곳: Google Gemini API (모델 ${ai?.settings.model})\n\n계속할까요?`)) return;
      const job = await waitForJob((await api.aiReview(projectId, boardId)).id, undefined, 1500);
      if (job.status === "failed") return setMessage(jobError(job));
      setBoardReview({ ...job.result.review, revision });
    });

  const recheck = (p: Proposal) =>
    run("다시 검사하는 중…", async () => {
      await api.checkProposal(projectId, boardId, p.id);
      await load();
    });

  return (
    <div className="proposals">
      <div className="card">
        <h3>변경 제안</h3>
        <p className="small">
          AI 설계안, AI 수정안, 직접 만든 변경은 모두 여기서 검토하고 승인합니다. 승인 전에는 회로가 바뀌지 않습니다. 비교 표는 앱이 회로
          데이터에서 직접 계산한 것이고, 검사 결과는 제안 전용 폴더에서 만든 KiCad 파일로 확인한 것입니다.
        </p>
        {revision && (
          <div className="toolbar">
            <button className="ghost" onClick={reviewBoard} disabled={!aiReady || busy !== null}
              title={aiReady ? "저장된 회로를 요구사항·검사 결과와 함께 AI가 검토합니다" : "설정에서 AI 기능을 켜고 Gemini API 키를 저장하세요"}>
              현재 회로 AI 검토
            </button>
          </div>
        )}
        {circuit && revision && <ManualProposal projectId={projectId} boardId={boardId} circuit={circuit} onCreated={async (id) => {
          setSelected(id);
          await load();
        }} />}
        {busy && <p className="small">{busy}</p>}
        {message && (
          <p className="small" role="status">
            {message}
          </p>
        )}
      </div>

      {boardReview && (
        <div className="card">
          <div className="toolbar">
            <h3>현재 회로 AI 검토 {boardReview.revision ? <span className="muted small">revision {boardReview.revision}</span> : null}</h3>
            <button className="ghost small-btn" onClick={() => revise(null)} disabled={!aiReady || busy !== null || boardReview.findings.length === 0}>
              검토 반영 수정안 만들기
            </button>
          </div>
          {boardReview.summary && <p className="small">{boardReview.summary}</p>}
          <FindingList findings={boardReview.findings} />
          {boardReview.revision !== undefined && boardReview.revision !== revision && (
            <p className="muted small">이 검토는 revision {boardReview.revision} 기준입니다. 회로가 바뀌었으면 다시 검토하세요.</p>
          )}
        </div>
      )}

      {list && list.length === 0 && (
        <div className="empty small">아직 제안이 없습니다. 「요구사항」 탭에서 AI 설계를 시작하거나, 위에서 값 변경을 제안해 보세요.</div>
      )}

      {list && list.length > 0 && (
        <div className="proposal-layout">
          <ul className="proposal-list" aria-label="제안 목록">
            {list.map((p) => {
              const st = proposalStatusLabel(p.status);
              return (
                <li key={p.id}>
                  <button className={`proposal-item ${selected === p.id ? "active" : ""}`} onClick={() => setSelected(p.id)}>
                    <span className="small muted">
                      #{Number(p.id)} · {proposalSourceLabel(p.source)} · {formatTime(p.created_at)}
                    </span>
                    <b>{p.title}</b>
                    <span className="small">{diffSummaryText(p.diff.summary)}</span>
                    <Chip text={st.text} tone={st.tone} />
                  </button>
                </li>
              );
            })}
          </ul>
          {detail && (
            <ProposalDetail
              key={detail.id}
              p={detail}
              projectId={projectId}
              boardId={boardId}
              busy={busy !== null}
              onApprove={() => approve(detail)}
              onReject={() => reject(detail)}
              onRecheck={() => recheck(detail)}
              aiReady={aiReady}
              onAiReview={() => reviewProposal(detail)}
              onAiRevise={() => revise(detail)}
            />
          )}
        </div>
      )}
    </div>
  );
}

function ManualProposal({ projectId, boardId, circuit, onCreated }: {
  projectId: string;
  boardId: string;
  circuit: CircuitIR;
  onCreated(id: string): Promise<void>;
}) {
  const [compId, setCompId] = useState(circuit.components[0]?.id ?? "");
  const [field, setField] = useState<(typeof FIELDS)[number][0]>("value");
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const comp = circuit.components.find((c) => c.id === compId);
  const current = comp ? ((comp as unknown as Record<string, string | null | undefined>)[field] ?? null) : null;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!comp) return;
    setError(null);
    try {
      const label = FIELDS.find((f) => f[0] === field)![1];
      const r = await api.createProposal(projectId, boardId, `${comp.reference} ${label}: ${current ?? "없음"} → ${value.trim() || "없음"}`, [
        { op: "set_property", component_id: comp.id, field, expect: current, value: value.trim() || null },
      ]);
      setValue("");
      await onCreated(r.proposal.id);
    } catch (err) {
      setError(errorText(err));
    }
  };

  return (
    <form className="toolbar" onSubmit={submit} aria-label="값 변경 제안">
      <span className="small muted">직접 제안:</span>
      <select aria-label="제안할 부품" value={compId} onChange={(e) => setCompId(e.target.value)}>
        {circuit.components.map((c) => (
          <option key={c.id} value={c.id}>
            {c.reference} ({c.value})
          </option>
        ))}
      </select>
      <select aria-label="바꿀 항목" value={field} onChange={(e) => setField(e.target.value as typeof field)}>
        {FIELDS.map(([k, label]) => (
          <option key={k} value={k}>
            {label}
          </option>
        ))}
      </select>
      <span className="small muted">현재 {current ?? "없음"} →</span>
      <input aria-label="새 값" value={value} onChange={(e) => setValue(e.target.value)} placeholder="새 값" style={{ width: 160 }} />
      <button type="submit" className="ghost" disabled={!comp || (field === "value" && !value.trim())}>
        제안 만들기
      </button>
      {error && <span className="small bad-text">{error}</span>}
    </form>
  );
}

interface DetailProps {
  p: Proposal;
  projectId: string;
  boardId: string;
  busy: boolean;
  onApprove(): void;
  onReject(): void;
  onRecheck(): void;
  aiReady: boolean;
  onAiReview(): void;
  onAiRevise(): void;
}

function ProposalDetail({ p, projectId, boardId, busy, onApprove, onReject, onRecheck, aiReady, onAiReview, onAiRevise }: DetailProps) {
  const st = proposalStatusLabel(p.status);
  const ev = p.evidence;
  const check = ev?.automated_check ? automatedLabel(ev.automated_check.status) : null;
  const d = p.diff;
  const open = ["ready", "invalid", "check_failed"].includes(p.status);
  return (
    <div className="proposal-detail">
      <div className="card">
        <div className="toolbar">
          <h3>
            #{Number(p.id)} {p.title}
          </h3>
          <Chip text={st.text} tone={st.tone} />
        </div>
        <p className="small muted">
          {proposalSourceLabel(p.source)} · 기준 revision {p.base_revision ?? "없음 (새 회로)"} · {formatTime(p.created_at)}
          {p.result_revision ? ` · 승인 → revision ${p.result_revision}` : ""}
          {p.ai?.model ? ` · 모델 ${p.ai.model}` : ""}
        </p>
        {p.rationale && <p className="small pre">{p.rationale}</p>}
        {p.decision_note && <p className="small warn-text">{p.decision_note}</p>}
        <div className="toolbar">
          <button onClick={onApprove} disabled={busy || p.status !== "ready"}>
            승인
          </button>
          <button className="ghost" onClick={onReject} disabled={busy || !open}>
            거부
          </button>
          <button className="ghost" onClick={onRecheck} disabled={busy || !open}>
            다시 검사
          </button>
          {p.status === "checking" && <span className="small">KiCad로 검사하는 중…</span>}
        </div>
        {aiReady && open && (
          <div className="toolbar">
            <button className="ghost small-btn" onClick={onAiReview} disabled={busy}>
              {p.ai?.review ? "AI 다시 검토" : "AI 검토"}
            </button>
            <button className="ghost small-btn" onClick={onAiRevise} disabled={busy || !(p.ai?.review?.findings.length)}
              title="AI 검토 의견을 반영한 새 제안을 만듭니다">
              검토 반영 수정안 만들기
            </button>
          </div>
        )}
      </div>

      {p.ai && <AiInfo p={p} />}

      <div className="card">
        <h3>바뀌는 내용 <span className="muted small">{diffSummaryText(d.summary)}</span></h3>
        {d.components_added.length > 0 && (
          <>
            <h4>{d.is_new_circuit ? "부품" : "추가되는 부품"}</h4>
            <table className="table compact">
              <thead>
                <tr><th>기호</th><th>값</th><th>KiCad 심볼</th><th>풋프린트</th><th>품번</th></tr>
              </thead>
              <tbody>
                {d.components_added.map((c) => (
                  <tr key={c.id}><td>{c.reference}</td><td>{c.value}</td><td>{c.symbol_id}</td><td>{c.footprint_id ?? "—"}</td><td>{c.manufacturer_part_number ?? "—"}</td></tr>
                ))}
              </tbody>
            </table>
          </>
        )}
        {d.components_removed.length > 0 && (
          <>
            <h4>삭제되는 부품</h4>
            <p className="small">{d.components_removed.map((c) => `${c.reference} (${c.value})`).join(", ")}</p>
          </>
        )}
        {d.properties_changed.length > 0 && (
          <>
            <h4>속성 변경</h4>
            <table className="table compact">
              <thead><tr><th>부품</th><th>항목</th><th>전</th><th>후</th></tr></thead>
              <tbody>
                {d.properties_changed.map((c, i) => (
                  <tr key={i}><td>{c.reference}</td><td>{diffFieldLabel(c.field)}</td><td>{c.before ?? "—"}</td><td><b>{c.after ?? "—"}</b></td></tr>
                ))}
              </tbody>
            </table>
          </>
        )}
        {d.connections.length > 0 && (
          <details open={d.connections.length <= 30}>
            <summary className="small">연결 {d.connections.length}개 {d.is_new_circuit ? "" : "변경"}</summary>
            <table className="table compact">
              <thead><tr><th>핀</th><th>전 Net</th><th>후 Net</th></tr></thead>
              <tbody>
                {d.connections.map((c, i) => (
                  <tr key={i}>
                    <td>{c.reference}.{c.pin}{c.pin_name && c.pin_name !== "~" ? ` (${c.pin_name})` : ""}</td>
                    <td>{c.before ?? "—"}</td>
                    <td><b>{c.after ?? "—"}</b></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        )}
        {(d.nets_added.length > 0 || d.nets_removed.length > 0) && (
          <p className="small">
            {d.nets_added.length > 0 && <>Net 추가: {d.nets_added.join(", ")} </>}
            {d.nets_removed.length > 0 && <>· Net 삭제: {d.nets_removed.join(", ")}</>}
          </p>
        )}
      </div>

      <div className="card">
        <h3>검사 결과 {ev && <span className="muted small">{formatTime(ev.checked_at)}</span>}</h3>
        {!ev && <p className="small muted">검사 중입니다…</p>}
        {ev && (
          <>
            <p className="small">
              자동 검사:{" "}
              {check ? <Chip text={check.text} tone={check.tone} /> : <Chip text={ev.blocking ? "규칙 오류로 생략" : "생성 실패"} tone="bad" />}
              {ev.automated_check?.kicad_version && <> · KiCad {ev.automated_check.kicad_version}</>}
              {ev.automated_check?.erc_errors !== undefined && ev.automated_check?.erc_errors !== null && (
                <> · ERC 오류 {ev.automated_check.erc_errors}, 경고 {ev.automated_check.erc_warnings}</>
              )}
              {ev.automated_check?.netlist_matches_ir !== undefined && ev.automated_check?.netlist_matches_ir !== null && (
                <> · Netlist {ev.automated_check.netlist_matches_ir ? "= IR" : "≠ IR"}</>
              )}
              {ev.automated_check?.reason && <span className="muted"> ({ev.automated_check.reason})</span>}
            </p>
            {ev.power_flags.length > 0 && (
              <p className="small muted">PWR_FLAG 자동 추가: {ev.power_flags.join(", ")} (커넥터로 전원이 들어오는 Net)</p>
            )}
            <IssueList issues={[...ev.ir_issues, ...(ev.generation_error?.issues ?? [])]} />
            {ev.generation_error && !ev.generation_error.issues && <p className="small bad-text">{ev.generation_error.message}</p>}
            {ev.automated_check?.violations && ev.automated_check.violations.length > 0 && (
              <ul className="issues">
                {ev.automated_check.violations.map((v, i) => (
                  <li key={i} className={`issue issue-${v.severity === "error" ? "error" : "warning"}`}>
                    <span className="issue-code">{String(v.type)}</span> {String(v.description)}{" "}
                    <span className="muted">{(v.items as string[] | undefined)?.join(", ")}</span>
                  </li>
                ))}
              </ul>
            )}
            {ev.datasheets.length > 0 && (
              <>
                <h4>데이터시트 대조</h4>
                <table className="table compact">
                  <thead><tr><th>부품</th><th>핀맵</th><th>전압</th><th>외부 부품</th></tr></thead>
                  <tbody>
                    {ev.datasheets.map((r) => (
                      <tr key={r.reference}>
                        <td>{r.reference}{!r.reviewed && <span className="muted small"> (요약 미확인)</span>}</td>
                        <td>{r.pin_map === "match" ? "일치" : r.pin_map === "mismatch" ? "불일치" : "확인 불가"}</td>
                        <td>{r.ratings_problems.length ? r.ratings_problems.join("; ") : r.ratings === "ok" ? "정상" : "확인 불가"}</td>
                        <td>{r.external_missing.length ? r.external_missing.join("; ") : r.external === "ok" ? "모두 있음" : "확인 불가"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            )}
            <Preview p={p} projectId={projectId} boardId={boardId} />
          </>
        )}
      </div>
    </div>
  );
}

function AiInfo({ p }: { p: Proposal }) {
  const ai = p.ai!;
  return (
    <div className="card">
      <h3>AI 설계 메모</h3>
      {ai.summary && <p className="small">{ai.summary}</p>}
      {ai.questions && ai.questions.length > 0 && (
        <>
          <h4>확인이 필요한 질문</h4>
          <ul className="small">{ai.questions.map((q, i) => <li key={i}>{q}</li>)}</ul>
        </>
      )}
      {ai.assumptions && ai.assumptions.length > 0 && (
        <>
          <h4>AI가 가정한 것</h4>
          <ul className="small">{ai.assumptions.map((q, i) => <li key={i}>{q}</li>)}</ul>
        </>
      )}
      {ai.parts && ai.parts.length > 0 && (
        <details>
          <summary className="small">부품 선택 근거 {ai.parts.length}개</summary>
          <table className="table compact">
            <thead><tr><th>기호</th><th>역할</th><th>KiCad 심볼</th><th>고른 방법</th></tr></thead>
            <tbody>{ai.parts.map((x) => <tr key={x.key}><td>{x.key}</td><td>{x.role}</td><td>{x.symbol_id}</td><td>{x.how}</td></tr>)}</tbody>
          </table>
        </details>
      )}
      {ai.citations && ai.citations.length > 0 && (
        <details>
          <summary className="small">데이터시트 인용 {ai.citations.length}개</summary>
          <ul className="small">{ai.citations.map((c, i) => <li key={i}>{c.reference} p.{c.page ?? "?"}: {c.text}</li>)}</ul>
        </details>
      )}
      {ai.review && (
        <>
          <h4>AI 검토 {ai.review.findings.length > 0 ? `(${ai.review.findings.length}건)` : ""}</h4>
          {ai.review.summary && <p className="small">{ai.review.summary}</p>}
          <FindingList findings={ai.review.findings} />
        </>
      )}
      <p className="muted small">AI 설계와 검토는 틀릴 수 있습니다. 위 검사 결과와 데이터시트 원문으로 확인한 뒤 승인하세요.</p>
    </div>
  );
}

export function FindingList({ findings }: { findings: { severity: string; title: string; detail: string; references: string[]; suggestion: string | null }[] }) {
  if (findings.length === 0) return <p className="small ok-text">지적 사항이 없습니다.</p>;
  return (
    <ul className="issues">
      {findings.map((f, i) => (
        <li key={i} className={`issue issue-${f.severity === "error" ? "error" : f.severity === "warning" ? "warning" : "info"}`}>
          <b>{f.title}</b> {f.references.length > 0 && <span className="muted">({f.references.join(", ")})</span>}
          <div>{f.detail}</div>
          {f.suggestion && <div className="issue-hint">→ {f.suggestion}</div>}
        </li>
      ))}
    </ul>
  );
}

function Preview({ p, projectId, boardId }: { p: Proposal; projectId: string; boardId: string }) {
  const page = p.evidence?.pages.find((x) => x.svg) ?? null;
  const [url, setUrl] = useState<string | null>(null);
  const last = useRef<string | null>(null);
  useEffect(() => {
    if (!page?.svg) return;
    let revoked = false;
    proposalFileText(projectId, boardId, p.id, page.svg)
      .then((text) => {
        if (revoked) return;
        const u = URL.createObjectURL(new Blob([cropSvg(text, page.bbox_mm)], { type: "image/svg+xml" }));
        if (last.current) URL.revokeObjectURL(last.current);
        last.current = u;
        setUrl(u);
      })
      .catch(() => setUrl(null));
    return () => {
      revoked = true;
    };
  }, [projectId, boardId, p.id, page?.svg, page?.bbox_mm]);
  if (!page?.svg) return null;
  return (
    <>
      <h4>제안된 회로도 미리보기{p.evidence!.pages.length > 1 ? ` (1/${p.evidence!.pages.length}쪽)` : ""}</h4>
      <div className="preview-area proposal-preview">{url && <img src={url} alt="제안된 회로도 미리보기" className="preview-img fit" />}</div>
    </>
  );
}

export { ROW_STATUS };
