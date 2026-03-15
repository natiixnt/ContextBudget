/**
 * Shared type definitions for the Redcon SDK.
 */

// ── Cloud API types ──────────────────────────────────────────────────────────

export interface OrgCreate {
  slug: string;
  display_name?: string;
}

export interface OrgResponse {
  id: number;
  slug: string;
  display_name: string | null;
  created_at: string;
}

export interface ProjectCreate {
  slug: string;
  display_name?: string;
}

export interface ProjectResponse {
  id: number;
  org_id: number;
  slug: string;
  display_name: string | null;
  created_at: string;
}

export interface RepoCreate {
  slug: string;
  display_name?: string;
  repository_id?: string;
}

export interface RepoResponse {
  id: number;
  project_id: number;
  slug: string;
  display_name: string | null;
  repository_id: string | null;
  created_at: string;
}

export interface ApiKeyCreate {
  label?: string;
  expires_at?: string;
}

export interface ApiKeyIssued {
  id: number;
  org_id: number;
  label: string | null;
  raw_key: string;
  created_at: string;
  expires_at: string | null;
}

export interface ApiKeyResponse {
  id: number;
  org_id: number;
  label: string | null;
  created_at: string;
  expires_at: string | null;
  revoked: boolean;
}

export interface IngestEvent {
  event_name: string;
  schema_version?: string;
  run_id?: string;
  repository_id?: string;
  command?: string;
  estimated_input_tokens?: number;
  estimated_saved_tokens?: number;
  tokens_saved_by_cache?: number;
  baseline_full_context_tokens?: number;
  cache_hit?: boolean;
  policy_passed?: boolean;
  [key: string]: unknown;
}

export interface IngestResponse {
  accepted: number;
  event_ids: number[];
}

export interface PolicyVersionCreate {
  version: string;
  spec: Record<string, unknown>;
  project_id?: number;
  repo_id?: number;
}

export interface PolicyVersionResponse {
  id: number;
  org_id: number;
  version: string;
  spec: Record<string, unknown>;
  active: boolean;
  created_at: string;
  project_id: number | null;
  repo_id: number | null;
}

export interface WebhookCreate {
  url: string;
  secret?: string;
  events?: string[];
}

export interface WebhookResponse {
  id: number;
  org_id: number;
  url: string;
  events: string[];
  active: boolean;
  created_at: string;
}

export interface AuditEntry {
  id: number;
  org_id: number;
  repository_id: string | null;
  run_id: string | null;
  task_hash: string | null;
  endpoint: string | null;
  policy_version: string | null;
  tokens_used: number;
  tokens_saved: number;
  violation_count: number;
  policy_passed: boolean | null;
  status_code: number | null;
  created_at: string;
}

export interface AuditLogResponse {
  entries: AuditEntry[];
  total: number;
}

// ── Analytics types ──────────────────────────────────────────────────────────

export interface CostSummaryResponse {
  run_count: number;
  total_baseline_tokens: number;
  total_optimized_tokens: number;
  total_tokens_saved: number;
  overall_savings_rate: number | null;
  total_tokens_saved_by_cache: number;
  cache_hit_run_count: number;
}

export interface CostByRepoRow {
  repository_id: string;
  run_count: number;
  total_baseline_tokens: number;
  total_optimized_tokens: number;
  total_tokens_saved: number;
  savings_rate: number | null;
  total_tokens_saved_by_cache: number;
}

export interface ROIRepoRow {
  repository_id: string;
  tokens_used: number;
  tokens_saved: number;
  baseline_tokens: number;
  run_count: number;
  savings_rate: number | null;
  dollars_saved: number;
}

export interface DashboardROI {
  total_tokens_used: number;
  total_tokens_saved: number;
  total_baseline_tokens: number;
  savings_rate: number | null;
  estimated_dollars_saved: number;
  cache_hit_rate_pct: number | null;
  total_runs: number;
  runs_with_cache_hits: number;
  price_per_1m_tokens: number;
  top_repos: ROIRepoRow[];
  note: string;
}

export interface QuotaConfig {
  token_allowance_monthly: number | null;
  event_allowance_monthly: number | null;
}

// ── Gateway types ─────────────────────────────────────────────────────────────

export interface PrepareContextRequest {
  task: string;
  repo?: string;
  workspace?: string;
  max_tokens?: number;
  top_files?: number;
  max_files?: number;
  max_context_size?: number;
  delta_from?: string;
  config_path?: string;
  session_id?: string;
  metadata?: Record<string, unknown>;
}

export interface OptimizedFile {
  path: string;
  strategy: string;
  original_tokens: number;
  compressed_tokens: number;
  text: string;
}

export interface PolicyStatus {
  passed: boolean;
  violations: string[];
}

export interface OptimizedContext {
  files: OptimizedFile[];
  prompt_text: string;
  files_included: string[];
}

export interface PrepareContextResponse {
  optimized_context: OptimizedContext;
  token_estimate: number;
  policy_status: PolicyStatus;
  run_id: string;
  session_id: string;
  cache_hits: number;
  quality_risk: string;
  tokens_saved: number;
}

export interface RunAgentStepRequest extends PrepareContextRequest {
  session_id?: string;
}

export interface RunAgentStepResponse extends PrepareContextResponse {
  turn: number;
  session_tokens: number;
  llm_response: string | null;
}

export interface ReportRunRequest {
  session_id: string;
  run_id: string;
  status: "success" | "failure" | "timeout" | "cancelled";
  tokens_used?: number;
  metadata?: Record<string, unknown>;
}

export interface ReportRunResponse {
  acknowledged: boolean;
  session_id: string;
  run_id: string;
}

// ── SDK options ───────────────────────────────────────────────────────────────

export interface CloudClientOptions {
  /** Base URL of the redcon-cloud service, e.g. https://cloud.example.com */
  baseUrl: string;
  /** Bearer API key (rck_...) */
  apiKey: string;
  /** Request timeout in milliseconds (default: 30000) */
  timeoutMs?: number;
}

export interface GatewayClientOptions {
  /** Base URL of the redcon gateway, e.g. http://localhost:8787 */
  baseUrl: string;
  /** Optional Bearer API key when gateway auth is enabled */
  apiKey?: string;
  /** Request timeout in milliseconds (default: 30000) */
  timeoutMs?: number;
}
