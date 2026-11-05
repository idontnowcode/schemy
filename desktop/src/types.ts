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
  last_board_id: string | null;
  last_tab: string | null;
}

export interface BoardSummary {
  schema_version: number;
  id: string;
  name: string;
  description: string;
  created_at: string;
  updated_at: string;
  current_revision: number | null;
  path: string;
  kicad_project: string;
}

export interface ProjectSummary {
  schema_version: number;
  id: string;
  name: string;
  description: string;
  created_at: string;
  updated_at: string;
  boards: string[];
  board_list: BoardSummary[];
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
  manufacturer?: string | null;
  manufacturer_part_number?: string | null;
  pins: Pin[];
  pin_map_evidence?: { status: VerificationStatus; source?: string | null; notes?: string | null };
  description?: string | null;
  /** "template:<id>@<version>", "ai", or absent for parts entered by hand. */
  origin?: string | null;
}

export interface Net {
  id: string;
  name: string;
  nodes: { component_id: string; pin: string }[];
  /** Volts for the technical review (IR 0.8.0); unset: read from the name. */
  voltage?: number | null;
  /** Amps the loads draw from this net, for the technical review (IR 0.8.0). */
  load_current?: number | null;
}

export interface Sheet {
  id: string;
  name: string;
  components: string[];
}

export interface CircuitIR {
  schema_version: string;
  project_id: string;
  title?: string | null;
  components: Component[];
  nets: Net[];
  sheets?: Sheet[];
  layout?: Record<string, { x: number; y: number; rotation: number; mirror?: boolean }>;
  notes?: { id: string; text: string; components: string[]; net?: string | null }[];
  decisions?: Decision[];
  validation?: { status: VerificationStatus; notes?: string | null };
}

export type DecisionBy = "requirement" | "changed" | "ai" | "template" | "engineer";

export interface Decision {
  id: string;
  topic: string;
  choice: string;
  by: DecisionBy;
  reason: string;
  request?: string | null;
  calc?: string | null;
  source?: string | null;
  components: string[];
}

export interface TechnicalFinding {
  rule: string;
  severity: "ok" | "info" | "warn" | "fail";
  title: string;
  detail: string;
  calc: string | null;
  components: string[];
  references: string[];
}

/** A net the review uses a voltage or current of, with what is written and what the review worked out. */
export interface SupplyNet {
  net: string;
  voltage: number | null;
  voltage_from_name: number | null;
  load_current: number | null;
  derived_current: number | null;
}

