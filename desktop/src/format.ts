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
  return { user_edit: "직접 편집", import: "가져오기", restore: "복원", inventory: "보유 자재 적용", proposal: "제안 승인" }[source];
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
  const bad = rows.filter((r) => ["missing", "short", "value_differs", "spec_differs"].includes(r.status));
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
  spec_differs: { text: "사양 불일치", tone: "bad" },
  same_as: { text: "같은 신호의 탭·중복 핀", tone: "ok" },
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

// --- proposals -------------------------------------------------------------------------------

export function proposalStatusLabel(status: string): Label {
  switch (status) {
    case "checking":
      return { text: "검사 중", tone: "info" };
    case "ready":
      return { text: "승인 대기", tone: "warn" };
    case "invalid":
      return { text: "규칙 오류", tone: "bad" };
    case "check_failed":
      return { text: "검사 실패", tone: "bad" };
    case "approved":
      return { text: "승인됨", tone: "ok" };
    case "rejected":
      return { text: "거부됨", tone: "muted" };
    case "stale":
      return { text: "기준 회로 바뀜", tone: "muted" };
    default:
      return { text: status, tone: "muted" };
  }
}

/** What a KiCad ERC finding means and whether it is about the circuit, the drawing or this computer's KiCad set-up. */
export const ERC_EXPLAIN: Record<string, { text: string; kind: "circuit" | "drawing" | "setup" }> = {
  pin_not_driven: { kind: "circuit", text: "입력 핀에 신호를 주는 출력이 없습니다(떠 있는 입력). 아래 「바로 고치기」로 풀업·풀다운을 넣거나 AI로 고치세요." },
  power_pin_not_driven: { kind: "circuit", text: "전원 입력 핀에 전원이 들어오지 않습니다. 전원 Net 연결을 확인하세요." },
  pin_not_connected: { kind: "circuit", text: "어디에도 연결되지 않은 핀입니다. 일부러 비워 두는 핀이면 미연결(no-connect) 표시를 하세요." },
  isolated_pin_label: { kind: "circuit", text: "라벨이 핀 하나에만 붙어 있습니다. 연결이 빠졌을 가능성이 큽니다." },
  pin_to_pin: { kind: "circuit", text: "같은 Net에 함께 두면 안 되는 종류의 핀이 있습니다(예: 출력끼리, 양방향 핀과 PWR_FLAG). 출력끼리라면 고쳐야 하고, PWR_FLAG와의 경고는 대개 동작과 무관합니다." },
  multiple_net_names: { kind: "drawing", text: "한 Net에 이름이 둘 붙었습니다." },
  lib_symbol_issues: { kind: "setup", text: "이 컴퓨터 KiCad의 심볼 라이브러리 표에 해당 라이브러리가 없다는 경고입니다. 회로도 파일에는 심볼이 들어 있어 연결과는 관계없습니다. KiCad를 한 번 실행해 기본 라이브러리 표를 설정하면 사라집니다." },
  lib_symbol_mismatch: { kind: "setup", text: "설치된 라이브러리의 심볼과 파일 안의 심볼이 조금 다릅니다(KiCad 버전 차이). 연결과는 관계없습니다." },
  footprint_link_issues: { kind: "setup", text: "풋프린트 라이브러리 표에 없는 풋프린트입니다. PCB로 넘어갈 때 확인하세요." },
};

export function proposalSourceLabel(source: string): string {
  return (
    {
      user: "직접 제안",
      ai_design: "AI 설계",
      ai_revision: "AI 수정안",
      ai_pdf_import: "PDF 가져오기 (AI)",
      template: "템플릿",
      agent: "AI Agent",
      kicad_layout: "KiCad 배치",
      kicad_edit: "KiCad 수정",
    }[source] ?? source
  );
}

const DIFF_FIELD: Record<string, string> = {
  reference: "기호",
  value: "값",
  symbol_id: "KiCad 심볼",
  footprint_id: "풋프린트",
  manufacturer: "제조사",
  manufacturer_part_number: "제조사 품번",
  description: "설명",
};

export const SPEC_LABEL: Record<string, string> = {
  voltage_rating: "정격 전압 (V)",
  current_rating: "정격 전류 (A)",
  power_rating: "정격 전력 (W)",
  tolerance: "허용 오차 (%)",
  dielectric: "종류",
  esr: "ESR (Ω)",
  forward_voltage: "순방향 전압 Vf (V)",
};

export const DIELECTRIC_LABEL: Record<string, string> = {
  C0G: "세라믹 C0G",
  X7R: "세라믹 X7R",
  X5R: "세라믹 X5R",
  X7S: "세라믹 X7S",
  Y5V: "세라믹 Y5V",
  ceramic: "세라믹 (등급 모름)",
  tantalum: "탄탈",
  polymer: "폴리머",
  electrolytic: "전해",
  film: "필름",
};

export function diffFieldLabel(field: string): string {
  if (DIFF_FIELD[field]) return DIFF_FIELD[field];
  const spec = /^specs\.(.+)$/.exec(field);
  if (spec) return `사양: ${SPEC_LABEL[spec[1]] ?? spec[1]}`;
  const nc = /^pin (.+) no_connect$/.exec(field);
  if (nc) return `핀 ${nc[1]} 미연결 표시`;
  const pin = /^pin (.+)$/.exec(field);
  return pin ? `핀 ${pin[1]}` : field;
}

