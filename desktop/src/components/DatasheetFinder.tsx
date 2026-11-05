import { useCallback, useEffect, useState } from "react";

import { api, errorText, openLink, waitForJob } from "../api";
import { AI_SETUP_HINT, datasheetLabel, providerLabel } from "../format";
import type { AiStatus, DatasheetComponentRow, DatasheetEntry, DatasheetFetchResult, DatasheetReport } from "../types";
import { Chip } from "./Chip";

const SOURCE_SHORT: Record<string, string> = {
  digikey: "DigiKey",
  mouser: "Mouser",
  kicad_symbol: "KiCad 링크",
  maker_site: "제조사 사이트",
};

function host(url: string): string {
  try {
    return new URL(url).hostname;
  } catch {
    return url;
  }
}

/** Confirm text before a datasheet PDF goes to the AI (what is sent and where). */
export function confirmSummary(ds: DatasheetEntry, ai: AiStatus | null): boolean {
  return window.confirm(
    `AI로 데이터시트 요약\n\n전송할 데이터: ${ds.original_name} (${(ds.bytes / 1024 / 1024).toFixed(1)} MB` +
      `${ds.page_count ? `, ${ds.page_count}쪽` : ""})\n받는 곳: ${providerLabel(ai?.settings.provider)} (모델 ${ai?.settings.model})\n\n` +
      "데이터시트는 공개 문서지만, 어떤 부품을 쓰는지는 전달됩니다. 요약은 원문과 대조한 뒤 '원문 확인함'을 눌러 주세요. 계속할까요?",
  );
}

interface FinderProps {
  projectId: string;
  boardId: string;
  proposalId?: string | null;
  report: DatasheetReport;
  disabled?: boolean;
  onChanged(): Promise<void>;
}

/**
 * Parts with no datasheet yet: pick where to get each one (distributor search result, KiCad symbol link,
 * the maker's address pattern), search the distributors, and download what was picked.
 */
