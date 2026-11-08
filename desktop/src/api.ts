import { desktop } from "./desktop";
import { toErrorBody } from "./format";
import type {
  AgentLimits,
  AgentRun,
  AgentScopeItem,
  AiProvider,
  AiStatus,
  ApiErrorBody,
  BoardReview,
  AppSettings,
  BoardSummary,
  BomAvailability,
  BomTable,
  BoardReviewStatus,
  ChecklistSuggestion,
  CircuitIR,
  ChatGptLoginStatus,
  CircuitTemplate,
  ClaudeLoginStatus,
  DatasheetEntry,
  DatasheetReport,
  DatasheetSearchKeys,
  DatasheetSummaryResult,
  GuideChecklist,
  GuideDraft,
  GuideEntry,
  GuideEvent,
  GuideListing,
  GuideOrigin,
  GuideStatus,
  DesignPreview,
  EngineeringReview,
  EvalCases,
  EvalRunInfo,
  EvalRunPage,
  EvalSuite,
  Example,
  InventoryApplyResult,
  InventoryImportResult,
  InventoryInfo,
  InventoryMatch,
  Job,
  MarkdownImportResult,
  NoteDraft,
  NotePage,
  NoteSummary,
  QuickFix,
  QuickFixOption,
  OutputsStatus,
  ProjectSummary,
  Proposal,
  RequirementsSpec,
  RevisionInfo,
  RevisionInputs,
  SaveCircuitResult,
  SystemInfo,
  TechnicalReview,
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
  bomAvailability: (p: string, b: string) => request<BomAvailability>("GET", `${board(p, b)}/documents/bom/availability`),
  lookupBomAvailability: (p: string, b: string) => request<Job>("POST", `${board(p, b)}/documents/bom/availability`),
  rationale: (p: string, b: string) =>
    request<{ revision: number; markdown: string; summary: Record<string, number> }>("GET", `${board(p, b)}/documents/rationale`),
  technicalReview: (p: string, b: string) => request<TechnicalReview>("GET", `${board(p, b)}/technical-review`),

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

  datasheetTemplate: (p: string, b: string, componentId: string) =>
    request<{ filename: string; text: string }>("GET", `${board(p, b)}/datasheets/template/${enc(componentId)}`),
  boardDatasheets: (p: string, b: string, proposalId?: string | null) =>
    request<DatasheetReport>("GET", `${board(p, b)}/datasheets${proposalId ? `?proposal_id=${enc(proposalId)}` : ""}`),
  fetchDatasheets: (p: string, b: string, candidates: string[], proposalId?: string | null) =>
    request<Job>("POST", `${board(p, b)}/datasheets/fetch`, { candidates, proposal_id: proposalId ?? null, fallback: true }),
  searchDatasheets: (p: string, b: string, proposalId?: string | null) =>
    request<Job>("POST", `${board(p, b)}/datasheets/search`, { proposal_id: proposalId ?? null }),
  recheckProposalDatasheets: (p: string, b: string, id: string) =>
    request<Proposal>("POST", `${board(p, b)}/proposals/${enc(id)}/recheck-datasheets`),
  guides: () => request<GuideListing>("GET", "/guides"),
  createGuide: (body: { draft: GuideDraft; origin: GuideOrigin; by: string; adopt: boolean; ai_text?: boolean }) =>
    request<GuideEntry>("POST", "/guides", body),
  updateGuide: (id: string, draft: GuideDraft, by: string) => request<GuideEntry>("PUT", `/guides/${enc(id)}`, { draft, by }),
  decideGuide: (id: string, status: GuideStatus, by: string, note?: string) =>
    request<GuideEntry>("POST", `/guides/${enc(id)}/decision`, { status, by, note: note ?? null }),
  deleteGuide: (id: string) => request<null>("DELETE", `/guides/${enc(id)}`),
  guideHistory: (id: string) => request<{ events: GuideEvent[] }>("GET", `/guides/${enc(id)}/history`),
  guidesHistory: (limit = 50) => request<{ events: GuideEvent[] }>("GET", `/guides-history?limit=${limit}`),
  importGuides: (filename: string, text: string, by: string, adopt: boolean) =>
    request<{ created: string[]; skipped: string[]; warnings: string[]; found: number }>("POST", "/guides-import", { filename, text, by, adopt }),
  guidesMarkdown: async (): Promise<string> => {
    const res = await fetch("/api/guides-export.md", { headers: authHeaders() });
    if (!res.ok) throw new ApiError(res.status, toErrorBody(await res.text(), res.status));
    return res.text();
  },
  boardGuides: (p: string, b: string, proposalId?: string | null) =>
    request<GuideChecklist>("GET", `${board(p, b)}/guides${proposalId ? `?proposal_id=${enc(proposalId)}` : ""}`),
  draftGuide: (p: string, b: string, feedback: string, proposalId?: string | null) =>
    request<Job>("POST", `${board(p, b)}/guides/draft`, { feedback, proposal_id: proposalId ?? null, include_circuit: true }),
  datasheetSearchKeys: () => request<DatasheetSearchKeys>("GET", "/datasheet-search/keys"),
  putDatasheetSearchKey: (name: "mouser" | "digikey", body: { api_key?: string; client_id?: string; client_secret?: string }) =>
    request<DatasheetSearchKeys>("PUT", `/datasheet-search/keys/${name}`, body),
  deleteDatasheetSearchKey: (name: "mouser" | "digikey") => request<DatasheetSearchKeys>("DELETE", `/datasheet-search/keys/${name}`),
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
  aiReview: (p: string, b: string, proposalId?: string, writeNote = false) =>
    request<Job>("POST", `${board(p, b)}/ai/review`, { proposal_id: proposalId ?? null, write_note: writeNote }),
  boardReview: (p: string, b: string) => request<{ review: BoardReview | null }>("GET", `${board(p, b)}/ai/review`),
  autoReview: (p: string, b: string, body: { proposal_id: string; max_warnings: number; max_rounds: number }) =>
    request<Job>("POST", `${board(p, b)}/ai/auto-review`, body),
  revisePreview: (p: string, b: string, proposalId?: string | null) =>
    request<RevisionInputs>("GET", `${board(p, b)}/ai/revise/preview${proposalId ? `?proposal_id=${enc(proposalId)}` : ""}`),
  aiRevise: (p: string, b: string, body: {
    proposal_id?: string | null;
    instructions?: string | null;
    finding_indexes?: number[] | null;
    include_checks: boolean;
    review: boolean;
  }) => request<Job>("POST", `${board(p, b)}/ai/revise`, body),

  quickFixes: (p: string, b: string, proposalId?: string | null) =>
    request<{ fixes: QuickFix[]; nets: string[] }>("GET", `${board(p, b)}/quick-fixes${proposalId ? `?proposal_id=${enc(proposalId)}` : ""}`),
  applyQuickFixes: (p: string, b: string, proposalId: string | null,
    choices: { fix_id: string; kind: QuickFixOption["kind"]; net: string | null; value: string | null }[]) =>
    request<{ proposal: Proposal; job: Job }>("POST", `${board(p, b)}/quick-fixes`, { proposal_id: proposalId, choices }),

  aiStatus: () => request<AiStatus>("GET", "/ai/settings"),
  saveAiSettings: (s: Partial<{ enabled: boolean; model: string; provider: AiProvider }>) =>
    request<AiStatus>("PUT", "/ai/settings", s),
  saveAiKey: (apiKey: string) => request<{ key: AiStatus["key"] }>("PUT", "/ai/key", { api_key: apiKey }),
  deleteAiKey: () => request<{ key: AiStatus["key"] }>("DELETE", "/ai/key"),
  templates: () => request<{ templates: CircuitTemplate[] }>("GET", "/templates"),
  agentDefaults: () => request<{ tools: string[]; defaults: AgentLimits }>("GET", "/agent/tools"),
  agentRuns: (p: string, b: string) => request<{ runs: AgentRun[] }>("GET", `${board(p, b)}/agent-runs`),
  agentRun: (p: string, b: string, id: string) => request<AgentRun>("GET", `${board(p, b)}/agent-runs/${enc(id)}`),
  startAgent: (p: string, b: string, text: string, limits?: AgentLimits) =>
    request<{ run: AgentRun; job: Job }>("POST", `${board(p, b)}/agent-runs`, { request: text, limits }),
  approveAgentScope: (p: string, b: string, id: string, scope: AgentScopeItem[] | null) =>
    request<{ run: AgentRun; job: Job }>("POST", `${board(p, b)}/agent-runs/${enc(id)}/approve-scope`, { scope }),
  cancelAgent: (p: string, b: string, id: string) => request<AgentRun>("POST", `${board(p, b)}/agent-runs/${enc(id)}/cancel`),
  reviews: (p: string, b: string) =>
    request<{ status: BoardReviewStatus; reviews: EngineeringReview[] }>("GET", `${board(p, b)}/reviews`),
  reviewChecklist: (p: string, b: string) =>
    request<{ revision: number | null; items: ChecklistSuggestion[] }>("GET", `${board(p, b)}/reviews/checklist`),
  addReview: (p: string, b: string, body: Omit<EngineeringReview, "id" | "reviewed_at" | "covered">) =>
    request<{ review: EngineeringReview; status: BoardReviewStatus }>("POST", `${board(p, b)}/reviews`, body),
  importKicadLayout: (p: string, b: string) =>
    request<{ proposal: Proposal; job: Job; unknown: string[]; extra_units: string[] }>(
      "POST", `${board(p, b)}/layout/import-kicad`),
  importKicadEdits: (p: string, b: string) =>
    request<{ proposal: Proposal; job: Job; skipped: string[]; counts: Record<string, number> }>(
      "POST", `${board(p, b)}/kicad/import-edits`),
  resetLayout: (p: string, b: string) => request<{ proposal: Proposal; job: Job }>("POST", `${board(p, b)}/layout/reset`),
  placeTemplate: (p: string, b: string, id: string, params: Record<string, string>) =>
    request<{ proposal: Proposal; job: Job }>("POST", `${board(p, b)}/templates/${encodeURIComponent(id)}`, { params }),
  chatgptLogin: () => request<ChatGptLoginStatus>("GET", "/ai/chatgpt/login"),
  startChatgptLogin: () => request<ChatGptLoginStatus>("POST", "/ai/chatgpt/login"),
  chatgptLogout: (forget = false) =>
    request<ChatGptLoginStatus>("POST", `/ai/chatgpt/logout${forget ? "?forget=true" : ""}`),
  claudeLogin: () => request<ClaudeLoginStatus>("GET", "/ai/claude/login"),
  startClaudeLogin: () => request<ClaudeLoginStatus & { started: boolean }>("POST", "/ai/claude/login"),
  claudeLogout: () => request<ClaudeLoginStatus>("POST", "/ai/claude/logout"),
  evalCases: () => request<EvalCases>("GET", "/evals/cases"),
  startEval: (suites: EvalSuite[], repeats: number, cases?: string[]) => request<Job>("POST", "/evals/runs", { suites, repeats, cases: cases ?? null }),
  evalProgress: (jobId: string) => request<{ done: number; total: number; current: string | null; run_id?: string }>("GET", `/evals/progress/${enc(jobId)}`),
  evalRuns: () => request<{ runs: EvalRunInfo[] }>("GET", "/evals/runs"),
  evalRun: (id: string, compareTo?: string) =>
    request<EvalRunPage>("GET", `/evals/runs/${enc(id)}${compareTo ? `?compare_to=${enc(compareTo)}` : ""}`),
  evalReport: (id: string) => fetchText(`/api/evals/runs/${enc(id)}/report.md`),
  deleteEvalRun: (id: string) => request<void>("DELETE", `/evals/runs/${enc(id)}`),
  notes: (filters: { project_id?: string; board_id?: string; status?: string; q?: string } = {}) => {
    const qs = new URLSearchParams(Object.entries(filters).filter(([, v]) => v) as [string, string][]).toString();
    return request<{ notes: NoteSummary[]; topics: Record<string, string> }>("GET", `/notes${qs ? `?${qs}` : ""}`);
  },
  note: (id: string) => request<NotePage>("GET", `/notes/${enc(id)}`),
  createNote: (body: { draft: Partial<NoteDraft> & { title: string }; by: string; project_id?: string; board_id?: string; revision?: number | null }) =>
    request<NotePage>("POST", "/notes", body),
  editNote: (id: string, draft: NoteDraft, by: string) => request<NotePage>("PUT", `/notes/${enc(id)}`, { draft, by }),
  reviewNote: (id: string, by: string, reviewed = true) => request<NotePage>("POST", `/notes/${enc(id)}/review`, { by, reviewed }),
  deleteNote: (id: string, by: string) => request<void>("DELETE", `/notes/${enc(id)}?by=${enc(by)}`),
  noteToGuide: (id: string, index: number, by: string) =>
    request<NotePage & { guide: GuideEntry }>("POST", `/notes/${enc(id)}/apply/${index}/guide`, { by }),
  noteMarkdown: (id: string) => fetchText(`/api/notes/${enc(id)}/markdown`),
  notesMarkdown: () => fetchText("/api/notes-export.md"),
  boardNotes: (p: string, b: string) => request<{ notes: NoteSummary[]; topics: Record<string, string> }>("GET", `${board(p, b)}/notes`),
  notePreview: (p: string, b: string, proposalId?: string | null) =>
    request<{ provider: AiProvider; model: string; enabled: boolean; revision: number | null; circuit_chars: number; evidence: number; evidence_kinds: Record<string, number> }>(
      "GET",
      `${board(p, b)}/notes/preview${proposalId ? `?proposal_id=${enc(proposalId)}` : ""}`,
    ),
  aiNote: (p: string, b: string, proposalId?: string | null, focus = "") =>
    request<Job>("POST", `${board(p, b)}/notes/ai`, { proposal_id: proposalId ?? null, focus }),
  aiTestPreview: () => request<{ prompt: string; provider: AiProvider; model: string }>("GET", "/ai/test"),
  aiTest: () =>
    request<{ ok: boolean; provider: AiProvider; model: string; reply: string; finish_reason: string | null; latency_ms: number }>(
      "POST",
      "/ai/test",
    ),
  aiModels: () =>
    request<{ models: { name: string; display_name: string | null; output_token_limit: number | null }[]; current: string }>(
      "GET",
      "/ai/models",
    ),
};

/** A text download from the API (Markdown exports) with the token. */
async function fetchText(path: string): Promise<string> {
  const res = await fetch(path, { headers: authHeaders() });
  if (!res.ok) throw new ApiError(res.status, toErrorBody(await res.text(), res.status));
  return res.text();
}

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

/** Open a web page (a datasheet link that could not be downloaded) in the system browser. */
export function openLink(url: string): void {
  if (!/^https?:\/\//i.test(url)) return;
  const secure = url.replace(/^http:\/\//i, "https://");
  if (desktop?.openExternal) void desktop.openExternal(secure);
  else window.open(secure, "_blank", "noopener,noreferrer");
}

/** Fetch a file generated for a proposal's check (e.g. its SVG preview) with the API token. */
export async function proposalFileText(projectId: string, boardId: string, id: string, name: string): Promise<string> {
  const res = await fetch(`/api${board(projectId, boardId)}/proposals/${enc(id)}/files/${enc(name)}`, { headers: authHeaders() });
  if (!res.ok) throw new ApiError(res.status, toErrorBody(await res.text(), res.status));
  return res.text();
}
