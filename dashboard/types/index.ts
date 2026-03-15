export interface Summary {
  total_runs: number;
  pack_runs: number;
  sim_runs: number;
  benchmark_runs: number;
  total_input_tokens: number;
  total_saved_tokens: number;
  savings_rate: number;
}

export interface RunHistoryEntry {
  command: string;
  task: string;
  generated_at: string;
  artifact: string;
  input_tokens: number;
  saved_tokens: number;
  files: number;
  risk: string;
  source: string;
  cost_usd?: number;
}

export interface TokenChartEntry {
  label: string;
  input_tokens: number;
  saved_tokens: number;
}

export interface RunTrendEntry {
  date: string;
  label: string;
  input_tokens: number;
  saved_tokens: number;
}

export interface SavingsBreakdownEntry {
  label: string;
  used: number;
  saved: number;
}

export interface HeatmapFile {
  path: string;
  total_compressed_tokens: number;
  total_original_tokens: number;
  total_saved_tokens: number;
  inclusion_count: number;
  inclusion_rate: number;
}

export interface HeatmapData {
  top_token_heavy_files?: HeatmapFile[];
  files?: HeatmapFile[];
  total_runs?: number;
  total_files_seen?: number;
}

export interface SimulationEntry {
  task: string;
  model: string;
  total_tokens: number;
  steps: number;
  context_mode: string;
  cost_usd: number;
  generated_at: string;
}

export interface BenchmarkStrategy {
  strategy: string;
  input_tokens: number;
  saved_tokens: number;
  risk: string;
  runtime_ms: number;
}

export interface BenchmarkEntry {
  task: string;
  baseline_tokens: number;
  generated_at: string;
  strategies: BenchmarkStrategy[];
}

export interface DashboardData {
  summary: Summary;
  run_history: RunHistoryEntry[];
  token_chart: TokenChartEntry[];
  run_trend: RunTrendEntry[];
  savings_breakdown: SavingsBreakdownEntry[];
  heatmap: HeatmapData;
  simulations: SimulationEntry[];
  benchmarks: BenchmarkEntry[];
  connected: boolean;
}
