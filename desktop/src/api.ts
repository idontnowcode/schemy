import { desktop } from "./desktop";
import { toErrorBody } from "./format";
import type {
  AiProvider,
  AiStatus,
  ApiErrorBody,
  BoardReview,
  AppSettings,
  BoardSummary,
  BomTable,
  CircuitIR,
  ClaudeLoginStatus,
  DatasheetEntry,
  DatasheetReport,
  DatasheetSummaryResult,
  DesignPreview,
  Example,
  InventoryApplyResult,
  InventoryImportResult,
  InventoryInfo,
  InventoryMatch,
  Job,
  MarkdownImportResult,
  OutputsStatus,
  ProjectSummary,
  Proposal,
  RequirementsSpec,
  RevisionInfo,
  RevisionInputs,
  SaveCircuitResult,
  SystemInfo,
  ValidationReport,
} from "./types";

export class ApiError extends Error {
  constructor(
    public status: number,
    public body: ApiErrorBody,
  ) {
    super(body.message);
  }
}

function authHeaders(): Record<string, string> {
  return desktop?.token ? { Authorization: `Bearer ${desktop.token}` } : {};
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { ...authHeaders() };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(`/api${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (!res.ok) throw new ApiError(res.status, toErrorBody(data, res.status));
  return data as T;
}

const enc = encodeURIComponent;
const board = (p: string, b: string) => `/projects/${enc(p)}/boards/${enc(b)}`;

/** Read a File as base64 (for uploads sent inside JSON). */
export async function fileToBase64(file: File): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

export const api = {
  system: () => request<SystemInfo>("GET", "/system"),
  examples: () => request<Example[]>("GET", "/examples"),
  settings: () => request<AppSettings>("GET", "/settings"),
  saveSettings: (s: AppSettings) => request<AppSettings>("PUT", "/settings", s),

  projects: () => request<ProjectSummary[]>("GET", "/projects"),
  createProject: (name: string) => request<ProjectSummary>("POST", "/projects", { name }),
  project: (id: string) => request<ProjectSummary>("GET", `/projects/${enc(id)}`),
  renameProject: (id: string, name: string) => request<ProjectSummary>("PATCH", `/projects/${enc(id)}`, { name }),
  deleteProject: (id: string) => request<{ moved_to: string }>("DELETE", `/projects/${enc(id)}`),
  openProject: (id: string) => request<ProjectSummary>("POST", `/projects/${enc(id)}/open`),

  requirements: (id: string) => request<{ text: string }>("GET", `/projects/${enc(id)}/requirements`),
  saveRequirements: (id: string, text: string) =>
    request<{ text: string }>("PUT", `/projects/${enc(id)}/requirements`, { text }),

  createBoard: (p: string, name: string) => request<BoardSummary>("POST", `/projects/${enc(p)}/boards`, { name }),
  renameBoard: (p: string, b: string, name: string) => request<BoardSummary>("PATCH", board(p, b), { name }),
  deleteBoard: (p: string, b: string) => request<{ moved_to: string }>("DELETE", board(p, b)),

  circuit: (p: string, b: string) =>
    request<{ circuit: CircuitIR | null; revision: number | null }>("GET", `${board(p, b)}/circuit`),
  validateCircuit: (p: string, b: string, ir: unknown) =>
    request<ValidationReport>("POST", `${board(p, b)}/circuit/validate`, ir),
  saveCircuit: (p: string, b: string, ir: unknown, source: "user_edit" | "import") =>
    request<SaveCircuitResult>("PUT", `${board(p, b)}/circuit?source=${source}`, ir),

  revisions: (p: string, b: string) => request<RevisionInfo[]>("GET", `${board(p, b)}/revisions`),
  restoreRevision: (p: string, b: string, rev: number) =>
    request<{ revision: RevisionInfo; created: boolean }>("POST", `${board(p, b)}/revisions/${rev}/restore`),

  markdown: (p: string, b: string) => request<{ revision: number; markdown: string }>("GET", `${board(p, b)}/documents/markdown`),
  bom: (p: string, b: string) => request<BomTable>("GET", `${board(p, b)}/documents/bom`),

  exportBoard: (p: string, b: string, force = false) => request<Job>("POST", `${board(p, b)}/exports`, { force }),
  outputs: (p: string, b: string) => request<OutputsStatus>("GET", `${board(p, b)}/outputs`),
  jobs: (p: string, b: string) => request<Job[]>("GET", `${board(p, b)}/jobs`),
  job: (jobId: string) => request<Job>("GET", `/jobs/${enc(jobId)}`),

  importMarkdown: (p: string, b: string, filename: string, text: string) =>
    request<MarkdownImportResult>("POST", `${board(p, b)}/imports/markdown`, { filename, text }),
  importPdf: (p: string, b: string, filename: string, content_base64: string) =>
    request<Job>("POST", `${board(p, b)}/imports/pdf`, { filename, content_base64 }),

  inventory: () => request<InventoryInfo>("GET", "/inventory"),
  importInventory: (filename: string, content_base64: string, save: boolean) =>
    request<InventoryImportResult>("POST", "/inventory/import", { filename, content_base64, save }),
  deleteInventory: () => request<{ deleted: boolean }>("DELETE", "/inventory"),
  inventoryMatch: (p: string, b: string, boards = 1) =>
    request<InventoryMatch>("GET", `${board(p, b)}/inventory-match?boards=${boards}`),
  inventoryApply: (p: string, b: string, applyMatched: boolean, choices: Record<string, string>) =>
    request<InventoryApplyResult>("POST", `${board(p, b)}/inventory-apply`, { apply_matched: applyMatched, choices }),
  suggestPreview: (p: string, b: string) =>
    request<{ provider: string; model: string; line_count: number; candidate_count: number }>(
      "GET",
      `${board(p, b)}/inventory-suggest/preview`,
    ),
  suggest: (p: string, b: string) => request<Job>("POST", `${board(p, b)}/inventory-suggest`),

  boardDatasheets: (p: string, b: string) => request<DatasheetReport>("GET", `${board(p, b)}/datasheets`),
  fetchDatasheets: (p: string, b: string, urls: string[]) => request<Job>("POST", `${board(p, b)}/datasheets/fetch`, { urls }),
  summarizeDatasheet: (p: string, b: string, id: string) =>
    request<Job>("POST", `${board(p, b)}/datasheets/${enc(id)}/summarize`),
  datasheets: () => request<{ entries: DatasheetEntry[]; dir: string }>("GET", "/datasheets"),
  uploadDatasheet: (body: {
    filename: string;
    content_base64: string;
    manufacturer?: string | null;
    part_numbers?: string[];
    symbols?: string[];
  }) => request<{ entry: DatasheetEntry; created: boolean }>("POST", "/datasheets/upload", body),
  deleteDatasheet: (id: string) => request<null>("DELETE", `/datasheets/${enc(id)}`),
  importDatasheetSummary: (id: string, filename: string, text: string) =>
    request<DatasheetSummaryResult>("POST", `/datasheets/${enc(id)}/summary`, { filename, text }),
  setDatasheetReviewed: (id: string, reviewed: boolean) =>
    request<DatasheetEntry>("PUT", `/datasheets/${enc(id)}/reviewed`, { reviewed }),
  prompt: (name: "datasheet-summary" | "pdf-schematic") =>
    request<{ name: string; text: string }>("GET", `/prompts/${name}`),

  proposals: (p: string, b: string) => request<Proposal[]>("GET", `${board(p, b)}/proposals`),
  proposal: (p: string, b: string, id: string) => request<Proposal>("GET", `${board(p, b)}/proposals/${enc(id)}`),
  createProposal: (p: string, b: string, title: string, ops: unknown[], rationale?: string) =>
    request<{ proposal: Proposal; job: Job }>("POST", `${board(p, b)}/proposals`, { title, ops, rationale }),
  checkProposal: (p: string, b: string, id: string) => request<Job>("POST", `${board(p, b)}/proposals/${enc(id)}/check`),
  approveProposal: (p: string, b: string, id: string, acceptFailedChecks: boolean) =>
    request<{ proposal: Proposal; revision: RevisionInfo; job: Job | null }>(
      "POST",
      `${board(p, b)}/proposals/${enc(id)}/approve`,
      { accept_failed_checks: acceptFailedChecks, generate: true },
    ),
  rejectProposal: (p: string, b: string, id: string, note?: string) =>
    request<Proposal>("POST", `${board(p, b)}/proposals/${enc(id)}/reject`, { note }),

  requirementsSpec: (p: string) => request<{ spec: RequirementsSpec | null }>("GET", `/projects/${enc(p)}/requirements/spec`),
  saveRequirementsSpec: (p: string, spec: RequirementsSpec | null) =>
    request<{ spec: RequirementsSpec | null }>("PUT", `/projects/${enc(p)}/requirements/spec`, { spec }),
  structureRequirements: (p: string) => request<Job>("POST", `/projects/${enc(p)}/requirements/structure`),
  designPreview: (p: string, b: string) => request<DesignPreview>("GET", `${board(p, b)}/ai/design/preview`),
  design: (p: string, b: string, body: { instructions?: string; use_inventory: boolean; review: boolean }) =>
    request<Job>("POST", `${board(p, b)}/ai/design`, body),
  aiReview: (p: string, b: string, proposalId?: string) =>
    request<Job>("POST", `${board(p, b)}/ai/review`, { proposal_id: proposalId ?? null }),
  boardReview: (p: string, b: string) => request<{ review: BoardReview | null }>("GET", `${board(p, b)}/ai/review`),
  revisePreview: (p: string, b: string, proposalId?: string | null) =>
    request<RevisionInputs>("GET", `${board(p, b)}/ai/revise/preview${proposalId ? `?proposal_id=${enc(proposalId)}` : ""}`),
  aiRevise: (p: string, b: string, body: {
    proposal_id?: string | null;
    instructions?: string | null;
    finding_indexes?: number[] | null;
    include_checks: boolean;
    review: boolean;
  }) => request<Job>("POST", `${board(p, b)}/ai/revise`, body),

  aiStatus: () => request<AiStatus>("GET", "/ai/settings"),
  saveAiSettings: (s: Partial<{ enabled: boolean; model: string; provider: AiProvider }>) =>
    request<AiStatus>("PUT", "/ai/settings", s),
  saveAiKey: (apiKey: string) => request<{ key: AiStatus["key"] }>("PUT", "/ai/key", { api_key: apiKey }),
  deleteAiKey: () => request<{ key: AiStatus["key"] }>("DELETE", "/ai/key"),
  claudeLogin: () => request<ClaudeLoginStatus>("GET", "/ai/claude/login"),
  startClaudeLogin: () => request<ClaudeLoginStatus & { started: boolean }>("POST", "/ai/claude/login"),
  claudeLogout: () => request<ClaudeLoginStatus>("POST", "/ai/claude/logout"),
  aiModels: () =>
    request<{ models: { name: string; display_name: string | null; output_token_limit: number | null }[]; current: string }>(
      "GET",
      "/ai/models",
    ),
};

/** Poll a job until it finishes. */
export async function waitForJob(jobId: string, onUpdate?: (job: Job) => void, intervalMs = 600): Promise<Job> {
  for (;;) {
    const job = await api.job(jobId);
    onUpdate?.(job);
    if (job.status === "succeeded" || job.status === "failed") return job;
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}

/** Fetch a text output file (e.g. an SVG page) with the API token. */
export async function outputText(projectId: string, boardId: string, name: string): Promise<string> {
  const res = await fetch(`/api${board(projectId, boardId)}/outputs/files/${enc(name)}`, { headers: authHeaders() });
  if (!res.ok) throw new ApiError(res.status, toErrorBody(await res.text(), res.status));
  return res.text();
}

export function errorText(e: unknown): string {
  if (e instanceof ApiError) {
    const hint = e.body.recovery_hint ? ` — ${e.body.recovery_hint}` : "";
    return `[${e.body.error_code}] ${e.body.message}${hint}`;
  }
  return e instanceof Error ? e.message : String(e);
}

/** Open a stored datasheet: the system PDF viewer in the desktop app, a browser tab otherwise. */
export function openDatasheet(entry: DatasheetEntry): void {
  if (desktop) void desktop.openPath(entry.path);
  else window.open(`/api/datasheets/${enc(entry.id)}/file`, "_blank", "noopener");
}

/** Fetch a file generated for a proposal's check (e.g. its SVG preview) with the API token. */
export async function proposalFileText(projectId: string, boardId: string, id: string, name: string): Promise<string> {
  const res = await fetch(`/api${board(projectId, boardId)}/proposals/${enc(id)}/files/${enc(name)}`, { headers: authHeaders() });
  if (!res.ok) throw new ApiError(res.status, toErrorBody(await res.text(), res.status));
  return res.text();
}
