// Pure helpers (unit-tested in format.test.ts).

import type {
  ApiErrorBody,
  AutomatedStatus,
  CircuitIR as IR,
  MatchStatus,
  OutputsStatus,
  RevisionInfo,
  Sheet as IRSheet,
  VerificationStatus,
} from "./types";

export type Tone = "ok" | "bad" | "warn" | "muted" | "info";

/** Plan v1.2 section 10.1: automated check results. */
export function automatedLabel(status: AutomatedStatus | null | undefined): { text: string; tone: Tone } {
  switch (status) {
    case "passed":
      return { text: "Automated checks passed", tone: "ok" };
    case "failed":
      return { text: "Automated checks failed", tone: "bad" };
    case "not_checked":
      return { text: "Not checked", tone: "muted" };
    default:
      return { text: "생성 전", tone: "muted" };
  }
}

/** Plan v1.2 section 10.1: engineering review. Only a person can set "verified". */
export function reviewLabel(status: VerificationStatus | null | undefined): { text: string; tone: Tone } {
  switch (status) {
    case "verified":
      return { text: "Engineer reviewed", tone: "ok" };
    case "needs_review":
      return { text: "검토 필요", tone: "warn" };
    case "unverified":
      return { text: "미확인", tone: "muted" };
    default:
      return { text: "회로 없음", tone: "muted" };
  }
}

/** One-line summary of the outputs folder for the header chip. */
export function outputsLabel(outputs: OutputsStatus | null): { text: string; tone: Tone } {
  if (!outputs?.manifest) return automatedLabel(null);
  const base = automatedLabel(outputs.manifest.automated_check.status);
  if (outputs.externally_modified.length > 0) return { text: `${base.text} · 외부에서 수정됨`, tone: "warn" };
  if (outputs.stale) return { text: `${base.text} · 오래됨 (rev ${outputs.manifest.source_revision})`, tone: "warn" };
  return base;
}

export function sourceLabel(source: RevisionInfo["source"]): string {
  return { user_edit: "직접 편집", import: "가져오기", restore: "복원", inventory: "보유 자재 적용" }[source];
}

/** Inventory match status (보유 자재 우선 사용). */
export function matchLabel(status: MatchStatus, stockShort: boolean | null): { text: string; tone: Tone } {
  if (status === "matched") return stockShort ? { text: "보유 · 재고 부족", tone: "warn" } : { text: "보유", tone: "ok" };
  if (status === "candidate") return { text: "대체 가능", tone: "warn" };
  return { text: "미보유", tone: "bad" };
}

export function formatTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function shortHash(hash: string): string {
  return hash.slice(0, 10);
}

/** Normalise any error response into the API error contract. */
export function toErrorBody(data: unknown, status: number): ApiErrorBody {
  if (data && typeof data === "object" && "error_code" in data && "message" in data) {
    const d = data as Partial<ApiErrorBody>;
    return {
      error_code: String(d.error_code),
      message: String(d.message),
      affected_component_ids: Array.isArray(d.affected_component_ids) ? d.affected_component_ids : [],
      recovery_hint: d.recovery_hint ?? null,
      details: d.details,
    };
  }
  return {
    error_code: `HTTP_${status}`,
    message: typeof data === "string" && data ? data : `Request failed with status ${status}`,
    affected_component_ids: [],
    recovery_hint: null,
  };
}

/** Parse editor text; returns either the value or a readable syntax error. */
export function parseJson(text: string): { ok: true; value: unknown } | { ok: false; error: string } {
  try {
    return { ok: true, value: JSON.parse(text) };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) };
  }
}

export function prettyJson(value: unknown): string {
  return JSON.stringify(value, null, 2) + "\n";
}

/**
 * Crop a KiCad SVG export to the drawing area for the on-screen preview.
 * Only the root <svg> width/height/viewBox change; the generated file on disk is untouched.
 */
export function cropSvg(svg: string, bbox: number[] | null | undefined): string {
  if (!bbox || bbox.length !== 4 || bbox.some((n) => !Number.isFinite(n)) || bbox[2] <= 0 || bbox[3] <= 0) return svg;
  const [x, y, w, h] = bbox;
  const setAttr = (tag: string, name: string, value: string): string => {
    const existing = new RegExp(`(\\s${name}=)"[^"]*"`);
    return existing.test(tag) ? tag.replace(existing, `$1"${value}"`) : tag.replace(/>$/, ` ${name}="${value}">`);
  };
  return svg.replace(/<svg\b[^>]*>/, (tag) =>
    setAttr(setAttr(setAttr(tag, "viewBox", `${x} ${y} ${w} ${h}`), "width", `${w}mm`), "height", `${h}mm`),
  );
}

// --- pages (sheets) in a Circuit IR ---------------------------------------------------
// Pages only group components for drawing; these helpers never touch nets.


/** Effective pages: an IR without sheets has one implicit page holding everything. */
export function pagesOf(ir: IR): IRSheet[] {
  const sheets = ir.sheets ?? [];
  if (sheets.length === 0) return [{ id: "p1", name: "Page 1", components: ir.components.map((c) => c.id) }];
  const listed = new Set(sheets.flatMap((s) => s.components));
  const unlisted = ir.components.filter((c) => !listed.has(c.id)).map((c) => c.id);
  return sheets.map((s, i) => (i === 0 ? { ...s, components: [...s.components, ...unlisted] } : s));
}

