"use client";

import { useMemo } from "react";
import { useData } from "@/hooks/useData";
import MetricCard from "@/components/MetricCard";
import HeatmapTable from "@/components/HeatmapTable";

export default function HeatmapPage() {
  const { data, loading } = useData();
  const { heatmap, run_trend } = data;

  const files = useMemo(
    () => heatmap.top_token_heavy_files ?? heatmap.files ?? [],
    [heatmap]
  );

  // Context drift: compare earliest vs latest run token usage
  const drift = useMemo(() => {
    if (run_trend.length < 2) return null;
    const earliest = run_trend[0].input_tokens;
    const latest = run_trend[run_trend.length - 1].input_tokens;
    if (!earliest) return null;
    return ((latest - earliest) / earliest) * 100;
  }, [run_trend]);

  const totalCompressed = files.reduce((s, f) => s + (f.total_compressed_tokens || 0), 0);
  const totalOriginal = files.reduce((s, f) => s + (f.total_original_tokens || 0), 0);
  const compressionRatio = totalOriginal > 0 ? (1 - totalCompressed / totalOriginal) : 0;

  const avgInclusionRate =
    files.length > 0
      ? files.reduce((s, f) => s + (f.inclusion_rate || 0), 0) / files.length
      : 0;

  function fmtTok(n: number) {
    if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
    if (n >= 1_000) return (n / 1_000).toFixed(0) + "k";
    return String(n);
  }

  return (
    <div>
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-slate-900">Heatmap</h1>
        <p className="text-slate-500 text-sm mt-1">
          File-level token consumption across pack runs.
          {!data.connected && !loading && (
            <span className="text-amber-600 font-medium ml-1">
              No live data — run <code className="font-mono bg-amber-50 px-1 rounded">redcon dashboard</code>.
            </span>
          )}
        </p>
      </div>

      {/* Summary */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-8">
        <MetricCard label="Files Tracked" value={files.length.toLocaleString()} color="blue" />
        <MetricCard
          label="Compression Ratio"
          value={(compressionRatio * 100).toFixed(1) + "%"}
          color={compressionRatio > 0.3 ? "green" : "default"}
          sub="original → compressed"
        />
        <MetricCard
          label="Avg Inclusion Rate"
          value={(avgInclusionRate * 100).toFixed(1) + "%"}
          sub="across tracked files"
        />
        <MetricCard
          label="Context Drift"
          value={
            drift === null
              ? "—"
              : (drift > 0 ? "+" : "") + drift.toFixed(1) + "%"
          }
          color={drift === null ? "default" : drift > 20 ? "red" : drift > 5 ? "amber" : "green"}
          sub="token usage, first → latest"
        />
      </div>

      {/* Drift Details */}
      {run_trend.length >= 2 && (
        <section className="mb-8">
          <h2 className="text-base font-semibold text-slate-800 mb-3 pb-2 border-b border-slate-200">
            Context Drift
          </h2>
          <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
            <p className="text-sm text-slate-600 mb-4">
              Context drift measures how token consumption has changed over time. A positive value means
              agents are pulling in more context per run; negative means tighter scoping.
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              <div className="text-center">
                <div className="text-xs text-slate-400 mb-1">First Run</div>
                <div className="text-xl font-bold text-slate-800">
                  {fmtTok(run_trend[0].input_tokens)}
                </div>
                <div className="text-xs text-slate-400">{run_trend[0].date}</div>
              </div>
              <div className="text-center flex flex-col items-center justify-center">
                <div
                  className={`text-2xl font-bold ${
                    drift === null ? "text-slate-400" : drift > 10 ? "text-red-500" : drift > 0 ? "text-amber-500" : "text-emerald-500"
                  }`}
                >
                  {drift === null ? "—" : (drift > 0 ? "+" : "") + drift.toFixed(1) + "%"}
                </div>
                <div className="text-xs text-slate-400 mt-1">drift</div>
              </div>
              <div className="text-center">
                <div className="text-xs text-slate-400 mb-1">Latest Run</div>
                <div className="text-xl font-bold text-slate-800">
                  {fmtTok(run_trend[run_trend.length - 1].input_tokens)}
                </div>
                <div className="text-xs text-slate-400">{run_trend[run_trend.length - 1].date}</div>
              </div>
            </div>
          </div>
        </section>
      )}

      {/* Heatmap Table */}
      <section>
        <h2 className="text-base font-semibold text-slate-800 mb-3 pb-2 border-b border-slate-200">
          Token-Heavy Files
        </h2>
        <HeatmapTable files={files} />
      </section>
    </div>
  );
}
