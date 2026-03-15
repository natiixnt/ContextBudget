"use client";

import { useState } from "react";
import { useData } from "@/hooks/useData";
import MetricCard from "@/components/MetricCard";

function riskBadge(risk: string) {
  const map: Record<string, string> = {
    low: "bg-emerald-100 text-emerald-800",
    medium: "bg-amber-100 text-amber-800",
    high: "bg-red-100 text-red-800",
  };
  return map[risk] || "bg-slate-100 text-slate-600";
}

export default function AgentRunsPage() {
  const { data, loading } = useData();
  const { simulations, benchmarks, run_history } = data;
  const [simFilter, setSimFilter] = useState("");
  const [benchFilter, setBenchFilter] = useState("");

  const simRows = simulations.filter((s) => {
    if (!simFilter) return true;
    const q = simFilter.toLowerCase();
    return s.task.toLowerCase().includes(q) || s.model.toLowerCase().includes(q);
  });

  const planRuns = run_history.filter(
    (r) => r.command === "plan" || r.command === "plan-agent"
  );

  const totalCost = simulations.reduce((s, r) => s + (r.cost_usd || 0), 0);
  const totalSteps = simulations.reduce((s, r) => s + r.steps, 0);
  const avgCostPerRun = simulations.length ? totalCost / simulations.length : 0;

  return (
    <div>
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-slate-900">Agent Runs</h1>
        <p className="text-slate-500 text-sm mt-1">
          Simulation, plan, and benchmark runs across agent workflows.
          {!data.connected && !loading && (
            <span className="text-amber-600 font-medium ml-1">
              No live data — run <code className="font-mono bg-amber-50 px-1 rounded">redcon dashboard</code>.
            </span>
          )}
        </p>
      </div>

      {/* Summary */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-8">
        <MetricCard label="Simulation Runs" value={simulations.length} color="blue" />
        <MetricCard label="Benchmark Runs" value={benchmarks.length} />
        <MetricCard label="Plan Runs" value={planRuns.length} />
        <MetricCard
          label="Total Cost"
          value={`$${totalCost.toFixed(4)}`}
          color="amber"
          sub={`avg $${avgCostPerRun.toFixed(4)} / run`}
        />
        <MetricCard label="Total Steps" value={totalSteps.toLocaleString()} />
      </div>

      {/* Simulation Runs Table */}
      <section className="mb-8">
        <h2 className="text-base font-semibold text-slate-800 mb-3 pb-2 border-b border-slate-200">
          Simulation Runs
        </h2>
        <div className="mb-3">
          <input
            type="text"
            placeholder="Filter by task or model…"
            value={simFilter}
            onChange={(e) => setSimFilter(e.target.value)}
            className="border border-slate-200 rounded-lg px-3 py-2 text-sm w-72 focus:outline-none focus:ring-2 focus:ring-accent/50"
          />
        </div>
        {simRows.length === 0 ? (
          <div className="rounded-xl border border-slate-200 bg-white p-10 text-center">
            <p className="text-slate-400 text-sm">
              {simulations.length === 0
                ? "No simulation runs found. Run redcon simulate-agent."
                : "No results for current filter."}
            </p>
          </div>
        ) : (
          <div className="overflow-x-auto rounded-xl border border-slate-200 shadow-sm">
            <table className="w-full text-sm bg-white">
              <thead className="bg-slate-50 border-b border-slate-200">
                <tr>
                  {["Task", "Model", "Total Tokens", "Steps", "Context Mode", "Cost (USD)", "Date"].map((h) => (
                    <th key={h} className="px-3 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-slate-500 whitespace-nowrap">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {simRows.map((s, i) => (
                  <tr key={i} className="border-b border-slate-100 last:border-0 hover:bg-slate-50">
                    <td className="px-3 py-2.5 text-slate-700 max-w-xs truncate" title={s.task}>{s.task || "—"}</td>
                    <td className="px-3 py-2.5 font-mono text-xs text-slate-600">{s.model || "—"}</td>
                    <td className="px-3 py-2.5 tabular-nums text-right text-blue-600 font-medium">{s.total_tokens.toLocaleString()}</td>
                    <td className="px-3 py-2.5 tabular-nums text-right">{s.steps}</td>
                    <td className="px-3 py-2.5 text-slate-500">{s.context_mode || "—"}</td>
                    <td className="px-3 py-2.5 tabular-nums text-right text-amber-700">${s.cost_usd?.toFixed(4) ?? "—"}</td>
                    <td className="px-3 py-2.5 text-slate-400 text-xs whitespace-nowrap">
                      {s.generated_at ? s.generated_at.slice(0, 16).replace("T", " ") : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Benchmark Comparisons */}
      <section className="mb-8">
        <h2 className="text-base font-semibold text-slate-800 mb-3 pb-2 border-b border-slate-200">
          Benchmark Comparisons
        </h2>
        <div className="mb-3">
          <input
            type="text"
            placeholder="Filter by task…"
            value={benchFilter}
            onChange={(e) => setBenchFilter(e.target.value)}
            className="border border-slate-200 rounded-lg px-3 py-2 text-sm w-72 focus:outline-none focus:ring-2 focus:ring-accent/50"
          />
        </div>
        {benchmarks.length === 0 ? (
          <div className="rounded-xl border border-slate-200 bg-white p-10 text-center">
            <p className="text-slate-400 text-sm">No benchmarks found. Run redcon benchmark.</p>
          </div>
        ) : (
          <div className="space-y-5">
            {benchmarks
              .filter((b) => !benchFilter || b.task.toLowerCase().includes(benchFilter.toLowerCase()))
              .map((b, i) => (
                <div key={i} className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
                  <div className="px-5 py-3 bg-slate-50 border-b border-slate-200 flex items-baseline gap-3">
                    <span className="font-semibold text-slate-800">{b.task || "Benchmark"}</span>
                    <span className="text-xs text-slate-400">baseline: {b.baseline_tokens.toLocaleString()} tokens</span>
                    <span className="text-xs text-slate-400 ml-auto">{b.generated_at?.slice(0, 10)}</span>
                  </div>
                  <table className="w-full text-sm">
                    <thead className="border-b border-slate-100">
                      <tr>
                        {["Strategy", "Input Tokens", "Saved", "Risk", "Runtime"].map((h) => (
                          <th key={h} className="px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {b.strategies.map((s, j) => (
                        <tr key={j} className="border-b border-slate-100 last:border-0 hover:bg-slate-50">
                          <td className="px-4 py-2.5 font-mono text-xs text-slate-700">{s.strategy}</td>
                          <td className="px-4 py-2.5 tabular-nums text-blue-600 font-medium">{s.input_tokens.toLocaleString()}</td>
                          <td className="px-4 py-2.5 tabular-nums text-emerald-600 font-medium">{s.saved_tokens.toLocaleString()}</td>
                          <td className="px-4 py-2.5">
                            {s.risk ? (
                              <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-semibold ${riskBadge(s.risk)}`}>
                                {s.risk}
                              </span>
                            ) : "—"}
                          </td>
                          <td className="px-4 py-2.5 tabular-nums text-slate-400">{s.runtime_ms ? `${s.runtime_ms} ms` : "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ))}
          </div>
        )}
      </section>
    </div>
  );
}
