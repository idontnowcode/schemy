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
  last_view?: "flow" | "advanced" | null;
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
  /** Ratings of the exact part from its datasheet (IR 0.9.0). */
  specs?: PartSpecs | null;
}

export type Dielectric = "C0G" | "X7R" | "X5R" | "X7S" | "Y5V" | "ceramic" | "tantalum" | "polymer" | "electrolytic" | "film";

export interface PartSpecs {
  voltage_rating?: number | null;
  current_rating?: number | null;
  power_rating?: number | null;
  tolerance?: number | null;
  dielectric?: Dielectric | null;
  esr?: number | null;
  forward_voltage?: number | null;
}

export interface Net {
  id: string;
  name: string;
  nodes: { component_id: string; pin: string }[];
  /** Volts for the technical review (IR 0.8.0); unset: read from the name. */
  voltage?: number | null;
  /** Amps the loads draw from this net, for the technical review (IR 0.8.0). */
  load_current?: number | null;
  /** Lowest / highest voltage for worst-case checks (IR 0.9.0). */
  voltage_min?: number | null;
  voltage_max?: number | null;
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
  voltage_min: number | null;
  voltage_max: number | null;
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
  /** Steps a long job has reached (AI design: parts → pins → check → proposal → review; auto review: rounds). */
  progress?: JobStep[];
}

export interface JobStep {
  at: string;
  step: string;
  text: string;
  data?: AutoReviewRound;
}

/** A job running (or just finished) in any project, for the status panel (GET /api/jobs/active). */
export interface ActiveJob {
  id: string;
  kind: string;
  status: Job["status"];
  project_id: string;
  project_name: string;
  board_id: string | null;
  board_name: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  step: string | null;
  proposal_id: string | null;
  error: string | null;
}

/** A way forward for a part with no KiCad symbol (an AI design stopped there; POST …/ai/design/resume). */
export interface DesignOption {
  id: string;
  kind: "replace" | "approach";
  title: string;
  detail: string;
  /** ai = the AI's alternative, library = close KiCad symbol, app = external-module header. */
  source: "ai" | "library" | "app";
  /** True: the part changes to a symbol checked in this library and the design continues from the saved plan. */
  executable: boolean;
  symbol_id: string | null;
}

export interface DesignChoice {
  ref: string;
  role: string;
  value: string;
  mpn: string;
  why: string;
  options: DesignOption[];
}

/** Details of an unreadable AI answer (error details of AI_BAD_OUTPUT). */
export interface AiFailure {
  finish_reason?: string | null;
  chars?: number;
  tail?: string;
  what?: string;
}

/** One round of the automatic review loop (POST /ai/auto-review). */
export interface AutoReviewRound {
  round: number;
  proposal_id: string;
  errors: number;
  warnings: number;
  check: string;
  check_failed: boolean;
  said: string[];
  repeated?: string[];
  revised_to?: string;
  fix?: { summary: string; diff: ProposalDiff["summary"] | null; method?: "edits" | "redesign" };
  unverified?: number;
  unverified_said?: string[];
  declined?: string[];
  /** Findings a person kept as designed: shown, not counted. */
  decided?: number;
  decided_said?: string[];
  /** A deep review: how many perspective passes ran, how many checklist answers, how many "cannot tell". */
  passes?: number;
  checks?: number;
  unknown?: number;
  unknown_said?: string[];
}

