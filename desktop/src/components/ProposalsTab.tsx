import { useCallback, useEffect, useRef, useState } from "react";

import { api, errorText, proposalFileText, waitForJob } from "../api";
import {
  AI_SETUP_HINT,
  automatedLabel,
  cropSvg,
  DECISION_BY,
  diffFieldLabel,
  ERC_EXPLAIN,
  ercWarningText,
  fixability,
  diffSummaryText,
  formatTime,
  originLabel,
  proposalSourceLabel,
  proposalStatusLabel,
  providerLabel,
  ROW_STATUS,
} from "../format";
import type { AiStatus, BoardReview, CircuitIR, DatasheetStep, ErcViolation, Job, Proposal, ProposalDiff, QuickFix, RevisionInputs } from "../types";
import { Chip } from "./Chip";
import { ProposalDatasheets } from "./DatasheetFinder";
import { emptyDraft, GuideChecklistPanel, savedName, saveFindingAsGuide } from "./Guides";
import { IssueList } from "./IssueList";
import { TemplateEvidence } from "./TemplatesTab";

const WRITE_NOTE_KEY = "aics.reviewWritesNote";

interface Props {
  projectId: string;
  boardId: string;
  revision: number | null;
  circuit: CircuitIR | null;
  focusId?: string | null;
  onChanged(): Promise<unknown>;
  /** One proposal only (no list or manual form), opened at ``initialStage`` — the step screens' 「자세히」. */
  single?: boolean;
  initialStage?: string;
  onBack?(): void;
  backLabel?: string;
}

const FIELDS = [
  ["value", "값"],
  ["footprint_id", "풋프린트"],
  ["manufacturer", "제조사"],
  ["manufacturer_part_number", "제조사 품번"],
] as const;

export function ProposalsTab({ projectId, boardId, revision, circuit, focusId, onChanged, single, initialStage, onBack, backLabel }: Props) {
  const [list, setList] = useState<Proposal[] | null>(null);
  const [selected, setSelected] = useState<string | null>(focusId ?? null);
  const [detail, setDetail] = useState<Proposal | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [writeNote, setWriteNoteState] = useState(() => {
    try {
      return window.localStorage.getItem(WRITE_NOTE_KEY) !== "no";
    } catch {
      return true;
    }
  });
  const setWriteNote = (on: boolean) => {
    setWriteNoteState(on);
    try {
      window.localStorage.setItem(WRITE_NOTE_KEY, on ? "yes" : "no");
    } catch {
      /* per-viewer convenience only */
    }
  };
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

  const reject = (p: Proposal, note: string, toGuide = false) =>
    run("거부하는 중…", async () => {
      await api.rejectProposal(projectId, boardId, p.id, note.trim() || undefined);
      let saved = "";
      if (toGuide && note.trim()) {
        const e = await api.createGuide({
          draft: emptyDraft({
            title: note.trim().split(/[.\n]/)[0].slice(0, 60),
            rule: note.trim(),
            sources: [{ kind: "feedback", text: `제안 #${Number(p.id)} 「${p.title}」 거부 이유`, ref: { project_id: projectId, board_id: boardId, proposal_id: p.id } }],
          }),
          origin: "feedback",
          by: savedName() || "사용자",
          adopt: false,
        });
        saved = ` 거부 이유를 설계 지침 후보 ${e.id}로 남겼습니다(설계 가이드에서 다듬고 채택하세요).`;
      }
      setMessage("거부했습니다. 회로는 바뀌지 않았습니다." + saved);
      await load();
    });

  const jobError = (job: Job) => (job.error ? `[${job.error.error_code}] ${job.error.message}` : "실패했습니다.");

  const noteLine = "\n연구노트: 검토 결과와 앱이 찾은 근거(계산·데이터시트·설계 결정)로 노트 초안도 씁니다(AI 호출 1회 추가)";
  const noteResult = (r: { note?: { id: string }; note_error?: { message: string } }) =>
    r.note ? ` 연구노트 ${r.note.id}(초안)도 썼습니다(사이드바 「연구노트」).` : r.note_error ? ` 연구노트는 쓰지 못했습니다: ${r.note_error.message}` : "";

  const reviewProposal = (p: Proposal) =>
    run("AI가 제안을 검토하는 중…", async () => {
      if (!window.confirm(`AI 검토\n\n전송할 데이터: 제안된 회로(부품·연결), 요구사항, 검사 결과, 데이터시트 요약\n받는 곳: ${providerLabel(ai?.settings.provider)} (모델 ${ai?.settings.model})${writeNote ? noteLine : ""}\n\n계속할까요?`)) return;
      const job = await waitForJob((await api.aiReview(projectId, boardId, p.id, writeNote)).id, undefined, 1500);
      if (job.status === "failed") return setMessage(jobError(job));
      setMessage(`검토 의견 ${job.result.review.findings.length}건을 받았습니다.` + noteResult(job.result));
      await load();
    });

  // Which revision request form is open: a proposal id, "board" (the saved circuit) or none, and what it starts with.
  const [revising, setRevising] = useState<string | null>(null);
  const [revisePreset, setRevisePreset] = useState<RevisePreset | null>(null);
  const openRevise = (target: string, preset: RevisePreset | null) => {
    setRevisePreset(preset);
    setRevising(target);
    window.setTimeout(() => document.getElementById(`revise-${target}`)?.scrollIntoView({ behavior: "smooth", block: "center" }), 50);
  };
  const onRevised = async (id: string) => {
    setRevising(null);
    setMessage(`수정안을 제안 #${Number(id)}(으)로 만들었습니다.`);
    setSelected(id);
    await load();
  };

  const reviewBoard = () =>
    run("AI가 현재 회로를 검토하는 중…", async () => {
      if (!window.confirm(`현재 회로 AI 검토\n\n전송할 데이터: 저장된 회로(부품·연결), 요구사항, 검사 결과, 데이터시트 요약\n받는 곳: ${providerLabel(ai?.settings.provider)} (모델 ${ai?.settings.model})${writeNote ? noteLine : ""}\n\n계속할까요?`)) return;
      const job = await waitForJob((await api.aiReview(projectId, boardId, undefined, writeNote)).id, undefined, 1500);
      if (job.status === "failed") return setMessage(jobError(job));
      setBoardReview({ ...job.result.review, revision });
      if (job.result.note || job.result.note_error) setMessage(noteResult(job.result).trim());
    });

  const recheck = (p: Proposal) =>
    run("다시 검사하는 중…", async () => {
      await api.checkProposal(projectId, boardId, p.id);
      await load();
    });

  const detailEl = detail && (
    <ProposalDetail
      key={detail.id}
      p={detail}
      projectId={projectId}
      boardId={boardId}
      busy={busy !== null}
      onApprove={() => approve(detail)}
      onReject={(note, toGuide) => reject(detail, note, toGuide)}
      onRecheck={() => recheck(detail)}
      aiReady={aiReady}
      boardRevision={revision}
      onAiReview={() => reviewProposal(detail)}
      revising={revising === detail.id}
      onToggleRevise={() => (revising === detail.id ? setRevising(null) : openRevise(detail.id, null))}
      onRevise={(preset) => openRevise(detail.id, preset)}
      onFixed={async (id) => {
        await load();
        setSelected(id);
        await onChanged();
      }}
      initialStage={initialStage}
      revisionPanel={
        <RevisionPanel key={JSON.stringify(revisePreset)} projectId={projectId} boardId={boardId} proposalId={detail.id}
          provider={ai?.settings.provider ?? "gemini"} model={ai?.settings.model ?? ""} preset={revisePreset}
          onCancel={() => setRevising(null)} onCreated={onRevised} />
      }
    />
  );

  if (single) {
    return (
      <div className="proposals single">
        {onBack && (
          <button className="link back-link" onClick={onBack}>
            ← {backLabel ?? "돌아가기"}
          </button>
        )}
        {busy && <p className="small">{busy}</p>}
        {message && (
          <p className="small" role="status">
            {message}
          </p>
        )}
        {detailEl}
      </div>
    );
  }

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
              title={aiReady ? "저장된 회로를 요구사항·검사 결과와 함께 AI가 검토합니다" : AI_SETUP_HINT}>
              현재 회로 AI 검토
            </button>
            <label className="small" title="검토하면서 확인한 것·배운 점·다음에 적용할 것을 연구노트 초안으로 남깁니다">
              <input type="checkbox" checked={writeNote} onChange={(e) => setWriteNote(e.target.checked)} /> AI 검토 때 연구노트도 쓰기
            </label>
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
            <button className="ghost small-btn" onClick={() => (revising === "board" ? setRevising(null) : openRevise("board", null))}
              disabled={!aiReady || busy !== null}>
              검토 반영 수정안 만들기
            </button>
          </div>
          {revising === "board" && (
            <div id="revise-board">
              <RevisionPanel key={JSON.stringify(revisePreset)} projectId={projectId} boardId={boardId} proposalId={null}
                provider={ai?.settings.provider ?? "gemini"} model={ai?.settings.model ?? ""} preset={revisePreset}
                onCancel={() => setRevising(null)} onCreated={onRevised} />
            </div>
          )}
          {boardReview.summary && <p className="small">{boardReview.summary}</p>}
          <FindingList findings={boardReview.findings}
            onUse={aiReady && busy === null ? (i) => openRevise("board", { findings: [i] }) : undefined}
            blocked={aiReady ? null : AI_SETUP_HINT}
            guideRef={{ project_id: projectId, board_id: boardId }} guideLabel={`AI 검토 의견 (보드 revision ${boardReview.revision ?? ""})`} />
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
          {detailEl}
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
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const comp = circuit.components.find((c) => c.id === compId);
  const current = comp ? ((comp as unknown as Record<string, string | null | undefined>)[field] ?? null) : null;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!comp) return;
    setError(null);
    try {
      const label = FIELDS.find((f) => f[0] === field)![1];
      const ops: unknown[] = [{ op: "set_property", component_id: comp.id, field, expect: current, value: value.trim() || null }];
      if (reason.trim()) {
        // The person's reason goes into the circuit as a design decision, so the rationale report shows it later.
        const id = `dec-${comp.id}-${field}`.slice(0, 64);
        const existing = circuit.decisions?.find((d) => d.id === id);
        ops.push({
          op: "set_decision",
          expect_choice: existing?.choice ?? null,
          decision: { id, topic: `${comp.reference} ${label}`, choice: value.trim() || "없음", by: "engineer", reason: reason.trim(), components: [comp.id] },
        });
      }
      const r = await api.createProposal(projectId, boardId, `${comp.reference} ${label}: ${current ?? "없음"} → ${value.trim() || "없음"}`, ops,
        reason.trim() || undefined);
      setValue("");
      setReason("");
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
      <input aria-label="바꾸는 이유" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="이유 (근거 리포트에 기록)" style={{ width: 240 }} />
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
  onReject(note: string, toGuide: boolean): void;
  onRecheck(): void;
  aiReady: boolean;
  boardRevision: number | null;
  onAiReview(): void;
  revising: boolean;
  onToggleRevise(): void;
  onRevise(preset: RevisePreset): void;
  onFixed(proposalId: string): Promise<void>;
  revisionPanel: React.ReactNode;
  /** Slide to open first (overview, changes, checks, review, datasheets, guides, decision). */
  initialStage?: string;
}

