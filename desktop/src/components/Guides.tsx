import { useCallback, useEffect, useRef, useState } from "react";

import { api, errorText, waitForJob } from "../api";
import { AI_SETUP_HINT, formatTime, providerLabel, type Tone } from "../format";
import type {
  AiStatus,
  GuideChecklist,
  GuideChecklistItem,
  GuideDraft,
  GuideEntry,
  GuideEvent,
  GuideListing,
  GuideOrigin,
  GuideSource,
  GuideStatus,
  GuideStrength,
} from "../types";
import { Chip } from "./Chip";

const NAME_KEY = "aics.reviewer";

export function savedName(): string {
  try {
    return window.localStorage.getItem(NAME_KEY) ?? "";
  } catch {
    return "";
  }
}

function saveName(name: string): void {
  try {
    window.localStorage.setItem(NAME_KEY, name);
  } catch {
    /* private mode: the name is just not remembered */
  }
}

export const STRENGTH_TEXT: Record<GuideStrength, string> = { must: "필수", should: "권장", consider: "검토" };
const STRENGTH_TONE: Record<GuideStrength, Tone> = { must: "bad", should: "warn", consider: "muted" };
const STATUS_TEXT: Record<GuideStatus, string> = { adopted: "채택", candidate: "후보", retired: "폐기" };
const STATUS_TONE: Record<GuideStatus, Tone> = { adopted: "ok", candidate: "warn", retired: "muted" };
const ORIGIN_TEXT: Record<GuideOrigin, string> = {
  seed: "앱 기본",
  engineer: "직접 작성",
  review: "엔지니어 검토",
  ai_review: "AI 검토 의견",
  feedback: "사용자 피드백",
  check: "검사 결과",
};
const CHECK_STATUS: Record<string, { text: string; tone: Tone }> = {
  fail: { text: "위반", tone: "bad" },
  warn: { text: "확인 필요", tone: "warn" },
  ok: { text: "검사 통과", tone: "ok" },
  manual: { text: "직접 확인", tone: "muted" },
  exception: { text: "예외", tone: "info" },
};
const EVENT_TEXT: Record<string, string> = {
  seeded: "앱 기본 지침 추가",
  created: "만듦",
  edited: "고침",
  adopted: "채택",
  retired: "폐기",
  reopened: "후보로 되돌림",
  deleted: "지움",
  cited: "AI 설계가 따름",
  flagged: "AI 검토가 지적",
  issue: "엔지니어 검토에서 문제",
};

export function emptyDraft(over: Partial<GuideDraft> = {}): GuideDraft {
  return { title: "", rule: "", why: "", strength: "should", topics: ["general"], scope: { parts: [], keywords: [] }, check: null, sources: [], ...over };
}

const splitWords = (text: string) =>
  text
    .split(/[,，\n]/)
    .map((w) => w.trim())
    .filter(Boolean);

interface EditorProps {
  initial: GuideDraft;
  listing: GuideListing;
  aiText?: boolean;
  /** Offer "바로 채택" (only for text a person wrote). */
  canAdopt?: boolean;
  saveLabel?: string;
  onSave(draft: GuideDraft, adopt: boolean): Promise<void>;
  onCancel(): void;
}

