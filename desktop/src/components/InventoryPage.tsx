import { useEffect, useMemo, useRef, useState } from "react";

import { api, errorText, fileToBase64 } from "../api";
import { formatTime } from "../format";
import type { InventoryImportResult, InventoryInfo } from "../types";

export function InventoryPage({ onError }: { onError(message: string | null): void }) {
  const [info, setInfo] = useState<InventoryInfo | null>(null);
  const [preview, setPreview] = useState<{ file: File; result: InventoryImportResult } | null>(null);
  const [query, setQuery] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);

  const load = () => api.inventory().then(setInfo).catch((e) => onError(errorText(e)));
  useEffect(() => {
    void load();
  }, []);

  const choose = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setMessage(null);
    try {
      setPreview({ file, result: await api.importInventory(file.name, await fileToBase64(file), false) });
    } catch (err) {
      setMessage(errorText(err));
    }
  };

  const save = async () => {
    if (!preview) return;
    try {
      const r = await api.importInventory(preview.file.name, await fileToBase64(preview.file), true);
      setMessage(`${r.part_count}종을 저장했습니다.`);
      setPreview(null);
      await load();
    } catch (err) {
      setMessage(errorText(err));
    }
  };

  const remove = async () => {
    if (!window.confirm("보유 자재 목록을 지울까요? 프로젝트 회로는 바뀌지 않습니다.")) return;
    await api.deleteInventory();
    await load();
  };

  const parts = useMemo(() => {
    const all = (preview ? preview.result.parts : info?.inventory?.parts) ?? [];
    const q = query.trim().toLowerCase();
    if (!q) return all;
    return all.filter((p) => [p.mpn, p.manufacturer, p.category_label, p.value, p.package, p.notes]
      .some((v) => v?.toLowerCase().includes(q)));
  }, [info, preview, query]);

  const labels = info?.category_labels ?? {};
  const columnLabels = info?.column_labels ?? {};

  return (
    <section className="project">
      <header className="project-header">
        <div className="project-title">
          <h1>보유 자재</h1>
          <div className="muted small">
            갖고 있는 자재 목록(엑셀 CSV/XLSX)을 올리면 모든 프로젝트의 「자재」 탭에서 회로 부품과 자동으로 대조합니다.
          </div>
        </div>
      </header>

      <div className="card">
        <div className="toolbar">
          <button onClick={() => input.current?.click()}>자재 목록 파일 선택</button>
          <input ref={input} type="file" accept=".csv,.tsv,.txt,.xlsx,.xlsm" hidden onChange={choose} />
          {info?.inventory && !preview && (
            <>
              <span className="small">
                현재: <b>{info.inventory.source_file}</b> · {info.inventory.parts.length}종 · {formatTime(info.inventory.imported_at)}
              </span>
              <button className="ghost" onClick={remove}>
                목록 지우기
              </button>
            </>
          )}
        </div>
        <p className="muted small">
          첫 줄에 열 이름이 있어야 합니다. 알아보는 열: {Object.values(columnLabels).join(", ")}. 한글·영문 열 이름(예: 품번/MPN, 값/Value,
          패키지/Package, 재고/Qty)을 모두 읽고, 한국어 엑셀에서 저장한 CSV(CP949)도 읽습니다. 우선순위는 숫자가 작을수록 먼저 고릅니다.
        </p>
        {message && (
          <p className="small" role="status">
            {message}
          </p>
        )}
      </div>

      {preview && (
        <div className="card">
          <h3>미리보기: {preview.file.name}</h3>
          {preview.result.ok ? (
            <p className="small">
              {preview.result.part_count}종을 읽었습니다. 열 대응:{" "}
              {Object.entries(preview.result.mapped_columns)
                .map(([k, v]) => `${k} → ${columnLabels[v] ?? v}`)
                .join(", ")}
              {preview.result.unknown_columns.length > 0 && ` · 무시한 열: ${preview.result.unknown_columns.join(", ")}`}
            </p>
          ) : (
            <p className="small bad-text">읽지 못했습니다.</p>
          )}
          <ul className="issues">
            {preview.result.errors.map((m, i) => (
              <li key={`e${i}`} className="issue issue-error">
                {m.row ? `${m.row}행: ` : ""}
                {m.message}
              </li>
            ))}
            {preview.result.warnings.slice(0, 20).map((m, i) => (
              <li key={`w${i}`} className="issue issue-warning">
                {m.row ? `${m.row}행: ` : ""}
                {m.message}
              </li>
            ))}
          </ul>
          <div className="toolbar">
            <button onClick={save} disabled={!preview.result.ok}>
              이 목록으로 저장
            </button>
            <button className="ghost" onClick={() => setPreview(null)}>
              취소
            </button>
          </div>
        </div>
      )}

      {(preview || info?.inventory) && (
        <div className="card">
          <div className="toolbar">
            <input aria-label="자재 검색" placeholder="검색 (품번, 값, 패키지…)" value={query} onChange={(e) => setQuery(e.target.value)} />
            <span className="muted small">{parts.length}종</span>
          </div>
          <table className="table">
            <thead>
              <tr>
                <th>행</th>
                <th>종류</th>
                <th>값</th>
                <th>패키지</th>
                <th>제조사 품번</th>
                <th>제조사</th>
                <th>재고</th>
                <th>우선순위</th>
                <th>비고</th>
              </tr>
            </thead>
            <tbody>
              {parts.slice(0, 500).map((p) => (
                <tr key={p.id}>
                  <td>{p.source_row}</td>
                  <td>{p.category ? labels[p.category] ?? p.category : <span className="warn-text">{p.category_label ?? "?"}</span>}</td>
                  <td>{p.value ?? "—"}</td>
                  <td>{p.package ?? p.footprint_id ?? "—"}</td>
                  <td>{p.mpn ?? "—"}</td>
                  <td>{p.manufacturer ?? "—"}</td>
                  <td>{p.stock ?? "—"}</td>
                  <td>{p.priority}</td>
                  <td>{p.notes ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {parts.length > 500 && <p className="muted small">처음 500종만 표시합니다. 검색으로 좁히세요.</p>}
        </div>
      )}
    </section>
  );
}
