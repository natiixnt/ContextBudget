/**
 * TypeScript interfaces matching Redcon Python data models.
 * Mirrors redcon/schemas/models.py for type-safe CLI output parsing.
 */

// --- Core file models ---

export interface FileRecord {
  path: string;
  absolute_path: string;
  extension: string;
  size_bytes: number;
  line_count: number;
  content_hash: string;
  content_preview: string;
  symbol_names?: string;
  relative_path?: string;
  repo_label?: string;
  repo_root?: string;
}

export interface RankedFileJson {
  path: string;
  score: number;
  heuristic_score: number;
  historical_score: number;
  reasons: string[];
  line_count: number;
  repo?: string;
  relative_path?: string;
}

export interface CompressedFileJson {
  path: string;
  strategy: CompressionStrategy;
  original_tokens: number;
  compressed_tokens: number;
  text: string;
  chunk_strategy: string;
  chunk_reason: string;
  selected_ranges: SelectedRange[];
  symbols: SymbolInfo[];
  cache_reference: string;
  cache_status: string;
  relative_path?: string;
  repo?: string;
}

export type CompressionStrategy =
  | 'full'
  | 'snippet'
  | 'symbol_extraction'
  | 'summary'
  | 'slicing'
  | 'cache_reuse';

export interface SelectedRange {
  start: number;
  end: number;
  type: string;
}

export interface SymbolInfo {
  name: string;
  kind: string;
  start: number;
  end: number;
  exported: boolean;
}

// --- Report models ---

export interface BudgetReport {
  max_tokens: number;
  estimated_input_tokens: number;
  estimated_saved_tokens: number;
  duplicate_reads_prevented: number;
  quality_risk_estimate: 'low' | 'medium' | 'high';
}

export interface CacheReport {
  backend: string;
  enabled: boolean;
  hits: number;
  misses: number;
  writes: number;
  tokens_saved: number;
  fragment_hits: number;
  fragment_misses: number;
  fragment_writes: number;
  slice_hits: number;
  slice_misses: number;
  slice_writes: number;
}

export interface SummarizerReport {
  selected_backend: string;
  external_adapter: string;
  effective_backend: string;
  external_configured: boolean;
  external_resolved: boolean;
  fallback_used: boolean;
  fallback_count: number;
  summary_count: number;
  logs: string[];
}

export interface TokenEstimatorReport {
  selected_backend: string;
  effective_backend: string;
  uncertainty: 'approximate' | 'exact';
  model: string;
  encoding: string;
  available: boolean;
  fallback_used: boolean;
  fallback_reason: string;
  notes: string[];
}

export interface ModelProfileReport {
  selected_profile: string;
  resolved_profile: string;
  family: string;
  tokenizer: string;
  context_window: number;
  recommended_compression_strategy: string;
  effective_max_tokens: number;
  reserved_output_tokens: number;
  budget_source: string;
  budget_clamped: boolean;
  notes: string[];
}

// --- Run report (main artifact) ---

export interface RunReport {
  command: string;
  task: string;
  repo: string;
  max_tokens: number;
  ranked_files: RankedFileJson[];
  compressed_context: CompressedFileJson[];
  files_included: string[];
  files_skipped: string[];
  budget: BudgetReport;
  cache: CacheReport;
  summarizer: SummarizerReport;
  token_estimator: TokenEstimatorReport;
  cache_hits: number;
  generated_at: string;
  model_profile?: ModelProfileReport;
  workspace?: string;
  scanned_repos?: Record<string, unknown>[];
  selected_repos?: string[];
  implementations?: Record<string, string>;
  delta?: Record<string, unknown>;
  degraded_files?: string[];
  degradation_savings?: number;
}

// --- Agent plan models ---

export interface AgentPlanContextFile {
  path: string;
  score: number;
  estimated_tokens: number;
  reasons: string[];
  line_count: number;
  source: string;
  relative_path?: string;
  repo?: string;
  reuse_count: number;
  step_ids: string[];
}

export interface AgentPlanStep {
  id: string;
  title: string;
  objective: string;
  planning_prompt: string;
  context: AgentPlanContextFile[];
  estimated_tokens: number;
  shared_context_tokens: number;
  step_context_tokens: number;
}

export interface AgentPlanReport {
  command: string;
  task: string;
  repo: string;
  scanned_files: number;
  ranked_files: RankedFileJson[];
  steps: AgentPlanStep[];
  shared_context: AgentPlanContextFile[];
  total_estimated_tokens: number;
  unique_context_tokens: number;
  reused_context_tokens: number;
  generated_at: string;
  workspace?: string;
  token_estimator?: TokenEstimatorReport;
  model_profile?: ModelProfileReport;
}

// --- Simulation ---

export interface SimulationStepCost {
  title: string;
  input_cost_usd: number;
  output_cost_usd: number;
  total_cost_usd: number;
}

export interface SimulationCostEstimate {
  model: string;
  provider: string;
  input_per_1m_usd: number;
  output_per_1m_usd: number;
  total_cost_usd: number;
  total_input_cost_usd: number;
  total_output_cost_usd: number;
  steps_cost: SimulationStepCost[];
}

export interface SimulationStep {
  title: string;
  context_tokens: number;
  step_total_tokens: number;
  cumulative_context_tokens: number;
}

export interface SimulationReport {
  context_mode: string;
  model: string;
  steps: SimulationStep[];
  cost_estimate: SimulationCostEstimate;
  total_tokens: number;
  token_variance: number;
  token_std_dev: number;
  min_step_tokens: number;
  avg_step_tokens: number;
  max_step_tokens: number;
}

// --- Benchmark ---

export interface BenchmarkStrategy {
  strategy: string;
  estimated_input_tokens: number;
  estimated_saved_tokens: number;
  files_included: string[];
  quality_risk_estimate: string;
  runtime_ms: number;
}

export interface BenchmarkReport {
  strategies: BenchmarkStrategy[];
  model_profile?: ModelProfileReport;
  token_estimator?: TokenEstimatorReport;
}

// --- Doctor ---

export interface DoctorCheck {
  name: string;
  status: 'ok' | 'warn' | 'fail';
  message: string;
  detail?: string;
}

export interface DoctorReport {
  redcon_version: string;
  python_version: string;
  platform: string;
  checks: DoctorCheck[];
  passed: number;
  warnings: number;
  failures: number;
}

// --- Pipeline ---

export interface PipelineStage {
  label: string;
  name: string;
  files_in: number;
  tokens_in: number;
  tokens_out: number;
  tokens_saved: number;
  reduction_pct: number;
  is_optimisation: boolean;
}

export interface PipelineReport {
  task: string;
  repo: string;
  stages: PipelineStage[];
  final_tokens: number;
  total_tokens_saved: number;
  total_reduction_pct: number;
}

// --- Drift ---

export interface DriftReport {
  task_filter: string;
  window: number;
  threshold: number;
  entries_analyzed: number;
  drift_detected: boolean;
  drift_pct: number;
  mean_tokens: number;
  latest_tokens: number;
  trend: string;
  message: string;
}

// --- Extension state ---

export interface RedconState {
  lastRun: RunReport | null;
  lastPlan: AgentPlanReport | null;
  lastSimulation: SimulationReport | null;
  lastDoctor: DoctorReport | null;
  lastBenchmark: BenchmarkReport | null;
  lastTask: string;
  isRunning: boolean;
  runHistory: RunHistoryEntry[];
}

export interface RunHistoryEntry {
  path: string;
  task: string;
  tokens: number;
  maxTokens: number;
  filesIncluded: number;
  generatedAt: string;
  risk: string;
}
