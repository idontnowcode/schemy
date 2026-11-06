import { useEffect, useState } from "react";

import { api, errorText } from "../api";
import { FINDING_SEVERITY } from "../format";
import type { SupplyNet, TechnicalReview } from "../types";
import { Chip } from "./Chip";

interface Props {
  projectId: string;
  boardId: string;
  revision: number | null;
  onOpenProposal?(id: string): void;
}

type Draft = Record<string, { voltage: string; current: string; min: string; max: string }>;
type Change =
  | { net: string; invalid: true; why?: string }
  | { net: string; invalid: false; voltage: number | null; current: number | null; min: number | null; max: number | null;
      before: SupplyNet };

const num = (text: string): number | null => {
  const t = text.trim().replace(",", ".");
  if (!t) return null;
  const v = Number(t);
  return Number.isFinite(v) ? v : NaN;
};
const mA = (amps: number | null) => (amps == null ? "" : String(+(amps * 1000).toFixed(3)));

const txt = (v: number | null | undefined) => (v == null ? "" : String(v));

function draftOf(nets: SupplyNet[]): Draft {
  return Object.fromEntries(
    nets.map((n) => [n.net, { voltage: txt(n.voltage), current: mA(n.load_current), min: txt(n.voltage_min), max: txt(n.voltage_max) }]),
  );
}

