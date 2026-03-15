import { NextResponse } from "next/server";

const EMPTY: Record<string, unknown> = {
  summary: {
    total_runs: 0,
    pack_runs: 0,
    sim_runs: 0,
    benchmark_runs: 0,
    total_input_tokens: 0,
    total_saved_tokens: 0,
    savings_rate: 0,
  },
  run_history: [],
  token_chart: [],
  run_trend: [],
  savings_breakdown: [],
  heatmap: {},
  simulations: [],
  benchmarks: [],
  connected: false,
};

export async function GET() {
  const apiUrl = process.env.CONTEXTBUDGET_API_URL ?? "http://localhost:7842";
  try {
    const res = await fetch(`${apiUrl}/api/data`, {
      next: { revalidate: 15 },
    });
    if (!res.ok) return NextResponse.json({ ...EMPTY });
    const data = await res.json();
    return NextResponse.json({ ...data, connected: true });
  } catch {
    return NextResponse.json({ ...EMPTY });
  }
}