export interface TechnicalReview {
  revision: number | null;
  summary: Record<"fail" | "warn" | "info" | "ok", number>;
  findings: TechnicalFinding[];
  supply_nets: SupplyNet[];
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
  source: "user_edit" | "import" | "restore" | "inventory";
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

export interface PageOutput {
  number: number;
  sheet_id: string;
  name: string;
  schematic: string;
  svg: string | null;
  paper: string;
  bbox_mm: number[] | null;
}

export interface Manifest {
  project_id: string;
  board_id: string;
  kicad_project: string;
  source_revision: number;
  generated_at: string;
  generator: string;
  automated_check: AutomatedCheck;
  warnings: Issue[];
  pages: PageOutput[];
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
  board_id: string | null;
  kind: string;
  status: "queued" | "running" | "succeeded" | "failed";
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  /** Shape depends on kind: export, pdf_import, inventory_suggest. */
  result: any;
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

// --- inventory -------------------------------------------------------------------

export interface InventoryPart {
  id: string;
  mpn: string | null;
  manufacturer: string | null;
  category: string | null;
  category_label: string | null;
  value: string | null;
  package: string | null;
  footprint_id: string | null;
  symbol_id: string | null;
  rating: string | null;
  tolerance: string | null;
  stock: number | null;
  priority: number;
  notes: string | null;
  source_row: number;
}

export interface InventoryInfo {
  inventory: { source_file: string; imported_at: string; parts: InventoryPart[] } | null;
  column_labels: Record<string, string>;
  category_labels: Record<string, string>;
}

export interface InventoryImportResult {
  ok: boolean;
  part_count: number;
  mapped_columns: Record<string, string>;
  unknown_columns: string[];
  warnings: { row: number | null; message: string }[];
  errors: { row: number | null; message: string }[];
  parts: InventoryPart[];
  saved: boolean;
}

export type MatchStatus = "matched" | "candidate" | "missing";

export interface LineMatch {
  component_ids: string[];
  references: string[];
  quantity: number;
  category: string | null;
  category_label: string | null;
  value: string;
  symbol_id: string;
  footprint_id: string | null;
  mpn: string | null;
  status: MatchStatus;
  chosen: InventoryPart | null;
  candidates: { part: InventoryPart; reasons: string[]; exact: boolean }[];
  reasons: string[];
  stock_short: boolean | null;
}

export interface InventoryMatch {
  available: boolean;
  revision: number | null;
  boards: number;
  inventory?: { source_file: string; imported_at: string; part_count: number };
  summary: { matched: number; candidate: number; missing: number; stock_short: number } | null;
  lines: LineMatch[];
}

export interface InventoryApplyResult {
  revision: RevisionInfo | null;
  created: boolean;
  changes: { component_id: string; reference: string; field: string; before: string | null; after: string | null }[];
}

export interface Suggestion {
  line: number;
  references: string[];
  inventory_part_id: string;
  mpn: string | null;
  fit: "good" | "acceptable" | "poor";
  reason: string;
}

// --- imports & AI ----------------------------------------------------------------------

export interface ImportIssue {
  severity: "error" | "warning" | "info";
  code: string;
  message: string;
  references: string[];
  line: number | null;
}

export interface MarkdownImportResult {
  ok: boolean;
  circuit: CircuitIR | null;
  issues: ImportIssue[];
  uncertain: Record<string, string>[];
  stats: Record<string, number>;
  saved_copy?: string;
}

export type AiProvider = "gemini" | "claude" | "chatgpt";

/** Sign in with ChatGPT: the user's own ChatGPT account and plan, no API key. */
export interface ChatGptLoginStatus {
  logged_in: boolean;
  email: string | null;
  plan_usage: boolean;
  registered: boolean;
  pending: boolean;
  authorize_url: string | null;
  error: string | null;
  usage_hint: string;
  revoked?: boolean | null;
}

export interface AiSettings {
  enabled: boolean;
  provider: AiProvider;
  model: string;
  max_output_tokens: number;
  timeout_s: number;
}

export interface ClaudeLoginStatus {
  logged_in: boolean;
  source: "ant_login" | "env_token" | null;
  profile: string | null;
  cli_found?: boolean;
  running?: boolean;
  last_error?: string | null;
  install_hint?: string;
}

export interface AiStatus {
  settings: AiSettings;
  key: {
    configured: boolean;
    source: "env" | "keyring" | "login" | null;
    keyring_available: boolean;
    login?: ClaudeLoginStatus;
  };
  providers: AiProvider[];
}

// --- datasheets --------------------------------------------------------------------------

export type IdentityStatus = "matched" | "family" | "not_found" | "no_text";

export type DatasheetSource = "upload" | "kicad_symbol" | "maker_site" | "mouser" | "digikey";

/** One place to get a part's datasheet; its URLs are tried in order. */
export interface DatasheetCandidate {
  id: string;
  source: Exclude<DatasheetSource, "upload">;
  label: string;
  urls: string[];
  manufacturer: string | null;
  part_number: string | null;
  description: string | null;
  product_url: string | null;
  status: string | null;
}

export interface DatasheetFetchable extends DatasheetCandidate {
  url: string;
  references: string[];
  symbol_ids: string[];
}

export interface DatasheetSearchError {
  source: string;
  error_code: string;
  message: string;
  hint: string | null;
}

export interface DatasheetFetchResult {
  url: string;
  references: string[];
  ok: boolean;
  entry_id?: string;
  identity?: IdentityStatus;
  source?: string;
  label?: string;
  error_code?: string;
  message?: string;
  hint?: string;
  product_url?: string | null;
  tried: { url: string; source: string; label: string; error_code: string; message: string }[];
}

export interface DatasheetSearchKeys {
  mouser: { configured: boolean; source: string | null };
  digikey: { configured: boolean; source: string | null };
  keyring_available: boolean;
}

export interface DatasheetEntry {
  id: string;
  file: string;
  original_name: string;
  sha256: string;
  bytes: number;
  page_count: number | null;
  title: string | null;
  source: DatasheetSource;
  urls: string[];
  final_url: string | null;
  added_at: string;
  manufacturer: string | null;
  part_numbers: string[];
  symbols: string[];
  identity: { status?: IdentityStatus; found?: string[]; checked?: string[] };
  summary: {
    origin: "markdown" | "ai";
    source_name: string;
    imported_at: string;
    reviewed: boolean;
    warnings: number;
    model: string | null;
  } | null;
  path: string;
}

export interface PinMapRow {
  number: string;
  datasheet_number: string | null;
  symbol_name: string | null;
  datasheet_name: string | null;
  kind: string | null;
  page: number | null;
  status: "match" | "mismatch" | "missing_in_symbol" | "missing_in_datasheet" | "no_name";
}

export interface RatingRef {
  parameter: string;
  min: number | null;
  max: number | null;
  condition: string | null;
  page: number | null;
}

export interface RatingRow {
  number: string;
  name: string;
  net: string | null;
  voltage: number | null;
  abs_max: RatingRef[];
  recommended: RatingRef[];
  status: "ok" | "over_abs_max" | "outside_recommended" | "unknown_voltage" | "unconnected";
}

export interface ExternalRow {
  requirement: "required" | "recommended";
  kind_label: string;
  value: string | null;
  quantity: number | null;
  endpoints: string[];
  description: string | null;
  page: number | null;
  found: string[];
  status: "present" | "short" | "value_differs" | "missing" | "direct" | "unchecked";
  reason: string;
}

export interface DatasheetChecks {
  package: { chosen: string | null; options: string[]; reason: string };
  pin_names_from: "library" | "circuit";
  pin_map: { status: "match" | "mismatch" | "unchecked"; rows: PinMapRow[]; counts: Record<string, number> };
  ratings: { status: "ok" | "warning" | "error" | "unchecked"; rows: RatingRow[]; skipped: number };
  external: { status: "ok" | "info" | "warning" | "unchecked"; rows: ExternalRow[] };
}

export interface DatasheetComponentRow {
  id: string;
  reference: string;
  value: string;
  symbol_id: string;
  footprint_id: string | null;
  manufacturer: string | null;
  manufacturer_part_number: string | null;
  category: string | null;
  category_label: string | null;
  identity: "mpn" | "symbol" | "generic";
  relevant: boolean;
  symbol_datasheet_url: string | null;
  can_fetch: boolean;
  /** Candidate ids (see DatasheetReport.fetchable), best first; empty once a datasheet is stored. */
  candidates: string[];
  searched: { at: string; term: string; found: number; errors: DatasheetSearchError[] } | null;
  datasheet: DatasheetEntry | null;
  checks: DatasheetChecks | null;
  notes: { text: string; page: number | null }[];
  uncertain: Record<string, string>[];
}

export interface DatasheetReport {
  revision: number | null;
  proposal_id: string | null;
  components: DatasheetComponentRow[];
  fetchable: DatasheetFetchable[];
  library_available: boolean;
  /** Distributor searches with keys. */
  search: { mouser: boolean; digikey: boolean };
}

export interface DatasheetSummaryResult {
  ok: boolean;
  issues: ImportIssue[];
  stats: Record<string, number>;
  entry: DatasheetEntry | null;
}

// --- proposals (Phase 2) -----------------------------------------------------------------

export type ProposalStatus = "checking" | "ready" | "invalid" | "check_failed" | "approved" | "rejected" | "stale";

// --- templates ---------------------------------------------------------------------------------

export interface TemplateBasis {
  part: string;
  document: string;
  section: string;
  supports: string;
  url: string | null;
  page: string | null;
}

export interface TemplateCheck {
  id: string;
  text: string;
  basis: string;
}

export interface CircuitTemplate {
  id: string;
  version: number;
  ref: string;
  title: string;
  category: string;
  summary: string;
  params: { name: string; label: string; default: string; kind: "net" | "value" | "flag" }[];
  basis: TemplateBasis[];
  checklist: TemplateCheck[];
  parts: { key: string; prefix: string; value: string; symbol_id: string; role: string; footprint_id: string | null; mpn: string | null }[];
}

export interface PlacedTemplate extends CircuitTemplate {
  params_used?: Record<string, string>;
  components: Record<string, string>;
}

export interface ProposalDiff {
  summary: Record<string, number>;
  is_new_circuit: boolean;
  components_added: {
    id: string;
    reference: string;
    value: string;
    symbol_id: string;
    footprint_id: string | null;
    manufacturer_part_number: string | null;
    origin?: string | null;
  }[];
  components_removed: { id: string; reference: string; value: string; symbol_id: string }[];
  properties_changed: { id: string; reference: string; field: string; before: string | null; after: string | null }[];
  connections: { reference: string; pin: string; pin_name: string | null; before: string | null; after: string | null }[];
  nets_added: string[];
  nets_removed: string[];
  moved: string[];
  page_moves: { reference: string; before: string; after: string }[];
  notes?: { id: string; target: string; before: string | null; after: string | null }[];
  decisions?: { id: string; topic: string; by: DecisionBy; before: string | null; after: string | null; reason: string | null }[];
  net_specs?: { net: string; field: "voltage" | "load_current"; before: number | null; after: number | null }[];
}

export interface AiFinding {
  severity: "error" | "warning" | "info";
  title: string;
  detail: string;
  references: string[];
  suggestion: string | null;
}

export interface ProposalAi {
  model?: string;
  summary?: string;
  assumptions?: string[];
  questions?: string[];
  citations?: { reference: string; page: number | null; text: string }[];
  parts?: { key: string; role: string; symbol_id: string; how: string }[];
  templates?: { ref: string; title: string; params: Record<string, string>; parts: string[] }[];
  attempts?: number;
  review?: { findings: AiFinding[]; summary: string; model?: string; created_at?: string } | null;
  parent?: string | null;
  instructions?: string | null;
  addressed?: { severity: string; title: string; detail: string; source: "ai" | "check" }[];
  diff_from_parent?: ProposalDiff | null;
  remaining_problems?: string[];
}

export interface QuickFixOption {
  kind: "resistor" | "direct" | "no_connect";
  net: string | null;
  value: string | null;
  label: string;
  recommended: boolean;
}

/** A floating input pin and the usual ways to fix it without AI. */
export interface QuickFix {
  id: string;
  component_id: string;
  reference: string;
  pin: string;
  pin_name: string | null;
  electrical_type: string;
  net: string | null;
  problem: string;
  options: QuickFixOption[];
}

export interface RevisionInputs {
  findings: (AiFinding & { index: number })[];
  checks: (AiFinding & { source: "check" })[];
}

export interface Proposal {
  id: string;
  project_id: string;
  board_id: string;
  title: string;
  rationale: string | null;
  source: "user" | "ai_design" | "ai_revision" | "ai_pdf_import" | "template" | "agent" | "kicad_layout" | "kicad_edit";
  base_revision: number | null;
  status: ProposalStatus;
  created_at: string;
  decided_at: string | null;
  decision_note: string | null;
  result_revision: number | null;
  diff: ProposalDiff;
  evidence: {
    checked_at: string;
    ir_issues: Issue[];
    blocking: boolean;
    generation_error: { error_code: string; message: string; issues?: Issue[] } | null;
    automated_check: AutomatedCheck | null;
    power_flags: string[];
    warnings: Issue[];
    pages: PageOutput[];
    files: string[];
    datasheets: {
      reference: string;
      reviewed: boolean;
      pin_map: string;
      ratings: string;
      external: string;
      external_missing: string[];
      ratings_problems: string[];
    }[];
  } | null;
  ai: ProposalAi | null;
  /** The open proposal this one fixes (a follow-up made from its check results). */
  parent?: string | null;
  diff_from_parent?: ProposalDiff | null;
  template?: (CircuitTemplate & { params: Record<string, string> | CircuitTemplate["params"]; components: Record<string, string> }) | null;
  agent?: { run_id: string; scope: ScopeItem[] } | null;
  candidate?: CircuitIR;
}

export interface RequirementsSpec {
  summary: string;
  functions: string[];
  power: { name: string; source: string; voltage: string; current?: string; notes?: string }[];
  interfaces: { name: string; kind: string; details?: string }[];
  parts_preferences: string[];
  constraints: string[];
  open_questions: string[];
}

export interface DesignPreview {
  provider: string;
  model: string;
  enabled: boolean;
  key: boolean;
  requirements_chars: number;
  spec: boolean;
  inventory_parts: number;
  datasheets: number;
  current_circuit: boolean;
  library: boolean;
}

export interface BoardReview {
  summary: string;
  findings: AiFinding[];
  model?: string;
  created_at?: string;
  revision?: number | null;
}

export interface ScopeItem {
  op: string;
  target: string;
  field?: string | null;
  net?: string | null;
}

// --- engineering review -------------------------------------------------------------------------

export type ReviewStatus = "unverified" | "needs_review" | "verified";

export interface ReviewCheckItem {
  item: string;
  source: string;
  result: "ok" | "issue" | "na";
  note?: string | null;
}

export interface EngineeringReview {
  id: string;
  revision: number;
  reviewer: string;
  reviewed_at: string;
  scope: "board" | "components";
  component_ids: string[];
  covered: string[];
  datasheets: { part: string; document: string; revision: string | null; pages: string | null }[];
  checks: ReviewCheckItem[];
  result: "verified" | "needs_changes";
  notes: string | null;
}

export interface BoardReviewStatus {
  board: ReviewStatus | null;
  components: Record<string, ReviewStatus>;
  revision: number | null;
  latest: EngineeringReview | null;
}

export interface ChecklistSuggestion {
  source: string;
  item: string;
  basis: string;
  group: string;
}

// --- agent runs (Phase 4) -------------------------------------------------------------------------

export type AgentState =
  | "PLANNING"
  | "PROPOSED_PLAN"
  | "SCOPE_APPROVED"
  | "SANDBOX_EXECUTION"
  | "VALIDATION"
  | "REVIEW_REQUIRED"
  | "COMMITTED"
  | "REJECTED"
  | "CANCELLED"
  | "FAILED"
  | "STALE";

export interface AgentScopeItem {
  op: string;
  target: string;
  field?: string | null;
  why?: string | null;
}

export interface AgentLimits {
  max_llm_calls: number;
  max_tokens: number;
  max_seconds: number;
  max_tool_calls: number;
  max_retries: number;
}

export interface AgentLogEntry {
  ts: string;
  type: string;
  tool?: string;
  step?: number;
  thought?: string;
  ok?: boolean;
  error?: { code: string; message: string } | null;
  result?: unknown;
  seconds?: number;
  args?: string;
  reason?: string;
  from?: string;
  to?: string;
  what?: string;
  tokens?: number;
  problems?: string[];
  attempt?: number;
}

export interface AgentRun {
  id: string;
  request: string;
  state: AgentState;
  created_at: string;
  updated_at: string;
  base_revision: number | null;
  provider: string;
  model: string;
  limits: AgentLimits;
  usage: { llm_calls: number; tokens: number; tool_calls: number; seconds: number };
  plan: {
    summary?: string;
    steps?: string[];
    scope?: AgentScopeItem[];
    risks?: string[];
    questions?: string[];
    dropped_scope?: string[];
    scope_request?: string;
  } | null;
  scope: AgentScopeItem[];
  pending_scope: AgentScopeItem[];
  attempts: number;
  summary: string | null;
  validation: {
    status: string;
    problems: string[];
    kicad: string;
    erc_errors?: number;
    erc_warnings?: number;
    netlist_matches_ir?: boolean;
  } | null;
  report: {
    values: { reference: string; field: string; before: string | null; after: string | null }[];
    connections: { reference: string; pin: string; before: string | null; after: string | null }[];
    components_added: string[];
    components_removed: string[];
    netlist: {
      nets_changed?: Record<string, { before: string[][]; after: string[][] }>;
      values_changed?: Record<string, { before: string | null; after: string | null }>;
      error?: string;
    } | null;
  } | null;
  proposal_id: string | null;
  failure: { code: string; message: string } | null;
  history: { at: string; from: string; to: string; reason: string }[];
  log?: AgentLogEntry[];
}

// --- design guide ------------------------------------------------------------------------------

export type GuideStatus = "candidate" | "adopted" | "retired";
export type GuideStrength = "must" | "should" | "consider";
export type GuideOrigin = "seed" | "engineer" | "review" | "ai_review" | "feedback" | "check";

export interface GuideSource {
  kind: "datasheet" | "app_note" | "standard" | "practice" | "app_rule" | "review" | "feedback" | "ai";
  text: string;
  url?: string | null;
  ref?: Record<string, string | number>;
}

export interface GuideDraft {
  title: string;
  rule: string;
  why: string;
  strength: GuideStrength;
  topics: string[];
  scope: { parts: string[]; keywords: string[] };
  check: string | null;
  sources: GuideSource[];
}

export interface GuideEntry extends GuideDraft {
  id: string;
  status: GuideStatus;
  origin: GuideOrigin;
  version: number;
  created_at: string;
  created_by: string;
  updated_at: string;
  decided_by: string | null;
  decided_at: string | null;
  note: string | null;
  stats?: { cited?: number; flagged?: number; issue?: number };
}

export interface GuideListing {
  entries: GuideEntry[];
  topics: Record<string, string>;
  checks: Record<string, string>;
  strengths: Record<GuideStrength, string>;
}

export interface GuideEvent {
  at: string;
  event: string;
  id: string | null;
  by: string;
  [key: string]: unknown;
}

export interface GuideChecklistItem {
  id: string;
  title: string;
  rule: string;
  strength: GuideStrength;
  topics: string[];
  check: string | null;
  status: "fail" | "warn" | "ok" | "manual";
  notes: string[];
  origin: GuideOrigin;
  ai_applied: boolean;
}

export interface GuideChecklist {
  revision: number | null;
  proposal_id?: string | null;
  items: GuideChecklistItem[];
  candidates: number;
  ai_applied: string[];
}