function withPages(ir: IR, sheets: IRSheet[]): IR {
  return { ...ir, sheets: sheets.length <= 1 && sheets[0]?.id === "p1" && sheets[0]?.name === "Page 1" ? [] : sheets };
}

export function addPage(ir: IR): IR {
  const pages = pagesOf(ir);
  let n = pages.length + 1;
  while (pages.some((p) => p.id === `p${n}`)) n += 1;
  return { ...ir, sheets: [...pages, { id: `p${n}`, name: `Page ${n}`, components: [] }] };
}

export function moveToPage(ir: IR, componentId: string, sheetId: string): IR {
  const pages = pagesOf(ir).map((p) => ({ ...p, components: p.components.filter((c) => c !== componentId) }));
  return withPages(ir, pages.map((p) => (p.id === sheetId ? { ...p, components: [...p.components, componentId] } : p)));
}

export function renamePage(ir: IR, sheetId: string, name: string): IR {
  return { ...ir, sheets: pagesOf(ir).map((p) => (p.id === sheetId ? { ...p, name } : p)) };
}

/** Remove a page; its components go to the first remaining page. */
export function removePage(ir: IR, sheetId: string): IR {
  const pages = pagesOf(ir);
  const gone = pages.find((p) => p.id === sheetId);
  const rest = pages.filter((p) => p.id !== sheetId);
  if (!gone || rest.length === 0) return ir;
  rest[0] = { ...rest[0], components: [...rest[0].components, ...gone.components] };
  return withPages(ir, rest);
}

// --- datasheets ----------------------------------------------------------------------------

type Label = { text: string; tone: Tone };

export function identityLabel(identity: "mpn" | "symbol" | "generic"): Label {
  if (identity === "mpn") return { text: "제조사·품번 확정", tone: "ok" };
  if (identity === "symbol") return { text: "부품 전용 심볼", tone: "info" };
  return { text: "일반 부품", tone: "muted" };
}

export function datasheetLabel(status: string | undefined): Label {
  switch (status) {
    case "matched":
      return { text: "품번 확인됨", tone: "ok" };
    case "family":
      return { text: "제품군 확인됨", tone: "info" };
    case "not_found":
      return { text: "품번이 안 보임", tone: "warn" };
    case "no_text":
      return { text: "글자를 못 읽음", tone: "warn" };
    default:
      return { text: "확인 안 됨", tone: "muted" };
  }
}

export function pinMapLabel(status: string, counts: Record<string, number>, packageChosen: boolean): Label {
  if (!packageChosen) return { text: "패키지 선택 필요", tone: "warn" };
  if (status === "match") return { text: "핀맵 일치", tone: "ok" };
  if (status === "mismatch") {
    const n = (counts.mismatch ?? 0) + (counts.missing_in_symbol ?? 0) + (counts.missing_in_datasheet ?? 0);
    return { text: `핀맵 불일치 ${n}`, tone: "bad" };
  }
  return { text: "핀맵 확인 불가", tone: "muted" };
}

export function ratingsLabel(status: string): Label {
  if (status === "error") return { text: "최대 정격 초과", tone: "bad" };
  if (status === "warning") return { text: "권장 범위 밖", tone: "warn" };
  if (status === "ok") return { text: "전압 정상", tone: "ok" };
  return { text: "전압 확인 불가", tone: "muted" };
}

export function externalLabel(rows: { status: string; requirement: string }[]): Label {
  const bad = rows.filter((r) => ["missing", "short", "value_differs"].includes(r.status));
  const required = bad.filter((r) => r.requirement === "required").length;
  if (required) return { text: `필수 부품 확인 ${required}`, tone: "warn" };
  if (bad.length) return { text: `권장 부품 확인 ${bad.length}`, tone: "info" };
  if (rows.some((r) => r.status === "present")) return { text: "외부 부품 있음", tone: "ok" };
  return { text: "외부 부품 확인 불가", tone: "muted" };
}

export const ROW_STATUS: Record<string, Label> = {
  match: { text: "일치", tone: "ok" },
  mismatch: { text: "불일치", tone: "bad" },
  missing_in_symbol: { text: "심볼에 없음", tone: "bad" },
  missing_in_datasheet: { text: "데이터시트에 없음", tone: "bad" },
  no_name: { text: "이름 없음", tone: "muted" },
  ok: { text: "정상", tone: "ok" },
  over_abs_max: { text: "최대 정격 초과", tone: "bad" },
  outside_recommended: { text: "권장 범위 밖", tone: "warn" },
  unknown_voltage: { text: "전압 모름", tone: "muted" },
  unconnected: { text: "미연결", tone: "muted" },
  present: { text: "있음", tone: "ok" },
  short: { text: "부족", tone: "warn" },
  value_differs: { text: "값 다름", tone: "warn" },
  missing: { text: "없음", tone: "warn" },
  direct: { text: "직접 연결", tone: "info" },
  unchecked: { text: "확인 불가", tone: "muted" },
};

export function rangeText(min: number | null, max: number | null): string {
  if (min !== null && max !== null) return `${min} ~ ${max} V`;
  if (max !== null) return `최대 ${max} V`;
  if (min !== null) return `최소 ${min} V`;
  return "—";
}
