import DOMPurify from "dompurify";
import { marked } from "marked";
import { useCallback, useEffect, useState } from "react";

import { api, errorText, waitForJob } from "../api";
import { AI_SETUP_HINT, formatTime, providerLabel } from "../format";
import type { NoteApply, NoteCheck, NoteDraft, NoteLine, NotePage, NoteSummary, ResearchNote } from "../types";
import { Chip } from "./Chip";
import { saveName, savedName } from "./Guides";

const EVIDENCE_KIND: Record<string, string> = {
  calc: "계산",
  datasheet: "데이터시트",
  guide: "설계 지침",
  decision: "설계 결정",
  kicad: "KiCad 검사",
  ai_review: "AI 검토",
  requirement: "요구사항",
  check: "자동 검사",
};

export function noteStatus(n: Pick<NoteSummary, "status" | "author_kind">): { text: string; tone: "ok" | "warn" | "muted" } {
  if (n.status === "reviewed") return { text: "검토함", tone: "ok" };
  return { text: n.author_kind === "ai" ? "AI 초안" : "초안", tone: "warn" };
}

export function noteWhere(c: NoteSummary["context"]): string {
  const board = c.board_name ?? c.board_id;
  return [c.project_name, board ? (board.startsWith("보드") ? board : `보드 ${board}`) : null, c.revision != null ? `rev ${c.revision}` : null,
    c.proposal_id ? `제안 #${Number(c.proposal_id) || c.proposal_id}` : null]
    .filter(Boolean)
    .join(" / ");
}

