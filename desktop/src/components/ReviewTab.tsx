import { useCallback, useEffect, useMemo, useState } from "react";

import { api, errorText } from "../api";
import { formatTime, originLabel, reviewLabel } from "../format";
import type { BoardReviewStatus, ChecklistSuggestion, CircuitIR, EngineeringReview, ReviewCheckItem } from "../types";
import { Chip } from "./Chip";
import { GuideChecklistPanel } from "./Guides";
import { PartSpecsCard } from "./PartSpecsCard";
import { TechnicalReviewCard } from "./TechnicalReviewCard";

interface Props {
  projectId: string;
  boardId: string;
  circuit: CircuitIR | null;
  revision: number | null;
  onChanged(): Promise<unknown>;
  onOpenProposal?(id: string): void;
}

type Result = "" | ReviewCheckItem["result"];
const REVIEWER_KEY = "aics.reviewer";

function savedReviewer(): string {
  try {
    return window.localStorage.getItem(REVIEWER_KEY) ?? "";
  } catch {
    return "";
  }
}

/** Engineering review records: the only thing that changes the "engineering review" status. */
export function ReviewTab({ projectId, boardId, circuit, revision, onChanged, onOpenProposal }: Props) {
  const [status, setStatus] = useState<BoardReviewStatus | null>(null);
  const [reviews, setReviews] = useState<EngineeringReview[]>([]);
  const [items, setItems] = useState<ChecklistSuggestion[]>([]);
  const [results, setResults] = useState<Record<string, { result: Result; note: string }>>({});
  const [custom, setCustom] = useState<{ item: string; result: Result; note: string }[]>([]);
  const [reviewer, setReviewer] = useState(savedReviewer);
  const [scope, setScope] = useState<"board" | "components">("board");
  const [picked, setPicked] = useState<string[]>([]);
  const [sheets, setSheets] = useState([{ part: "", document: "", revision: "", pages: "" }]);
  const [outcome, setOutcome] = useState<"verified" | "needs_changes">("verified");
  const [notes, setNotes] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [problems, setProblems] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [aiReady, setAiReady] = useState(false);

  useEffect(() => {
    api.aiStatus().then((a) => setAiReady(Boolean(a.settings.enabled && a.key.configured))).catch(() => setAiReady(false));
  }, []);

  const load = useCallback(async () => {
    try {
      const [r, c] = await Promise.all([api.reviews(projectId, boardId), api.reviewChecklist(projectId, boardId)]);
      setStatus(r.status);
      setReviews(r.reviews);
      setItems(c.items);
    } catch (e) {
      setMessage(errorText(e));
    }
  }, [projectId, boardId]);

  useEffect(() => {
    void load();
  }, [load, revision]);

  const groups = useMemo(() => {
    const out = new Map<string, ChecklistSuggestion[]>();
    for (const i of items) out.set(i.group, [...(out.get(i.group) ?? []), i]);
    return [...out.entries()];
  }, [items]);

  if (!circuit || revision === null) return <div className="empty">검토할 회로가 없습니다. 먼저 회로를 저장하거나 제안을 승인하세요.</div>;

  const board = reviewLabel(status?.board ?? "unverified");

  const submit = async () => {
    setMessage(null);
    setProblems([]);
    const checks: ReviewCheckItem[] = [
      ...items
        .filter((i) => results[i.source]?.result)
        .map((i) => ({ item: i.item, source: i.source, result: results[i.source].result as ReviewCheckItem["result"], note: results[i.source].note || null })),
      ...custom.filter((c) => c.item.trim() && c.result).map((c) => ({ item: c.item.trim(), source: "custom", result: c.result as ReviewCheckItem["result"], note: c.note || null })),
    ];
    if (!reviewer.trim()) return setMessage("검토자 이름을 적으세요.");
    if (!checks.length) return setMessage("확인한 항목을 하나 이상 기록하세요.");
    setBusy(true);
    try {
      try {
        window.localStorage.setItem(REVIEWER_KEY, reviewer.trim());
      } catch {
        /* per-viewer convenience only */
      }
      const r = await api.addReview(projectId, boardId, {
        revision,
        reviewer: reviewer.trim(),
        scope,
        component_ids: scope === "components" ? picked : [],
        datasheets: sheets
          .filter((s) => s.part.trim())
          .map((s) => ({ part: s.part.trim(), document: s.document.trim(), revision: s.revision.trim() || null, pages: s.pages.trim() || null })),
        checks,
        result: outcome,
        notes: notes.trim() || null,
      });
      setStatus(r.status);
      setResults({});
      setCustom([]);
      setNotes("");
      setMessage(`검토를 기록했습니다 (#${Number(r.review.id)}).`);
      await load();
      await onChanged();
    } catch (e) {
      setMessage(errorText(e));
      const details = (e as { body?: { details?: { missing?: string[]; issues?: string[] } } }).body?.details;
      setProblems([...(details?.missing ?? []), ...(details?.issues ?? [])]);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="requirements-layout">
      <div className="panel">
        <TechnicalReviewCard projectId={projectId} boardId={boardId} revision={revision} onOpenProposal={onOpenProposal} />
        <PartSpecsCard projectId={projectId} boardId={boardId} circuit={circuit} onOpenProposal={onOpenProposal} />
        <div className="card">
          <GuideChecklistPanel projectId={projectId} boardId={boardId} refreshKey={revision} aiReady={aiReady} onOpenProposal={onOpenProposal} />
        </div>
        <div className="toolbar">
          <Chip label="엔지니어링 검토" text={board.text} tone={board.tone} />
          <span className="muted small">revision {revision}</span>
        </div>
        <p className="muted small">
          엔지니어가 데이터시트와 대조해 확인한 내용을 기록합니다. 이 기록만 엔지니어링 검토 상태를 바꿉니다(자동 검사·AI는 바꾸지 않음).
          회로가 바뀌면 바뀐 revision을 다시 검토해야 합니다.
        </p>
        <table className="table compact">
          <thead>
            <tr>
              <th>기호</th>
              <th>값</th>
              <th>출처</th>
              <th>상태</th>
              {scope === "components" && <th>이번 검토</th>}
            </tr>
          </thead>
          <tbody>
            {circuit.components.map((c) => {
              const s = reviewLabel(status?.components[c.id] ?? "unverified");
              return (
                <tr key={c.id}>
                  <td>{c.reference}</td>
                  <td>{c.value}</td>
                  <td className="small">{originLabel(c.origin)}</td>
                  <td>
                    <span className={`chip chip-${s.tone}`}>{s.text}</span>
                  </td>
                  {scope === "components" && (
                    <td>
                      <input
                        type="checkbox"
                        aria-label={`${c.reference} 검토 범위`}
                        checked={picked.includes(c.id)}
                        onChange={(e) => setPicked(e.target.checked ? [...picked, c.id] : picked.filter((x) => x !== c.id))}
                      />
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>

        <h3>검토 이력</h3>
        {reviews.length === 0 && <p className="muted small">아직 기록이 없습니다.</p>}
        {reviews.map((r) => (
          <details key={r.id} className="small">
            <summary>
              #{Number(r.id)} · revision {r.revision} · {r.reviewer} · {r.result === "verified" ? "검토 완료" : "수정 필요"} · {formatTime(r.reviewed_at)}
            </summary>
            <p>
              범위: {r.scope === "board" ? "보드 전체" : r.covered.length + "개 부품"}
              {r.notes ? ` · ${r.notes}` : ""}
            </p>
            {r.datasheets.length > 0 && (
              <ul>
                {r.datasheets.map((d, i) => (
                  <li key={i}>
                    {d.part}: {d.document} {d.revision ? `(${d.revision})` : ""} {d.pages ? `쪽 ${d.pages}` : ""}
                  </li>
                ))}
              </ul>
            )}
            <ul>
              {r.checks.map((c, i) => (
                <li key={i}>
                  [{c.result === "ok" ? "확인" : c.result === "issue" ? "문제" : "해당 없음"}] {c.item}
                  {c.note ? ` — ${c.note}` : ""}
                </li>
              ))}
            </ul>
          </details>
        ))}
      </div>

      <div className="side">
        <div className="card">
          <h3>검토 기록 추가</h3>
          <div className="form-grid">
            <label className="small">
              검토자
              <input aria-label="검토자" value={reviewer} onChange={(e) => setReviewer(e.target.value)} />
            </label>
            <label className="small">
              범위
              <select aria-label="검토 범위" value={scope} onChange={(e) => setScope(e.target.value as "board" | "components")}>
                <option value="board">보드 전체</option>
                <option value="components">고른 부품 (왼쪽 표에서 선택)</option>
              </select>
            </label>
          </div>

          <h4>확인한 데이터시트</h4>
          {sheets.map((s, i) => (
            <div key={i} className="form-grid">
              {(["part", "document", "revision", "pages"] as const).map((k) => (
                <input
                  key={k}
                  aria-label={`데이터시트 ${i + 1} ${{ part: "부품", document: "문서", revision: "개정", pages: "쪽" }[k]}`}
                  placeholder={{ part: "부품 (예: SN65HVD230)", document: "문서 (예: SLOS346)", revision: "개정/날짜", pages: "쪽 (예: 12, 20-21)" }[k]}
                  value={s[k]}
                  onChange={(e) => setSheets(sheets.map((x, j) => (j === i ? { ...x, [k]: e.target.value } : x)))}
                />
              ))}
            </div>
          ))}
          <button className="ghost small-btn" onClick={() => setSheets([...sheets, { part: "", document: "", revision: "", pages: "" }])}>
            데이터시트 추가
          </button>

          <h4>체크리스트</h4>
          {groups.map(([group, list]) => (
            <div key={group}>
              <p className="small">
                <b>{group}</b>
              </p>
              {list.map((i) => {
                const r = results[i.source] ?? { result: "", note: "" };
                return (
                  <div key={i.source} className="check-row small">
                    <select
                      aria-label={`결과: ${i.item}`}
                      value={r.result}
                      onChange={(e) => setResults({ ...results, [i.source]: { ...r, result: e.target.value as Result } })}
                    >
                      <option value="">—</option>
                      <option value="ok">확인</option>
                      <option value="issue">문제</option>
                      <option value="na">해당 없음</option>
                    </select>
                    <div>
                      {i.item} <span className="muted">— {i.basis}</span>
                      {r.result && (
                        <input
                          aria-label={`메모: ${i.item}`}
                          placeholder="메모 (근거 쪽, 값 등)"
                          value={r.note}
                          onChange={(e) => setResults({ ...results, [i.source]: { ...r, note: e.target.value } })}
                        />
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          ))}
          {custom.map((c, i) => (
            <div key={i} className="check-row small">
              <select
                aria-label={`직접 항목 ${i + 1} 결과`}
                value={c.result}
                onChange={(e) => setCustom(custom.map((x, j) => (j === i ? { ...x, result: e.target.value as Result } : x)))}
              >
                <option value="">—</option>
                <option value="ok">확인</option>
                <option value="issue">문제</option>
                <option value="na">해당 없음</option>
              </select>
              <input
                aria-label={`직접 항목 ${i + 1}`}
                placeholder="확인한 내용"
                value={c.item}
                onChange={(e) => setCustom(custom.map((x, j) => (j === i ? { ...x, item: e.target.value } : x)))}
              />
            </div>
          ))}
          <button className="ghost small-btn" onClick={() => setCustom([...custom, { item: "", result: "", note: "" }])}>
            직접 항목 추가
          </button>

          <h4>결과</h4>
          <div className="toolbar" role="radiogroup" aria-label="검토 결과">
            <label className="small">
              <input type="radio" name="outcome" checked={outcome === "verified"} onChange={() => setOutcome("verified")} /> 검토 완료 (Engineer reviewed)
            </label>
            <label className="small">
              <input type="radio" name="outcome" checked={outcome === "needs_changes"} onChange={() => setOutcome("needs_changes")} /> 수정 필요
            </label>
          </div>
          <textarea className="small-textarea" aria-label="검토 메모" placeholder="메모 (선택)" value={notes} onChange={(e) => setNotes(e.target.value)} />
          <div className="toolbar">
            <button onClick={submit} disabled={busy}>
              검토 기록 저장
            </button>
          </div>
          {message && (
            <p className="small" role="status">
              {message}
            </p>
          )}
          {problems.length > 0 && (
            <ul className="small warn-text">
              {problems.map((x, i) => (
                <li key={i}>{x}</li>
              ))}
            </ul>
          )}
          <p className="muted small">「검토 완료」는 문제 항목이 없고 템플릿 체크리스트를 모두 기록했을 때만 저장됩니다.</p>
        </div>
      </div>
    </div>
  );
}
