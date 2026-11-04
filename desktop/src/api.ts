import { desktop } from "./desktop";
import { toErrorBody } from "./format";
import type {
  ApiErrorBody,
  AppSettings,
  BomTable,
  CircuitIR,
  Example,
  Job,
  OutputsStatus,
  ProjectSummary,
  RevisionInfo,
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

  circuit: (id: string) =>
    request<{ circuit: CircuitIR | null; revision: number | null }>("GET", `/projects/${enc(id)}/circuit`),
  validateCircuit: (id: string, ir: unknown) =>
    request<ValidationReport>("POST", `/projects/${enc(id)}/circuit/validate`, ir),
  saveCircuit: (id: string, ir: unknown, source: "user_edit" | "import") =>
    request<SaveCircuitResult>("PUT", `/projects/${enc(id)}/circuit?source=${source}`, ir),

  revisions: (id: string) => request<RevisionInfo[]>("GET", `/projects/${enc(id)}/revisions`),
  restoreRevision: (id: string, rev: number) =>
    request<{ revision: RevisionInfo; created: boolean }>("POST", `/projects/${enc(id)}/revisions/${rev}/restore`),

  markdown: (id: string) => request<{ revision: number; markdown: string }>("GET", `/projects/${enc(id)}/documents/markdown`),
  bom: (id: string) => request<BomTable>("GET", `/projects/${enc(id)}/documents/bom`),

  exportProject: (id: string, force = false) => request<Job>("POST", `/projects/${enc(id)}/exports`, { force }),
  outputs: (id: string) => request<OutputsStatus>("GET", `/projects/${enc(id)}/outputs`),
  jobs: (id: string) => request<Job[]>("GET", `/projects/${enc(id)}/jobs`),
  job: (jobId: string) => request<Job>("GET", `/jobs/${enc(jobId)}`),
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

/** Fetch a text output file (e.g. the SVG preview) with the API token. */
export async function outputText(projectId: string, name: string): Promise<string> {
  const res = await fetch(`/api/projects/${enc(projectId)}/outputs/files/${enc(name)}`, { headers: authHeaders() });
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