function download(name: string, text: string): void {
  const url = URL.createObjectURL(new Blob([text], { type: "text/markdown" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

// --- editing as plain lines -------------------------------------------------------------------

/** Lists are edited one item per line: checks as "항목 | 방법 | 결과 | 판단", apply-next items as "할 일 — 이유". */
export const lineFormats = {
  check: (c: Pick<NoteCheck, "what" | "how" | "result" | "status">) => [c.what, c.how, c.result, c.status].join(" | "),
  apply: (a: Pick<NoteApply, "text" | "why">) => (a.why ? `${a.text} — ${a.why}` : a.text),
};

export function parseChecks(text: string): Pick<NoteCheck, "what" | "how" | "result" | "status">[] {
  return text
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean)
    .map((l) => {
      const [what, how = "", result = "", status = "unknown"] = l.split("|").map((x) => x.trim());
      return { what, how, result, status: (["ok", "issue", "unknown"].includes(status) ? status : "unknown") as NoteCheck["status"] };
    });
}

export function parseApply(text: string): Pick<NoteApply, "text" | "why">[] {
  return text
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean)
    .map((l) => {
      const i = l.indexOf(" — ");
      return i > 0 ? { text: l.slice(0, i).trim(), why: l.slice(i + 3).trim() } : { text: l, why: "" };
    });
}

const lines = (text: string) =>
  text
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean);

interface EditorProps {
  initial: NoteDraft | null;
  topics: Record<string, string>;
  onSave(draft: NoteDraft): Promise<void>;
  onCancel(): void;
}

/** Write or edit a note. Lines left as they were keep their author and evidence; changed lines become yours. */
export function NoteEditor({ initial, topics, onSave, onCancel }: EditorProps) {
  const [title, setTitle] = useState(initial?.title ?? "");
  const [summary, setSummary] = useState(initial?.summary ?? "");
  const [background, setBackground] = useState(initial?.background ?? "");
  const [parts, setParts] = useState((initial?.parts ?? []).join(", "));
  const [picked, setPicked] = useState<string[]>(initial?.topics ?? []);
  const [checks, setChecks] = useState((initial?.checks ?? []).map(lineFormats.check).join("\n"));
  const [learnings, setLearnings] = useState((initial?.learnings ?? []).map((x) => x.text).join("\n"));
  const [apply, setApply] = useState((initial?.apply_next ?? []).map(lineFormats.apply).join("\n"));
  const [questions, setQuestions] = useState((initial?.open_questions ?? []).map((x) => x.text).join("\n"));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const line = (text: string): NoteLine => ({ text, evidence: [], by: "person" });
    try {
      await onSave({
        title: title.trim(),
        summary: summary.trim(),
        background: background.trim(),
        parts: parts.split(",").map((p) => p.trim()).filter(Boolean),
        topics: picked,
        checks: parseChecks(checks).map((c) => ({ ...c, evidence: [], by: "person" })),
        learnings: lines(learnings).map(line),
        apply_next: parseApply(apply).map((a) => ({ ...a, evidence: [], by: "person", guide_id: null })),
        open_questions: lines(questions).map(line),
      });
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="note-editor" onSubmit={submit} aria-label="연구노트 편집">
      <label>
        제목 <input aria-label="노트 제목" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="예: 5V→3.3V LDO 보드: 드롭아웃 여유" />
      </label>
      <label>
        요약 <textarea aria-label="노트 요약" rows={2} value={summary} onChange={(e) => setSummary(e.target.value)} placeholder="무엇을 검토했고 가장 중요한 결론" />
      </label>
      <label>
        배경 <textarea aria-label="노트 배경" rows={2} value={background} onChange={(e) => setBackground(e.target.value)} placeholder="회로의 목적, 주요 부품, 전원·인터페이스 조건" />
      </label>
      <label>
        확인한 것 <span className="small muted">한 줄에 하나: 항목 | 어떻게 확인했나 | 결과 | ok·issue·unknown</span>
        <textarea aria-label="확인한 것" rows={3} value={checks} onChange={(e) => setChecks(e.target.value)} />
      </label>
      <label>
        배운 점·깨달은 점 <span className="small muted">한 줄에 하나. 다른 회로에도 통하는 교훈으로</span>
        <textarea aria-label="배운 점" rows={3} value={learnings} onChange={(e) => setLearnings(e.target.value)} />
      </label>
      <label>
        다음 설계에 적용할 것 <span className="small muted">한 줄에 하나: 할 일 — 이유</span>
        <textarea aria-label="다음에 적용할 것" rows={3} value={apply} onChange={(e) => setApply(e.target.value)} />
      </label>
      <label>
        열린 질문 <span className="small muted">한 줄에 하나: 측정·원문 확인처럼 아직 답이 없는 것</span>
        <textarea aria-label="열린 질문" rows={2} value={questions} onChange={(e) => setQuestions(e.target.value)} />
      </label>
      <label>
        부품 <input aria-label="노트 부품" value={parts} onChange={(e) => setParts(e.target.value)} placeholder="쉼표로: LM1117-3.3, PTC 0.2A" />
      </label>
      <fieldset className="topic-picks">
        <legend className="small">분야</legend>
        {Object.entries(topics).map(([k, label]) => (
          <label key={k} className="small">
            <input type="checkbox" checked={picked.includes(k)}
              onChange={(e) => setPicked(e.target.checked ? [...picked, k] : picked.filter((t) => t !== k))} /> {label}
          </label>
        ))}
      </fieldset>
      {error && <p className="small bad-text">{error}</p>}
      <div className="toolbar">
        <button type="submit" disabled={busy || title.trim().length < 2}>
          저장
        </button>
        <button type="button" className="ghost" onClick={onCancel}>
          취소
        </button>
      </div>
    </form>
  );
}

// --- one note -----------------------------------------------------------------------------------

interface ViewProps {
  id: string;
  topics: Record<string, string>;
  onChanged?(): void;
  onDeleted?(): void;
}

/** A note as a readable page, with what can be done to it: review, edit, turn a lesson into a guide, download. */
export function NoteView({ id, topics, onChanged, onDeleted }: ViewProps) {
  const [page, setPage] = useState<NotePage | null>(null);
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(savedName());
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setPage(await api.note(id));
    } catch (e) {
      setMessage(errorText(e));
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  if (!page) return message ? <p className="small bad-text">{message}</p> : <p className="small muted">불러오는 중…</p>;
  const n: ResearchNote = page.note;
  const html = DOMPurify.sanitize(marked.parse(page.markdown, { async: false }) as string);

  const act = async (fn: () => Promise<NotePage | void>, done?: string) => {
    setMessage(null);
    try {
      if (name.trim()) saveName(name.trim());
      const r = await fn();
      if (r) setPage(r);
      if (done) setMessage(done);
      onChanged?.();
    } catch (e) {
      setMessage(errorText(e));
    }
  };

  const needName = () => {
    if (name.trim()) return false;
    setMessage("기록할 이름을 적으세요.");
    return true;
  };

  if (editing) {
    return (
      <NoteEditor
        initial={n}
        topics={topics}
        onCancel={() => setEditing(false)}
        onSave={async (draft) => {
          if (needName()) throw new Error("기록할 이름을 적으세요.");
          setPage(await api.editNote(n.id, draft, name.trim()));
          setEditing(false);
          onChanged?.();
        }}
      />
    );
  }

  return (
    <section className="note-view" aria-label={`연구노트 ${n.id}`}>
      <div className="toolbar">
        <Chip {...noteStatus(n)} />
        <span className="small muted">
          {n.author_kind === "ai" ? n.written_by : `작성 ${n.written_by}`} · v{n.version} · {formatTime(n.updated_at)}
        </span>
        <label className="small">
          기록할 이름 <input aria-label="노트 기록할 이름" value={name} onChange={(e) => setName(e.target.value)} placeholder="예: 김엔지니어" size={10} />
        </label>
        {n.status === "draft" ? (
          <button onClick={() => !needName() && void act(() => api.reviewNote(n.id, name.trim()), "검토함으로 표시했습니다.")}
            title="근거와 내용을 읽고 맞는지 확인했다면 누르세요">
            검토함으로 표시
          </button>
        ) : (
          <button className="ghost" onClick={() => void act(() => api.reviewNote(n.id, name.trim(), false), "초안으로 되돌렸습니다.")}>
            초안으로 되돌리기
          </button>
        )}
        <button className="ghost" onClick={() => setEditing(true)}>
          고치기
        </button>
        <button className="ghost" onClick={() => void act(async () => download(`${n.id}.md`, await api.noteMarkdown(n.id)))}>
          Markdown 받기
        </button>
        {n.status === "draft" && (
          <button className="ghost danger"
            onClick={() => window.confirm(`${n.id} 초안을 지울까요?`) && void act(async () => {
              await api.deleteNote(n.id, name.trim());
              onDeleted?.();
            })}>
            지우기
          </button>
        )}
      </div>
      {message && (
        <p className="small" role="status">
          {message}
        </p>
      )}
      <article className="markdown" dangerouslySetInnerHTML={{ __html: html }} />
      {n.apply_next.length > 0 && (
        <div className="note-apply">
          <h4>다음 설계에 적용할 것 → 설계 지침</h4>
          <ul>
            {n.apply_next.map((a, i) => (
              <li key={i}>
                {a.text}{" "}
                {a.guide_id ? (
                  <span className="small ok-text">설계 지침 {a.guide_id}(후보)로 남김</span>
                ) : (
                  <button className="link small"
                    onClick={() => void act(() => api.noteToGuide(n.id, i, name.trim()), "설계 지침 후보로 남겼습니다. 설계 가이드에서 다듬고 채택하세요.")}>
                    지침 후보로
                  </button>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
      {n.evidence.length > 0 && (
        <details className="small">
          <summary>AI에게 준 근거 전체 ({n.evidence.length}개)</summary>
          <ul>
            {n.evidence.map((e) => (
              <li key={e.id}>
                <b>{e.id}</b> [{EVIDENCE_KIND[e.kind] ?? e.kind}] {e.text}
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

// --- the notebook page ----------------------------------------------------------------------------

export function NotesPage({ onError }: { onError(message: string | null): void }) {
  const [list, setList] = useState<NoteSummary[]>([]);
  const [topics, setTopics] = useState<Record<string, string>>({});
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [writing, setWriting] = useState(false);
  const [by, setBy] = useState(savedName());

  const load = useCallback(async () => {
    try {
      const r = await api.notes({ q, status });
      setList(r.notes);
      setTopics(r.topics);
    } catch (e) {
      onError(errorText(e));
    }
  }, [q, status, onError]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <section className="page notes-page" aria-label="연구노트">
      <div className="card">
        <h2>연구노트</h2>
        <p className="small">
          회로를 검토하면서 확인한 것, 배운 점, 다음 설계에 적용할 것을 남기는 노트입니다. AI 검토 때 AI가 초안을 쓰고(「검토」 탭·「제안」 탭),
          엔지니어가 직접 쓸 수도 있습니다. AI가 쓴 문장마다 앱이 준 근거 번호(계산·데이터시트 쪽·설계 결정 …)가 붙고, 근거가 없는 문장은 「일반 지식,
          확인 필요」로 표시됩니다. 읽고 맞으면 <b>검토함으로 표시</b>하고, 적용할 것은 <b>지침 후보로</b> 설계 가이드에 넘기세요.
        </p>
        <div className="toolbar">
          <input aria-label="노트 검색" placeholder="검색 (부품, 단어)" value={q} onChange={(e) => setQ(e.target.value)} />
          <select aria-label="노트 상태" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">모든 노트</option>
            <option value="draft">초안</option>
            <option value="reviewed">검토함</option>
          </select>
          <label className="small">
            기록할 이름{" "}
            <input aria-label="기록할 이름" value={by} onChange={(e) => setBy(e.target.value)} onBlur={() => by.trim() && saveName(by.trim())}
              placeholder="예: 김엔지니어" size={10} />
          </label>
          <button onClick={() => setWriting(!writing)}>{writing ? "쓰기 닫기" : "노트 쓰기"}</button>
          <button className="ghost" onClick={() => void api.notesMarkdown().then((t) => download("research-notes.md", t)).catch((e) => onError(errorText(e)))}>
            모두 Markdown으로 받기
          </button>
        </div>
        {writing && (
          <NoteEditor
            initial={null}
            topics={topics}
            onCancel={() => setWriting(false)}
            onSave={async (draft) => {
              if (!by.trim()) throw new Error("「기록할 이름」을 먼저 적으세요.");
              saveName(by.trim());
              const r = await api.createNote({ draft, by: by.trim() });
              setWriting(false);
              setSelected(r.note.id);
              await load();
            }}
          />
        )}
      </div>
      {list.length === 0 ? (
        <div className="empty">노트가 없습니다. 「검토」 탭의 「AI로 연구노트 쓰기」나 「노트 쓰기」로 시작하세요.</div>
      ) : (
        <div className="notes-list">
          {list.map((n) => (
            <div key={n.id} className={`card note-card ${selected === n.id ? "selected" : ""}`} aria-label={`노트 ${n.id}`}>
              <button className="link note-title" onClick={() => setSelected(selected === n.id ? null : n.id)} aria-expanded={selected === n.id}>
                {n.id} {n.title}
              </button>{" "}
              <Chip {...noteStatus(n)} />
              <div className="small muted">
                {noteWhere(n.context) || "보드 없음"} · {formatTime(n.created_at)} · 배운 점 {n.counts.learnings} · 적용할 것 {n.counts.apply_next}
                {n.topics.length > 0 && ` · ${n.topics.map((t) => topics[t] ?? t).join(", ")}`}
              </div>
              {selected !== n.id && n.summary && <p className="small">{n.summary}</p>}
              {selected === n.id && (
                <NoteView id={n.id} topics={topics} onChanged={() => void load()} onDeleted={() => {
                  setSelected(null);
                  void load();
                }} />
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

// --- on a board (검토 tab) --------------------------------------------------------------------------

interface BoardProps {
  projectId: string;
  boardId: string;
  revision: number | null;
  aiReady: boolean;
}

/** This board's notes, and the button for the AI to write one from the review evidence. */
export function BoardNotesCard({ projectId, boardId, revision, aiReady }: BoardProps) {
  const [list, setList] = useState<NoteSummary[]>([]);
  const [topics, setTopics] = useState<Record<string, string>>({});
  const [open, setOpen] = useState<string | null>(null);
  const [focus, setFocus] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const b = await api.boardNotes(projectId, boardId);
      setList(b.notes);
      setTopics(b.topics);
    } catch (e) {
      setMessage(errorText(e));
    }
  }, [projectId, boardId]);

  useEffect(() => {
    void load();
  }, [load, revision]);

  const write = async () => {
    setMessage(null);
    try {
      const p = await api.notePreview(projectId, boardId);
      const kinds = Object.entries(p.evidence_kinds)
        .map(([k, n]) => `${EVIDENCE_KIND[k] ?? k} ${n}`)
        .join(", ");
      const go = window.confirm(
        `AI 연구노트 쓰기\n\n받는 곳: ${providerLabel(p.provider)} · 모델 ${p.model}\n` +
          `보내는 것: 저장된 회로(부품·연결, ${p.circuit_chars}자), 요구사항, 앱이 찾은 근거 ${p.evidence}개(${kinds})` +
          (focus.trim() ? `, 다룰 것: "${focus.trim()}"` : "") +
          "\n노트는 초안으로 저장되고, 사람이 읽고 「검토함」으로 표시해야 확인된 노트가 됩니다.\n\n계속할까요?",
      );
      if (!go) return;
      setBusy(true);
      const job = await waitForJob((await api.aiNote(projectId, boardId, null, focus.trim())).id, undefined, 1500);
      if (job.status === "failed") {
        setMessage(job.error ? `[${job.error.error_code}] ${job.error.message}` : "실패했습니다.");
        return;
      }
      setFocus("");
      setOpen(job.result.note_id);
      setMessage(`연구노트 ${job.result.note_id}(초안)를 썼습니다. 읽고 근거를 확인한 뒤 「검토함으로 표시」하세요.`);
      await load();
    } catch (e) {
      setMessage(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="card" aria-label="연구노트">
      <h3>
        연구노트 <span className="small muted">이 보드 {list.length}개 · 모든 노트는 사이드바 「연구노트」</span>
      </h3>
      <p className="small muted">
        검토하면서 확인한 것, 배운 점, 다음 설계에 적용할 것을 동료가 읽고 쓸 수 있게 남깁니다. AI는 앱이 계산·확인한 근거(기술 검토, 데이터시트 대조·쪽 번호,
        설계 지침, 설계 결정, KiCad 검사, AI 검토)만 근거로 인용합니다.
      </p>
      <div className="toolbar">
        <input aria-label="노트에서 다룰 것" placeholder="특히 다룰 것 (선택, 예: 전원 입력 보호)" value={focus} onChange={(e) => setFocus(e.target.value)}
          style={{ minWidth: 260 }} />
        <button onClick={() => void write()} disabled={!aiReady || busy || !revision} title={aiReady ? "AI가 이 보드의 검토 내용을 연구노트 초안으로 씁니다" : AI_SETUP_HINT}>
          {busy ? "쓰는 중…" : "AI로 연구노트 쓰기"}
        </button>
      </div>
      {message && (
        <p className="small" role="status">
          {message}
        </p>
      )}
      {list.length > 0 && (
        <ul className="note-rows">
          {list.map((n) => (
            <li key={n.id}>
              <button className="link" onClick={() => setOpen(open === n.id ? null : n.id)} aria-expanded={open === n.id}>
                {n.id} {n.title}
              </button>{" "}
              <Chip {...noteStatus(n)} /> <span className="small muted">rev {n.context.revision ?? "—"} · {formatTime(n.created_at)}</span>
              {open === n.id && <NoteView id={n.id} topics={topics} onChanged={() => void load()} onDeleted={() => void load()} />}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
