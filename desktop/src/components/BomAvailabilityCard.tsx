import { useEffect, useState } from "react";

import { api, errorText, waitForJob } from "../api";
import { lifecycleTone, priceText } from "../format";
import type { BomAvailability, BomAvailabilityLine } from "../types";

interface Props {
  projectId: string;
  boardId: string;
  revision: number;
  /** BOM lines that have a maker part number (only those are looked up). */
  mpnLines: number;
}

/** What the user should notice about a line: not enough stock for the quantity, or a part going away. */
export function lineWarnings(line: BomAvailabilityLine): string[] {
  if (line.offers.length === 0) return line.errors.length ? [] : ["유통사에서 찾지 못함"];
  const out: string[] = [];
  const stocks = line.offers.map((o) => o.stock).filter((s): s is number => s != null);
  if (stocks.length && Math.max(...stocks) < line.quantity) out.push(`재고 부족 (필요 ${line.quantity}개)`);
  const tones = line.offers.map((o) => lifecycleTone(o.lifecycle));
  if (tones.includes("bad")) out.push("단종·생산 종료");
  else if (tones.includes("warn")) out.push("신규 설계 비추천");
  return out;
}

/**
 * Stock, unit price and lifecycle from Mouser / DigiKey for the BOM lines with an MPN. Only the part numbers
 * are sent; the result is kept with the board and shown again until the next lookup.
 */
export function BomAvailabilityCard({ projectId, boardId, revision, mpnLines }: Props) {
  const [data, setData] = useState<BomAvailability | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    api.bomAvailability(projectId, boardId).then(setData).catch(() => setData(null));
  }, [projectId, boardId]);

  const hasKeys = Boolean(data?.search.mouser || data?.search.digikey);
  const result = data?.result ?? null;

  const lookup = async () => {
    setBusy(true);
    setMessage(null);
    try {
      const job = await waitForJob((await api.lookupBomAvailability(projectId, boardId)).id);
      if (job.status === "failed") setMessage(job.error?.message ?? "조회가 실패했습니다.");
      setData(await api.bomAvailability(projectId, boardId));
    } catch (e) {
      setMessage(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="card" aria-label="유통사 재고·가격">
      <div className="toolbar">
        <h3>유통사 재고·가격</h3>
        <button disabled={busy || !hasKeys || mpnLines === 0} onClick={() => void lookup()}>
          {busy ? "조회 중…" : "유통사 재고·가격 조회"}
        </button>
        <span className="small muted">보내는 것: MPN {mpnLines}개 (다른 회로 정보는 보내지 않음)</span>
      </div>
      {!hasKeys && (
        <p className="small muted">
          설정 › 데이터시트 검색에 Mouser API 키나 DigiKey Client ID·Secret을 넣으면 재고·단가·단종 여부를 조회합니다.
        </p>
      )}
      {hasKeys && mpnLines === 0 && (
        <p className="small muted">
          제조사 품번(MPN)이 있는 줄이 없습니다. 회로 (IR)에서 manufacturer_part_number를 넣거나 「보유 자재 적용」으로 채우세요.
        </p>
      )}
      {message && <p className="small bad-text">{message}</p>}
      {result && (
        <>
          <p className="small muted">
            revision {result.revision} 기준 · {new Date(result.checked_at).toLocaleString()} · {result.sources.join(", ")}
            {result.revision !== revision && <span className="warn-text"> · 회로가 바뀌었습니다. 다시 조회하세요.</span>}
          </p>
          <table className="table compact">
            <thead>
              <tr>
                <th>부품</th>
                <th>수량</th>
                <th>MPN</th>
                <th>유통사</th>
                <th>재고</th>
                <th>단가</th>
                <th>상태</th>
                <th>확인할 점</th>
              </tr>
            </thead>
            <tbody>
              {result.lines.map((line) => {
                const warnings = lineWarnings(line);
                const offers = line.offers.length ? line.offers : [null];
                return offers.map((o, i) => (
                  <tr key={`${line.mpn}-${i}`}>
                    {i === 0 && (
                      <>
                        <td rowSpan={offers.length}>{line.references}</td>
                        <td rowSpan={offers.length}>{line.quantity}</td>
                        <td rowSpan={offers.length}>{line.mpn}</td>
                      </>
                    )}
                    {o ? (
                      <>
                        <td>
                          {o.product_url ? (
                            <a href={o.product_url} target="_blank" rel="noreferrer">
                              {o.source}
                            </a>
                          ) : (
                            o.source
                          )}
                          {o.part_number && o.part_number !== line.mpn && <span className="small muted"> {o.part_number}</span>}
                        </td>
                        <td className={o.stock != null && o.stock < line.quantity ? "bad-text" : ""}>
                          {o.stock != null ? o.stock.toLocaleString() : (o.availability ?? "—")}
                        </td>
                        <td>{priceText(o.price, o.currency, o.price_qty)}</td>
                        <td>{o.lifecycle ? <span className={`chip chip-${lifecycleTone(o.lifecycle)}`}>{o.lifecycle}</span> : "—"}</td>
                      </>
                    ) : (
                      <td colSpan={4} className="small muted">
                        {line.errors.length ? line.errors.map((e) => `${e.source}: ${e.message}`).join(" / ") : "찾지 못함"}
                      </td>
                    )}
                    {i === 0 && (
                      <td rowSpan={offers.length} className={warnings.length ? "small warn-text" : "small ok-text"}>
                        {warnings.length ? warnings.join(", ") : line.offers.length ? "문제 없음" : "—"}
                      </td>
                    )}
                  </tr>
                ));
              })}
            </tbody>
          </table>
          <p className="small muted">재고·가격은 조회한 때의 값입니다. 주문 전에 유통사 페이지에서 다시 확인하세요.</p>
        </>
      )}
    </section>
  );
}