export interface AutoReviewResult {
  rounds: AutoReviewRound[];
  stop: "met" | "max_rounds" | "repeated" | "check_failed" | "revise_failed" | "ai_failed" | "no_progress" | "no_change" | "declined";
  message: string;
  /** Why the AI step failed, when known: how its answer ended and the answer's last characters. */
  why?: AiFailure | null;
  proposal_id: string;
  met: boolean;
  max_warnings: number;
  max_rounds: number;
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

/** One distributor's answer for a maker part number (stock, smallest price break, lifecycle). */
export interface BomOffer {
  source: string;
  manufacturer: string | null;
  part_number: string | null;
  distributor_part: string | null;
  stock: number | null;
  availability: string | null;
  price: number | null;
  price_qty: number | null;
  currency: string | null;
  lifecycle: string | null;
  product_url: string | null;
}

export interface BomAvailabilityLine {
  references: string;
  quantity: number;
  mpn: string;
  manufacturer: string;
  offers: BomOffer[];
  errors: { source: string; error_code: string; message: string }[];
}

export interface BomAvailability {
  result: { revision: number; checked_at: string; lines: BomAvailabilityLine[]; sources: string[] } | null;
  search: { mouser: boolean; digikey: boolean };
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
  status: "match" | "mismatch" | "missing_in_symbol" | "missing_in_datasheet" | "no_name" | "same_as";
  /** For a tab or repeated pin: the symbol pin it is the same signal as. */
  twin?: string | null;
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
  voltage_min?: number | null;
  voltage_max?: number | null;
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
  status: "present" | "short" | "value_differs" | "missing" | "direct" | "unchecked" | "spec_differs";
  reason: string;
  /** What the description requires (type, ESR) and how each found part compares. */
  spec?: { required: string; verdicts: { status: "ok" | "differs" | "unknown" | "none"; note: string }[] };
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
  net_specs?: { net: string; field: "voltage" | "voltage_min" | "voltage_max" | "load_current"; before: number | null; after: number | null }[];
}

export interface AiFinding {
  severity: "error" | "warning" | "info";
  title: string;
  detail: string;
  references: string[];
  suggestion: string | null;
  /** What the finding rests on (evidence number, guide, requirement, a pin or net, or judgment) and whether the app
   * could confirm it. Unconfirmed 수정 필요/권장 do not drive automatic revisions. */
  basis?: "evidence" | "guide" | "requirement" | "circuit" | "judgment";
  evidence?: string | null;
  verified?: boolean | null;
  /** A person decided to keep this as designed (the reason): not counted, not sent to a revision. */
  decided?: { id: string; reason: string } | null;
  /** The AI's reason for not fixing this in a revision (kept here because a refusal makes no new proposal). */
  ai_declined?: string | null;
  /** Which pass of a deep review raised it: general, or a perspective key (requirements, power, signals, control). */
  perspective?: string;
  perspectives?: string[];
}

export interface ReviewDecision {
  id: string;
  title: string;
  reason: string;
  proposal_id: string | null;
  by: string;
  at: string;
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
  review?: { findings: AiFinding[]; summary: string; model?: string; created_at?: string;
    /** deep = the perspective passes ran as well; ``passes`` is what each checked. */
    depth?: "deep"; passes?: ReviewPass[] } | null;
  parent?: string | null;
  instructions?: string | null;
  addressed?: { severity: string; title: string; detail: string; source: "ai" | "check" }[];
  diff_from_parent?: ProposalDiff | null;
  remaining_problems?: string[];
  /** edits = the revision edited the current circuit; redesign = it was designed again. */
  method?: "edits" | "redesign";
  changes?: string[];
  not_addressed?: { title: string; why: string }[];
  /** The app's guide self-check of the fresh design: guide ids violated before / after its edit pass. */
  guide_check?: { before: string[]; after: string[]; changes?: string[]; error?: string } | null;
  /** The design job's datasheet step: per part, whether its datasheet was there, downloaded, summarized. */
  datasheets?: DatasheetStep | null;
}

/** One answered checklist item of a perspective pass. `unknown` = the AI had no material to judge it. */
export interface ReviewCheck {
  item: string;
  result: "ok" | "issue" | "unknown" | "na";
  note: string;
  references: string[];
}

/** One perspective pass of a deep review: the checklist items it answered and the findings it raised. */
export interface ReviewPass {
  key: string;
  label: string;
  summary: string;
  asked: number;
  /** Checklist items that were asked and not answered. */
  skipped: string[];
  checks: ReviewCheck[];
  /** Titles of the review's findings this pass raised. */
  found: string[];
  error?: string;
}

export interface DatasheetStepPart {
  key: string;
  part: string;
  references: string[];
  datasheet: string | null;
  /** had = already in the library, downloaded, failed, no_source = nowhere to get it, off = automatic fetch off. */
  download: "had" | "downloaded" | "failed" | "no_source" | "off" | null;
  summary: "had" | "made" | "failed" | "skipped" | "off" | null;
  message: string | null;
  product_url?: string | null;
  reviewed?: boolean;
  pages_sent?: number[] | null;
  pages?: number | null;
}

export interface DatasheetStep {
  fetch: boolean;
  parts: DatasheetStepPart[];
  ready: number;
  downloaded: number;
  summarized: number;
  missing: number;
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
  /** Adopted design guides the AI will be given for these requirements. */
  guides?: { id: string; title: string }[];
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
  status: "fail" | "warn" | "ok" | "manual" | "exception";
  notes: string[];
  origin: GuideOrigin;
  ai_applied: boolean;
  /** For an exception: the design decision that does not follow this guide. */
  decision?: string;
}

export interface GuideChecklist {
  revision: number | null;
  proposal_id?: string | null;
  items: GuideChecklistItem[];
  candidates: number;
  ai_applied: string[];
}

// --- research notes (연구노트) ---------------------------------------------------------

export type NoteAuthor = "ai" | "person";

export interface NoteEvidence {
  id: string;
  kind: string;
  text: string;
}

export interface NoteLine {
  text: string;
  evidence: string[];
  by: NoteAuthor;
}

export interface NoteCheck {
  what: string;
  how: string;
  result: string;
  status: "ok" | "issue" | "unknown";
  evidence: string[];
  by: NoteAuthor;
}

export interface NoteApply extends NoteLine {
  why: string;
  guide_id: string | null;
}

export interface NoteDraft {
  title: string;
  summary: string;
  background: string;
  parts: string[];
  topics: string[];
  checks: NoteCheck[];
  learnings: NoteLine[];
  apply_next: NoteApply[];
  open_questions: NoteLine[];
}

export interface NoteContext {
  project_id: string | null;
  project_name: string | null;
  board_id: string | null;
  board_name: string | null;
  revision: number | null;
  proposal_id: string | null;
}

export interface ResearchNote extends NoteDraft {
  id: string;
  status: "draft" | "reviewed";
  author_kind: NoteAuthor;
  written_by: string;
  created_at: string;
  updated_at: string;
  version: number;
  edited_by: string[];
  reviewed_by: string | null;
  reviewed_at: string | null;
  context: NoteContext;
  evidence: NoteEvidence[];
}

export interface NoteSummary {
  id: string;
  title: string;
  summary: string;
  status: "draft" | "reviewed";
  author_kind: NoteAuthor;
  written_by: string;
  reviewed_by: string | null;
  created_at: string;
  updated_at: string;
  version: number;
  parts: string[];
  topics: string[];
  context: NoteContext;
  counts: { checks: number; learnings: number; apply_next: number; open_questions: number };
}

export interface NotePage {
  note: ResearchNote;
  markdown: string;
  history: { at: string; event: string; by: string }[];
}

// --- AI performance evaluation ---------------------------------------------------------

export type EvalSuite = "design" | "review" | "revise" | "notes";

export interface EvalCases {
  design: { id: string; title: string; example: string; checks: string[] }[];
  review: { id: string; title: string; example: string }[];
  revise?: { id: string; title: string; example: string }[];
  notes: { id: string; example: string }[];
  calls_per_repeat: Record<EvalSuite, number>;
  provider: AiProvider;
  model: string;
  enabled: boolean;
  kicad: boolean;
  metrics: Record<string, string>;
}

export type EvalSummary = Record<string, Record<string, number | null>>;

export interface EvalRunInfo {
  id: string;
  created_at: string;
  provider: string;
  model: string;
  suites: EvalSuite[];
  repeats: number;
  summary: EvalSummary;
  app_version: string;
  kicad: boolean;
}

export interface EvalChange {
  key: string;
  label: string;
  kind: "rate" | "count" | "number";
  new: number | null;
  old: number | null;
  better: boolean | null;
}

export interface EvalRunPage {
  run: EvalRunInfo & { results: Record<string, unknown>[] };
  previous_id: string | null;
  changes: EvalChange[];
  markdown: string;
}
