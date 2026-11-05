import { Fragment, useCallback, useEffect, useRef, useState } from "react";

import { api, errorText, fileToBase64, openDatasheet, waitForJob } from "../api";
import {
  datasheetSourceLabel,
  AI_SETUP_HINT,
  datasheetLabel,
  externalLabel,
  identityLabel,
  pinMapLabel,
  rangeText,
  ratingsLabel,
  ROW_STATUS,
} from "../format";
import type { AiStatus, DatasheetComponentRow, DatasheetReport, DatasheetSummaryResult } from "../types";
import { Chip } from "./Chip";
import { confirmSummary, DatasheetFinder } from "./DatasheetFinder";

interface Props {
  projectId: string;
  boardId: string;
  revision: number | null;
}

const SEVERITY_LABEL = { error: "오류", warning: "확인 필요", info: "기록" } as const;

export function DatasheetsTab({ projectId, boardId, revision }: Props) {
  const [report, setReport] = useState<DatasheetReport | null>(null);
  const [showAll, setShowAll] = useState(false);
  const [openRow, setOpenRow] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [ai, setAi] = useState<AiStatus | null>(null);
  const [summaryResult, setSummaryResult] = useState<{ ref: string; result: DatasheetSummaryResult } | null>(null);
  const pdfInput = useRef<HTMLInputElement>(null);
  const mdInput = useRef<HTMLInputElement>(null);
  const target = useRef<DatasheetComponentRow | null>(null);

  const load = useCallback(async () => {
    if (!revision) {
      setReport(null);
      return;
    }
    try {
      setReport(await api.boardDatasheets(projectId, boardId));
    } catch (e) {
      setMessage(errorText(e));
    }
  }, [projectId, boardId, revision]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    api.aiStatus().then(setAi).catch(() => setAi(null));
  }, []);

  if (!revision) return <div className="empty">먼저 회로 (IR) 탭에서 회로를 저장하세요.</div>;
  if (!report) return <div className="empty">불러오는 중…</div>;

  const aiReady = Boolean(ai?.settings.enabled && ai.key.configured);
  const rows = report.components.filter((r) => showAll || r.relevant);
  const hint = fetchHint(report);

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

  const choosePdf = (row: DatasheetComponentRow) => {
    target.current = row;
    pdfInput.current?.click();
  };
  const chooseMd = (row: DatasheetComponentRow) => {
    target.current = row;
    mdInput.current?.click();
  };

  const onPdf = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    const row = target.current;
    if (!file || !row) return;
    await run("PDF를 올리는 중…", async () => {
      const r = await api.uploadDatasheet({
        filename: file.name,
        content_base64: await fileToBase64(file),
        manufacturer: row.manufacturer,
        part_numbers: row.manufacturer_part_number ? [row.manufacturer_part_number] : [],
        symbols: [row.symbol_id],
      });
      const label = datasheetLabel(r.entry.identity.status);
      setMessage(`${row.reference}: ${file.name}을(를) 저장했습니다 (${label.text}).`);
      await load();
    });
  };

  const onMd = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    const row = target.current;
    if (!file || !row?.datasheet) return;
    await run("요약을 읽는 중…", async () => {
      const result = await api.importDatasheetSummary(row.datasheet!.id, file.name, await file.text());
      setSummaryResult({ ref: row.reference, result });
      setMessage(result.ok ? `${row.reference}: 요약을 저장했습니다.` : `${row.reference}: 요약을 읽지 못했습니다. 아래 오류를 확인하세요.`);
      if (result.ok) setOpenRow(row.id);
      await load();
    });
  };

  const summarize = (row: DatasheetComponentRow) =>
    run("AI가 데이터시트를 요약하는 중… (1~2분 걸릴 수 있습니다)", async () => {
      const ds = row.datasheet!;
      if (!confirmSummary(ds, ai)) return;
      const job = await waitForJob((await api.summarizeDatasheet(projectId, boardId, ds.id)).id, undefined, 1500);
      if (job.status === "failed") {
        setMessage(job.error ? `[${job.error.error_code}] ${job.error.message}` : "실패했습니다.");
        return;
      }
      setSummaryResult({ ref: row.reference, result: { ...job.result.import, entry: null } });
      setMessage(job.result.saved ? `${row.reference}: AI 요약을 저장했습니다. 원문과 대조하세요.` : `${row.reference}: AI 요약을 읽지 못했습니다.`);
      if (job.result.saved) setOpenRow(row.id);
      await load();
    });

  const toggleReviewed = (row: DatasheetComponentRow) =>
    run("저장하는 중…", async () => {
      await api.setDatasheetReviewed(row.datasheet!.id, !row.datasheet!.summary!.reviewed);
      await load();
    });

  const copyPrompt = () =>
    run("복사하는 중…", async () => {
      const { text } = await api.prompt("datasheet-summary");
      await navigator.clipboard.writeText(text);
      setMessage("코파일럿용 프롬프트를 복사했습니다. 코파일럿에 붙여 넣고 데이터시트 PDF를 첨부하세요.");
    });

  return (
    <div className="datasheets">
      <div className="card">
        <h3>데이터시트</h3>
        <p className="small">
          제조사와 품번이 확실한 부품(또는 부품 전용 KiCad 심볼)은 데이터시트를 받아 핀맵·전원 전압·주변 부품을 회로와 대조합니다. 대조에는
          데이터시트 <b>요약 MD</b>가 필요합니다. 코파일럿에 프롬프트와 PDF를 주고 만든 요약을 가져오거나, AI 기능을 켜고 「AI로 요약」을 쓰세요.
        </p>
        <div className="toolbar">
          <button className="ghost" onClick={copyPrompt} disabled={busy !== null}>
            코파일럿 프롬프트 복사
          </button>
          <label className="small">
            <input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} /> 모든 부품 보기
          </label>
        </div>
        {hint && (
          <p className="small warn-text" role="note">
            {hint}
          </p>
        )}
        <p className="muted small">
          대조 결과는 「요약이 맞다면」 성립하는 자동 검사입니다. 회로를 바꾸지 않고, 엔지니어링 검토 상태도 바꾸지 않습니다. 제조사·품번은 회로
          (IR)에서 <code>manufacturer</code>, <code>manufacturer_part_number</code>로 넣거나 「보유 자재 적용」으로 채울 수 있습니다.
          {!report.library_available && " KiCad 심볼 라이브러리를 찾지 못해 심볼 링크와 핀 이름을 쓸 수 없습니다."}
        </p>
        {busy && <p className="small">{busy}</p>}
        {message && (
          <p className="small" role="status">
            {message}
          </p>
        )}
        <DatasheetFinder projectId={projectId} boardId={boardId} report={report} disabled={busy !== null} onChanged={load} />
        <input ref={pdfInput} type="file" accept=".pdf,application/pdf" hidden onChange={onPdf} aria-label="데이터시트 PDF 파일" />
        <input ref={mdInput} type="file" accept=".md,.markdown,.txt,text/markdown" hidden onChange={onMd} aria-label="요약 MD 파일" />
      </div>

      {summaryResult && <SummaryReport refName={summaryResult.ref} result={summaryResult.result} onClose={() => setSummaryResult(null)} />}

      <div className="card">
        {rows.length === 0 ? (
          <p className="small muted">
            데이터시트를 찾을 부품이 없습니다. IC처럼 제조사가 정해진 부품이 없거나 제조사·품번이 비어 있습니다. 「모든 부품 보기」로 전체를 볼 수 있습니다.
          </p>
        ) : (
          <table className="table" aria-label="부품별 데이터시트">
            <thead>
              <tr>
                <th>부품</th>
                <th>제조사 · 품번</th>
                <th>데이터시트</th>
                <th>요약</th>
                <th>대조 결과</th>
                <th>작업</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <Fragment key={row.id}>
                  <ComponentRow
                    row={row}
                    open={openRow === row.id}
                    busy={busy !== null}
                    aiReady={aiReady}
                    onToggle={() => setOpenRow(openRow === row.id ? null : row.id)}
                    onPdf={() => choosePdf(row)}
                    onMd={() => chooseMd(row)}
                    onSummarize={() => summarize(row)}
                    onReviewed={() => toggleReviewed(row)}
                  />
                  {openRow === row.id && (
                    <tr className="detail-row">
                      <td colSpan={6}>
                        <Details row={row} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

/** Why there is nothing to download, in words the user can act on. */
export function fetchHint(report: DatasheetReport): string | null {
  if (report.fetchable.length > 0) return null;
  if (!report.library_available) {
    return "KiCad 심볼 라이브러리를 찾지 못해 데이터시트 링크를 읽을 수 없습니다. 설정 › 환경의 「심볼 라이브러리」를 확인하세요 (KiCad 10 기본 위치: C:\\Program Files\\KiCad\\10.0\\share\\kicad\\symbols).";
  }
  const sure = report.components.filter((c) => c.identity !== "generic");
  if (sure.length === 0) {
    return "받을 부품이 없습니다. 제조사·품번이 있거나 부품 전용 KiCad 심볼(예: MCU, CAN 트랜시버, 레귤레이터)을 쓰는 부품이 없습니다. 저항·콘덴서·커넥터 같은 일반 부품은 자동으로 받지 않습니다.";
  }
  const noLink = sure.filter((c) => !c.datasheet && (c.candidates ?? []).length === 0).map((c) => c.reference);
  if (noLink.length > 0) {
    return (
      `${noLink.join(", ")}: 받을 곳을 찾지 못했습니다(KiCad 심볼 링크 없음, 주소 규칙을 아는 제조사 아님). ` +
      (report.search?.mouser || report.search?.digikey ? "「유통사에서 찾기」를 누르거나 " : "설정 › 데이터시트 검색에 Mouser·DigiKey 키를 넣거나 ") +
      "제조사 사이트에서 받은 PDF를 「PDF 올리기」로 넣으세요."
    );
  }
  return "받을 곳이 있는 부품은 모두 데이터시트를 이미 받았습니다.";
}

interface RowProps {
  row: DatasheetComponentRow;
  open: boolean;
  busy: boolean;
  aiReady: boolean;
  onToggle(): void;
  onPdf(): void;
  onMd(): void;
  onSummarize(): void;
  onReviewed(): void;
}

function ComponentRow({ row, open, busy, aiReady, onToggle, onPdf, onMd, onSummarize, onReviewed }: RowProps) {
  const ds = row.datasheet;
  const ident = identityLabel(row.identity);
  const linkable = Boolean(row.manufacturer_part_number) || row.identity === "symbol";
  const c = row.checks;
  return (
    <tr>
      <td>
        <b>{row.reference}</b> {row.value}
        <div className="muted small">{row.symbol_id}</div>
      </td>
      <td>
        {row.manufacturer ?? <span className="muted">제조사 없음</span>}
        <div className="small">{row.manufacturer_part_number ?? <span className="muted">품번 없음</span>}</div>
        <Chip text={ident.text} tone={ident.tone} />
      </td>
      <td>
        {ds ? (
          <>
            <Chip text={datasheetLabel(ds.identity.status).text} tone={datasheetLabel(ds.identity.status).tone} />
            <div className="muted small">
              {datasheetSourceLabel(ds.source)}
              {ds.page_count ? ` · ${ds.page_count}쪽` : ""}
            </div>
          </>
        ) : row.can_fetch ? (
          <span className="small">링크 있음 (받기 전)</span>
        ) : (
          <span className="muted small">없음</span>
        )}
      </td>
      <td>
        {ds?.summary ? (
          <>
            <span className="small">{ds.summary.origin === "ai" ? "AI 요약" : "요약 MD"}</span>
            <div>
              <Chip
                text={ds.summary.reviewed ? "원문 확인함" : "원문 미확인"}
                tone={ds.summary.reviewed ? "ok" : "warn"}
              />
            </div>
          </>
        ) : (
          <span className="muted small">{ds ? "없음" : "—"}</span>
        )}
      </td>
      <td>
        {c ? (
          <div className="chip-stack">
            <Chip {...pinMapLabel(c.pin_map.status, c.pin_map.counts, Boolean(c.package.chosen))} />
            <Chip {...ratingsLabel(c.ratings.status)} />
            <Chip {...externalLabel(c.external.rows)} />
          </div>
        ) : (
          <span className="muted small">요약이 있어야 대조합니다</span>
        )}
      </td>
      <td>
        <div className="row-actions">
          <button className="ghost small-btn" onClick={onPdf} disabled={busy || !linkable}
            title={linkable ? "제조사 사이트에서 받은 PDF를 이 부품에 연결합니다" : "품번이 없어 연결할 수 없습니다. 회로 (IR)에서 제조사·품번을 넣으세요"}>
            PDF 올리기
          </button>
          {ds && (
            <>
              <button className="ghost small-btn" onClick={() => openDatasheet(ds)}>
                PDF 열기
              </button>
              <button className="ghost small-btn" onClick={onMd} disabled={busy}>
                요약 MD 가져오기
              </button>
              <button className="ghost small-btn" onClick={onSummarize} disabled={busy || !aiReady}
                title={aiReady ? "AI가 데이터시트를 요약합니다" : AI_SETUP_HINT}>
                AI로 요약
              </button>
            </>
          )}
          {ds?.summary && (
            <button className="ghost small-btn" onClick={onReviewed} disabled={busy}>
              {ds.summary.reviewed ? "확인 취소" : "원문 확인함"}
            </button>
          )}
          {(c || ds) && (
            <button className="link" onClick={onToggle} aria-expanded={open}>
              {open ? "접기" : "자세히"}
            </button>
          )}
        </div>
      </td>
    </tr>
  );
}

function Details({ row }: { row: DatasheetComponentRow }) {
  const ds = row.datasheet;
  const c = row.checks;
  return (
    <div className="ds-details">
      {ds && (
        <p className="small">
          <b>{ds.title ?? ds.original_name}</b> · {ds.original_name} · {(ds.bytes / 1024).toFixed(0)} KB
          {ds.urls[0] && (
            <>
              {" "}
              · 출처 <code>{ds.final_url ?? ds.urls[0]}</code>
            </>
          )}
          <br />
          <span className="muted">
            품번 확인: {datasheetLabel(ds.identity.status).text}
            {ds.identity.found?.length ? ` (본문에서 찾음: ${ds.identity.found.join(", ")})` : ""}
            {ds.identity.status === "not_found" && " — 다른 부품의 문서일 수 있습니다. PDF를 열어 확인하세요."}
          </span>
        </p>
      )}
      {!c && <p className="small muted">요약 MD를 가져오면 핀맵·전압·외부 부품을 대조합니다.</p>}
      {c && (
        <>
          <h4>
            핀맵 대조 <span className="muted small">패키지: {c.package.chosen ?? "—"} ({c.package.reason})</span>
          </h4>
          {c.pin_map.rows.length > 0 && (
            <table className="table compact">
              <thead>
                <tr>
                  <th>핀</th>
                  <th>{c.pin_names_from === "library" ? "KiCad 심볼" : "회로"}</th>
                  <th>데이터시트</th>
                  <th>종류</th>
                  <th>쪽</th>
                  <th>결과</th>
                </tr>
              </thead>
              <tbody>
                {c.pin_map.rows.map((r) => (
                  <tr key={`${r.number}-${r.datasheet_number}`}>
                    <td>{r.number}{r.datasheet_number && r.datasheet_number !== r.number ? ` (${r.datasheet_number})` : ""}</td>
                    <td>{r.symbol_name ?? "—"}</td>
                    <td>{r.datasheet_name ?? "—"}</td>
                    <td>{r.kind ?? ""}</td>
                    <td>{r.page ?? ""}</td>
                    <td>
                      <Chip {...ROW_STATUS[r.status]} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <h4>전원 전압 점검</h4>
          {c.ratings.rows.length === 0 ? (
            <p className="small muted">대조할 전압 항목이 없습니다 (요약의 3·4장 「관련 핀」과 핀 이름이 맞는지 확인하세요).</p>
          ) : (
            <table className="table compact">
              <thead>
                <tr>
                  <th>핀</th>
                  <th>Net (전압)</th>
                  <th>절대 최대 정격</th>
                  <th>권장 동작 조건</th>
                  <th>결과</th>
                </tr>
              </thead>
              <tbody>
                {c.ratings.rows.map((r) => (
                  <tr key={r.number}>
                    <td>
                      {r.number} {r.name}
                    </td>
                    <td>
                      {r.net ?? "미연결"} {r.voltage !== null ? `(${r.voltage} V)` : ""}
                    </td>
                    <td>{r.abs_max.map((x) => `${rangeText(x.min, x.max)} (p.${x.page ?? "?"})`).join(", ") || "—"}</td>
                    <td>{r.recommended.map((x) => `${rangeText(x.min, x.max)} (p.${x.page ?? "?"})`).join(", ") || "—"}</td>
                    <td>
                      <Chip {...ROW_STATUS[r.status]} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <p className="muted small">전압은 Net 이름에서 읽은 공칭값입니다(+3V3 = 3.3 V). 이름에 전압이 없는 Net(VCC 등)은 판단하지 않습니다.</p>

          <h4>외부 부품 체크리스트</h4>
          {c.external.rows.length === 0 ? (
            <p className="small muted">요약에 외부 부품이 없습니다.</p>
          ) : (
            <table className="table compact">
              <thead>
                <tr>
                  <th>구분</th>
                  <th>부품</th>
                  <th>연결</th>
                  <th>회로에서 찾은 부품</th>
                  <th>쪽</th>
                  <th>결과</th>
                </tr>
              </thead>
              <tbody>
                {c.external.rows.map((r, i) => (
                  <tr key={i}>
                    <td>{r.requirement === "required" ? "필수" : "권장"}</td>
                    <td>
                      {r.kind_label} {r.value ?? ""} {r.quantity && r.quantity > 1 ? `× ${r.quantity}` : ""}
                      {r.description && <div className="muted small">{r.description}</div>}
                    </td>
                    <td>{r.endpoints.join(" ~ ") || "—"}</td>
                    <td>{r.found.join(", ") || "—"}</td>
                    <td>{r.page ?? ""}</td>
                    <td>
                      <Chip {...ROW_STATUS[r.status]} />
                      {r.reason && <div className="muted small">{r.reason}</div>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
      {row.notes.length > 0 && (
        <>
          <h4>응용 회로 메모 (설계 참고)</h4>
          <ul className="small">
            {row.notes.map((n, i) => (
              <li key={i}>
                {n.text} {n.page ? <span className="muted">(p.{n.page})</span> : null}
              </li>
            ))}
          </ul>
        </>
      )}
      {row.uncertain.length > 0 && (
        <>
          <h4>요약의 불확실 항목</h4>
          <ul className="small">
            {row.uncertain.map((u, i) => (
              <li key={i}>
                {u["내용"]} {u["페이지"] ? <span className="muted">(p.{u["페이지"]})</span> : null}
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

function SummaryReport({ refName, result, onClose }: { refName: string; result: DatasheetSummaryResult; onClose(): void }) {
  const order = ["error", "warning", "info"] as const;
  return (
    <div className="card">
      <div className="toolbar">
        <h3>요약 가져오기 결과: {refName}</h3>
        <button className="ghost small-btn" onClick={onClose}>
          닫기
        </button>
      </div>
      {result.ok ? (
        <p className="small">
          패키지 {result.stats.packages} · 핀 {result.stats.pins} · 절대 최대 정격 {result.stats.abs_max} · 권장 조건{" "}
          {result.stats.recommended} · 외부 부품 {result.stats.external_parts} · 확인 필요 {result.stats.warnings}건
        </p>
      ) : (
        <p className="small bad-text">요약을 저장하지 않았습니다. 아래 오류를 Markdown에서 고친 뒤 다시 가져오세요.</p>
      )}
      {order.map((sev) => {
        const items = result.issues.filter((i) => i.severity === sev);
        if (items.length === 0) return null;
        return (
          <details key={sev} open={sev !== "info"}>
            <summary className="small">
              {SEVERITY_LABEL[sev]} {items.length}건
            </summary>
            <ul className="issues">
              {items.map((i, k) => (
                <li key={k} className={`issue issue-${sev === "info" ? "info" : sev}`}>
                  <span className="issue-code">{i.code}</span> {i.message}
                  {i.line ? <span className="muted"> (줄 {i.line})</span> : null}
                </li>
              ))}
            </ul>
          </details>
        );
      })}
    </div>
  );
}
