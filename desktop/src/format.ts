// Pure helpers (unit-tested in format.test.ts).

import type { ApiErrorBody, AutomatedStatus, OutputsStatus, RevisionInfo, VerificationStatus } from "./types";

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
  return { user_edit: "직접 편집", import: "가져오기", restore: "복원" }[source];
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
