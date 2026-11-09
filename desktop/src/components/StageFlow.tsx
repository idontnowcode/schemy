import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api, errorText, outputText, waitForJob } from "../api";
import { desktop } from "../desktop";
import {
  AI_SETUP_HINT,
  automatedLabel,
  cropSvg,
  diffSummaryText,
  ercWarningText,
  outputsLabel,
  providerLabel,
  reviewLabel,
} from "../format";
import type {
  AiFailure,
  AiStatus,
  AutoReviewResult,
  AutoReviewRound,
  BoardSummary,
  CircuitIR,
  CircuitTemplate,
  DesignPreview,
  GuideChecklist,
  Job,
  JobStep,
  OutputsStatus,
  ProjectSummary,
  Proposal,
  RequirementsSpec,
  ReviewStatus,
  SystemInfo,
} from "../types";
import { Chip } from "./Chip";
import { canResume, DesignChoices } from "./DesignChoices";
import { Icon, type IconName } from "./Icons";
import type { TabId } from "./ProjectView";
import { ProposalsTab } from "./ProposalsTab";

export const STEPS = ["아이디어", "요구사항", "설계 준비", "AI 설계", "검토", "확인·승인", "회로도·출력"] as const;
const LAST = STEPS.length - 1;

interface Props {
  project: ProjectSummary;
  board: BoardSummary;
  circuit: CircuitIR | null;
  revision: number | null;
  outputs: OutputsStatus | null;
  review: ReviewStatus | null;
  system: SystemInfo | null;
  onChanged(): Promise<unknown>;
  onAdvanced(tab: TabId, proposalId?: string): void;
  onError(message: string | null): void;
  /** Opened from the job status panel: show the stage with that job's result. */
  jump?: { kind: string; n: number } | null;
}

/** Per-board flow state kept for this viewer: where they were and their choices. */
interface FlowState {
  stage: number;
  reached: number;
  useInventory: boolean;
  maxWarnings: number;
  maxRounds: number;
}

const DEFAULT_STATE: FlowState = { stage: -1, reached: 0, useInventory: true, maxWarnings: 1, maxRounds: 3 };

function loadState(key: string): FlowState {
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? { ...DEFAULT_STATE, ...(JSON.parse(raw) as Partial<FlowState>) } : DEFAULT_STATE;
  } catch {
    return DEFAULT_STATE;
  }
}

function saveState(key: string, state: FlowState): void {
  try {
    window.localStorage.setItem(key, JSON.stringify(state));
  } catch {
    /* per-viewer convenience only */
  }
}

/** The new-project requirements file is a heading and a comment: that is not an idea yet. */
export function ideaText(text: string): string {
  const meaningful = text.replace(/<!--[\s\S]*?-->/g, "").replace(/^#\s*요구사항\s*$/m, "").trim();
  return meaningful ? text : "";
}

const PLACEHOLDER = "예) STM32F103으로 스테핑 모터 2축을 제어하는 보드. 12V 어댑터 전원, PC와 USB로 통신하고, 동작 상태를 LED로 보여 준다.";
const OPEN = ["ready", "checking", "invalid", "check_failed"];
const AI_SOURCES = ["ai_design", "ai_revision"];
const finished = (job: Job | null) => job !== null && (job.status === "succeeded" || job.status === "failed");
const running = (job: Job | null) => job !== null && !finished(job);
const jobError = (job: Job) =>
  job.error ? `${job.error.message}${job.error.recovery_hint ? ` — ${job.error.recovery_hint}` : ""}` : "실패했습니다.";
const num = (id: string) => Number(id);
const clock = (iso: string) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toTimeString().slice(0, 8);
};
/** Worst case AI calls of the loop: a first review, then per revision plan 1 + connect ≤ 3 + review 1. */
const autoCalls = (rounds: number) => 1 + (rounds - 1) * 5;

/** Keep polling a job until it finishes. */
function usePoll(job: Job | null, setJob: (j: Job) => void, onDone?: (j: Job) => void) {
  useEffect(() => {
    if (!running(job)) return;
    const t = setTimeout(async () => {
      try {
        const next = await api.job(job!.id);
        setJob(next);
        if (finished(next)) onDone?.(next);
      } catch {
        /* try again on the next tick */
      }
    }, 900);
    return () => clearTimeout(t);
  }, [job, setJob, onDone]);
}

/**
 * The default project screen: one stage at a time, from an idea in plain words to a checked KiCad schematic.
 * Every stage uses the same back-end steps as the advanced tabs; AI results stay proposals until approved.
 */