export function DatasheetFinder({ projectId, boardId, proposalId, report, disabled, onChanged }: FinderProps) {
  const [choice, setChoice] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [results, setResults] = useState<DatasheetFetchResult[] | null>(null);
  const rows = report.components.filter((r) => r.identity !== "generic" && !r.datasheet);
  const byId = new Map(report.fetchable.map((f) => [f.id, f]));
  const canSearch = report.search.mouser || report.search.digikey;
  const picked = (row: DatasheetComponentRow) => choice[row.reference] ?? row.candidates[0] ?? "";
  const chosen = [...new Set(rows.map(picked).filter(Boolean))];

  if (rows.length === 0 && !results) return null;

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

  const search = () =>
    run("유통사에서 찾는 중…", async () => {
      const where = [report.search.digikey && "DigiKey", report.search.mouser && "Mouser"].filter(Boolean).join("·");
      const terms = rows.map((r) => `· ${r.reference}: ${r.manufacturer_part_number ?? r.symbol_id.split(":")[1] ?? r.value}`);
      const ok = window.confirm(
        `유통사에서 데이터시트 찾기\n\n${where} 검색 API에 아래 품번만 보냅니다(회로 내용은 보내지 않음).\n${terms.join("\n")}\n\n계속할까요?`,
      );
      if (!ok) return;
      const job = await waitForJob((await api.searchDatasheets(projectId, boardId, proposalId)).id);
      if (job.status === "failed") {
        setMessage(job.error ? `[${job.error.error_code}] ${job.error.message}` : "실패했습니다.");
        return;
      }
      const list = job.result.results as { term: string; references: string[]; found: number; errors: { source: string; message: string }[] }[];
      setMessage(
        list
          .map((r) => `${r.references.join(",")} ${r.term}: ${r.found}곳 찾음` + (r.errors.length ? ` (${r.errors.map((e) => `${e.source}: ${e.message}`).join("; ")})` : ""))
          .join(" / "),
      );
      setChoice({});
      await onChanged();
    });

  const fetchChosen = () =>
    run("데이터시트를 받는 중…", async () => {
      const lines = rows
        .filter((r) => picked(r))
        .map((r) => {
          const c = byId.get(picked(r));
          const others = r.candidates.filter((id) => id !== picked(r)).map((id) => byId.get(id));
          const fallbacks = others.length ? ` (안 되면 ${others.map((o) => `${SOURCE_SHORT[o!.source]} ${host(o!.url)}`).join(", ")})` : "";
          return `· ${r.reference}: ${c ? `${SOURCE_SHORT[c.source]} — ${host(c.url)}` : "?"}${fallbacks}`;
        });
      const ok = window.confirm(
        `데이터시트 받기\n\n고른 곳에서 PDF를 받습니다 (HTTPS). 실패하면 같은 부품의 다른 곳을 차례로 시도합니다.\n${lines.join("\n")}\n\n` +
          "보내는 정보는 없습니다. 링크 주소로 파일을 요청만 합니다. 계속할까요?",
      );
      if (!ok) return;
      const job = await waitForJob((await api.fetchDatasheets(projectId, boardId, chosen, proposalId)).id);
      if (job.status === "failed") {
        setMessage(job.error ? `[${job.error.error_code}] ${job.error.message}` : "실패했습니다.");
        return;
      }
      setResults(job.result.results as DatasheetFetchResult[]);
      setMessage(`${job.result.downloaded}개를 받았습니다${job.result.failed ? `, ${job.result.failed}개는 받지 못했습니다` : ""}.`);
      await onChanged();
    });

  return (
    <section className="ds-finder" aria-label="데이터시트 찾기">
      <h4>데이터시트 찾기</h4>
      {rows.length > 0 && (
        <>
          <p className="small">
            부품마다 받을 곳을 고르세요. 실패하면 같은 부품의 다른 곳을 차례로 시도합니다. 유통사 검색은 품번으로 정확한 문서를 찾고, KiCad 링크는
            제품군 문서일 수 있으며, 제조사 사이트는 품번으로 만든 주소라 없을 수도 있습니다.
          </p>
          <table className="table compact">
            <thead>
              <tr>
                <th>부품</th>
                <th>받을 곳</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.reference}>
                  <td>
                    <b>{r.reference}</b> {r.manufacturer_part_number ?? r.value}
                    {r.manufacturer && <span className="muted small"> · {r.manufacturer}</span>}
                  </td>
                  <td>
                    {r.candidates.length === 0 ? (
                      <span className="small muted">찾은 곳 없음 — {canSearch ? "유통사에서 찾아 보거나 " : ""}PDF를 직접 올리세요.</span>
                    ) : (
                      <select
                        aria-label={`받을 곳: ${r.reference}`}
                        value={picked(r)}
                        onChange={(e) => setChoice({ ...choice, [r.reference]: e.target.value })}
                      >
                        {r.candidates.map((id) => {
                          const c = byId.get(id);
                          if (!c) return null;
                          return (
                            <option key={id} value={id}>
                              {SOURCE_SHORT[c.source]}: {host(c.url)}
                              {c.part_number ? ` · ${c.part_number}` : ""}
                              {c.status ? ` · ${c.status}` : ""}
                            </option>
                          );
                        })}
                        <option value="">받지 않음</option>
                      </select>
                    )}
                    {r.searched && (
                      <div className="small muted">
                        유통사 검색({r.searched.term}): {r.searched.found}곳 찾음
                        {r.searched.errors.map((e) => (
                          <span key={e.source} className="warn-text"> · {e.source}: {e.message}</span>
                        ))}
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="toolbar">
            <button onClick={fetchChosen} disabled={disabled || busy !== null || chosen.length === 0}>
              선택한 곳에서 받기 ({chosen.length})
            </button>
            <button className="ghost" onClick={search} disabled={disabled || busy !== null || !canSearch}
              title={canSearch ? "Mouser·DigiKey 검색 API로 품번의 데이터시트 링크를 찾습니다" : "설정 › 데이터시트 검색에서 키를 넣으세요"}>
              유통사에서 찾기 (Mouser·DigiKey)
            </button>
          </div>
          {!canSearch && (
            <p className="small muted">
              설정 › 데이터시트 검색에 Mouser API 키나 DigiKey Client ID·Secret을 넣으면 유통사에서 품번으로 정확한 링크를 찾습니다. 키 없이도 위의
              링크로 받을 수 있습니다.
            </p>
          )}
        </>
      )}
      {busy && <p className="small">{busy}</p>}
      {message && (
        <p className="small" role="status">
          {message}
        </p>
      )}
      {results && results.length > 0 && (
        <ul className="small">
          {results.map((r) => {
            const label = r.identity ? datasheetLabel(r.identity) : null;
            const page = r.product_url ?? r.tried[r.tried.length - 1]?.url ?? r.url;
            return (
              <li key={r.references.join(",")}>
                <b>{r.references.join(", ")}</b>:{" "}
                {r.ok ? (
                  <>
                    {r.label ?? SOURCE_SHORT[r.source ?? ""]}에서 받음 {label && <Chip text={label.text} tone={label.tone} />}
                  </>
                ) : (
                  <>
                    <span className="warn-text">받지 못함 — {r.message}</span>{" "}
                    <button className="link" onClick={() => openLink(page)}>
                      브라우저에서 열기
                    </button>
                    <span className="muted"> (사이트가 자동 다운로드를 막으면 브라우저에서 받아 「PDF 올리기」로 넣으세요)</span>
                  </>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

interface ProposalDatasheetsProps {
  projectId: string;
  boardId: string;
  proposalId: string;
  checkedAt: string;
  busy: boolean;
  aiReady: boolean;
  onRechecked(): Promise<void>;
}

/**
 * The parts of a proposal (e.g. an AI design) checked against their datasheets before approval:
 * get the missing datasheets, summarize them, then compare again.
 */
export function ProposalDatasheets({ projectId, boardId, proposalId, checkedAt, busy, aiReady, onRechecked }: ProposalDatasheetsProps) {
  const [report, setReport] = useState<DatasheetReport | null>(null);
  const [ai, setAi] = useState<AiStatus | null>(null);
  const [working, setWorking] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setReport(await api.boardDatasheets(projectId, boardId, proposalId));
    } catch {
      setReport(null);
    }
  }, [projectId, boardId, proposalId]);

  useEffect(() => {
    void load();
  }, [load, checkedAt]);

  useEffect(() => {
    if (aiReady) api.aiStatus().then(setAi).catch(() => setAi(null));
  }, [aiReady]);

  if (!report) return null;
  const specific = report.components.filter((r) => r.identity !== "generic");
  if (specific.length === 0) return null;
  const unsummarized = specific.filter((r) => r.datasheet && !r.datasheet.summary);
  const withSummary = specific.filter((r) => r.datasheet?.summary);

  const act = async (label: string, fn: () => Promise<void>) => {
    setMessage(null);
    setWorking(label);
    try {
      await fn();
    } catch (e) {
      setMessage(errorText(e));
    } finally {
      setWorking(null);
    }
  };

  const summarize = (row: DatasheetComponentRow) =>
    act("AI가 데이터시트를 요약하는 중… (1~2분 걸릴 수 있습니다)", async () => {
      const ds = row.datasheet!;
      if (!confirmSummary(ds, ai)) return;
      const job = await waitForJob((await api.summarizeDatasheet(projectId, boardId, ds.id)).id, undefined, 1500);
      if (job.status === "failed") {
        setMessage(job.error ? `[${job.error.error_code}] ${job.error.message}` : "실패했습니다.");
        return;
      }
      setMessage(job.result.saved ? `${row.reference}: AI 요약을 저장했습니다. 「데이터시트 대조 다시 하기」를 누르세요.` : `${row.reference}: AI 요약을 읽지 못했습니다.`);
      await load();
    });

  const recheck = () =>
    act("데이터시트와 다시 대조하는 중…", async () => {
      await api.recheckProposalDatasheets(projectId, boardId, proposalId);
      await onRechecked();
    });

  return (
    <div className="proposal-datasheets">
      <DatasheetFinder projectId={projectId} boardId={boardId} proposalId={proposalId} report={report} disabled={busy || working !== null}
        onChanged={load} />
      {unsummarized.length > 0 && (
        <>
          <h4>요약이 없는 데이터시트</h4>
          <p className="small muted">대조에는 쪽 번호가 붙은 요약이 필요합니다. AI로 요약하거나 「데이터시트」 탭에서 요약 MD를 가져오세요.</p>
          <ul className="small">
            {unsummarized.map((r) => (
              <li key={r.reference}>
                <b>{r.reference}</b> {r.datasheet!.original_name}{" "}
                <button className="ghost small-btn" disabled={!aiReady || busy || working !== null} onClick={() => summarize(r)}
                  title={aiReady ? "데이터시트 PDF를 AI에 보내 요약합니다" : AI_SETUP_HINT}>
                  AI로 요약
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
      {withSummary.length > 0 && (
        <div className="toolbar">
          <button className="ghost small-btn" onClick={recheck} disabled={busy || working !== null}
            title="받은 데이터시트와 요약으로 이 제안의 핀맵·전압·주변 부품을 다시 대조합니다 (KiCad 검사는 다시 하지 않음)">
            데이터시트 대조 다시 하기
          </button>
          <span className="small muted">요약이 있는 부품: {withSummary.map((r) => r.reference).join(", ")}</span>
        </div>
      )}
      {working && <p className="small">{working}</p>}
      {message && (
        <p className="small" role="status">
          {message}
        </p>
      )}
    </div>
  );
}
