"use client";

import { useState } from "react";
import { useData } from "@/hooks/useData";
import MetricCard from "@/components/MetricCard";

function riskBadge(risk: string) {
  const map: Record<string, string> = {
    low: "bg-emerald-900/50 text-emerald-400",
    medium: "bg-amber-900/50 text-amber-400",
    high: "bg-red-900/50 text-red-400",
  };
  return map[risk] || "bg-white/10 text-white/60";
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

  const planRuns = run_history.filter((r) => r.command === "plan" || r.command === "plan-agent");
  const totalCost = simulations.reduce((s, r) => s + (r.cost_usd || 0), 0);
  const totalSteps = simulations.reduce((s, r) => s + r.steps, 0);
  const avgCostPerRun = simulations.length ? totalCost / simulations.length : 0;

  return (
    <div>
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-white">Agent Runs</h1>
        <p className="text-white/50 text-sm mt-1">
          Simulation, plan, and benchmark runs across agent workflows.
          {!data.connected && !loading && (
            <span className="text-amber-400 font-medium ml-1">
              No live data - run <code className="font-mono bg-white/5 px-1 rounded">redcon dashboard</code>.
            </span>
          )}
        </p>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-8">
        <MetricCard label="Simulation Runs" value={simulations.length} color="blue" />
        <MetricCard label="Benchmark Runs" value={benchmarks.length} />
        <MetricCard label="Plan Runs" value={planRuns.length} />
        <MetricCard label="Total Cost" value={`$${totalCost.toFixed(4)}`} color="amber" sub={`avg $${avgCostPerRun.toFixed(4)} / run`} />
        <MetricCard label="Total Steps" value={totalSteps.toLocaleString()} />
      </div>

      <section className="mb-8">
        <h2 className="text-base font-semibold text-white mb-3 pb-2 border-b border-white/10">Simulation Runs</h2>
        <div className="mb-3">
          <input
            type="text"
            placeholder="Filter by task or model..."
            value={simFilter}
            onChange={(e) => setSimFilter(e.target.value)}
            className="bg-card border border-white/10 rounded-lg px-3 py-2 text-sm text-white placeholder-white/30 w-72 focus:outline-none focus:ring-2 focus:ring-accent/50"
          />
        </div>
        {simRows.length === 0 ? (
          <div className="rounded-xl border border-white/10 bg-card p-10 text-center">
            <p className="text-white/30 text-sm">
              {simulations.length === 0 ? "No simulation runs found. Run redcon simulate-agent." : "No results for current filter."}
            </p>
          </div>
        ) : (
          <div className="overflow-x-auto rounded-xl border border-white/10">
            <table className="w-full text-sm bg-card">
              <thead className="border-b border-white/10">
                <tr>
                  {["Task", "Model", "Total Tokens", "Steps", "Context Mode", "Cost (USD)", "Date"].map((h) => (
                    <th key={h} className="px-3 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-white/40 whitespace-nowrap">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {simRows.map((s, i) => (
                  <tr key={i} className="border-b border-white/5 last:border-0 hover:bg-white/5">
                    <td className="px-3 py-2.5 text-white/70 max-w-xs truncate" title={s.task}>{s.task || "-"}</td>
                    <td className="px-3 py-2.5 font-mono text-xs text-white/50">{s.model || "-"}</td>
                    <td className="px-3 py-2.5 tabular-nums text-right text-accent font-medium">{s.total_tokens.toLocaleString()}</td>
                    <td className="px-3 py-2.5 tabular-nums text-right text-white/70">{s.steps}</td>
                    <td className="px-3 py-2.5 text-white/50">{s.context_mode || "-"}</td>
                    <td className="px-3 py-2.5 tabular-nums text-right text-amber-400">${s.cost_usd?.toFixed(4) ?? "-"}</td>
                    <td className="px-3 py-2.5 text-white/30 text-xs whitespace-nowrap">
                      {s.generated_at ? s.generated_at.slice(0, 16).replace("T", " ") : "-"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="mb-8">
        <h2 className="text-base font-semibold text-white mb-3 pb-2 border-b border-white/10">Benchmark Comparisons</h2>
        <div className="mb-3">
          <input
            type="text"
            placeholder="Filter by task..."
            value={benchFilter}
            onChange={(e) => setBenchFilter(e.target.value)}
            className="bg-card border border-white/10 rounded-lg px-3 py-2 text-sm text-white placeholder-white/30 w-72 focus:outline-none focus:ring-2 focus:ring-accent/50"
          />
        </div>
        {benchmarks.length === 0 ? (
          <div className="rounded-xl border border-white/10 bg-card p-10 text-center">
            <p className="text-white/30 text-sm">No benchmarks found. Run redcon benchmark.</p>
          </div>
        ) : (
          <div className="space-y-5">
            {benchmarks
              .filter((b) => !benchFilter || b.task.toLowerCase().includes(benchFilter.toLowerCase()))
              .map((b, i) => (
                <div key={i} className="bg-card rounded-xl border border-white/10 overflow-hidden">
                  <div className="px-5 py-3 bg-white/5 border-b border-white/10 flex items-baseline gap-3">
                    <span className="font-semibold text-white">{b.task || "Benchmark"}</span>
                    <span className="text-xs text-white/30">baseline: {b.baseline_tokens.toLocaleString()} tokens</span>
                    <span className="text-xs text-white/30 ml-auto">{b.generated_at?.slice(0, 10)}</span>
                  </div>
                  <table className="w-full text-sm">
                    <thead className="border-b border-white/10">
                      <tr>
                        {["Strategy", "Input Tokens", "Saved", "Risk", "Runtime"].map((h) => (
                          <th key={h} className="px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-white/40">{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {b.strategies.map((s, j) => (
                        <tr key={j} className="border-b border-white/5 last:border-0 hover:bg-white/5">
                          <td className="px-4 py-2.5 font-mono text-xs text-white/60">{s.strategy}</td>
                          <td className="px-4 py-2.5 tabular-nums text-accent font-medium">{s.input_tokens.toLocaleString()}</td>
                          <td className="px-4 py-2.5 tabular-nums text-emerald-400 font-medium">{s.saved_tokens.toLocaleString()}</td>
                          <td className="px-4 py-2.5">
                            {s.risk ? (
                              <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-semibold ${riskBadge(s.risk)}`}>
                                {s.risk}
                              </span>
                            ) : "-"}
                          </td>
                          <td className="px-4 py-2.5 tabular-nums text-white/30">{s.runtime_ms ? `${s.runtime_ms} ms` : "-"}</td>
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