const SLIDE_KEYS = ["overview", "changes", "checks", "review", "datasheets", "guides", "decision"];

/** What the AI revision form starts with: chosen review findings (indexes, or all) and the check problems. */
export interface RevisePreset {
  findings?: number[] | "all";
  checks?: boolean;
}

const VIEW_KEY = "aics.proposalView";

function readView(): "slides" | "all" {
  try {
    return window.localStorage.getItem(VIEW_KEY) === "all" ? "all" : "slides";
  } catch {
    return "slides";
  }
}

function writeView(view: "slides" | "all"): void {
  try {
    window.localStorage.setItem(VIEW_KEY, view);
  } catch {
    /* per-viewer convenience only */
  }
}

interface Slide {
  key: string;
  title: string;
  /** What this stage found, shown on the step bar and the decision slide. */
  badge?: { text: string; tone: "ok" | "bad" | "warn" | "muted" | "info" };
  body: React.ReactNode;
}

/**
 * A proposal is reviewed like a slide deck: one stage at a time (overview → changes → checks → AI review →
 * datasheets → design guides → decision), each with only its own actions. ← / → (or the buttons) move between
 * stages; "한 화면에 모두 보기" shows every stage stacked, for people who prefer one long page.
 */
function ProposalDetail({ p, projectId, boardId, busy, onApprove, onReject, onRecheck, aiReady, boardRevision, onAiReview, revising,
  onToggleRevise, onRevise, onFixed, revisionPanel, initialStage }: DetailProps) {
  const [rejecting, setRejecting] = useState(false);
  const [note, setNote] = useState("");
  const [noteToGuide, setNoteToGuide] = useState(false);
  const [view, setViewState] = useState(readView);
  const [stage, setStage] = useState(() => Math.max(0, SLIDE_KEYS.indexOf(initialStage ?? "overview")));
  const st = proposalStatusLabel(p.status);
  const ev = p.evidence;
  const check = ev?.automated_check ? automatedLabel(ev.automated_check.status) : null;
  const d = p.diff;
  const open = ["ready", "invalid", "check_failed"].includes(p.status);
  const fix = fixability(p, boardRevision);
  const fixBlocked = !aiReady ? AI_SETUP_HINT : fix.why;
  const setView = (v: "slides" | "all") => {
    setViewState(v);
    writeView(v);
  };
  const review = p.ai?.review ?? null;
  const errors = review?.findings.filter((f) => f.severity === "error").length ?? 0;
  const warnings = review?.findings.filter((f) => f.severity === "warning").length ?? 0;
  const dsProblems = (ev?.datasheets ?? []).filter((r) => r.pin_map === "mismatch" || r.ratings_problems.length > 0
    || r.external_missing.length > 0).length;
  const parentDiff = p.ai?.diff_from_parent ?? (p.parent ? p.diff_from_parent : null);

  const slides: Slide[] = [
    {
      key: "overview",
      title: "개요",
      body: (
        <>
          {p.rationale && <p className="small pre">{p.rationale}</p>}
          {p.decision_note && <p className="small warn-text">{p.decision_note}</p>}
          {p.template && (
            <>
              <h4>템플릿: {p.template.title} <span className="muted small">{p.template.ref}</span></h4>
              <p className="small">{p.template.summary}</p>
              <p className="small muted">템플릿은 검토의 시작점입니다. 승인 후 「검토」 탭에서 아래 체크리스트를 데이터시트와 대조해 기록하세요.</p>
              <TemplateEvidence template={p.template} />
            </>
          )}
          {p.ai && <AiMemo p={p} />}
          {!p.rationale && !p.template && !p.ai && <p className="small muted">설명이 없는 제안입니다. 다음 장에서 바뀌는 내용을 보세요.</p>}
        </>
      ),
    },
    {
      key: "changes",
      title: "바뀌는 내용",
      badge: { text: diffSummaryText(d.summary).split(" · ")[0] || "변경 없음", tone: "info" },
      body: (
        <>
          {parentDiff && (
            <details open>
              <summary className="small">
                {p.parent ? `제안 #${Number(p.parent)}` : "이전 회로"} 대비 바뀐 점: {diffSummaryText(parentDiff.summary)}
              </summary>
              <DiffView d={parentDiff} />
            </details>
          )}
          <p className="small muted">{p.base_revision ? `저장된 회로(revision ${p.base_revision}) 대비` : "새 회로"}: {diffSummaryText(d.summary)}</p>
          <DiffView d={d} />
          {p.ai?.parts && p.ai.parts.length > 0 && (
            <details>
              <summary className="small">부품 선택 근거 {p.ai.parts.length}개</summary>
              <table className="table compact">
                <thead><tr><th>기호</th><th>역할</th><th>KiCad 심볼</th><th>고른 방법</th></tr></thead>
                <tbody>{p.ai.parts.map((x) => <tr key={x.key}><td>{x.key}</td><td>{x.role}</td><td>{x.symbol_id}</td><td>{x.how}</td></tr>)}</tbody>
              </table>
            </details>
          )}
          {p.ai?.citations && p.ai.citations.length > 0 && (
            <details>
              <summary className="small">데이터시트 인용 {p.ai.citations.length}개</summary>
              <ul className="small">{p.ai.citations.map((c, i) => <li key={i}>{c.reference} p.{c.page ?? "?"}: {c.text}</li>)}</ul>
            </details>
          )}
          {ev && <Preview p={p} projectId={projectId} boardId={boardId} />}
        </>
      ),
    },
    {
      key: "checks",
      title: "자동 검사",
      badge: !ev ? { text: "검사 중", tone: "muted" }
        : check ? { text: check.text, tone: check.tone } : { text: ev.blocking ? "규칙 오류" : "생성 실패", tone: "bad" },
      body: !ev ? <p className="small muted">KiCad로 검사하는 중입니다…</p> : (
        <>
          <p className="small">
            자동 검사:{" "}
            {check ? <Chip text={check.text} tone={check.tone} /> : <Chip text={ev.blocking ? "규칙 오류로 생략" : "생성 실패"} tone="bad" />}
            {ev.automated_check?.kicad_version && <> · KiCad {ev.automated_check.kicad_version}</>}
            {ev.automated_check?.erc_errors !== undefined && ev.automated_check?.erc_errors !== null && (
              <> · ERC 오류 {ev.automated_check.erc_errors}, {ercWarningText(ev.automated_check.erc_warnings, ev.automated_check.violations)}</>
            )}
            {ev.automated_check?.netlist_matches_ir !== undefined && ev.automated_check?.netlist_matches_ir !== null && (
              <> · Netlist {ev.automated_check.netlist_matches_ir ? "= IR" : "≠ IR"}</>
            )}
            {ev.automated_check?.reason && <span className="muted"> ({ev.automated_check.reason})</span>}
            <span className="muted"> · {formatTime(ev.checked_at)}</span>
          </p>
          {ev.power_flags.length > 0 && (
            <p className="small muted">PWR_FLAG 자동 추가: {ev.power_flags.join(", ")} (커넥터로 전원이 들어오는 Net)</p>
          )}
          <IssueList issues={[...ev.ir_issues, ...(ev.generation_error?.issues ?? [])]} />
          {ev.generation_error && !ev.generation_error.issues && <p className="small bad-text">{ev.generation_error.message}</p>}
          {ev.automated_check?.violations && ev.automated_check.violations.length > 0 && (
            <ErcViolations violations={ev.automated_check.violations}
              refresh={open ? { label: "다시 검사해 설정 경고 없애기", run: onRecheck, disabled: busy } : undefined} />
          )}
          {fix.ok && (
            <QuickFixPanel projectId={projectId} boardId={boardId} proposalId={fix.quickFixOn} checkedAt={ev.checked_at} busy={busy}
              onFixed={onFixed} />
          )}
          <div className="toolbar">
            {(ev.ir_issues.length > 0 || (ev.automated_check?.violations ?? []).some((v) => ERC_EXPLAIN[String(v.type)]?.kind !== "setup")) && (
              <button className="ghost small-btn" disabled={busy || !aiReady || !fix.ok} onClick={() => onRevise({ checks: true })}
                title={fixBlocked ?? "검사에서 나온 문제를 AI가 고친 새 제안을 만듭니다"}>
                AI로 검사 문제 고치기
              </button>
            )}
            {open && <button className="ghost small-btn" onClick={onRecheck} disabled={busy}>다시 검사</button>}
          </div>
        </>
      ),
    },
    {
      key: "review",
      title: "AI 검토",
      badge: !review ? { text: "안 함", tone: "muted" } : errors ? { text: `오류 ${errors}`, tone: "bad" }
        : warnings ? { text: `주의 ${warnings}`, tone: "warn" } : { text: review.findings.length ? `참고 ${review.findings.length}` : "지적 없음", tone: "ok" },
      body: (
        <>
          {review ? (
            <AiReview p={p} aiReady={aiReady} busy={busy} fix={fix} onRevise={onRevise} />
          ) : (
            <p className="small muted">
              아직 AI 검토를 하지 않았습니다.{" "}
              {open ? (aiReady ? "아래 버튼으로 검토하면 의견마다 「이 의견 반영하기」가 생깁니다." : AI_SETUP_HINT) : "열린 제안만 AI로 검토할 수 있습니다."}
            </p>
          )}
          {open && aiReady && (
            <div className="toolbar">
              <button className="ghost small-btn" onClick={onAiReview} disabled={busy}>{review ? "AI 다시 검토" : "AI 검토"}</button>
            </div>
          )}
          <p className="muted small">AI 설계와 검토는 틀릴 수 있습니다. 검사 결과와 데이터시트 원문으로 확인한 뒤 승인하세요.</p>
        </>
      ),
    },
    {
      key: "datasheets",
      title: "데이터시트",
      badge: !ev || ev.datasheets.length === 0 ? { text: "대조 없음", tone: "muted" }
        : dsProblems ? { text: `확인 ${dsProblems}`, tone: "warn" } : { text: `${ev.datasheets.length}개 대조`, tone: "ok" },
      body: !ev ? <p className="small muted">검사가 끝나면 데이터시트 대조가 나옵니다.</p> : (
        <>
          {ev.datasheets.length > 0 ? (
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
          ) : (
            <p className="small muted">대조할 데이터시트가 아직 없습니다. 아래에서 받거나 올리면 핀맵·전압·외부 부품을 대조합니다.</p>
          )}
          {p.ai?.datasheets && p.ai.datasheets.parts.length > 0 && <DatasheetStepList step={p.ai.datasheets} />}
          {(open || p.status === "approved") && (
            <ProposalDatasheets projectId={projectId} boardId={boardId} proposalId={p.id} checkedAt={ev.checked_at} busy={busy}
              aiReady={aiReady} onRechecked={() => onFixed(p.id)} />
          )}
        </>
      ),
    },
    {
      key: "guides",
      title: "설계 지침",
      body: !ev ? <p className="small muted">검사가 끝나면 설계 지침 확인이 나옵니다.</p> : p.status === "stale"
        ? <p className="small muted">기준 회로가 바뀐 낡은 제안이라 지침 확인을 하지 않습니다.</p>
        : <GuideChecklistPanel projectId={projectId} boardId={boardId} proposalId={p.id} refreshKey={ev.checked_at} aiReady={aiReady} />,
    },
  ];
  const decision: Slide = {
    key: "decision",
    title: "결정",
    badge: { text: st.text, tone: st.tone },
    body: (
      <>
        <table className="table compact" aria-label="단계별 결과">
          <tbody>
            {slides.slice(1).map((s, i) => (
              <tr key={s.key}>
                <td>{s.title}</td>
                <td>{s.badge ? <Chip text={s.badge.text} tone={s.badge.tone} /> : <span className="muted small">직접 확인</span>}</td>
                <td>
                  {view === "slides" && (
                    <button className="link" onClick={() => setStage(i + 1)} aria-label={`${s.title} 다시 보기`}>다시 보기</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {open ? (
          <>
            <div className="toolbar">
              <button onClick={onApprove} disabled={busy || p.status !== "ready"}
                title={p.status === "ready" ? "새 revision으로 저장하고 회로도를 다시 생성합니다" : "자동 검사가 끝나지 않았거나 규칙 오류가 있어 승인할 수 없습니다"}>
                승인
              </button>
              <button className="ghost" onClick={() => setRejecting(!rejecting)} disabled={busy}>
                거부
              </button>
              <button className={revising ? "small-btn" : "ghost small-btn"} onClick={onToggleRevise} disabled={busy || !aiReady || !fix.ok}
                title={fixBlocked ?? "검토 의견·검사 문제·직접 요청을 반영한 새 제안을 AI가 만듭니다"}>
                검토 반영 수정안 만들기
              </button>
            </div>
            {rejecting && (
              <form className="toolbar" onSubmit={(e) => { e.preventDefault(); setRejecting(false); onReject(note, noteToGuide); }}>
                <input aria-label="거부 이유" placeholder="거부 이유 (선택)" value={note} onChange={(e) => setNote(e.target.value)} style={{ minWidth: 260 }} />
                <label className="small" title="다음 설계에서도 같은 실수를 하지 않도록 설계 가이드에 후보로 남깁니다">
                  <input type="checkbox" checked={noteToGuide} disabled={!note.trim()} onChange={(e) => setNoteToGuide(e.target.checked)} /> 설계 지침 후보로도 남기기
                </label>
                <button type="submit" className="ghost">거부 확정</button>
                <button type="button" className="ghost" onClick={() => setRejecting(false)}>취소</button>
              </form>
            )}
            <p className="small muted">승인하기 전에는 회로가 바뀌지 않습니다. 승인하면 새 revision으로 저장하고 회로도를 다시 만듭니다.</p>
          </>
        ) : (
          <>
            <p className="small">
              {p.status === "approved" ? `승인했습니다 → revision ${p.result_revision}.` : p.status === "rejected" ? "거부한 제안입니다. 회로는 바뀌지 않았습니다."
                : p.status === "checking" ? "검사가 끝나면 승인할 수 있습니다." : "기준 회로가 바뀐 낡은 제안입니다."}
            </p>
            {p.decision_note && <p className="small warn-text">{p.decision_note}</p>}
            {p.status !== "checking" && (
              <div className="toolbar">
                <button className={revising ? "small-btn" : "ghost small-btn"} onClick={onToggleRevise} disabled={busy || !aiReady || !fix.ok}
                  title={fixBlocked ?? "지금 회로(이 제안을 승인한 revision)에 검토 의견·검사 문제·직접 요청을 반영한 새 제안을 AI가 만듭니다"}>
                  검토 반영 수정안 만들기
                </button>
                {fixBlocked && <span className="small muted">{fixBlocked}</span>}
              </div>
            )}
          </>
        )}
      </>
    ),
  };
  slides.push(decision);
  const last = slides.length - 1;
  const current = Math.min(stage, last);

  // ← / → move between stages like a slide deck (not while typing).
  useEffect(() => {
    if (view !== "slides") return;
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (e.altKey || e.ctrlKey || e.metaKey || (t && (t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName)))) return;
      if (e.key === "ArrowRight") setStage((s) => Math.min(s + 1, last));
      if (e.key === "ArrowLeft") setStage((s) => Math.max(s - 1, 0));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [view, last]);

  return (
    <div className="proposal-detail">
      <div className="card">
        <div className="toolbar">
          <h3>
            #{Number(p.id)} {p.title}
          </h3>
          <Chip text={st.text} tone={st.tone} />
          <span className="spacer" />
          <label className="small" title="모든 단계를 한 페이지에 이어서 봅니다">
            <input type="checkbox" checked={view === "all"} onChange={(e) => setView(e.target.checked ? "all" : "slides")} /> 한 화면에 모두 보기
          </label>
        </div>
        <p className="small muted">
          {proposalSourceLabel(p.source)} · 기준 revision {p.base_revision ?? "없음 (새 회로)"} · {formatTime(p.created_at)}
          {p.result_revision ? ` · 승인 → revision ${p.result_revision}` : ""}
          {p.ai?.model ? ` · 모델 ${p.ai.model}` : ""}
          {p.status === "checking" && " · KiCad로 검사하는 중…"}
        </p>
        {view === "slides" && (
          <nav className="stage-bar" aria-label="검토 단계">
            {slides.map((s, i) => (
              <button key={s.key} className={`stage ${i === current ? "current" : ""} ${i < current ? "seen" : ""}`}
                aria-current={i === current ? "step" : undefined} onClick={() => setStage(i)}>
                <span className="stage-no">{i + 1}</span> {s.title}
                {s.badge && <span className={`stage-dot tone-${s.badge.tone}`} title={s.badge.text} />}
              </button>
            ))}
          </nav>
        )}
      </div>

      {view === "all" ? (
        slides.map((s, i) => (
          <section key={s.key} className="card" aria-label={s.title}>
            <h3>{i + 1}. {s.title}</h3>
            {s.body}
          </section>
        ))
      ) : (
        <section className="card slide" aria-label={slides[current].title}>
          <div className="toolbar">
            <h3>{slides[current].title}</h3>
            <span className="spacer" />
            <span className="small muted">{current + 1} / {slides.length}</span>
          </div>
          {slides[current].body}
          <div className="slide-nav">
            <button className="ghost" onClick={() => setStage(current - 1)} disabled={current === 0}>← 이전</button>
            <span className="small muted">←·→ 키로도 넘길 수 있습니다</span>
            {current < last ? (
              <button onClick={() => setStage(current + 1)}>다음: {slides[current + 1].title} →</button>
            ) : (
              <button className="ghost" onClick={() => setStage(0)}>처음으로</button>
            )}
          </div>
        </section>
      )}
      {revising && <div className="card" id={`revise-${p.id}`}>{revisionPanel}</div>}
    </div>
  );
}

/** What the AI said about its design: summary, templates used, what it was asked to address, open questions. */
function AiMemo({ p }: { p: Proposal }) {
  const ai = p.ai!;
  return (
    <>
      <h4>{ai.parent ? `AI 수정안 메모 — 제안 #${Number(ai.parent)}의 수정안` : "AI 설계 메모"}</h4>
      {ai.summary && <p className="small">{ai.summary}</p>}
      {ai.method === "edits" && (
        <p className="small">
          <Chip text="고칠 곳만 편집" tone="info" /> {ai.changes?.join(", ") || "바뀐 곳 없음"}
        </p>
      )}
      {ai.not_addressed && ai.not_addressed.length > 0 && (
        <>
          <h4>AI가 고치지 않은 의견</h4>
          <ul className="small">{ai.not_addressed.map((x, i) => <li key={i}>{x.title}: <span className="muted">{x.why}</span></li>)}</ul>
        </>
      )}
      {ai.guide_check && ai.guide_check.before.length > 0 && (
        <p className="small">
          설계 지침 자동 점검: 위반 {ai.guide_check.before.join(", ")}
          {ai.guide_check.changes ? ` → 편집으로 고침 (${ai.guide_check.changes.join(", ")})` : ""}
          {ai.guide_check.after.length ? ` · 남은 위반 ${ai.guide_check.after.join(", ")}` : " · 남은 위반 없음"}
        </p>
      )}
      {ai.templates && ai.templates.length > 0 && (
        <>
          <h4>사용한 검토된 템플릿</h4>
          <ul className="small">
            {ai.templates.map((t) => (
              <li key={t.ref}>
                {t.title} <span className="muted">({t.ref})</span>: {t.parts.join(", ")} —{" "}
                {Object.entries(t.params).map(([k, v]) => `${k}=${v}`).join(", ")}
              </li>
            ))}
          </ul>
          <p className="small muted">템플릿 부품은 표의 「출처」에 「템플릿」으로, AI가 고른 부품은 「AI」로 표시됩니다.</p>
        </>
      )}
      {(ai.addressed?.length || ai.instructions) && (
        <>
          <h4>반영을 요청한 내용</h4>
          <ul className="small">
            {ai.addressed?.map((a, i) => (
              <li key={i}>
                <span className="muted">[{a.source === "check" ? "검사" : "AI 검토"}]</span> {a.title}: {a.detail}
              </li>
            ))}
            {ai.instructions && <li><span className="muted">[직접 요청]</span> {ai.instructions}</li>}
          </ul>
        </>
      )}
      {ai.remaining_problems && ai.remaining_problems.length > 0 && (
        <>
          <h4 className="warn-text">AI가 고치지 못한 연결 문제</h4>
          <ul className="small">{ai.remaining_problems.map((x, i) => <li key={i}>{x}</li>)}</ul>
        </>
      )}
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
    </>
  );
}

/** The AI review of a proposal, each finding with its own fix button. */
function AiReview({ p, aiReady, busy, fix, onRevise }: {
  p: Proposal;
  aiReady: boolean;
  busy: boolean;
  fix: { ok: boolean; why: string | null };
  onRevise(preset: RevisePreset): void;
}) {
  const review = p.ai!.review!;
  const blocked = !aiReady ? AI_SETUP_HINT : fix.why;
  return (
    <>
      {review.summary && <p className="small">{review.summary}</p>}
      <FindingList findings={review.findings}
        onUse={fix.ok && aiReady && !busy ? (i) => onRevise({ findings: [i] }) : undefined}
        blocked={blocked}
        guideRef={{ project_id: p.project_id, board_id: p.board_id, proposal_id: p.id }} guideLabel={`AI 검토 의견 (제안 #${Number(p.id)})`} />
      {review.findings.length > 0 && (
        <div className="toolbar">
          <button className="small-btn" disabled={busy || !aiReady || !fix.ok} onClick={() => onRevise({ findings: "all", checks: true })}
            title={blocked ?? "모든 의견과 검사 문제를 반영한 새 제안을 AI가 만듭니다"}>
            의견 모두 반영한 수정안 만들기 (AI)
          </button>
          <span className="small muted">
            {blocked ?? (p.status === "approved"
              ? "지금 회로(이 제안을 승인한 revision)에서 시작하는 새 제안이 됩니다. 승인하기 전에는 회로가 바뀌지 않습니다."
              : "새 제안으로 만들어지고, 승인하기 전에는 회로가 바뀌지 않습니다.")}
          </span>
        </div>
      )}
    </>
  );
}

const BASIS_LABEL: Record<string, string> = {
  evidence: "검사·계산·데이터시트", guide: "설계 지침", requirement: "요구사항", circuit: "회로의 핀·Net", judgment: "판단",
};

const STEP_DOWNLOAD: Record<string, string> = {
  had: "보관된 데이터시트", downloaded: "새로 받음", failed: "받지 못함", no_source: "받을 곳 없음", off: "받지 않음(자동 받기 꺼짐)",
};
const STEP_SUMMARY: Record<string, string> = {
  had: "요약 있음", made: "AI가 새로 요약", failed: "요약 실패", skipped: "요약 안 함", off: "요약 안 함",
};

/** What the design job's datasheet step did for each part, and what a person still has to get by hand. */
export function DatasheetStepList({ step }: { step: DatasheetStep }) {
  const missing = step.parts.filter((r) => r.summary !== "had" && r.summary !== "made");
  return (
    <details className="small datasheet-step" open={missing.length > 0}>
      <summary>
        설계 때 자동으로 확인한 데이터시트 {step.parts.length}종 · 요약 있음 {step.ready}
        {missing.length ? ` · 없음 ${missing.length}` : ""}{step.fetch ? "" : " (자동 받기 꺼짐)"}
      </summary>
      <ul>
        {step.parts.map((r) => {
          const ok = r.summary === "had" || r.summary === "made";
          // A KiCad symbol link may be http; the download tried it as https, and only https opens in the browser.
          const href = r.product_url?.replace(/^http:\/\//, "https://");
          return (
            <li key={r.key} className={ok ? undefined : "warn-text"}>
              <b>{r.references.join(", ")}</b> {r.part} — {[r.download ? STEP_DOWNLOAD[r.download] : null, r.summary ? STEP_SUMMARY[r.summary] : null].filter(Boolean).join(" · ")}
              {r.pages_sent ? ` (${r.pages}쪽 중 ${r.pages_sent.length}쪽만 보냄: ${r.pages_sent.join(", ")})` : ""}
              {r.summary === "made" || (r.summary === "had" && r.reviewed === false) ? <span className="muted"> · 사람이 원문과 확인하기 전</span> : null}
              {r.message ? <span className="muted"> — {r.message}</span> : null}
              {href && /^https:\/\//.test(href) ? <> <a href={href} target="_blank" rel="noreferrer">브라우저에서 열기</a></> : null}
            </li>
          );
        })}
      </ul>
      {missing.length > 0 && <p className="muted">없는 부품은 아래 「데이터시트 찾기」에서 받거나 PDF를 올린 뒤 「데이터시트 대조 다시 하기」를 누르세요.</p>}
    </details>
  );
}

export function FindingList({ findings, onUse, blocked, guideRef, guideLabel }: {
  findings: { severity: string; title: string; detail: string; references: string[]; suggestion: string | null; guide?: string | null;
    basis?: string; evidence?: string | null; verified?: boolean | null; decided?: { id: string; reason: string } | null;
    ai_declined?: string | null }[];
  onUse?(index: number): void;
  /** Why "이 의견 반영하기" cannot be used now (AI off, an approved proposal the board moved on from...): the button
   * stays visible, disabled, with this as its tooltip, so the action is not silently missing. */
  blocked?: string | null;
  /** Where the findings came from, to keep with a finding saved as a design-guide candidate. */
  guideRef?: Record<string, string>;
  guideLabel?: string;
}) {
  const [saved, setSaved] = useState<Record<number, string>>({});
  if (findings.length === 0) return <p className="small ok-text">지적 사항이 없습니다.</p>;
  return (
    <ul className="issues">
      {findings.map((f, i) => (
        <li key={i} className={`issue issue-${f.severity === "error" ? "error" : f.severity === "warning" ? "warning" : "info"}`}>
          <b>{f.title}</b> {f.references.length > 0 && <span className="muted">({f.references.join(", ")})</span>}
          {f.verified === false && (
            <span className="chip chip-warn finding-unverified" title="AI가 댄 근거를 앱이 확인하지 못했습니다. 자동 반복 검토와 자동 수정안에서는 빼고, 사람이 판단하도록 남깁니다.">
              근거 확인 안 됨
            </span>
          )}
          {f.decided && (
            <span className="chip chip-ok finding-decided" title="사람이 의도한 설계로 정한 의견입니다. 자동 반복 검토에서 세지 않습니다.">
              사람이 정함
            </span>
          )}
          <div>{f.detail}</div>
          {f.decided && <div className="small muted">의도한 설계로 둔 이유: {f.decided.reason}</div>}
          {f.ai_declined && !f.decided && <div className="small muted">AI가 고치지 않은 이유: {f.ai_declined}</div>}
          {f.evidence && f.severity !== "info" && <div className="small muted">근거{f.basis ? ` (${BASIS_LABEL[f.basis] ?? f.basis})` : ""}: {f.evidence}</div>}
          {f.suggestion && <div className="issue-hint">→ {f.suggestion}</div>}
          {f.guide && <div className="small muted">설계 지침 {f.guide}을(를) 지키지 않았다는 의견입니다.</div>}
          {(onUse || blocked) && (
            <button className="ghost small-btn" onClick={() => onUse?.(i)} disabled={!onUse} title={onUse ? undefined : (blocked ?? undefined)}
              aria-label={`의견 반영: ${f.title}`}>
              이 의견 반영하기
            </button>
          )}
          {guideRef && (
            saved[i] ? (
              <span className="small ok-text"> 지침 후보 {saved[i]}로 저장함</span>
            ) : (
              <button className="ghost small-btn" aria-label={`지침 후보로: ${f.title}`}
                title="다음 설계에도 반영하도록 설계 가이드에 후보로 남깁니다 (설계 가이드 화면에서 채택)"
                onClick={async () => {
                  try {
                    const e = await saveFindingAsGuide(f, guideRef, guideLabel ?? "AI 검토 의견");
                    setSaved({ ...saved, [i]: e.id });
                  } catch (err) {
                    window.alert(errorText(err));
                  }
                }}>
                지침 후보로
              </button>
            )
          )}
        </li>
      ))}
    </ul>
  );
}

/** KiCad ERC findings, with what each kind means; this computer's KiCad set-up warnings are folded away. */
export function ErcViolations({ violations, refresh }: {
  violations: ErcViolation[];
  /** Regenerate / re-check: a project made by this app version carries its library tables, so the set-up
   * warnings of an older result go away. */
  refresh?: { label: string; run(): void; disabled?: boolean };
}) {
  const setup = violations.filter((v) => ERC_EXPLAIN[String(v.type)]?.kind === "setup");
  const rest = violations.filter((v) => ERC_EXPLAIN[String(v.type)]?.kind !== "setup");
  const explained = new Set<string>();
  const item = (v: ErcViolation, i: number) => {
    const type = String(v.type);
    const first = !explained.has(type);
    explained.add(type);
    return (
      <li key={i} className={`issue issue-${v.severity === "error" ? "error" : "warning"}`}>
        <span className="issue-code">{type}</span> {String(v.description)}{" "}
        <span className="muted">{(v.items as string[] | undefined)?.join(", ")}</span>
        {first && ERC_EXPLAIN[type] && <div className="issue-hint">→ {ERC_EXPLAIN[type].text}</div>}
      </li>
    );
  };
  return (
    <>
      {rest.length > 0 && <ul className="issues">{rest.map(item)}</ul>}
      {setup.length > 0 && (
        <details>
          <summary className="small muted">
            이 컴퓨터의 KiCad 설정 경고 {setup.length}건 (회로와 무관: {[...new Set(setup.map((v) => String(v.type)))].join(", ")})
          </summary>
          <ul className="issues">{setup.map(item)}</ul>
        </details>
      )}
      {setup.length > 0 && refresh && (
        <div className="toolbar">
          <button className="ghost small-btn" onClick={refresh.run} disabled={refresh.disabled}
            title="앱이 KiCad 기본 라이브러리를 프로젝트의 라이브러리 표에 함께 적어, 이 컴퓨터의 KiCad 설정과 상관없이 찾게 합니다">
            {refresh.label}
          </button>
          <span className="small muted">이 경고 {setup.length}건은 회로가 아니라 KiCad 라이브러리 설정 문제입니다. 다시 만들면 대부분 사라집니다.</span>
        </div>
      )}
    </>
  );
}

/** Floating input pins and the usual fixes, applied without AI as a follow-up proposal. */
export function QuickFixPanel({ projectId, boardId, proposalId, checkedAt, busy, onFixed }: {
  projectId: string;
  boardId: string;
  proposalId: string | null;
  checkedAt: string;
  busy: boolean;
  onFixed(proposalId: string): Promise<void>;
}) {
  const [fixes, setFixes] = useState<QuickFix[] | null>(null);
  const [nets, setNets] = useState<string[]>([]);
  const [choice, setChoice] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [working, setWorking] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api.quickFixes(projectId, boardId, proposalId).then((r) => {
      if (cancelled) return;
      setFixes(r.fixes);
      setNets(r.nets);
      setChoice(Object.fromEntries(r.fixes.map((f) => [f.id, String(Math.max(0, f.options.findIndex((o) => o.recommended)))])));
    }).catch((e) => !cancelled && setError(errorText(e)));
    return () => { cancelled = true; };
  }, [projectId, boardId, proposalId, checkedAt]);

  if (!fixes || fixes.length === 0) return error ? <p className="small bad-text">{error}</p> : null;
  const chosen = fixes.filter((f) => choice[f.id] !== "skip");
  const apply = async () => {
    setError(null);
    const picks = chosen.map((f) => {
      const c = choice[f.id];
      if (c.startsWith("net:")) return { fix_id: f.id, kind: "direct" as const, net: c.slice(4), value: null };
      const o = f.options[Number(c)];
      return { fix_id: f.id, kind: o.kind, net: o.net, value: o.value };
    });
    if (!window.confirm(`떠 있는 입력 핀 ${picks.length}개를 고친 새 제안을 만듭니다(AI 사용 안 함).\n승인하기 전에는 회로가 바뀌지 않습니다. 계속할까요?`)) return;
    setWorking(true);
    try {
      const r = await api.applyQuickFixes(projectId, boardId, proposalId, picks);
      await onFixed(r.proposal.id);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setWorking(false);
    }
  };
  return (
    <div className="quick-fixes" aria-label="바로 고치기">
      <h4>바로 고치기 (AI 없이)</h4>
      <p className="small muted">아무것도 연결되지 않은 입력 핀입니다. 핀 이름으로 흔한 처리를 골라 두었으니 확인하고 바꾸세요. 데이터시트의 권장 처리와 맞는지 확인하세요.</p>
      <table className="table compact">
        <thead><tr><th>핀</th><th>문제</th><th>조치</th></tr></thead>
        <tbody>
          {fixes.map((f) => (
            <tr key={f.id}>
              <td>{f.reference}.{f.pin}{f.pin_name && f.pin_name !== "~" ? ` ${f.pin_name}` : ""}</td>
              <td className="small">{f.problem}</td>
              <td>
                <select aria-label={`조치: ${f.reference}.${f.pin}`} value={choice[f.id]} onChange={(e) => setChoice({ ...choice, [f.id]: e.target.value })}>
                  {f.options.map((o, i) => <option key={i} value={String(i)}>{o.label}{o.recommended ? " (추천)" : ""}</option>)}
                  {nets.filter((n) => n !== f.net).map((n) => <option key={n} value={`net:${n}`}>{n}에 직접 연결</option>)}
                  <option value="skip">고치지 않음</option>
                </select>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="toolbar">
        <button onClick={() => void apply()} disabled={busy || working || chosen.length === 0}>
          선택한 조치로 수정 제안 만들기 ({chosen.length}개)
        </button>
      </div>
      {error && <p className="small bad-text">{error}</p>}
    </div>
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

export function DiffView({ d }: { d: ProposalDiff }) {
  return (
    <>
          {d.components_added.length > 0 && (
            <>
              <h4>{d.is_new_circuit ? "부품" : "추가되는 부품"}</h4>
              <table className="table compact">
                <thead>
                  <tr><th>기호</th><th>값</th><th>KiCad 심볼</th><th>풋프린트</th><th>품번</th><th>출처</th></tr>
                </thead>
                <tbody>
                  {d.components_added.map((c) => (
                    <tr key={c.id}>
                      <td>{c.reference}</td><td>{c.value}</td><td>{c.symbol_id}</td><td>{c.footprint_id ?? "—"}</td>
                      <td>{c.manufacturer_part_number ?? "—"}</td>
                      <td><span className={`chip ${c.origin?.startsWith("template:") ? "chip-ok" : c.origin === "ai" ? "chip-info" : "chip-muted"}`}>{originLabel(c.origin)}</span></td>
                    </tr>
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
          {d.net_specs && d.net_specs.length > 0 && (
            <>
              <h4>전원 조건 (기술 검토용)</h4>
              <table className="table compact">
                <thead><tr><th>Net</th><th>항목</th><th>전</th><th>후</th></tr></thead>
                <tbody>
                  {d.net_specs.map((x, i) => {
                    const amps = x.field === "load_current";
                    const show = (v: number | null) => (v == null ? "—" : amps ? `${+(v * 1000).toFixed(3)} mA` : `${v} V`);
                    const label = { voltage: "전압", voltage_min: "최저 전압", voltage_max: "최고 전압", load_current: "부하 전류" }[x.field] ?? x.field;
                    return (
                      <tr key={i}>
                        <td>{x.net}</td><td>{label}</td><td>{show(x.before)}</td><td><b>{show(x.after)}</b></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </>
          )}
          {d.decisions && d.decisions.length > 0 && (
            <>
              <h4>설계 결정과 근거</h4>
              <table className="table compact">
                <thead><tr><th>결정</th><th>누가</th><th>전</th><th>후</th><th>근거</th></tr></thead>
                <tbody>
                  {d.decisions.map((x) => (
                    <tr key={x.id}>
                      <td>{x.topic}</td><td>{DECISION_BY[x.by] ?? x.by}</td><td>{x.before ?? "—"}</td><td><b>{x.after ?? "—"}</b></td>
                      <td className="small">{x.reason ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
          {d.notes && d.notes.length > 0 && (
            <>
              <h4>회로도 주석 (영문)</h4>
              <table className="table compact">
                <thead><tr><th>위치</th><th>전</th><th>후</th></tr></thead>
                <tbody>
                  {d.notes.map((n) => (
                    <tr key={n.id}><td>{n.target}</td><td>{n.before ?? "—"}</td><td><b>{n.after ?? "—"}</b></td></tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
    </>
  );
}

export function RevisionPanel({ projectId, boardId, proposalId, provider, model, preset, onCancel, onCreated }: {
  projectId: string;
  boardId: string;
  proposalId: string | null;
  provider: string;
  model: string;
  preset?: RevisePreset | null;
  onCancel(): void;
  onCreated(id: string): Promise<void>;
}) {
  const [inputs, setInputs] = useState<RevisionInputs | null>(null);
  const [chosen, setChosen] = useState<Set<number>>(new Set());
  const [includeChecks, setIncludeChecks] = useState(true);
  const [instructions, setInstructions] = useState("");
  const [instructionsToGuide, setInstructionsToGuide] = useState(false);
  const [review, setReview] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .revisePreview(projectId, boardId, proposalId)
      .then((r) => {
        setInputs(r);
        if (preset) {
          setChosen(new Set(preset.findings === "all" ? r.findings.map((f) => f.index) : (preset.findings ?? [])));
          setIncludeChecks(Boolean(preset.checks) && r.checks.length > 0);
        } else {
          setChosen(new Set(r.findings.filter((f) => f.severity !== "info").map((f) => f.index)));
          setIncludeChecks(r.checks.length > 0);
        }
      })
      .catch((e) => setError(errorText(e)));
  }, [projectId, boardId, proposalId, preset]);

  const toggle = (i: number) =>
    setChosen((cur) => {
      const next = new Set(cur);
      if (next.has(i)) next.delete(i);
      else next.add(i);
      return next;
    });

  const count = chosen.size + (includeChecks ? (inputs?.checks.length ?? 0) : 0);
  const canSubmit = count > 0 || instructions.trim().length > 0;

  const submit = async () => {
    setError(null);
    const ok = window.confirm(
      `AI 수정안 만들기\n\n전송할 데이터: ${proposalId ? `제안 #${Number(proposalId)}의 회로` : "저장된 회로"}, 요구사항, ` +
        `반영할 항목 ${count}개${instructions.trim() ? ", 직접 요청" : ""}, 고른 부품의 핀 표와 데이터시트 요약\n받는 곳: ${providerLabel(provider)} (모델 ${model})\n\n` +
        "결과는 새 제안이 되고, 승인하기 전에는 회로가 바뀌지 않습니다. 계속할까요?",
    );
    if (!ok) return;
    setBusy("AI가 수정안을 만드는 중… 고칠 곳만 편집하고, 안 되면 다시 설계합니다 (1~3분). 다른 화면으로 가도 계속되고, 오른쪽 아래 「작업」에서 볼 수 있습니다.");
    try {
      const job = await waitForJob(
        (await api.aiRevise(projectId, boardId, {
          proposal_id: proposalId, instructions: instructions.trim() || null,
          finding_indexes: [...chosen].sort((a, b) => a - b), include_checks: includeChecks, review,
        })).id,
        undefined,
        1500,
      );
      if (job.status === "failed") {
        setError(job.error ? `[${job.error.error_code}] ${job.error.message}${job.error.recovery_hint ? ` — ${job.error.recovery_hint}` : ""}` : "실패했습니다.");
        return;
      }
      if (instructionsToGuide && instructions.trim()) {
        await api.createGuide({
          draft: emptyDraft({
            title: instructions.trim().split(/[.\n]/)[0].slice(0, 60),
            rule: instructions.trim(),
            sources: [{ kind: "feedback", text: `수정 요청${proposalId ? ` (제안 #${Number(proposalId)})` : ""}`,
              ref: { project_id: projectId, board_id: boardId, ...(proposalId ? { proposal_id: proposalId } : {}) } }],
          }),
          origin: "feedback",
          by: savedName() || "사용자",
          adopt: false,
        });
      }
      await onCreated(job.result.proposal_id);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="revision-panel">
      <h4>수정 요청</h4>
      {!inputs && !error && <p className="small muted">불러오는 중…</p>}
      {inputs && inputs.findings.length > 0 && (
        <>
          <p className="small">반영할 AI 검토 의견</p>
          <ul className="check-list">
            {inputs.findings.map((f) => (
              <li key={f.index}>
                <label className="small">
                  <input type="checkbox" checked={chosen.has(f.index)} onChange={() => toggle(f.index)} />{" "}
                  <span className={f.severity === "error" ? "bad-text" : f.severity === "warning" ? "warn-text" : "muted"}>[{f.severity}]</span>{" "}
                  <b>{f.title}</b> — {f.detail}
                  {f.suggestion && <span className="muted"> → {f.suggestion}</span>}
                </label>
              </li>
            ))}
          </ul>
        </>
      )}
      {inputs && inputs.checks.length > 0 && (
        <label className="small block">
          <input type="checkbox" checked={includeChecks} onChange={(e) => setIncludeChecks(e.target.checked)} /> 검사에서 나온 문제{" "}
          {inputs.checks.length}개도 반영
          <ul className="small muted">
            {inputs.checks.slice(0, 8).map((c, i) => (
              <li key={i}>{c.title}: {c.detail}</li>
            ))}
            {inputs.checks.length > 8 && <li>… 외 {inputs.checks.length - 8}개</li>}
          </ul>
        </label>
      )}
      {inputs && inputs.findings.length === 0 && inputs.checks.length === 0 && (
        <p className="small muted">AI 검토 의견과 검사 문제가 없습니다. 고칠 내용을 직접 적어 주세요.</p>
      )}
      <label className="small block">
        직접 요청
        <textarea className="small-textarea" aria-label="수정 요청 내용" value={instructions} onChange={(e) => setInstructions(e.target.value)}
          placeholder="예: LM2596의 ON/OFF 핀은 GND에 연결, 출력 콘덴서는 220uF 전해로" />
      </label>
      <label className="small" title="같은 종류의 회로를 다시 그릴 때도 적용되도록 설계 가이드에 후보로 남깁니다">
        <input type="checkbox" checked={instructionsToGuide} disabled={!instructions.trim()} onChange={(e) => setInstructionsToGuide(e.target.checked)} />{" "}
        이 요청을 다음 설계에도 반영 (설계 지침 후보로 저장)
      </label>
      <label className="small">
        <input type="checkbox" checked={review} onChange={(e) => setReview(e.target.checked)} /> 수정안도 AI 검토
      </label>
      <div className="toolbar">
        <button onClick={submit} disabled={!canSubmit || busy !== null}>
          수정안 만들기 ({count + (instructions.trim() ? 1 : 0)}개 반영)
        </button>
        <button className="ghost" onClick={onCancel} disabled={busy !== null}>
          닫기
        </button>
      </div>
      {busy && <p className="small">{busy}</p>}
      {error && <p className="small bad-text">{error}</p>}
    </div>
  );
}