export function StageFlow({ project, board, circuit, revision, outputs, review, system, onChanged, onAdvanced, onError, jump }: Props) {
  const key = `aics.flow.${project.id}.${board.id}`;
  const [flow, setFlowState] = useState<FlowState>(() => loadState(key));
  const flowRef = useRef(flow);
  // Saved at once (not inside a state update), so a stage reached after the screen closed — say, "AI 설계" once the
  // design request returns while the person is already in 설정 — is still where the board reopens.
  const setFlow = useCallback((patch: Partial<FlowState>) => {
    const next = { ...flowRef.current, ...patch };
    flowRef.current = next;
    saveState(key, next);
    setFlowState(next);
  }, [key]);

  const [loaded, setLoaded] = useState(false);
  const [text, setText] = useState("");
  const [saved, setSaved] = useState("");
  const [spec, setSpec] = useState<RequirementsSpec | null>(null);
  const [draft, setDraft] = useState<RequirementsSpec | null>(null);
  const [answers, setAnswers] = useState<Record<number, string>>({});
  const [ai, setAi] = useState<AiStatus | null>(null);
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [stock, setStock] = useState<number | null>(null);
  const [designJob, setDesignJob] = useState<Job | null>(null);
  const [autoJob, setAutoJob] = useState<Job | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [detail, setDetail] = useState<{ id: string; stage: string; from: number } | null>(null);
  const [prep, setPrep] = useState<{ preview: DesignPreview | null; templates: CircuitTemplate[]; error: string | null }>(
    { preview: null, templates: [], error: null });
  const [instructions, setInstructions] = useState("");
  const aiReady = Boolean(ai?.settings.enabled && ai.key.configured);
  const pid = project.id;
  const bid = board.id;

  const loadProposals = useCallback(async () => {
    const list = await api.proposals(pid, bid);
    setProposals(list);
    return list;
  }, [pid, bid]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [req, sp, status, list, inv, jobs] = await Promise.all([
          api.requirements(pid),
          api.requirementsSpec(pid).catch(() => ({ spec: null })),
          api.aiStatus().catch(() => null),
          api.proposals(pid, bid),
          api.inventory().catch(() => null),
          api.jobs(pid, bid).catch(() => [] as Job[]),
        ]);
        if (cancelled) return;
        setText(ideaText(req.text));
        setSaved(ideaText(req.text));
        setSpec(sp.spec);
        setAi(status);
        setProposals(list);
        setStock(inv?.inventory ? inv.inventory.parts.length : null);
        const design = jobs.find((j) => j.kind === "ai_design") ?? null;
        const auto = jobs.find((j) => j.kind === "ai_auto_review") ?? null;
        setDesignJob(design);
        setAutoJob(auto);
        // Work still running on this board: open at its stage.
        if (running(auto)) setFlow({ stage: 4, reached: Math.max(flowRef.current.reached, 4) });
        else if (running(design)) setFlow({ stage: 3, reached: Math.max(flowRef.current.reached, 3) });
      } catch (e) {
        onError(errorText(e));
      } finally {
        if (!cancelled) setLoaded(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [pid, bid, onError, setFlow]);

  // From the job status panel: the stage that shows that job's result.
  useEffect(() => {
    if (!jump) return;
    const stage = { ai_design: 3, ai_auto_review: 4, ai_revision: 5, export: 6 }[jump.kind];
    if (stage !== undefined) setFlow({ stage, reached: Math.max(flowRef.current.reached, stage) });
    void loadProposals();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jump?.n]);

  const aiProposals = useMemo(() => proposals.filter((p) => AI_SOURCES.includes(p.source)).sort((a, b) => num(a.id) - num(b.id)),
    [proposals]);
  // The proposal to approve: the newest open AI proposal. That is the loop's last one, unless a revision was made
  // after it (e.g. 「이 의견 반영하기」 on its slide), which then supersedes it.
  const focus = useMemo(() => {
    const newest = (list: Proposal[]) => [...list].sort((a, b) => num(b.id) - num(a.id)).find((p) => OPEN.includes(p.status));
    return newest(aiProposals) ?? newest(proposals) ?? null;
  }, [proposals, aiProposals]);

  // How far this board has come, from its data; the viewer's own furthest stage can only add to it.
  const derived = useMemo(() => {
    let r = 0;
    if (saved.trim()) r = 1;
    if (spec) r = 2;
    if (designJob || aiProposals.length) r = 3;
    if (aiProposals.length) r = 4;
    if (finished(autoJob) && focus) r = 5;
    if (revision) r = LAST;
    return r;
  }, [saved, spec, designJob, aiProposals.length, autoJob, focus, revision]);
  const reached = Math.max(derived, Math.min(flow.reached, LAST));
  const stage = flow.stage >= 0 && flow.stage <= reached ? flow.stage : reached;

  const go = (i: number) => {
    if (i < 0 || i > reached) return;
    setMessage(null);
    setDetail(null);
    setFlow({ stage: i });
  };
  const advance = (to = stage + 1) => {
    setMessage(null);
    setFlow({ stage: to, reached: Math.max(reached, to) });
  };

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

  const saveText = async (value = text) => {
    if (value === saved) return;
    await api.saveRequirements(pid, value);
    setSaved(value);
    await onChanged();
  };

  const onDesignDone = useCallback(async (job: Job) => {
    await loadProposals();
    if (job.status === "succeeded") await onChanged();
  }, [loadProposals, onChanged]);
  const onAutoDone = useCallback(async () => {
    await loadProposals();
  }, [loadProposals]);
  usePoll(designJob, setDesignJob, onDesignDone);
  usePoll(autoJob, setAutoJob, onAutoDone);

  // Arrow keys move between stages already reached (not while typing).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (e.altKey || e.ctrlKey || e.metaKey || detail) return;
      if (t && (t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName))) return;
      if (e.key === "ArrowRight" && stage < reached) go(stage + 1);
      if (e.key === "ArrowLeft" && stage > 0) go(stage - 1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  // What a design request would send (stage 3), fetched when that stage opens.
  const onPrep = loaded && stage === 2;
  useEffect(() => {
    if (!onPrep) return;
    api.designPreview(pid, bid).then((preview) => setPrep((c) => ({ ...c, preview, error: null })))
      .catch((e) => setPrep((c) => ({ ...c, error: errorText(e) })));
    api.templates().then((r) => setPrep((c) => ({ ...c, templates: r.templates }))).catch(() => undefined);
  }, [onPrep, pid, bid]);

  if (!loaded) return <div className="empty">불러오는 중…</div>;

  const provider = `${providerLabel(ai?.settings.provider)}${ai?.settings.model ? ` · ${ai.settings.model}` : ""}`;
  const common = { pid, bid, aiReady, provider, run, busy };
  let body: Stage;
  if (stage === 0) {
    body = ideaStage({
      ...common, text, setText, stock, useInventory: flow.useInventory, setUseInventory: (v) => setFlow({ useInventory: v }),
      dirty: text !== saved, save: () => run("저장하는 중…", () => saveText()),
      onReference: () => onAdvanced("datasheets"),
      structure: () =>
        run("AI가 요구사항을 정리하는 중…", async () => {
          await saveText();
          const job = await waitForJob((await api.structureRequirements(pid)).id, undefined, 800);
          if (job.status === "failed") return setMessage(jobError(job));
          setDraft(job.result.spec);
          setAnswers({});
          advance(1);
        }),
    });
  } else if (stage === 1) {
    body = requirementsStage({
      ...common, spec: draft ?? spec, unsaved: draft !== null, setSpec: (s) => setDraft(s), answers, setAnswers,
      onEditText: () => go(0),
      next: () =>
        run("저장하는 중…", async () => {
          const current = draft ?? spec;
          if (current) {
            const qs = current.open_questions;
            const answered = qs.map((q, i) => [q, (answers[i] ?? "").trim()] as const).filter(([, a]) => a);
            const next = { ...current, open_questions: qs.filter((_, i) => !(answers[i] ?? "").trim()) };
            await api.saveRequirementsSpec(pid, next);
            setSpec(next);
            setDraft(null);
            setAnswers({});
            if (answered.length) {
              const block = answered.map(([q, a]) => `- 질문: ${q}\n  답: ${a}`).join("\n");
              const value = `${text.trimEnd()}\n\n## AI 질문에 대한 답\n${block}\n`;
              setText(value);
              await saveText(value);
            }
          }
          advance(2);
        }),
    });
  } else if (stage === 2) {
    const ready = aiReady && prep.preview?.library !== false;
    body = {
      lead: "AI가 설계할 때 따를 것들입니다. 확인만 하고 넘어가도 됩니다.",
      body: <PrepStage {...common} {...prep} useInventory={flow.useInventory} hasSpec={spec !== null}
        instructions={instructions} setInstructions={setInstructions} />,
      next: {
        label: "AI 설계 시작", disabled: !ready,
        title: !aiReady ? AI_SETUP_HINT : "결과는 제안으로 만들어지고, 승인하기 전에는 회로가 바뀌지 않습니다",
        onClick: () =>
          run("AI 설계를 시작하는 중…", async () => {
            const job = await api.design(pid, bid, { instructions: instructions.trim() || undefined, use_inventory: flow.useInventory, review: true });
            setDesignJob(job);
            advance(3);
          }),
      },
    };
  } else if (stage === 3) {
    body = designStage({ ...common, job: designJob, latest: aiProposals[aiProposals.length - 1] ?? null, back: () => go(2),
      next: () => advance(4), onResumed: setDesignJob });
  } else if (stage === 4) {
    body = reviewStage({
      ...common, focus, job: autoJob, maxWarnings: flow.maxWarnings, maxRounds: flow.maxRounds,
      setLimits: (w, r) => setFlow({ maxWarnings: w, maxRounds: r }),
      start: () =>
        run("자동 반복 검토를 시작하는 중…", async () => {
          if (!focus) return;
          setAutoJob(await api.autoReview(pid, bid, { proposal_id: focus.id, max_warnings: flow.maxWarnings, max_rounds: flow.maxRounds }));
        }),
      openOne: () => focus && setDetail({ id: focus.id, stage: "review", from: 4 }),
      next: () => advance(5),
    });
  } else if (stage === 5) {
    body = {
      lead: focus ? `마지막 제안 #${num(focus.id)}을 한 장으로 요약했습니다.` : "승인할 제안이 없습니다.",
      body: <ApproveStage key={focus?.id ?? "none"} {...common} target={focus} revision={revision}
        onDetail={(s) => focus && setDetail({ id: focus.id, stage: s, from: 5 })}
        onDecided={async (approved) => {
          await loadProposals();
          await onChanged();
          if (approved) advance(6);
        }} />,
      next: revision && !focus ? { label: "다음: 회로도·출력 →", onClick: () => advance(6) } : null,
    };
  } else {
    body = {
      lead: revision ? `revision ${revision}의 KiCad 회로도입니다. 여기서부터는 KiCad에서 다듬거나, 출력물을 받거나, 연구노트를 남깁니다.`
        : "아직 승인된 회로가 없습니다. 「확인·승인」에서 제안을 승인하면 회로도가 만들어집니다.",
      body: <OutputStage {...common} board={board} revision={revision} outputs={outputs} review={review} circuit={circuit}
        system={system} onAdvanced={onAdvanced} onChanged={onChanged} />,
      next: null,
    };
  }

  return (
    <div className="flow">
      <nav className="flow-rail" aria-label="진행 단계">
        {STEPS.map((s, i) => {
          const done = i < reached && i !== stage;
          return (
            <button key={s} type="button" className={`flow-step ${done ? "done" : ""} ${i === stage && !detail ? "on" : ""}`}
              disabled={i > reached} aria-current={i === stage ? "step" : undefined} onClick={() => go(i)}>
              <span className="flow-dot">{done ? <Icon name="check" size={16} /> : i + 1}</span>
              <span className="flow-label">{s}</span>
            </button>
          );
        })}
      </nav>

      {detail ? (
        <div className="card flow-card flow-detail">
          <ProposalsTab projectId={pid} boardId={bid} revision={revision} circuit={circuit} focusId={detail.id}
            single initialStage={detail.stage} backLabel={`${STEPS[detail.from]}(으)로 돌아가기`}
            onBack={() => {
              setDetail(null);
              void loadProposals();
            }}
            onChanged={async () => {
              await loadProposals();
              await onChanged();
            }} />
        </div>
      ) : (
        <section className="card flow-card" aria-label={STEPS[stage]}>
          <header className="flow-head">
            <h2>{STEPS[stage]}</h2>
            <span className="flow-count">{stage + 1} / {STEPS.length}</span>
          </header>
          {body.lead && <p className="flow-lead">{body.lead}</p>}
          <div className="flow-body">{body.body}</div>
          {busy && <p className="small" role="status">{busy}</p>}
          {message && <p className="small bad-text" role="alert">{message}</p>}
          <footer className="flow-nav">
            <button type="button" className="ghost" onClick={() => go(stage - 1)} disabled={stage === 0}>
              ← 이전
            </button>
            {body.extra}
            <span className="spacer" />
            {body.next && (
              <button type="button" className="flow-next" onClick={body.next.onClick} disabled={body.next.disabled || busy !== null}
                title={body.next.title}>
                {body.next.label}
              </button>
            )}
          </footer>
        </section>
      )}
    </div>
  );
}

interface Stage {
  lead: React.ReactNode;
  body: React.ReactNode;
  next: { label: string; onClick(): void; disabled?: boolean; title?: string } | null;
  extra?: React.ReactNode;
}

interface Common {
  pid: string;
  bid: string;
  aiReady: boolean;
  provider: string;
  busy: string | null;
  run(label: string, fn: () => Promise<void>): Promise<void>;
}

// --- 1 아이디어 -------------------------------------------------------------------------------------------------

function ideaStage(p: Common & {
  text: string; setText(v: string): void; stock: number | null; useInventory: boolean; setUseInventory(v: boolean): void;
  dirty: boolean; save(): void; structure(): void; onReference(): void;
}): Stage {
  return {
    lead: "만들고 싶은 보드를 평소 말로 적으세요. 부품 이름이나 숫자를 몰라도 됩니다.",
    body: (
      <>
        <textarea className="flow-idea" aria-label="아이디어" value={p.text} placeholder={PLACEHOLDER} spellCheck={false}
          onChange={(e) => p.setText(e.target.value)} />
        <div className="flow-row">
          <button type="button" className="ghost" onClick={p.onReference} title="회로도 PDF·데이터시트는 고급 화면의 「데이터시트」에서 올립니다">
            <Icon name="upload" /> 참고 자료 올리기
          </button>
          <label className="flow-check">
            <input type="checkbox" checked={p.useInventory && p.stock !== null} disabled={p.stock === null}
              onChange={(e) => p.setUseInventory(e.target.checked)} />
            보유 자재를 먼저 쓰기 ({p.stock === null ? "목록 없음" : `${p.stock}개`})
          </label>
        </div>
        <p className="flow-note">다음 버튼을 누르면 이 아이디어만 AI({p.provider})에 보냅니다. 다른 프로젝트 내용은 보내지 않습니다.</p>
        {!p.aiReady && <p className="small warn-text">{AI_SETUP_HINT}. AI 없이 만들려면 「고급 화면(탭)」에서 회로를 직접 입력하세요.</p>}
      </>
    ),
    extra: p.dirty ? <button type="button" className="ghost" onClick={p.save}>저장</button> : null,
    next: {
      label: "AI로 요구사항 정리 →", onClick: p.structure,
      disabled: !p.aiReady || p.text.trim().length < 10, title: !p.aiReady ? AI_SETUP_HINT : undefined,
    },
  };
}

// --- 2 요구사항 -------------------------------------------------------------------------------------------------

type CardKey = "functions" | "power" | "interfaces" | "prefs";

function cardLines(spec: RequirementsSpec, key: CardKey): string[] {
  if (key === "functions") return spec.functions;
  if (key === "power")
    return spec.power.map((x) => [`${x.name}: ${[x.source, x.voltage].filter(Boolean).join(" ")}`, x.current, x.notes && `(${x.notes})`]
      .filter(Boolean).join(", "));
  if (key === "interfaces") return spec.interfaces.map((x) => `${x.name}${x.kind ? ` (${x.kind})` : ""}${x.details ? ` — ${x.details}` : ""}`);
  return [...spec.parts_preferences, ...spec.constraints];
}

/** Lines edited in a card back into the spec; untouched lines keep their structured fields. */
function withLines(spec: RequirementsSpec, key: CardKey, lines: string[]): RequirementsSpec {
  const clean = lines.map((l) => l.trim()).filter(Boolean);
  if (key === "functions") return { ...spec, functions: clean };
  if (key === "prefs") return { ...spec, parts_preferences: clean, constraints: [] };
  const before = cardLines(spec, key);
  if (key === "power") {
    return { ...spec, power: clean.map((l) => spec.power[before.indexOf(l)] ?? { name: l, source: "", voltage: "", current: "", notes: "" }) };
  }
  return { ...spec, interfaces: clean.map((l) => spec.interfaces[before.indexOf(l)] ?? { name: l, kind: "", details: "" }) };
}

const CARDS: { key: CardKey; title: string; icon: IconName }[] = [
  { key: "functions", title: "기능", icon: "chip" },
  { key: "power", title: "전원", icon: "bolt" },
  { key: "interfaces", title: "인터페이스", icon: "plug" },
  { key: "prefs", title: "부품 선호·제약", icon: "layers" },
];

function requirementsStage(p: Common & {
  spec: RequirementsSpec | null; unsaved: boolean; setSpec(s: RequirementsSpec): void;
  answers: Record<number, string>; setAnswers(a: Record<number, string>): void; onEditText(): void; next(): void;
}): Stage {
  const spec = p.spec;
  return {
    lead: spec
      ? "AI가 아이디어를 요구사항으로 정리했습니다. 고칠 곳은 고치고, 질문에 답하세요."
      : "아직 정리된 요구사항이 없습니다. 이전 단계에서 AI로 정리하거나, 그대로 다음으로 넘어가도 됩니다 (아이디어 글만으로 설계).",
    body: spec ? (
      <>
        {spec.summary && <p className="small">{spec.summary}</p>}
        <div className="flow-grid">
          {CARDS.map((c) => <SpecCard key={c.key} spec={spec} card={c} onChange={p.setSpec} />)}
        </div>
        {spec.open_questions.length > 0 && (
          <div className="flow-questions">
            <h3>
              <Icon name="question" /> AI의 질문
            </h3>
            {spec.open_questions.map((q, i) => (
              <label key={i} className="flow-question">
                <span>{q}</span>
                <input aria-label={q} value={p.answers[i] ?? ""} placeholder="답 (비워 두면 질문으로 남김)"
                  onChange={(e) => p.setAnswers({ ...p.answers, [i]: e.target.value })} />
              </label>
            ))}
            <p className="muted small">답은 요구사항 글 끝 「AI 질문에 대한 답」에 적혀 설계에 함께 쓰입니다.</p>
          </div>
        )}
        <div className="flow-row">
          <button type="button" className="ghost" onClick={p.onEditText}>
            <Icon name="pencil" /> 글로 고치기
          </button>
          {p.unsaved && <span className="muted small">정리 결과는 다음 버튼을 누르면 저장됩니다.</span>}
        </div>
      </>
    ) : null,
    next: { label: "다음: 설계 준비 →", onClick: p.next },
  };
}

function SpecCard({ spec, card, onChange }: { spec: RequirementsSpec; card: (typeof CARDS)[number]; onChange(s: RequirementsSpec): void }) {
  const [editing, setEditing] = useState<string | null>(null);
  const lines = cardLines(spec, card.key);
  return (
    <div className="flow-box">
      <div className="flow-box-head">
        <Icon name={card.icon} />
        <h3>{card.title}</h3>
        <span className="spacer" />
        <button type="button" className="icon" aria-label={`${card.title} 고치기`} title="한 줄에 하나씩 고칩니다"
          onClick={() => {
            if (editing !== null) {
              onChange(withLines(spec, card.key, editing.split("\n")));
              setEditing(null);
            } else setEditing(lines.join("\n"));
          }}>
          {editing !== null ? <Icon name="check" /> : <Icon name="pencil" />}
        </button>
      </div>
      {editing !== null ? (
        <textarea aria-label={`${card.title} 항목`} className="small-textarea" value={editing} onChange={(e) => setEditing(e.target.value)} />
      ) : lines.length ? (
        <ul className="flow-list">{lines.map((l, i) => <li key={i}>{l}</li>)}</ul>
      ) : (
        <p className="muted small">없음</p>
      )}
    </div>
  );
}

// --- 3 설계 준비 ------------------------------------------------------------------------------------------------

function PrepStage(p: Common & {
  preview: DesignPreview | null; templates: CircuitTemplate[]; error: string | null; useInventory: boolean; hasSpec: boolean;
  instructions: string; setInstructions(v: string): void;
}) {
  const { preview, templates } = p;
  const guides = preview?.guides ?? [];
  const inv = preview?.inventory_parts ?? 0;
  return (
    <>
      <div className="flow-grid">
        <div className="flow-box">
          <div className="flow-box-head"><Icon name="layers" /><h3>적용할 설계 지침 · {guides.length}개</h3></div>
          {guides.length ? (
            <div className="flow-chips">
              {guides.slice(0, 4).map((g) => (
                <span key={g.id} className="guide-chip" title={g.title}><code>{g.id}</code> {g.title}</span>
              ))}
              {guides.length > 4 && <span className="muted small">외 {guides.length - 4}개</span>}
            </div>
          ) : (
            <p className="muted small">이 요구사항에 맞는 채택된 지침이 없습니다. 지침은 「설계 가이드」에서 채택합니다.</p>
          )}
        </div>
        <div className="flow-box">
          <div className="flow-box-head"><Icon name="file" /><h3>검토된 템플릿 · {templates.length}개</h3></div>
          {templates.slice(0, 3).map((t) => (
            <div key={t.id} className="flow-template"><span>{t.title}</span><Chip text="검토됨" tone="ok" /></div>
          ))}
          <p className="muted small">맞는 블록이 있으면 AI가 템플릿을 그대로 씁니다.</p>
        </div>
        <div className="flow-box">
          <div className="flow-box-head"><Icon name="cube" /><h3>보유 자재</h3></div>
          <p>{p.useInventory && inv ? <>목록 <b className="ok-text">{inv}종</b>을 함께 보내 먼저 씀</> : p.useInventory ? "보유 자재 목록이 없습니다" : "쓰지 않음 (1단계에서 켤 수 있음)"}</p>
        </div>
        <div className="flow-box">
          <div className="flow-box-head"><Icon name="brain" /><h3>AI 사용</h3></div>
          <p>{p.provider} · 호출 약 3~5회</p>
          <p className="muted small">부품 계획 1회, 핀 연결 1~3회, 검토 1회</p>
        </div>
      </div>
      <details className="flow-extra">
        <summary className="small">추가 요청 (선택)</summary>
        <textarea aria-label="AI 설계 추가 요청" className="small-textarea" value={p.instructions} placeholder="예: 커넥터는 JST-XH로, LED 상태 표시 추가"
          onChange={(e) => p.setInstructions(e.target.value)} />
      </details>
      <p className="flow-note">
        <b>보낼 데이터:</b> 요구사항 {preview?.requirements_chars ?? 0}자{p.hasSpec ? ", 정리한 요구사항" : ""}, 설계 지침 {guides.length}개
        {p.useInventory && inv ? `, 보유 자재 ${inv}종` : ""}, 고른 부품의 KiCad 핀 표와 데이터시트 요약
      </p>
      {preview && !preview.library && <p className="small warn-text">KiCad 심볼 라이브러리를 찾지 못해 AI 설계를 할 수 없습니다. KiCad 10을 설치하세요.</p>}
      {p.error && <p className="small bad-text">{p.error}</p>}
      {!p.aiReady && <p className="small warn-text">{AI_SETUP_HINT}</p>}
    </>
  );
}

// --- 4 AI 설계 --------------------------------------------------------------------------------------------------

const DESIGN_ROWS: { key: string; title: string; steps: string[]; idle: string }[] = [
  { key: "parts", title: "부품 고르기", steps: ["parts"], idle: "요구사항·설계 지침·보유 자재로 부품을 고릅니다" },
  { key: "pins", title: "핀 연결", steps: ["pins"], idle: "KiCad 심볼의 핀 표로 연결합니다" },
  { key: "check", title: "KiCad 검사", steps: ["check"], idle: "회로도를 만들어 ERC를 실행합니다" },
  { key: "proposal", title: "제안 만들기", steps: ["proposal", "review"], idle: "승인 전에는 회로가 바뀌지 않음" },
];

function rowOf(step: string): number {
  return DESIGN_ROWS.findIndex((r) => r.steps.includes(step));
}

function designStage(p: Common & { job: Job | null; latest: Proposal | null; back(): void; next(): void; onResumed(job: Job): void }): Stage {
  const job = p.job;
  const steps = job?.progress ?? [];
  const lastRow = steps.length ? Math.max(...steps.map((s) => rowOf(s.step))) : -1;
  const ok = job?.status === "succeeded";
  const failed = job?.status === "failed";
  const details = (job?.error?.details ?? {}) as { problems?: string[]; questions?: string[] };
  const paused = failed && canResume(job);
  const state = (i: number) => (ok || i < lastRow ? "done" : i === lastRow ? (paused ? "paused" : failed ? "failed" : "run") : "idle");
  return {
    lead: running(job) ? "AI가 설계하는 중입니다 (보통 1~3분)." : ok ? "설계안이 나왔습니다. 다음 단계에서 AI가 검토하고, 필요하면 스스로 고친 안을 다시 만듭니다."
      : failed ? (canResume(job) ? "부품 고르기에서 멈췄습니다. 진행할 방법을 고르세요." : "AI 설계를 마치지 못했습니다.") : p.latest ? `최근 AI 설계안: 제안 #${num(p.latest.id)} 「${p.latest.title}」` : "아직 AI 설계를 하지 않았습니다.",
    body: (
      <div className="flow-design">
        {job && (
          <>
            <ol className="flow-progress">
              {DESIGN_ROWS.map((r, i) => {
                const mine = steps.filter((s) => r.steps.includes(s.step));
                const last = mine[mine.length - 1];
                // A finished row shows its result ("부품 39개 선택"), not its last "doing" line ("…로 연결합니다").
                const result = [...mine].reverse().find((s) => !/(요청|중|중…|합니다|실행)$/.test(s.text.trim()));
                const shown = i < lastRow || ok ? result ?? last : last;
                const st = state(i);
                return (
                  <li key={r.key} className={`flow-progress-row ${st}`}>
                    <span className={`tick ${st}`}>{st === "done" ? <Icon name="check" size={14} /> : null}</span>
                    <span>
                      <b>{r.title}</b>
                      <br />
                      <span className="muted small">{shown?.text ?? r.idle}</span>
                    </span>
                  </li>
                );
              })}
            </ol>
            <div className="flow-log" aria-label="설계 진행 기록">
              <h3>설계 진행 기록</h3>
              <ul>{steps.map((s, i) => <li key={i}><span className="muted">[{clock(s.at)}]</span> {s.text}</li>)}</ul>
              {running(job) && <p className="muted small">진행 중…</p>}
            </div>
          </>
        )}
        {failed && job && canResume(job) && (
          <div className="flow-result stop flow-choices">
            <DesignChoices key={job.id} job={job} projectId={p.pid} boardId={p.bid} onStarted={p.onResumed} onBack={p.back} />
          </div>
        )}
        {failed && job && !canResume(job) && (
          <div className="flow-result stop">
            <p>{jobError(job)}</p>
            <FailureDetails why={job.error?.details as AiFailure | undefined} />
            {details.problems?.length ? <><b className="small">완성하지 못한 이유</b><ul className="small">{details.problems.map((x, i) => <li key={i}>{x}</li>)}</ul></> : null}
            {details.questions?.length ? <><b className="small">AI의 질문 — 요구사항에 답을 적고 다시 시도하세요</b><ul className="small">{details.questions.map((x, i) => <li key={i}>{x}</li>)}</ul></> : null}
            <button type="button" className="ghost" onClick={p.back}>설계 준비로 돌아가기</button>
          </div>
        )}
        {ok && job && <div className="flow-result">제안 #{num(job.result.proposal_id)} 만듦 · {steps.filter((s) => s.step === "check").pop()?.text ?? ""}
          {steps.some((s) => s.step === "review") ? ` · ${steps.filter((s) => s.step === "review").pop()!.text}` : ""}</div>}
      </div>
    ),
    next: { label: "다음: 검토 →", onClick: p.next, disabled: running(job) || (!ok && !p.latest) },
  };
}

// --- 5 검토 (자동 반복) -------------------------------------------------------------------------------------------

const STOP_LABEL: Record<AutoReviewResult["stop"], string> = {
  met: "조건 충족",
  max_rounds: "최대 횟수에 도달",
  repeated: "같은 의견이 다시 나옴",
  check_failed: "수정안이 KiCad 검사를 통과하지 못함",
  revise_failed: "수정안을 만들지 못함",
  ai_failed: "AI 검토 실패",
  no_progress: "의견이 줄지 않음",
  no_change: "수정안이 회로를 바꾸지 않음",
  declined: "AI가 남은 의견을 고치지 않기로 함",
};

/** The loop's rounds as reported so far (the latest report of each round wins). */
export function roundsOf(steps: JobStep[]): AutoReviewRound[] {
  const byRound = new Map<number, AutoReviewRound>();
  for (const s of steps) if (s.data && (s.step === "round" || s.step === "revised")) byRound.set(s.data.round, s.data);
  return [...byRound.values()].sort((a, b) => a.round - b.round);
}

function reviewStage(p: Common & {
  focus: Proposal | null; job: Job | null; maxWarnings: number; maxRounds: number; setLimits(w: number, r: number): void;
  start(): void; openOne(): void; next(): void;
}): Stage {
  const job = p.job;
  const rounds = roundsOf(job?.progress ?? []);
  const result = job?.status === "succeeded" ? (job.result as AutoReviewResult) : null;
  const live = running(job) ? (job!.progress ?? []).at(-1)?.text ?? "시작하는 중…" : null;
  const last = rounds.at(-1);
  return {
    lead: "AI 검토 → 수정안 → 다시 검토를 조건이 맞을 때까지 반복합니다. 근거를 확인한 의견만 세고, 수정안은 고칠 곳만 편집합니다. 회차마다 새 제안이 생기고, 회로는 아직 바뀌지 않습니다.",
    body: !p.focus && !job ? (
      <p className="muted">검토할 AI 설계안이 없습니다. 「AI 설계」 단계에서 설계안을 먼저 만드세요.</p>
    ) : (
      <>
        <div className="flow-limits">
          <label className="flow-check"><input type="checkbox" checked disabled /> 수정 필요 0건</label>
          <label>
            수정 권장{" "}
            <input type="number" aria-label="수정 권장 허용 건수" min={0} max={9} value={p.maxWarnings} disabled={running(job)}
              onChange={(e) => p.setLimits(Math.max(0, Math.min(9, Number(e.target.value) || 0)), p.maxRounds)} /> 건 이하
          </label>
          <label>
            최대{" "}
            <input type="number" aria-label="최대 반복 횟수" min={1} max={5} value={p.maxRounds} disabled={running(job)}
              onChange={(e) => p.setLimits(p.maxWarnings, Math.max(1, Math.min(5, Number(e.target.value) || 1)))} /> 회
          </label>
          <span className="muted small">의견이 줄지 않거나, 같은 의견이 다시 나오거나, KiCad 검사가 실패하면 멈춤 · AI 호출 최대 약 {autoCalls(p.maxRounds)}회</span>
        </div>
        <div className="flow-row">
          <button type="button" onClick={p.start} disabled={!p.aiReady || !p.focus || running(job) || p.busy !== null}
            title={!p.aiReady ? AI_SETUP_HINT : !p.focus ? "검토할 열린 제안이 없습니다" : undefined}>
            <Icon name="refresh" /> {job ? "다시 실행" : "자동 반복 검토 시작"}
          </button>
          <button type="button" className="ghost" onClick={p.openOne} disabled={!p.focus}>
            <Icon name="list" /> 의견을 하나씩 직접 보기
          </button>
          {p.focus && !job && <span className="muted small">대상: 제안 #{num(p.focus.id)} 「{p.focus.title}」</span>}
        </div>
        {rounds.length > 0 && (
          <ol className="flow-rounds" aria-label="검토 회차">
            {rounds.map((r) => (
              <li key={r.round} className="flow-round">
                <span className="flow-round-no">{r.round}회차 · 제안 #{num(r.proposal_id)}</span>
                <span className="flow-round-chips">
                  <Chip text={`수정 필요 ${r.errors}`} tone={r.errors ? "bad" : "ok"} />
                  <Chip text={`수정 권장 ${r.warnings}`} tone={r.warnings ? "warn" : "ok"} />
                  {r.unverified ? <span title={`근거를 확인하지 못해 세지 않은 의견: ${(r.unverified_said ?? []).join(" / ")}`}>
                    <Chip text={`근거 확인 안 됨 ${r.unverified}`} tone="muted" /></span> : null}
                </span>
                <span className="flow-round-what">
                  <span>{r.said.length ? r.said.join(" / ") : "지적 없음"}</span>
                  {r.revised_to ? (
                    <span className="muted small">
                      → 수정안 #{num(r.revised_to)}{r.fix?.method === "edits" ? " (고칠 곳만 편집)" : r.fix?.method === "redesign" ? " (다시 설계)" : ""}
                      {r.fix?.diff ? `: ${diffSummaryText(r.fix.diff)}` : ""}
                    </span>
                  ) : result && r === last ? (
                    <span className="muted small">{STOP_LABEL[result.stop]} → 멈춤</span>
                  ) : null}
                </span>
              </li>
            ))}
          </ol>
        )}
        {live && <p className="small" role="status">{live}</p>}
        {job?.status === "failed" && <div className="flow-result stop">{jobError(job)}</div>}
        {result && (
          <div className={`flow-result ${result.met ? "" : "stop"}`}>
            {result.met
              ? `조건 충족: 제안 #${num(result.proposal_id)} (수정 필요 ${last?.errors ?? 0} · 수정 권장 ${last?.warnings ?? 0})`
              : `멈춤 (${STOP_LABEL[result.stop]}${result.message ? `: ${result.message}` : ""}): 마지막 제안 #${num(result.proposal_id)}` +
                ` (수정 필요 ${last?.errors ?? "?"} · 수정 권장 ${last?.warnings ?? "?"}). 다시 실행하거나, 남은 의견은 다음 단계에서 직접 판단하세요.`}
            <FailureDetails why={result.why} />
          </div>
        )}
      </>
    ),
    next: { label: "다음: 확인·승인 →", onClick: p.next, disabled: running(job) || !p.focus },
  };
}

/** How an unreadable AI answer ended and its last characters, folded away, for looking into a failure. */
function FailureDetails({ why }: { why?: AiFailure | null }) {
  if (!why || (!why.tail && !why.finish_reason)) return null;
  return (
    <details className="small failure-details">
      <summary>자세히 (AI 응답이 끝난 모양)</summary>
      <p className="muted">
        종료 이유: {why.finish_reason ?? "알 수 없음"} · 응답 {(why.chars ?? 0).toLocaleString()}자{why.what ? ` · 단계: ${why.what}` : ""}
      </p>
      {why.tail && <pre className="failure-tail">…{why.tail}</pre>}
    </details>
  );
}

// --- 6 확인·승인 ------------------------------------------------------------------------------------------------

function ApproveStage(p: Common & { target: Proposal | null; revision: number | null; onDetail(stage: string): void; onDecided(approved: boolean): Promise<void> }) {
  const [full, setFull] = useState<Proposal | null>(null);
  const [guides, setGuides] = useState<GuideChecklist | null | undefined>(undefined); // undefined: loading, null: failed
  const [confirming, setConfirming] = useState<"approve" | "reject" | null>(null);
  const [note, setNote] = useState("");
  useEffect(() => {
    if (!p.target) return;
    api.proposal(p.pid, p.bid, p.target.id).then(setFull).catch(() => setFull(p.target));
    setGuides(undefined);
    api.boardGuides(p.pid, p.bid, p.target.id).then(setGuides).catch(() => setGuides(null));
  }, [p.pid, p.bid, p.target]);

  if (!p.target) {
    return p.revision ? (
      <p>열려 있는 제안이 없습니다. 이 보드의 회로는 revision {p.revision}입니다. 다음 단계에서 회로도를 보세요.</p>
    ) : (
      <p className="muted">승인할 제안이 없습니다. 「AI 설계」와 「검토」를 먼저 진행하세요.</p>
    );
  }
  const x = full ?? p.target;
  const ev = x.evidence;
  const ac = ev?.automated_check ?? null;
  const check = ac ? automatedLabel(ac.status) : { text: ev ? (ev.blocking ? "규칙 오류" : "생성 실패") : "검사 중", tone: ev ? "bad" : "muted" } as const;
  const passed = ac?.status === "passed";
  const findings = x.ai?.review?.findings ?? [];
  const errors = findings.filter((f) => f.severity === "error");
  const warnings = findings.filter((f) => f.severity === "warning");
  const ds = (ev?.datasheets ?? []).flatMap((r) => [
    ...(r.pin_map === "mismatch" ? [`${r.reference} 핀맵 불일치`] : []),
    ...r.ratings_problems.map((t) => `${r.reference}: ${t}`),
    ...r.external_missing.map((t) => `${r.reference} 외부 부품: ${t}`),
  ]);
  const compared = (ev?.datasheets ?? []).length;
  const items = guides?.items ?? [];
  const gFail = items.filter((i) => i.status === "fail" || i.status === "warn").length;
  const gOk = items.filter((i) => i.status === "ok").length;
  const gManual = items.filter((i) => i.status === "manual").length;
  const rows: { name: string; chip: React.ReactNode; text: string; stage: string }[] = [
    { name: "바뀌는 내용", chip: <Chip text={x.diff.is_new_circuit ? `새 회로 · 부품 ${x.diff.summary.components_added ?? 0}` : diffSummaryText(x.diff.summary).split(" · ")[0] || "변경 없음"} tone="info" />,
      text: x.ai?.summary?.split(/(?<=[.。])\s/)[0] ?? diffSummaryText(x.diff.summary), stage: "changes" },
    { name: "자동 검사", chip: <Chip text={check.text} tone={check.tone} />,
      text: ac && ac.erc_errors != null ? `KiCad ERC 오류 ${ac.erc_errors} · ${ercWarningText(ac.erc_warnings, ac.violations)}${ac.netlist_matches_ir != null ? ` · Netlist ${ac.netlist_matches_ir ? "일치" : "불일치"}` : ""}` : ac?.reason ?? "", stage: "checks" },
    { name: "AI 검토", chip: <Chip text={x.ai?.review ? `수정 필요 ${errors.length} · 권장 ${warnings.length}` : "검토 전"} tone={errors.length ? "bad" : warnings.length ? "warn" : x.ai?.review ? "ok" : "muted"} />,
      text: [...errors, ...warnings].map((f) => f.title).join(" / ") || (x.ai?.review?.summary ?? ""), stage: "review" },
    { name: "데이터시트",
      chip: <Chip text={ds.length ? `확인 ${ds.length}` : compared ? "문제 없음" : "대조 안 함"} tone={ds.length ? "warn" : compared ? "ok" : "muted"} />,
      text: ds[0] ?? (compared ? `${compared}개 부품 대조` : "받은 데이터시트가 없어 대조하지 않았습니다"), stage: "datasheets" },
    { name: "설계 지침",
      chip: guides ? <Chip text={`주의 ${gFail}`} tone={gFail ? "warn" : "ok"} />
        : <Chip text={guides === undefined ? "확인 중…" : "불러오지 못함"} tone="muted" />,
      text: guides ? `통과 ${gOk} · 직접 확인 ${gManual}` : guides === undefined ? "지침별 자동 검사를 돌리는 중" : "「자세히」에서 다시 보세요",
      stage: "guides" },
  ];
  const decide = (approve: boolean) =>
    p.run(approve ? "승인하는 중… 회로도를 만듭니다" : "거부하는 중…", async () => {
      if (approve) {
        const r = await api.approveProposal(p.pid, p.bid, x.id, !passed);
        if (r.job) await waitForJob(r.job.id, undefined, 1000);
      } else {
        await api.rejectProposal(p.pid, p.bid, x.id, note.trim() || undefined);
      }
      setConfirming(null);
      await p.onDecided(approve);
    });

  return (
    <>
      <div className="table-scroll">
        <table className="table flow-summary" aria-label="제안 요약">
          <thead><tr><th>항목</th><th>상태</th><th>설명</th><th /></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.name}>
                <td><b>{r.name}</b></td>
                <td>{r.chip}</td>
                <td className="small">{r.text}</td>
                <td><button type="button" className="link" aria-label={`${r.name} 자세히`} onClick={() => p.onDetail(r.stage)}>자세히 ›</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flow-row">
        <button type="button" onClick={() => setConfirming("approve")} disabled={p.busy !== null || x.status === "checking"}>승인</button>
        <button type="button" className="ghost" onClick={() => setConfirming("reject")} disabled={p.busy !== null}>거부</button>
        <button type="button" className="ghost" onClick={() => p.onDetail("review")} disabled={p.busy !== null}>수정안 하나 더</button>
      </div>
      {confirming === "approve" && (
        <div className={`flow-confirm ${passed && !errors.length ? "" : "warn"}`}>
          <Icon name="info" />
          <span>
            {passed
              ? `승인하면 revision ${(p.revision ?? 0) + 1}로 저장하고 KiCad 회로도를 만듭니다.`
              : `이 제안은 자동 검사를 통과하지 못했거나 검사하지 않았습니다(${check.text}). 그래도 승인하면 revision ${(p.revision ?? 0) + 1}로 저장하고 회로도를 만듭니다.`}
            {errors.length > 0 &&
              ` AI 검토의 수정 필요 ${errors.length}건이 남아 있습니다(${errors.map((f) => f.title).slice(0, 2).join(" / ")}). 승인한 뒤에는 새 제안이나 회로 데이터에서 고쳐야 합니다.`}
          </span>
          <span className="spacer" />
          <button type="button" onClick={() => decide(true)} disabled={p.busy !== null}>승인 확정</button>
          <button type="button" className="ghost" onClick={() => setConfirming(null)}>취소</button>
        </div>
      )}
      {confirming === "reject" && (
        <div className="flow-confirm warn">
          <input aria-label="거부 이유" placeholder="거부 이유 (선택, 근거 리포트에 남음)" value={note} onChange={(e) => setNote(e.target.value)} />
          <button type="button" onClick={() => decide(false)} disabled={p.busy !== null}>거부 확정</button>
          <button type="button" className="ghost" onClick={() => setConfirming(null)}>취소</button>
        </div>
      )}
    </>
  );
}

// --- 7 회로도·출력 -----------------------------------------------------------------------------------------------

function OutputStage(p: Common & {
  board: BoardSummary; revision: number | null; outputs: OutputsStatus | null; review: ReviewStatus | null; circuit: CircuitIR | null;
  system: SystemInfo | null; onAdvanced(tab: TabId): void; onChanged(): Promise<unknown>;
}) {
  const manifest = p.outputs?.manifest ?? null;
  const pages = manifest?.pages ?? [];
  const [index, setIndex] = useState(0);
  const [svg, setSvg] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const page = pages[Math.min(index, Math.max(pages.length - 1, 0))] ?? null;
  useEffect(() => {
    if (!page?.svg) {
      setSvg(null);
      return;
    }
    let url: string | null = null;
    let cancelled = false;
    // Rendered through <img> from a blob URL, so nothing inside the SVG can run.
    outputText(p.pid, p.bid, page.svg)
      .then((t) => {
        url = URL.createObjectURL(new Blob([cropSvg(t, page.bbox_mm)], { type: "image/svg+xml" }));
        if (!cancelled) setSvg(url);
      })
      .catch(() => setSvg(null));
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [p.pid, p.bid, page?.svg, page?.bbox_mm]);

  if (!p.revision) return null;
  const automated = outputsLabel(p.outputs);
  const ac = manifest?.automated_check ?? null;
  const eng = reviewLabel(p.review ?? "unverified");
  const needsBuild = !manifest || Boolean(p.outputs?.stale) || (p.outputs?.missing.length ?? 0) > 0;
  const kicadPro = manifest && p.outputs ? `${p.outputs.path}/${manifest.kicad_project}.kicad_pro` : null;
  const build = () =>
    p.run("회로도를 만드는 중…", async () => {
      const job = await waitForJob((await api.exportBoard(p.pid, p.bid)).id, undefined, 1000);
      if (job.status === "failed") throw new Error(jobError(job));
      await p.onChanged();
    });
  const writeNote = () =>
    p.run("AI가 연구노트 초안을 쓰는 중…", async () => {
      const job = await waitForJob((await api.aiNote(p.pid, p.bid)).id, undefined, 1200);
      if (job.status === "failed") throw new Error(jobError(job));
      setNote(`연구노트 초안 「${job.result.note?.title ?? job.result.title ?? "새 노트"}」을 만들었습니다. 왼쪽 「연구노트」에서 확인하세요.`);
    });

  return (
    <>
      <div className="chips" aria-label="회로도 상태">
        <Chip label="자동 검사" text={automated.text} tone={automated.tone} />
        {ac && ac.erc_errors != null && <Chip text={`KiCad ERC 오류 ${ac.erc_errors} · ${ercWarningText(ac.erc_warnings, ac.violations)}`} tone={ac.erc_errors ? "bad" : "ok"} />}
        {ac && ac.netlist_matches_ir != null && <Chip text={ac.netlist_matches_ir ? "Netlist 일치" : "Netlist 불일치"} tone={ac.netlist_matches_ir ? "ok" : "bad"} />}
        <Chip label="엔지니어 검토" text={eng.text} tone={eng.tone} />
      </div>
      <div className="flow-output">
        <div className="flow-preview">
          {pages.length > 1 && (
            <div className="flow-preview-bar">
              <button type="button" className="ghost small-btn" aria-label="이전 페이지" disabled={index === 0} onClick={() => setIndex(index - 1)}>‹</button>
              <span className="small">{index + 1} / {pages.length}</span>
              <button type="button" className="ghost small-btn" aria-label="다음 페이지" disabled={index >= pages.length - 1} onClick={() => setIndex(index + 1)}>›</button>
            </div>
          )}
          {svg ? (
            <button type="button" className="flow-preview-img" onClick={() => p.onAdvanced("schematic")} title="크게 보기 (회로도 · 검증)">
              <img src={svg} alt={`회로도 미리보기 ${page?.name ?? ""}`} />
            </button>
          ) : (
            <p className="muted small flow-preview-empty">
              {manifest ? "미리보기가 없습니다 (KiCad로 SVG를 만들지 못함)." : "아직 회로도를 만들지 않았습니다."}
            </p>
          )}
        </div>
        <div className="flow-todo">
          <h3>다음 할 일</h3>
          <button type="button" className="ghost" disabled={!desktop || !kicadPro} onClick={() => kicadPro && desktop?.openPath(kicadPro)}
            title={!desktop ? "데스크톱 앱에서만 열 수 있습니다" : !kicadPro ? "회로도를 먼저 만드세요" : undefined}>
            <Icon name="external" /> KiCad에서 열기
          </button>
          <button type="button" className="ghost" onClick={() => p.onAdvanced("documents")}>
            <Icon name="notes" /> BOM·문서 받기
          </button>
          <button type="button" className="ghost" onClick={() => p.onAdvanced("datasheets")}>
            <Icon name="download" /> 데이터시트 받기
          </button>
          <button type="button" className="ghost" onClick={writeNote} disabled={!p.aiReady || p.busy !== null}
            title={!p.aiReady ? AI_SETUP_HINT : "이번 설계·검토에서 확인한 것과 배운 점을 AI가 연구노트 초안으로 씁니다"}>
            <Icon name="pencil" /> 연구노트 쓰기
          </button>
          {note && <p className="small ok-text" role="status">{note}</p>}
          {needsBuild && (
            <button type="button" onClick={build} disabled={p.busy !== null}>
              {manifest ? "회로도 다시 만들기" : "회로도 만들기"}
            </button>
          )}
        </div>
      </div>
      <p className="muted small">회로를 바꾸려면 새 제안을 만드세요(AI 수정안, 또는 고급 화면의 「제안」). 승인 전에 다시 「검토」를 거칩니다.</p>
    </>
  );
}
