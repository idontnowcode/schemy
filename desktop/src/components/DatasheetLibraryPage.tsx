import { useCallback, useEffect, useState } from "react";

import { api, errorText, openDatasheet } from "../api";
import { datasheetLabel, formatTime } from "../format";
import type { DatasheetEntry } from "../types";
import { Chip } from "./Chip";

export function DatasheetLibraryPage({ onError }: { onError(message: string | null): void }) {
  const [entries, setEntries] = useState<DatasheetEntry[] | null>(null);
  const [dir, setDir] = useState("");
  const [query, setQuery] = useState("");

  const load = useCallback(async () => {
    try {
      const r = await api.datasheets();
      setEntries(r.entries);
      setDir(r.dir);
    } catch (e) {
      onError(errorText(e));
    }
  }, [onError]);

  useEffect(() => {
    void load();
  }, [load]);

  const remove = async (entry: DatasheetEntry) => {
    if (!window.confirm(`'${entry.original_name}' 데이터시트와 요약을 지울까요?\n회로는 바뀌지 않습니다.`)) return;
    try {
      await api.deleteDatasheet(entry.id);
      await load();
    } catch (e) {
      onError(errorText(e));
    }
  };

  const q = query.trim().toLowerCase();
  const shown = (entries ?? []).filter(
    (e) =>
      !q ||
      [e.original_name, e.title, e.manufacturer, ...e.part_numbers, ...e.symbols].some((v) => v?.toLowerCase().includes(q)),
  );

  return (
    <section className="project">
      <header className="project-header">
        <div className="project-title">
          <h1>데이터시트</h1>
          <div className="muted small">
            받은 데이터시트와 요약은 모든 프로젝트가 함께 씁니다. 보관 위치: <code>{dir}</code>
          </div>
        </div>
      </header>
      <div className="card">
        {entries === null ? (
          <p className="small">불러오는 중…</p>
        ) : entries.length === 0 ? (
          <p className="small muted">
            아직 데이터시트가 없습니다. 프로젝트 보드의 「데이터시트」 탭에서 링크로 받거나 PDF를 올리세요.
          </p>
        ) : (
          <>
            <div className="toolbar">
              <input aria-label="데이터시트 검색" placeholder="검색 (품번, 제조사, 파일 이름…)" value={query}
                onChange={(e) => setQuery(e.target.value)} />
              <span className="muted small">{shown.length}개</span>
            </div>
            <table className="table">
              <thead>
                <tr>
                  <th>파일</th>
                  <th>제조사 · 연결된 부품</th>
                  <th>품번 확인</th>
                  <th>요약</th>
                  <th>받은 날</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {shown.map((e) => (
                  <tr key={e.id}>
                    <td>
                      <b>{e.original_name}</b>
                      <div className="muted small">
                        {e.title ?? ""} {e.page_count ? `· ${e.page_count}쪽` : ""} · {e.source === "kicad_symbol" ? "심볼 링크" : "직접 올림"}
                      </div>
                    </td>
                    <td>
                      {e.manufacturer ?? <span className="muted">제조사 모름</span>}
                      <div className="small">{[...e.part_numbers, ...e.symbols].join(", ") || <span className="muted">연결 없음</span>}</div>
                    </td>
                    <td>
                      <Chip {...datasheetLabel(e.identity.status)} />
                    </td>
                    <td>
                      {e.summary ? (
                        <Chip text={e.summary.reviewed ? "원문 확인함" : "원문 미확인"} tone={e.summary.reviewed ? "ok" : "warn"} />
                      ) : (
                        <span className="muted small">없음</span>
                      )}
                    </td>
                    <td className="small">{formatTime(e.added_at)}</td>
                    <td>
                      <div className="row-actions">
                        <button className="ghost small-btn" onClick={() => openDatasheet(e)}>
                          열기
                        </button>
                        <button className="ghost small-btn" onClick={() => remove(e)}>
                          삭제
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </div>
    </section>
  );
}
