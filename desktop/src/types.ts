// Shapes returned by the local backend (/api). Kept in sync with backend/schemas.py.

export type VerificationStatus = "unverified" | "needs_review" | "verified";
export type AutomatedStatus = "not_checked" | "passed" | "failed";

export interface ApiErrorBody {
  error_code: string;
  message: string;
  affected_component_ids: string[];
  recovery_hint: string | null;
  details?: unknown;
}

export interface SystemInfo {
  app_version: string;
  workspace: string;
  symbol_dirs: string[];
  kicad: { available: boolean; version: string | null; runner: string | null; error?: string };
}

export interface AppSettings {
  last_project_id: string | null;
  last_tab: string | null;
}

export interface ProjectSummary {
  schema_version: number;
  id: string;
  name: string;
  description: string;
  created_at: string;
  updated_at: string;
  current_revision: number | null;
  path: string;
  last_opened_at: string | null;
}

export interface Pin {
  number: string;
  name?: string | null;
}

export interface Component {
  id: string;
  reference: string;
  value: string;
  symbol_id: string;
  footprint_id?: string | null;
  manufacturer_part_number?: string | null;
  pins: Pin[];
  pin_map_evidence?: { status: VerificationStatus; source?: string | null; notes?: string | null };
  description?: string | null;
}

export interface Net {
  id: string;
  name: string;
  nodes: { component_id: string; pin: string }[];
}

export interface CircuitIR {
  schema_version: string;
  project_id: string;
  title?: string | null;
  components: Component[];
  nets: Net[];
  layout?: Record<string, { x: number; y: number; rotation: number }>;
  validation?: { status: VerificationStatus; notes?: string | null };
}

export interface Issue {
  error_code: string;
  severity: "error" | "warning";
  message: string;
  affected_component_ids: string[];
  recovery_hint: string | null;
  level?: "ir" | "library";
}

export interface SchemaError {
  loc: string[];
  message: string;
  type: string;
}

export interface ValidationReport {
  valid: boolean;
  schema_errors: SchemaError[];
  issues: Issue[];
  library_checked: boolean;
}

export interface RevisionInfo {
  revision: number;
  created_at: string;
  source: "user_edit" | "import" | "restore";
  checksum: string;
  parent: number | null;
  restored_from: number | null;
  note: string | null;
  component_count: number;
  net_count: number;
}

export interface SaveCircuitResult {
  revision: RevisionInfo;
  created: boolean;
  warnings: string[];
  validation: ValidationReport;
}

export interface ErcViolation {
  severity: string;
  type: string;
  description: string;
  items: string[];
}

export interface AutomatedCheck {
  status: AutomatedStatus;
  reason: string | null;
  kicad_version: string | null;
  erc_errors: number | null;
  erc_warnings: number | null;
  netlist_matches_ir: boolean | null;
  violations: ErcViolation[];
  netlist_diff: string | null;
}

export interface Artifact {
  artifact_id: string;
  artifact_type: string;
  path: string;
  source_revision: number;
  tool_version: string;
  checksum: string;
  validation_status: AutomatedStatus | null;
  created_at: string;
}

export interface Manifest {
  project_id: string;
  source_revision: number;
  generated_at: string;
  generator: string;
  automated_check: AutomatedCheck;
  warnings: Issue[];
  preview_bbox_mm: number[] | null;
  artifacts: Artifact[];
}

export interface OutputsStatus {
  manifest: Manifest | null;
  stale: boolean;
  externally_modified: string[];
  missing: string[];
  current_revision: number | null;
  path: string;
}

export interface Job {
  id: string;
  project_id: string;
  kind: string;
  status: "queued" | "running" | "succeeded" | "failed";
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  result: { source_revision: number; automated_check: AutomatedCheck; artifacts: string[] } | null;
  error: ApiErrorBody | null;
}

export interface Example {
  id: string;
  title: string;
  circuit: CircuitIR;
}

export interface BomTable {
  revision: number;
  columns: string[];
  rows: Record<string, string>[];
}
