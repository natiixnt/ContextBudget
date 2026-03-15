"use client";

import { useEffect, useState } from "react";
import type { DashboardData } from "@/types";

const EMPTY_DATA: DashboardData = {
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

export function useData() {
  const [data, setData] = useState<DashboardData>(EMPTY_DATA);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const res = await fetch("/api/data");
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const json: DashboardData = await res.json();
        if (!cancelled) {
          setData(json);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) setError(String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    load();
    const interval = setInterval(load, 30_000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  return { data, loading, error };
}