/** Calculated technical review of the current revision (no AI): what to look at, not a verification. */
export function TechnicalReviewCard({ projectId, boardId, revision, onOpenProposal }: Props) {
  const [review, setReview] = useState<TechnicalReview | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [showOk, setShowOk] = useState(false);
  const [draft, setDraft] = useState<Draft>({});
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!revision) {
      setReview(null);
      return;
    }
    setMessage(null);
    api
      .technicalReview(projectId, boardId)
      .then((r) => {
        setReview(r);
        setDraft(draftOf(r.supply_nets ?? []));
      })
      .catch((e) => setMessage(errorText(e)));
  }, [projectId, boardId, revision]);

  if (!revision) return null;
  const rows = review?.findings.filter((f) => showOk || f.severity !== "ok") ?? [];
  const nets = review?.supply_nets ?? [];
  const changes: Change[] = nets.flatMap((n): Change[] => {
    const d = draft[n.net];
    if (!d) return [];
    const voltage = num(d.voltage);
    const milli = num(d.current);
    const current = milli == null || Number.isNaN(milli) ? milli : milli / 1000;
    const min = num(d.min);
    const max = num(d.max);
    if ([voltage, current, min, max].some((v) => Number.isNaN(v))) return [{ net: n.net, invalid: true }];
    const nominal = voltage ?? n.voltage_from_name;
    if ((min != null && max != null && min > max) || (nominal != null && ((min != null && nominal < min) || (max != null && nominal > max))))
      return [{ net: n.net, invalid: true, why: "최저 ≤ 전압 ≤ 최고" }];
    if (voltage === n.voltage && current === n.load_current && min === n.voltage_min && max === n.voltage_max) return [];
    return [{ net: n.net, invalid: false, voltage, current, min, max, before: n }];
  });
  const invalid = changes.filter((c) => c.invalid).map((c) => (c.invalid && c.why ? `${c.net}(${c.why})` : c.net));

  const propose = async () => {
    setBusy(true);
    setMessage(null);
    try {
      const ops = changes.flatMap((c) =>
        c.invalid
          ? []
          : [{
              op: "set_net_spec",
              net: c.net,
              voltage: c.voltage,
              load_current: c.current,
              expect_voltage: c.before.voltage,
              expect_load_current: c.before.load_current,
              set_range: true,
              voltage_min: c.min,
              voltage_max: c.max,
              expect_voltage_min: c.before.voltage_min,
              expect_voltage_max: c.before.voltage_max,
            }],
      );
      const r = await api.createProposal(projectId, boardId, `전원 조건: ${changes.map((c) => c.net).join(", ")}`, ops,
        "기술 검토에 쓰는 Net 전압(최저·최고 포함)·부하 전류를 적습니다(연결과 회로도는 바뀌지 않음).");
      onOpenProposal?.(r.proposal.id);
    } catch (e) {
      setMessage(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card" aria-label="기술 검토">
      <h3>기술 검토 (자동 계산)</h3>
      <p className="muted small">
        회로 데이터로 직접 계산한 결과입니다(AI 아님): 콘덴서 정격 전압·극성, LED 방향·전류, 저항 소비 전력, 레귤레이터 드롭아웃·발열·전류,
        다이오드 전류·역전압, IC 디커플링, I2C 풀업 값, 크리스탈 부하 콘덴서, 모터 드라이버, 퓨즈 정격. 전압은 아래 전원 조건에 적은 값, 없으면 Net 이름(+5V, +3V3)에서
        읽습니다. 확인할 곳을 알려 줄 뿐, 엔지니어 검토를 대신하지 않습니다.
      </p>
      {message && <p className="small bad-text">{message}</p>}
      {review && (
        <>
          <div className="toolbar">
            {(["fail", "warn", "info", "ok"] as const).map((k) => (
              <Chip key={k} text={`${FINDING_SEVERITY[k].text} ${review.summary[k]}`} tone={FINDING_SEVERITY[k].tone} />
            ))}
            <label className="small">
              <input type="checkbox" checked={showOk} onChange={(e) => setShowOk(e.target.checked)} /> 통과 항목도 보기
            </label>
          </div>
          {rows.length === 0 ? (
            <p className="small">주의할 항목이 없습니다.</p>
          ) : (
            <table className="table compact">
              <thead>
                <tr><th>결과</th><th>항목</th><th>내용</th><th>계산</th></tr>
              </thead>
              <tbody>
                {rows.map((f, i) => (
                  <tr key={i}>
                    <td><Chip text={FINDING_SEVERITY[f.severity].text} tone={FINDING_SEVERITY[f.severity].tone} /></td>
                    <td>{f.title}</td>
                    <td className="small">{f.detail}</td>
                    <td className="small mono">{f.calc ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {nets.length > 0 && (
            <details open={nets.some((n) => n.voltage == null && n.voltage_from_name == null)} aria-label="전원 조건">
              <summary className="small">전원 조건 (Net 전압·부하 전류)</summary>
              <p className="muted small">
                계산에 쓰는 값입니다. 전압을 비우면 Net 이름에서 읽고, 부하 전류를 비우면 레귤레이터·직렬 다이오드 뒤쪽 Net의 값을 더해 계산합니다(그것도
                없으면 레귤레이터 발열은 100 mA 기준). <b>최저·최고</b>를 적으면 최악 조건으로 계산합니다: 드롭아웃은 최저 전압, 정격·발열은 최고 전압(예: 5 V
                ±5% → 4.75~5.25). 바꾸면 제안으로 만들어지고, 승인해야 회로에 들어갑니다. 연결과 회로도는 바뀌지 않습니다.
              </p>
              <table className="table compact">
                <thead>
                  <tr><th>Net</th><th>전압 (V)</th><th>최저 (V)</th><th>최고 (V)</th><th>부하 전류 (mA)</th></tr>
                </thead>
                <tbody>
                  {nets.map((n) => (
                    <tr key={n.net}>
                      <td>{n.net}</td>
                      <td>
                        <input
                          aria-label={`${n.net} 전압`}
                          inputMode="decimal"
                          size={7}
                          placeholder={n.voltage_from_name == null ? "모름" : `이름: ${n.voltage_from_name}`}
                          value={draft[n.net]?.voltage ?? ""}
                          onChange={(e) => setDraft({ ...draft, [n.net]: { ...draft[n.net], voltage: e.target.value } })}
                        />
                      </td>
                      {(["min", "max"] as const).map((k) => (
                        <td key={k}>
                          <input
                            aria-label={`${n.net} ${k === "min" ? "최저" : "최고"} 전압`}
                            inputMode="decimal"
                            size={6}
                            placeholder="공칭"
                            value={draft[n.net]?.[k] ?? ""}
                            onChange={(e) => setDraft({ ...draft, [n.net]: { ...draft[n.net], [k]: e.target.value } })}
                          />
                        </td>
                      ))}
                      <td>
                        <input
                          aria-label={`${n.net} 부하 전류`}
                          inputMode="decimal"
                          size={7}
                          placeholder={n.derived_current == null ? "모름" : `계산: ${mA(n.derived_current)}`}
                          value={draft[n.net]?.current ?? ""}
                          onChange={(e) => setDraft({ ...draft, [n.net]: { ...draft[n.net], current: e.target.value } })}
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {invalid.length > 0 && <p className="small bad-text">값을 확인하세요: {invalid.join(", ")}</p>}
              <div className="toolbar">
                <button disabled={busy || changes.length === 0 || invalid.length > 0} onClick={() => void propose()}>
                  전원 조건 제안 만들기
                </button>
              </div>
            </details>
          )}
        </>
      )}
    </div>
  );
}
