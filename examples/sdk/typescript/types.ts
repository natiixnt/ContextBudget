/**
 * ContextBudget TypeScript type definitions.
 *
 * These types mirror the Python SDK's run artifact schema so TypeScript agents
 * get full type safety when consuming ContextBudget output.
 */

// ---------------------------------------------------------------------------
// Shared building blocks
// ---------------------------------------------------------------------------

export interface BudgetBlock {
  max_tokens: number;
  estimated_input_tokens: number;
  estimated_saved_tokens: number;
  duplicate_reads_prevented: number;
  quality_risk_estimate: "low" | "medium" | "high" | "unknown";
}

export interface CacheBlock {
  backend: string;
  enabled: boolean;
  hits: number;
  misses: number;
  writes: number;
  tokens_saved: number;
}

export interface CompressedEntry {
  path: string;
  strategy: string;
  original_tokens: number;
  compressed_tokens: number;
  text: string;
  chunk_strategy: string;
  cache_status: "hit" | "miss" | "skip";
}

export interface RankedFile {
  path: string;
  score: number;
  heuristic_score: number;
  historical_score: number;
  reasons: string[];
}

export interface PolicyResult {
  passed: boolean;
  violations: string[];
  checks: Record<string, boolean>;
}

// ---------------------------------------------------------------------------
// prepare_context / pack result
// ---------------------------------------------------------------------------

export interface PrepareContextResult {
  command: string;
  task: string;
  repo: string;
  workspace?: string;
  max_tokens: number;
  ranked_files: RankedFile[];
  compressed_context: CompressedEntry[];
  files_included: string[];
  files_skipped: string[];
  budget: BudgetBlock;
  cache: CacheBlock;
  policy?: PolicyResult;
  agent_middleware: {
    request: {
      task: string;
      repo: string;
      max_tokens: number;
      metadata: Record<string, unknown>;
    };
    metadata: {
      estimated_input_tokens: number;
      estimated_saved_tokens: number;
      files_included_count: number;
      files_removed_count: number;
      files_skipped_count: number;
      quality_risk_estimate: string;
      delta_enabled: boolean;
      cache: CacheBlock;
    };
    recorded_path?: string;
    adapter?: string;
  };
}

// ---------------------------------------------------------------------------
// simulate_agent result
// ---------------------------------------------------------------------------

export interface SimulationStep {
  id: string;
  context_tokens: number;
  prompt_overhead: number;
  output_tokens: number;
  step_total_tokens: number;
  cumulative_tokens: number;
  input_cost_usd: number;
  output_cost_usd: number;
  step_cost_usd: number;
}

export interface CostEstimate {
  total_input_tokens: number;
  total_output_tokens: number;
  total_tokens: number;
  total_cost_usd: number;
  price_per_1m_input: number;
  price_per_1m_output: number;
}

export interface SimulateAgentResult {
  task: string;
  repo: string;
  model: string;
  context_mode: string;
  total_tokens: number;
  steps: SimulationStep[];
  cost_estimate: CostEstimate;
}

// ---------------------------------------------------------------------------
// profile_run result
// ---------------------------------------------------------------------------

export interface ProfileBlock {
  elapsed_ms: number;
  estimated_input_tokens: number;
  estimated_saved_tokens: number;
  compression_ratio: number;
  files_included_count: number;
  files_skipped_count: number;
  quality_risk_estimate: string;
}

export interface ProfileRunResult extends PrepareContextResult {
  profile: ProfileBlock;
}

// ---------------------------------------------------------------------------
// SDK options
// ---------------------------------------------------------------------------

export interface SDKOptions {
  /** Path to the contextbudget Python binary (default: "contextbudget") */
  pythonBin?: string;
  /** Default token budget for pack operations */
  maxTokens?: number;
  /** Maximum ranked files to consider */
  topFiles?: number;
}

export interface PrepareContextOptions {
  workspace?: string;
  maxTokens?: number;
  topFiles?: number;
  deltaFrom?: string;
  metadata?: Record<string, unknown>;
  configPath?: string;
}

export interface SimulateAgentOptions {
  workspace?: string;
  model?: string;
  topFiles?: number;
  pricePerMillionInput?: number;
  pricePerMillionOutput?: number;
  configPath?: string;
}

export interface ProfileRunOptions {
  workspace?: string;
  maxTokens?: number;
  topFiles?: number;
  configPath?: string;
}