export function diffSummaryText(s: Record<string, number>): string {
  const parts = [
    s.components_added && `부품 +${s.components_added}`,
    s.components_removed && `부품 −${s.components_removed}`,
    s.properties_changed && `속성 ${s.properties_changed}`,
    s.connections_changed && `연결 ${s.connections_changed}`,
    s.nets_added && `Net +${s.nets_added}`,
    s.nets_removed && `Net −${s.nets_removed}`,
    s.moved && `배치 ${s.moved}`,
    s.notes_changed && `주석 ${s.notes_changed}`,
    s.decisions_changed && `결정 ${s.decisions_changed}`,
    s.net_specs_changed && `전원 조건 ${s.net_specs_changed}`,
  ].filter(Boolean);
  return parts.length ? parts.join(" · ") : "바뀐 것 없음";
}

/** Who receives the data when AI is called (for the confirm dialogs). */
/** What to do when AI is not ready, for buttons' tooltips and notes. */
export const AI_SETUP_HINT = "설정에서 AI 기능을 켜고 Gemini API 키를 저장하거나 Claude·ChatGPT에 로그인하세요";

export function providerShortName(provider: string | undefined | null): string {
  return provider === "claude" ? "Claude" : provider === "chatgpt" ? "ChatGPT" : "Gemini";
}

export function providerLabel(provider: string | undefined | null): string {
  return provider === "claude" ? "Anthropic Claude API" : provider === "chatgpt" ? "OpenAI ChatGPT (내 계정)" : "Google Gemini API";
}

/** Where a part came from: a reviewed template, an AI design, or by hand. */
export function originLabel(origin: string | null | undefined): string {
  if (!origin) return "직접";
  if (origin === "ai") return "AI";
  const m = /^template:(.+)$/.exec(origin);
  return m ? `템플릿 ${m[1]}` : origin;
}

export const AGENT_FLOW = ["PLANNING", "PROPOSED_PLAN", "SCOPE_APPROVED", "SANDBOX_EXECUTION", "VALIDATION", "REVIEW_REQUIRED", "COMMITTED"] as const;

export function agentStateLabel(state: string): { text: string; tone: Tone } {
  return (
    (
      {
        PLANNING: { text: "계획 중", tone: "info" },
        PROPOSED_PLAN: { text: "범위 승인 대기", tone: "warn" },
        SCOPE_APPROVED: { text: "범위 승인됨", tone: "info" },
        SANDBOX_EXECUTION: { text: "격리 실행 중", tone: "info" },
        VALIDATION: { text: "검증 중", tone: "info" },
        REVIEW_REQUIRED: { text: "제안 검토 필요", tone: "warn" },
        COMMITTED: { text: "반영됨", tone: "ok" },
        REJECTED: { text: "거부됨", tone: "muted" },
        CANCELLED: { text: "취소됨", tone: "muted" },
        FAILED: { text: "실패", tone: "bad" },
        STALE: { text: "기준 바뀜 (STALE)", tone: "muted" },
      } as Record<string, { text: string; tone: Tone }>
    )[state] ?? { text: state, tone: "muted" }
  );
}

export function scopeText(s: { op: string; target: string; field?: string | null }): string {
  const op =
    { set_property: "속성 변경", add_component: "부품 추가", remove_component: "부품 삭제", connect: "연결", disconnect: "연결 해제", rename_net: "Net 이름 변경", set_no_connect: "NC 설정", move: "위치 이동" }[s.op] ?? s.op;
  return `${op} — ${s.target}${s.field ? ` (${s.field})` : ""}`;
}

/** Who made a design decision (IR ``decisions[].by``). */
export const DECISION_BY: Record<string, string> = {
  requirement: "요구 반영",
  changed: "요구 수정",
  ai: "AI 선정",
  template: "템플릿",
  engineer: "사람 결정",
};

export const FINDING_SEVERITY: Record<string, { text: string; tone: "ok" | "warn" | "bad" | "info" | "muted" }> = {
  fail: { text: "실패", tone: "bad" },
  warn: { text: "주의", tone: "warn" },
  info: { text: "참고", tone: "info" },
  ok: { text: "통과", tone: "ok" },
};

const DATASHEET_SOURCE: Record<string, string> = {
  upload: "직접 올림",
  kicad_symbol: "심볼 링크",
  maker_site: "제조사 사이트",
  mouser: "Mouser",
  digikey: "DigiKey",
};

/** Where a stored datasheet came from. */
export function datasheetSourceLabel(source: string): string {
  return DATASHEET_SOURCE[source] ?? source;
}

/** BOM column headers in words (the CSV keeps the KiCad-style names). */
export const BOM_HEADER: Record<string, string> = {
  References: "부품",
  Quantity: "수량",
  Value: "값",
  Symbol: "심볼",
  Footprint: "풋프린트",
  Manufacturer: "제조사",
  MPN: "MPN",
  PinMapStatus: "핀 검토(엔지니어)",
};

/** A distributor lifecycle word: discontinued/obsolete is bad, not-for-new-designs a warning, active fine. */
export function lifecycleTone(status: string | null | undefined): Tone {
  const s = (status ?? "").toLowerCase();
  if (!s) return "muted";
  if (/obsolete|discontinued|end of life|\beol\b|last time buy/.test(s)) return "bad";
  if (/nrnd|not recommended|not for new/.test(s)) return "warn";
  if (/active|new product|production/.test(s)) return "ok";
  return "muted";
}

/** "$2.35 (10개~)" from the smallest price break. */
export function priceText(price: number | null, currency: string | null, qty: number | null): string {
  if (price == null) return "—";
  const unit = currency === "USD" ? `$${price}` : `${price} ${currency ?? ""}`.trim();
  return qty && qty > 1 ? `${unit} (${qty}개~)` : unit;
}
