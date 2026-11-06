import { useEffect, useState } from "react";

import { api, errorText } from "../api";
import { DIELECTRIC_LABEL, SPEC_LABEL } from "../format";
import type { CircuitIR, Component, Dielectric, PartSpecs } from "../types";

interface Props {
  projectId: string;
  boardId: string;
  circuit: CircuitIR | null;
  onOpenProposal?(id: string): void;
}

type Field = keyof PartSpecs;

/** Which ratings matter for a part, by its symbol: what the technical review and datasheet checks can use. */
function fieldsFor(c: Component): Field[] {
  const [lib, sym] = c.symbol_id.split(":");
  const s = (sym ?? "").toUpperCase();
  if (lib === "Device" && (s === "C" || s.startsWith("C_") || s.startsWith("CP"))) return ["voltage_rating", "dielectric", "esr", "tolerance"];
  if (lib === "Device" && (s === "R" || s.startsWith("R_"))) return ["power_rating", "tolerance"];
  if (s.startsWith("LED")) return ["forward_voltage", "current_rating"];
  if (s.includes("FUSE")) return ["current_rating", "voltage_rating"];
  if (lib === "Diode" || (lib === "Device" && (s === "D" || s.startsWith("D_")))) return ["voltage_rating", "current_rating", "forward_voltage"];
  if (lib === "Device" && (s === "L" || s.startsWith("L_"))) return ["current_rating"];
  return [];
}

type Draft = Record<string, Partial<Record<Field, string>>>;

const show = (v: unknown) => (v == null ? "" : String(v));

function draftOf(parts: Component[]): Draft {
  return Object.fromEntries(parts.map((c) => [c.id, Object.fromEntries(Object.entries(c.specs ?? {}).map(([k, v]) => [k, show(v)]))]));
}

function specsOf(d: Partial<Record<Field, string>> | undefined): { specs: PartSpecs | null; bad: string | null } {
  const out: PartSpecs = {};
  for (const [k, raw] of Object.entries(d ?? {}) as [Field, string][]) {
    const t = (raw ?? "").trim();
    if (!t) continue;
    if (k === "dielectric") {
      out.dielectric = t as Dielectric;
      continue;
    }
    const v = Number(t.replace(",", "."));
    if (!Number.isFinite(v) || v <= 0) return { specs: null, bad: SPEC_LABEL[k] };
    (out as Record<string, number>)[k] = v;
  }
  return { specs: Object.keys(out).length ? out : null, bad: null };
}

const same = (a: PartSpecs | null | undefined, b: PartSpecs | null) =>
  JSON.stringify(Object.entries(a ?? {}).filter(([, v]) => v != null).sort()) === JSON.stringify(Object.entries(b ?? {}).sort());

/**
 * Ratings of the exact parts (from their datasheets): capacitor type and ESR, voltage/current/power ratings,
 * an LED's forward voltage. The review uses them instead of guesses; saving makes a proposal.
 */
export function PartSpecsCard({ projectId, boardId, circuit, onOpenProposal }: Props) {
  const parts = (circuit?.components ?? []).filter((c) => fieldsFor(c).length > 0);
  const [draft, setDraft] = useState<Draft>({});
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    setDraft(draftOf(circuit?.components ?? []));
  }, [circuit]);

  if (parts.length === 0) return null;
  type Change = { c: Component; specs: PartSpecs | null; bad: string | null };
  const changes = parts.flatMap((c): Change[] => {
    const { specs, bad } = specsOf(draft[c.id]);
    if (bad) return [{ c, specs: null, bad }];
    return same(c.specs, specs) ? [] : [{ c, specs, bad: null }];
  });
  const bad = changes.filter((x) => x.bad).map((x) => `${x.c.reference} ${x.bad}`);
  const filled = parts.filter((c) => c.specs && Object.values(c.specs).some((v) => v != null)).length;

  const propose = async () => {
    setBusy(true);
    setMessage(null);
    try {
      const ops = changes.map((x) => ({ op: "set_specs", component_id: x.c.id, specs: x.specs, expect_specs: x.c.specs ?? null }));
      const r = await api.createProposal(projectId, boardId, `부품 사양: ${changes.map((x) => x.c.reference).join(", ")}`, ops,
        "부품 데이터시트의 정격(종류·ESR·정격 전압/전류/전력·Vf)을 적습니다. 기술 검토와 데이터시트 대조가 이 값을 씁니다(연결·회로도는 바뀌지 않음).");
      onOpenProposal?.(r.proposal.id);
    } catch (e) {
      setMessage(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const set = (id: string, k: Field, v: string) => setDraft({ ...draft, [id]: { ...draft[id], [k]: v } });

  return (
    <details className="card" aria-label="부품 사양">
      <summary>
        <b>부품 사양</b> <span className="small muted">적은 부품 {filled}/{parts.length} · 콘덴서 종류·ESR, 정격 전압·전류·전력, LED Vf</span>
      </summary>
      <p className="muted small">
        고른 부품의 데이터시트 값을 적으면 기술 검토가 값 문자열(예: 10uF/16V) 대신 이 값으로 계산하고, 데이터시트 대조가 「출력 콘덴서는 탄탈, ESR
        0.3~22 Ω」 같은 조건을 확인합니다. 비우면 모르는 값으로 봅니다. 바꾸면 제안으로 만들어집니다.
      </p>
      <table className="table compact">
        <thead>
          <tr><th>부품</th><th>값</th><th>사양</th></tr>
        </thead>
        <tbody>
          {parts.map((c) => (
            <tr key={c.id}>
              <td>{c.reference}</td>
              <td className="small">{c.value}</td>
              <td>
                <div className="spec-fields">
                  {fieldsFor(c).map((k) =>
                    k === "dielectric" ? (
                      <label key={k} className="small">
                        {SPEC_LABEL[k]}{" "}
                        <select aria-label={`${c.reference} ${SPEC_LABEL[k]}`} value={draft[c.id]?.[k] ?? ""} onChange={(e) => set(c.id, k, e.target.value)}>
                          <option value="">모름</option>
                          {Object.entries(DIELECTRIC_LABEL).map(([v, label]) => (
                            <option key={v} value={v}>
                              {label}
                            </option>
                          ))}
                        </select>
                      </label>
                    ) : (
                      <label key={k} className="small">
                        {SPEC_LABEL[k]}{" "}
                        <input aria-label={`${c.reference} ${SPEC_LABEL[k]}`} inputMode="decimal" size={5} value={draft[c.id]?.[k] ?? ""}
                          onChange={(e) => set(c.id, k, e.target.value)} />
                      </label>
                    ),
                  )}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {bad.length > 0 && <p className="small bad-text">0보다 큰 숫자를 적으세요: {bad.join(", ")}</p>}
      <div className="toolbar">
        <button disabled={busy || changes.length === 0 || bad.length > 0} onClick={() => void propose()}>
          부품 사양 제안 만들기{changes.length ? ` (${changes.length})` : ""}
        </button>
      </div>
      {message && <p className="small bad-text">{message}</p>}
      <p className="small muted">단위: V, A, W, %, Ω.</p>
    </details>
  );
}