/** Form for one guide entry. */
export function GuideEditor({ initial, listing, aiText, canAdopt = true, saveLabel = "저장", onSave, onCancel }: EditorProps) {
  const [d, setD] = useState<GuideDraft>(initial);
  const [parts, setParts] = useState(initial.scope.parts.join(", "));
  const [keywords, setKeywords] = useState(initial.scope.keywords.join(", "));
  const [source, setSource] = useState(initial.sources.map((s) => s.text).join("\n"));
  const [adopt, setAdopt] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setMessage(null);
    const typed = splitWords(source);
    const kept = initial.sources.filter((s) => typed.includes(s.text));
    const added: GuideSource[] = typed.filter((t) => !kept.some((s) => s.text === t)).map((text) => ({ kind: "practice", text }));
    try {
      await onSave({ ...d, scope: { parts: splitWords(parts), keywords: splitWords(keywords) }, sources: [...kept, ...added] }, adopt);
    } catch (err) {
      setMessage(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="guide-editor" onSubmit={submit} aria-label="지침 편집">
      {aiText && <p className="small warn-text">AI가 쓴 문장입니다. 고친 뒤 저장하면 「후보」로 저장되고, 설계 가이드 화면에서 채택해야 쓰입니다.</p>}
      <label>
        제목
        <input aria-label="지침 제목" value={d.title} onChange={(e) => setD({ ...d, title: e.target.value })} maxLength={120} required />
      </label>
      <label>
        할 일 (지침)
        <textarea aria-label="지침 내용" value={d.rule} onChange={(e) => setD({ ...d, rule: e.target.value })} rows={3} required />
      </label>
      <label>
        이유
        <textarea aria-label="지침 이유" value={d.why} onChange={(e) => setD({ ...d, why: e.target.value })} rows={2} />
      </label>
      <div className="toolbar">
        <label>
          강도{" "}
          <select aria-label="지침 강도" value={d.strength} onChange={(e) => setD({ ...d, strength: e.target.value as GuideStrength })}>
            <option value="must">필수 (지키지 않으면 결함)</option>
            <option value="should">권장 (이유 없으면 따름)</option>
            <option value="consider">검토 (상황에 따라)</option>
          </select>
        </label>
        <label>
          자동 검사{" "}
          <select aria-label="연결할 자동 검사" value={d.check ?? ""} onChange={(e) => setD({ ...d, check: e.target.value || null })}>
            <option value="">없음 (사람이 확인)</option>
            {Object.entries(listing.checks).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </label>
      </div>
      <fieldset className="topics">
        <legend className="small">분야 (이 분야의 회로를 그릴 때 적용)</legend>
        {Object.entries(listing.topics).map(([k, v]) => (
          <label key={k} className="small">
            <input
              type="checkbox"
              checked={d.topics.includes(k)}
              onChange={(e) => setD({ ...d, topics: e.target.checked ? [...d.topics, k] : d.topics.filter((t) => t !== k) })}
            />{" "}
            {v}
          </label>
        ))}
      </fieldset>
      <div className="toolbar">
        <input aria-label="해당 부품" placeholder="특정 부품만: 품번·심볼 일부 (쉼표로)" value={parts} onChange={(e) => setParts(e.target.value)} />
        <input aria-label="요구사항 단어" placeholder="요구사항에 이 단어가 있으면 적용 (쉼표로)" value={keywords} onChange={(e) => setKeywords(e.target.value)} />
      </div>
      <label>
        출처 (한 줄에 하나: 데이터시트 절, 앱 노트, 검토 기록 …)
        <textarea aria-label="지침 출처" value={source} onChange={(e) => setSource(e.target.value)} rows={2} />
      </label>
      <div className="toolbar">
        <button type="submit" disabled={busy || d.topics.length === 0}>
          {saveLabel}
        </button>
        {canAdopt && !aiText && (
          <label className="small">
            <input type="checkbox" checked={adopt} onChange={(e) => setAdopt(e.target.checked)} /> 바로 채택 (내가 쓴 지침)
          </label>
        )}
        <button type="button" className="ghost" onClick={onCancel}>
          취소
        </button>
      </div>
      {message && <p className="small bad-text">{message}</p>}
    </form>
  );
}

/** One entry as a card. */
function GuideCard({ e, listing, by, onChanged, onError }: { e: GuideEntry; listing: GuideListing; by: string; onChanged(): Promise<void>; onError(m: string): void }) {
  const [editing, setEditing] = useState(false);
  const [history, setHistory] = useState<GuideEvent[] | null>(null);
  const need = () => {
    if (!by.trim()) {
      onError("위쪽 「기록할 이름」을 먼저 적으세요. 누가 채택·폐기했는지 이력에 남습니다.");
      return false;
    }
    return true;
  };
  const decide = async (status: GuideStatus) => {
    if (!need()) return;
    const note = status === "retired" ? window.prompt("폐기하는 이유 (이력에 남습니다)", "") ?? undefined : undefined;
    try {
      await api.decideGuide(e.id, status, by.trim(), note || undefined);
      await onChanged();
    } catch (err) {
      onError(errorText(err));
    }
  };
  const remove = async () => {
    if (!window.confirm(`후보 '${e.title}'를 지울까요? (이력에는 남습니다)`)) return;
    try {
      await api.deleteGuide(e.id);
      await onChanged();
    } catch (err) {
      onError(errorText(err));
    }
  };
  const showHistory = async () => {
    if (history) {
      setHistory(null);
      return;
    }
    try {
      setHistory((await api.guideHistory(e.id)).events);
    } catch (err) {
      onError(errorText(err));
    }
  };
  const s = e.stats ?? {};
  return (
    <li className={`guide-card guide-${e.status}`} aria-label={`지침 ${e.id}`}>
      <div className="toolbar">
        <b>
          <span className="muted">{e.id}</span> {e.title}
        </b>
        <Chip text={STRENGTH_TEXT[e.strength]} tone={STRENGTH_TONE[e.strength]} />
        <Chip text={STATUS_TEXT[e.status]} tone={STATUS_TONE[e.status]} />
        <span className="small muted">
          {ORIGIN_TEXT[e.origin]} · v{e.version}
          {e.topics.length > 0 && ` · ${e.topics.map((t) => listing.topics[t] ?? t).join(", ")}`}
        </span>
      </div>
      {editing ? (
        <GuideEditor
          initial={e}
          listing={listing}
          canAdopt={false}
          onCancel={() => setEditing(false)}
          onSave={async (draft) => {
            if (!need()) return;
            await api.updateGuide(e.id, draft, by.trim());
            setEditing(false);
            await onChanged();
          }}
        />
      ) : (
        <>
          <p className="small">{e.rule}</p>
          {e.why && <p className="small muted">이유: {e.why}</p>}
          <p className="small muted">
            {e.check && <>자동 검사: {listing.checks[e.check] ?? e.check} · </>}
            {e.scope.parts.length > 0 && <>부품: {e.scope.parts.join(", ")} · </>}
            {e.sources.map((src) => src.text).join(" / ")}
          </p>
          {(s.cited || s.flagged || s.issue) && (
            <p className="small">
              {s.cited ? `AI 설계가 따름 ${s.cited}회 · ` : ""}
              {s.flagged ? `AI 검토가 지적 ${s.flagged}회 · ` : ""}
              {s.issue ? <span className="warn-text">엔지니어 검토에서 문제 {s.issue}회</span> : ""}
            </p>
          )}
          {e.note && <p className="small muted">메모: {e.note}</p>}
        </>
      )}
      {!editing && (
        <div className="toolbar">
          {e.status !== "adopted" && (
            <button className="small-btn" onClick={() => decide("adopted")}>
              채택
            </button>
          )}
          {e.status !== "retired" && (
            <button className="ghost small-btn" onClick={() => decide("retired")}>
              폐기
            </button>
          )}
          {e.status === "retired" && (
            <button className="ghost small-btn" onClick={() => decide("candidate")}>
              후보로 되돌리기
            </button>
          )}
          <button className="ghost small-btn" onClick={() => setEditing(true)}>
            고치기
          </button>
          {e.status === "candidate" && e.origin !== "seed" && (
            <button className="ghost small-btn" onClick={remove}>
              지우기
            </button>
          )}
          <button className="link small" onClick={showHistory}>
            {history ? "이력 닫기" : "이력"}
          </button>
        </div>
      )}
      {history && (
        <ul className="small muted">
          {history.map((ev, i) => (
            <li key={i}>
              {formatTime(ev.at)} · {EVENT_TEXT[ev.event] ?? ev.event} {ev.by && `· ${ev.by}`}
              {typeof ev.note === "string" && ev.note ? ` · ${ev.note}` : ""}
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

/** Sidebar page: the workspace design guide. */
export function GuidesPage({ onError }: { onError(message: string | null): void }) {
  const [listing, setListing] = useState<GuideListing | null>(null);
  const [status, setStatus] = useState<GuideStatus | "all">("adopted");
  const [topic, setTopic] = useState("");
  const [query, setQuery] = useState("");
  const [by, setBy] = useState(savedName());
  const [adding, setAdding] = useState(false);
  const [recent, setRecent] = useState<GuideEvent[]>([]);
  const [imported, setImported] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      setListing(await api.guides());
      setRecent((await api.guidesHistory(30)).events);
    } catch (e) {
      onError(errorText(e));
    }
  }, [onError]);

  useEffect(() => {
    void load();
  }, [load]);

  if (!listing) return <div className="empty">불러오는 중…</div>;
  const count = (s: GuideStatus) => listing.entries.filter((e) => e.status === s).length;
  const q = query.trim().toLowerCase();
  const shown = listing.entries.filter(
    (e) =>
      (status === "all" || e.status === status) &&
      (!topic || e.topics.includes(topic)) &&
      (!q || `${e.id} ${e.title} ${e.rule} ${e.why}`.toLowerCase().includes(q)),
  );

  const onImport = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    try {
      const r = await api.importGuides(file.name, await file.text(), by.trim(), false);
      setImported(
        `${file.name}: 지침 ${r.found}개 중 ${r.created.length}개를 후보로 가져왔습니다` +
          (r.skipped.length ? `, 같은 제목 ${r.skipped.length}개는 건너뜀` : "") +
          (r.warnings.length ? `. 확인할 것: ${r.warnings.slice(0, 3).join(" / ")}` : "") +
          ". 「후보」에서 검토하고 채택하세요.",
      );
      setStatus("candidate");
      await load();
    } catch (err) {
      onError(errorText(err));
    }
  };

  const exportMd = async () => {
    try {
      const text = await api.guidesMarkdown();
      const url = URL.createObjectURL(new Blob([text], { type: "text/markdown" }));
      const a = document.createElement("a");
      a.href = url;
      a.download = "design-guide.md";
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      onError(errorText(e));
    }
  };

  return (
    <section className="page guides-page" aria-label="설계 가이드">
      <div className="card">
        <h2>설계 가이드</h2>
        <p className="small">
          회로를 그릴 때 공통으로 지킬 지침입니다. 앱 기본 지침에서 시작해, 엔지니어 검토·AI 검토 의견·제안 거부 이유·수정 요청·직접 쓴 피드백으로 쌓입니다.
          <b> 채택된 지침만</b> AI 설계·수정·검토에 주어지고, 제안과 「검토」 탭의 지침 체크리스트에 나옵니다. AI가 쓴 문장은 항상 후보로 들어오고 사람이 채택합니다.
        </p>
        <div className="toolbar">
          <span className="small">
            채택 {count("adopted")} · 후보 <b className={count("candidate") ? "warn-text" : ""}>{count("candidate")}</b> · 폐기 {count("retired")}
          </span>
          <label className="small">
            기록할 이름{" "}
            <input aria-label="기록할 이름" value={by} onChange={(e) => setBy(e.target.value)} onBlur={() => saveName(by.trim())} placeholder="예: 김엔지니어" />
          </label>
          <button onClick={() => setAdding(!adding)}>{adding ? "추가 닫기" : "지침 추가"}</button>
          <button className="ghost" onClick={exportMd}>
            문서로 내보내기 (Markdown)
          </button>
          <button className="ghost" onClick={() => fileInput.current?.click()} title="사내 설계 기준서를 Markdown 목록이나 CSV로 가져옵니다(후보로)">
            문서 가져오기 (Markdown·CSV)
          </button>
          <input ref={fileInput} type="file" accept=".md,.markdown,.txt,.csv,text/markdown,text/csv" hidden onChange={onImport}
            aria-label="가져올 설계 가이드 파일" />
        </div>
        {imported && (
          <p className="small" role="status">
            {imported}
          </p>
        )}
        <details className="small">
          <summary>가져올 수 있는 형식</summary>
          <ul>
            <li>이 앱에서 내보낸 Markdown(분야·강도·이유·자동 검사·출처가 그대로 돌아옴)</li>
            <li>
              Markdown 목록: <code>## 전원</code> 아래 <code>- [필수] 제목: 할 일</code> (강도는 필수·권장·검토)
            </li>
            <li>CSV: 제목, 지침, 이유, 강도, 분야, 자동 검사, 출처 열 (첫 줄이 열 이름)</li>
            <li>가져온 지침은 모두 「후보」로 들어오고, 같은 제목이 이미 있으면 건너뜁니다.</li>
          </ul>
        </details>
        {adding && (
          <GuideEditor
            initial={emptyDraft()}
            listing={listing}
            onCancel={() => setAdding(false)}
            onSave={async (draft, adopt) => {
              await api.createGuide({ draft, origin: "engineer", by: by.trim() || "사용자", adopt });
              setAdding(false);
              await load();
            }}
          />
        )}
        <div className="toolbar">
          <select aria-label="상태로 거르기" value={status} onChange={(e) => setStatus(e.target.value as GuideStatus | "all")}>
            <option value="adopted">채택</option>
            <option value="candidate">후보 (검토 대기)</option>
            <option value="retired">폐기</option>
            <option value="all">전체</option>
          </select>
          <select aria-label="분야로 거르기" value={topic} onChange={(e) => setTopic(e.target.value)}>
            <option value="">모든 분야</option>
            {Object.entries(listing.topics).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
          <input aria-label="지침 검색" placeholder="검색" value={query} onChange={(e) => setQuery(e.target.value)} />
        </div>
      </div>
      <div className="card">
        {shown.length === 0 ? (
          <p className="small muted">{status === "candidate" ? "검토를 기다리는 후보가 없습니다." : "해당하는 지침이 없습니다."}</p>
        ) : (
          <ul className="guide-list">
            {shown.map((e) => (
              <GuideCard key={e.id} e={e} listing={listing} by={by} onChanged={load} onError={(m) => onError(m)} />
            ))}
          </ul>
        )}
      </div>
      {recent.length > 0 && (
        <div className="card">
          <h3>최근 기록</h3>
          <ul className="small">
            {recent.map((ev, i) => (
              <li key={i}>
                {formatTime(ev.at)} · {ev.id ?? ""} {EVENT_TEXT[ev.event] ?? ev.event} {ev.by && `· ${ev.by}`}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

interface FeedbackProps {
  projectId: string;
  boardId: string;
  proposalId?: string | null;
  aiReady: boolean;
  listing: GuideListing;
  onSaved(): Promise<void>;
}

/** "다음에는 이렇게": a person's feedback becomes a guide entry (as written, or rephrased by the AI). */
export function GuideFeedback({ projectId, boardId, proposalId, aiReady, listing, onSaved }: FeedbackProps) {
  const [text, setText] = useState("");
  const [draft, setDraft] = useState<{ d: GuideDraft; ai: boolean } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const ref = { project_id: projectId, board_id: boardId, ...(proposalId ? { proposal_id: proposalId } : {}) };

  const asWritten = () =>
    setDraft({
      d: emptyDraft({
        title: text.trim().split(/[.\n]/)[0].slice(0, 60),
        rule: text.trim(),
        sources: [{ kind: "feedback", text: `사용자 피드백${proposalId ? ` (제안 #${Number(proposalId)})` : ""}`, ref }],
      }),
      ai: false,
    });

  const withAi = async () => {
    setMessage(null);
    let ai: AiStatus | null = null;
    try {
      ai = await api.aiStatus();
    } catch {
      ai = null;
    }
    const ok = window.confirm(
      `AI로 지침 문장 만들기\n\n전송할 데이터: 적은 피드백(${text.trim().length}자), 이 ${proposalId ? "제안의" : "보드의"} 회로 요약, 지금 있는 지침의 제목\n` +
        `받는 곳: ${providerLabel(ai?.settings.provider)} (모델 ${ai?.settings.model})\n\n만든 문장은 고친 뒤 「후보」로 저장됩니다. 계속할까요?`,
    );
    if (!ok) return;
    setBusy("AI가 지침 문장을 만드는 중…");
    try {
      const job = await waitForJob((await api.draftGuide(projectId, boardId, text.trim(), proposalId)).id, undefined, 1000);
      if (job.status === "failed") {
        setMessage(job.error ? `[${job.error.error_code}] ${job.error.message}` : "실패했습니다.");
        return;
      }
      const r = job.result as { draft: Omit<GuideDraft, "sources">; source: GuideSource };
      setDraft({ d: { ...emptyDraft(), ...r.draft, sources: [r.source] }, ai: true });
    } catch (e) {
      setMessage(errorText(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="guide-feedback" aria-label="설계 가이드에 남기기">
      <h4>다음 설계에 반영할 점 남기기</h4>
      {!draft && (
        <>
          <textarea
            aria-label="피드백"
            rows={2}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="예: 모터 전원 입력에는 TVS를 꼭 넣어 주세요. / BOOT0은 항상 10k 풀다운."
          />
          <div className="toolbar">
            <button className="ghost small-btn" disabled={text.trim().length < 3 || busy !== null} onClick={asWritten}>
              지침으로 정리하기
            </button>
            <button className="ghost small-btn" disabled={text.trim().length < 3 || busy !== null || !aiReady} onClick={withAi}
              title={aiReady ? "AI가 피드백을 지침 문장(제목·할 일·이유·분야)으로 다듬습니다" : AI_SETUP_HINT}>
              AI로 지침 문장 만들기
            </button>
          </div>
        </>
      )}
      {draft && (
        <GuideEditor
          initial={draft.d}
          listing={listing}
          aiText={draft.ai}
          saveLabel={draft.ai ? "후보로 저장" : "저장"}
          onCancel={() => setDraft(null)}
          onSave={async (d, adopt) => {
            const entry = await api.createGuide({ draft: d, origin: "feedback", by: savedName() || "사용자", adopt, ai_text: draft.ai });
            setDraft(null);
            setText("");
            setMessage(`${entry.id} 「${entry.title}」을(를) ${entry.status === "adopted" ? "채택된 지침으로" : "후보로"} 저장했습니다. 설계 가이드 화면에서 볼 수 있습니다.`);
            await onSaved();
          }}
        />
      )}
      {busy && <p className="small">{busy}</p>}
      {message && (
        <p className="small" role="status">
          {message}
        </p>
      )}
    </div>
  );
}

interface ChecklistProps {
  projectId: string;
  boardId: string;
  proposalId?: string | null;
  /** Reload when this changes (a new check of the proposal, a new revision). */
  refreshKey?: string | number | null;
  aiReady: boolean;
  /** Board context: "예외로 기록" makes a proposal and opens it. */
  onOpenProposal?(id: string): void;
}

/** "이 보드에서는 따르지 않음": a design decision that waives the guide, as a proposal (the reason is the decision's). */
function WaiveForm({ item, projectId, boardId, onDone }: { item: GuideChecklistItem; projectId: string; boardId: string; onDone(id: string): void }) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const decision = {
        id: `dec-waive-${item.id.toLowerCase()}`,
        topic: `설계 지침 예외: ${item.title}`.slice(0, 120),
        choice: `${item.id}를 이 보드에서는 따르지 않음`,
        by: "engineer",
        reason: reason.trim(),
        components: [],
        waives: [item.id],
      };
      const r = await api.createProposal(projectId, boardId, `지침 예외: ${item.id} ${item.title}`.slice(0, 150),
        [{ op: "set_decision", decision }], "이 보드에서 설계 지침을 따르지 않는 이유를 설계 결정으로 남깁니다(연결·회로도는 바뀌지 않음).");
      onDone(r.proposal.id);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="toolbar" onSubmit={submit} aria-label={`${item.id} 예외 기록`}>
      <input aria-label="예외 이유" placeholder="따르지 않는 이유 (예: 직렬 다이오드를 넣으면 드롭아웃 조건을 못 지킴)" value={reason}
        onChange={(e) => setReason(e.target.value)} style={{ minWidth: 360 }} required />
      <button type="submit" className="small-btn" disabled={busy || reason.trim().length < 4}>예외 제안 만들기</button>
      {error && <span className="small bad-text">{error}</span>}
    </form>
  );
}

/** The adopted guides that apply to this circuit, with what the app could check itself, and a feedback box. */
export function GuideChecklistPanel({ projectId, boardId, proposalId, refreshKey, aiReady, onOpenProposal }: ChecklistProps) {
  const [data, setData] = useState<GuideChecklist | null>(null);
  const [listing, setListing] = useState<GuideListing | null>(null);
  const [showOk, setShowOk] = useState(false);
  const [waiving, setWaiving] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [c, l] = await Promise.all([api.boardGuides(projectId, boardId, proposalId), api.guides()]);
      setData(c);
      setListing(l);
    } catch {
      setData(null);
    }
  }, [projectId, boardId, proposalId]);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  if (!data || !listing || data.revision === null && !proposalId) return null;
  const order = { fail: 0, warn: 1, manual: 2, exception: 3, ok: 4 } as const;
  const items = [...data.items].sort((a, b) => order[a.status] - order[b.status]);
  const problems = items.filter((i) => i.status === "fail" || i.status === "warn");
  const visible = showOk ? items : items.filter((i) => i.status !== "ok");
  const canWaive = !proposalId && Boolean(onOpenProposal);
  return (
    <section className="guide-checklist" aria-label="설계 지침 확인">
      <h4>
        설계 지침 확인{" "}
        <span className="muted small">
          적용 {items.length}개 · 자동 검사에서 문제 {problems.length}개{data.candidates ? ` · 검토 대기 후보 ${data.candidates}개` : ""}
        </span>
      </h4>
      <ul className="issues">
        {visible.map((i) => (
          <li key={i.id} className={`issue issue-${i.status === "fail" ? "error" : i.status === "warn" ? "warning" : "info"}`}>
            <Chip text={CHECK_STATUS[i.status].text} tone={CHECK_STATUS[i.status].tone} /> <span className="muted">{i.id}</span>{" "}
            <b>{i.title}</b> <span className="small muted">({STRENGTH_TEXT[i.strength]})</span>
            {i.ai_applied && <span className="small ok-text"> · AI가 따랐다고 함</span>}
            <div className="small">{i.rule}</div>
            {i.notes.length > 0 && i.status !== "manual" && <div className="issue-hint">→ {i.notes.join(" / ")}</div>}
            {canWaive && i.status !== "ok" && i.status !== "exception" && (
              waiving === i.id ? (
                <WaiveForm item={i} projectId={projectId} boardId={boardId} onDone={(id) => { setWaiving(null); onOpenProposal?.(id); }} />
              ) : (
                <button className="link small" onClick={() => setWaiving(i.id)} title="이 보드에서는 일부러 따르지 않는다면 이유를 설계 결정으로 남깁니다">
                  예외로 기록
                </button>
              )
            )}
          </li>
        ))}
      </ul>
      <p className="small muted">
        「검사 통과」는 지침에 연결된 자동 검사(계산·연결 확인)가 보는 부분만 통과했다는 뜻입니다. 부품 종류·ESR·배치처럼 회로 정보에 없는 내용은 직접 확인하세요.
      </p>
      <label className="small">
        <input type="checkbox" checked={showOk} onChange={(e) => setShowOk(e.target.checked)} /> 자동 검사를 통과한 것도 보기
      </label>
      <GuideFeedback projectId={projectId} boardId={boardId} proposalId={proposalId} aiReady={aiReady} listing={listing} onSaved={load} />
    </section>
  );
}

/** "지침 후보로": an AI review finding saved as a candidate (the AI wrote it, so a person adopts it later). */
export async function saveFindingAsGuide(
  f: { title: string; detail: string; suggestion: string | null },
  ref: Record<string, string>,
  label: string,
): Promise<GuideEntry> {
  return api.createGuide({
    draft: emptyDraft({
      title: f.title.slice(0, 120),
      rule: (f.suggestion || f.detail).slice(0, 2000),
      why: f.suggestion ? f.detail.slice(0, 2000) : "",
      sources: [{ kind: "ai", text: label, ref }],
    }),
    origin: "ai_review",
    by: savedName() || "사용자",
    adopt: false,
    ai_text: true,
  });
}
