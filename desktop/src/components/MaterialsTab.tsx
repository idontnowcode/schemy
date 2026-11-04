import { useCallback, useEffect, useState } from "react";

import { api, errorText, waitForJob } from "../api";
import { AI_SETUP_HINT, matchLabel, providerLabel } from "../format";
import type { AiStatus, InventoryMatch, Suggestion } from "../types";
import { Chip } from "./Chip";

interface Props {
  projectId: string;
  boardId: string;
  revision: number | null;
  onApplied(): Promise<unknown>;
}

const FIT_LABEL = { good: "적합", acceptable: "검토 후 가능", poor: "권장 안 함" } as const;

const FIELD_LABELS: Record<string, string> = {
  manufacturer_part_number: "품번",
  manufacturer: "제조사",
  footprint_id: "풋프린트",
};

export function MaterialsTab({ projectId, boardId, revision, onApplied }: Props) {
  const [match, setMatch] = useState<InventoryMatch | null>(null);
  const [boards, setBoards] = useState(1);
  const [choices, setChoices] = useState<Record<string, string>>({});
  const [message, setMessage] = useState<string | null>(null);
  const [ai, setAi] = useState<AiStatus | null>(null);
  const [suggestions, setSuggestions] = useState<Suggestion[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!revision) {
      setMatch(null);
      return;
    }
    try {
      setMatch(await api.inventoryMatch(projectId, boardId, boards));
    } catch (e) {
      setMessage(errorText(e));
    }
  }, [projectId, boardId, revision, boards]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    api.aiStatus().then(setAi).catch(() => setAi(null));
  }, []);

  useEffect(() => {
    setChoices({});
    setSuggestions(null);
  }, [boardId, revision]);

  if (!revision) return <div className="empty">먼저 회로 (IR) 탭에서 회로를 저장하세요.</div>;
  if (!match) return <div className="empty">불러오는 중…</div>;
  if (!match.available) {
    return (
      <div className="empty">
        <p>보유 자재 목록이 없습니다.</p>
        <p className="small">왼쪽 아래 「보유 자재」에서 엑셀(CSV/XLSX) 자재 목록을 올리면, 이 회로의 부품과 자동으로 대조합니다.</p>
      </div>
    );
  }

  const apply = async (applyMatched: boolean) => {
    setMessage(null);
    const picked: Record<string, string> = {};
    for (const line of match.lines) {
      const part = choices[line.references.join(",")];
      if (part) for (const id of line.component_ids) picked[id] = part;
    }
    try {
      const r = await api.inventoryApply(projectId, boardId, applyMatched, picked);
      setMessage(
        r.created
          ? `revision ${r.revision?.revision}으로 저장했습니다: ${r.changes.map((c) => `${c.reference} ${FIELD_LABELS[c.field] ?? c.field} → ${c.after}`).join(", ")}`
          : "바뀐 것이 없습니다 (이미 적용됨).",
      );
      setChoices({});
      await onApplied();
      await load();
    } catch (e) {
      setMessage(errorText(e));
    }
  };

  const suggest = async () => {
    setMessage(null);
    try {
      const preview = await api.suggestPreview(projectId, boardId);
      if (preview.line_count === 0) {
        setMessage("보유 자재 중에 같은 종류의 후보가 있는 미보유·대체 줄이 없어 AI에 보낼 내용이 없습니다.");
        return;
      }
      const ok = window.confirm(
        `AI 대체 자재 추천\n\n전송할 데이터: BOM ${preview.line_count}줄의 부품 사양(기호·값·풋프린트)과 보유 자재 후보 ${preview.candidate_count}개\n` +
          `받는 곳: ${providerLabel(preview.provider)} (모델 ${preview.model})\n\n추천은 제안일 뿐이며, 고른 것만 적용됩니다. 계속할까요?`,
      );
      if (!ok) return;
      setBusy("AI가 대체 자재를 고르는 중…");
      const job = await waitForJob((await api.suggest(projectId, boardId)).id, undefined, 1200);
      if (job.status === "failed") {
        setMessage(job.error ? `[${job.error.error_code}] ${job.error.message}` : "실패했습니다.");
        return;
      }
      setSuggestions(job.result.suggestions);
      if (job.result.dropped?.length) setMessage(job.result.dropped.join(" "));
    } catch (e) {
      setMessage(errorText(e));
    } finally {
      setBusy(null);
    }
  };

  const s = match.summary!;
  const aiReady = Boolean(ai?.settings.enabled && ai.key.configured);
  const chosenCount = Object.keys(choices).length;

  return (
    <div className="materials">
      <div className="card">
        <h3>보유 자재 대조</h3>
        <p className="small">
          자재 목록 <b>{match.inventory?.source_file}</b> ({match.inventory?.part_count}종) · 보유 {s.matched} · 대체 가능{" "}
          {s.candidate} · 미보유 {s.missing}
          {s.stock_short > 0 && <span className="warn-text"> · 재고 부족 {s.stock_short}</span>}
        </p>
        <div className="toolbar">
          <label className="small">
            제작 수량{" "}
            <input
              type="number"
              min={1}
              value={boards}
              onChange={(e) => setBoards(Math.max(1, Number(e.target.value) || 1))}
              style={{ width: 80 }}
              aria-label="제작 수량"
            />{" "}
            장
          </label>
          <button onClick={() => apply(true)} disabled={s.matched === 0 && chosenCount === 0}>
            보유 자재 적용
          </button>
          {chosenCount > 0 && (
            <button className="ghost" onClick={() => apply(false)}>
              고른 것만 적용 ({chosenCount})
            </button>
          )}
          <button
            className="ghost"
            onClick={suggest}
            disabled={!aiReady || busy !== null}
            title={aiReady ? "미보유·대체 줄에 쓸 보유 자재를 AI가 추천합니다" : AI_SETUP_HINT}
          >
            AI 대체 추천
          </button>
        </div>
        <p className="muted small">
          「보유 자재 적용」은 보유로 판정된 부품에 보유 자재의 제조사·제조사 품번·풋프린트를 넣어 새 revision으로 저장합니다. 값·심볼·연결은
          바꾸지 않습니다.
        </p>
        {busy && <p className="small">{busy}</p>}
        {message && (
          <p className="small" role="status">
            {message}
          </p>
        )}
      </div>

      <div className="card">
        <table className="table">
          <thead>
            <tr>
              <th>부품</th>
              <th>수량</th>
              <th>값 / 풋프린트</th>
              <th>상태</th>
              <th>보유 자재</th>
              <th>재고</th>
            </tr>
          </thead>
          <tbody>
            {match.lines.map((line) => {
              const key = line.references.join(",");
              const label = matchLabel(line.status, line.stock_short);
              const lineIndex = match.lines.indexOf(line);
              const tips = suggestions?.filter((x) => x.line === lineIndex) ?? [];
              return (
                <tr key={key}>
                  <td>{line.references.join(" ")}</td>
                  <td>{line.quantity * match.boards}</td>
                  <td>
                    {line.value}
                    <div className="muted small">{line.footprint_id ?? "풋프린트 없음"}</div>
                  </td>
                  <td>
                    <Chip text={label.text} tone={label.tone} />
                    {line.reasons.map((r) => (
                      <div key={r} className="muted small">
                        {r}
                      </div>
                    ))}
                  </td>
                  <td>
                    {line.status === "matched" && line.chosen && (
                      <>
                        <b>{line.chosen.mpn ?? line.chosen.value}</b>
                        <div className="muted small">
                          {[line.chosen.manufacturer, line.chosen.package, line.candidates[0]?.reasons[0]].filter(Boolean).join(" · ")}
                        </div>
                      </>
                    )}
                    {line.candidates.length > 0 && (
                      <select
                        aria-label={`${key} 보유 자재 선택`}
                        value={choices[key] ?? ""}
                        onChange={(e) => setChoices((c) => ({ ...c, [key]: e.target.value }))}
                      >
                        <option value="">{line.status === "matched" ? "다른 자재로 바꾸기…" : "보유 자재에서 고르기…"}</option>
                        {line.candidates.map((c) => (
                          <option key={c.part.id} value={c.part.id}>
                            {c.part.mpn ?? c.part.value} · {c.part.package ?? "?"} · 재고 {c.part.stock ?? "?"} — {c.reasons[0]}
                          </option>
                        ))}
                      </select>
                    )}
                    {tips.map((t) => (
                      <div key={t.inventory_part_id} className="suggestion small">
                        AI 추천: <b>{t.mpn ?? t.inventory_part_id}</b> ({FIT_LABEL[t.fit]}) — {t.reason}{" "}
                        <button className="link" onClick={() => setChoices((c) => ({ ...c, [key]: t.inventory_part_id }))}>
                          이걸로 고르기
                        </button>
                      </div>
                    ))}
                  </td>
                  <td>{line.chosen?.stock ?? "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
