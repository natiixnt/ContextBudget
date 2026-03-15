"use client";

import { useData } from "@/hooks/useData";
import MetricCard from "@/components/MetricCard";
import SavingsDonut from "@/components/charts/SavingsDonut";
import SavingsBarChart from "@/components/charts/SavingsBarChart";
import TokenTrendChart from "@/components/charts/TokenTrendChart";

// Rough cost estimate using a mid-range model price (~$3/1M input tokens)
const COST_PER_1M = 3.0;

function fmtTok(n: number) {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(2) + "M";
  if (n >= 1_000) return (n / 1_000).toFixed(1) + "k";
  return String(n);
}

export default function SavingsPage() {
  const { data, loading } = useData();
  const { summary, savings_breakdown, run_trend } = data;

  const estimatedCostSaved = (summary.total_saved_tokens / 1_000_000) * COST_PER_1M;
  const totalTokens = summary.total_input_tokens + summary.total_saved_tokens;

  return (
    <div>
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-slate-900">Savings</h1>
        <p className="text-slate-500 text-sm mt-1">
          Token compression and cache reuse savings across all runs.
          {!data.connected && !loading && (
            <span className="text-amber-600 font-medium ml-1">
              No live data — run <code className="font-mono bg-amber-50 px-1 rounded">redcon dashboard</code>.
            </span>
          )}
        </p>
      </div>

      {/* Summary Cards */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-8">
        <MetricCard
          label="Total Tokens Saved"
          value={fmtTok(summary.total_saved_tokens)}
          color="green"
          sub="across all runs"
        />
        <MetricCard
          label="Savings Rate"
          value={(summary.savings_rate * 100).toFixed(1) + "%"}
          color={summary.savings_rate > 0.25 ? "green" : summary.savings_rate > 0.1 ? "amber" : "default"}
          sub="of total context"
        />
        <MetricCard
          label="Est. Cost Saved"
          value={`$${estimatedCostSaved.toFixed(2)}`}
          color="green"
          sub={`at $${COST_PER_1M}/1M tokens`}
        />
        <MetricCard
          label="Total Tokens"
          value={fmtTok(totalTokens)}
          color="blue"
          sub={`${fmtTok(summary.total_input_tokens)} used`}
        />
      </div>

      {/* Donut + Bar Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
        <section>
          <h2 className="text-base font-semibold text-slate-800 mb-3 pb-2 border-b border-slate-200">
            Used vs. Saved
          </h2>
          <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
            <SavingsDonut
              used={summary.total_input_tokens}
              saved={summary.total_saved_tokens}
            />
          </div>
        </section>

        <section>
          <h2 className="text-base font-semibold text-slate-800 mb-3 pb-2 border-b border-slate-200">
            By Command Type
          </h2>
          <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
            <SavingsBarChart data={savings_breakdown} />
          </div>
        </section>
      </div>

      {/* Trend */}
      <section className="mb-8">
        <h2 className="text-base font-semibold text-slate-800 mb-3 pb-2 border-b border-slate-200">
          Savings Trend
        </h2>
        <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
          <TokenTrendChart data={run_trend} />
        </div>
      </section>

      {/* Breakdown Table */}
      {savings_breakdown.length > 0 && (
        <section>
          <h2 className="text-base font-semibold text-slate-800 mb-3 pb-2 border-b border-slate-200">
            Savings Breakdown
          </h2>
          <div className="overflow-x-auto rounded-xl border border-slate-200 shadow-sm">
            <table className="w-full text-sm bg-white">
              <thead className="bg-slate-50 border-b border-slate-200">
                <tr>
                  <th className="px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">Command</th>
                  <th className="px-4 py-2.5 text-right text-xs font-semibold uppercase tracking-wide text-slate-500">Tokens Used</th>
                  <th className="px-4 py-2.5 text-right text-xs font-semibold uppercase tracking-wide text-slate-500">Tokens Saved</th>
                  <th className="px-4 py-2.5 text-right text-xs font-semibold uppercase tracking-wide text-slate-500">Savings Rate</th>
                  <th className="px-4 py-2.5 text-right text-xs font-semibold uppercase tracking-wide text-slate-500">Est. Cost Saved</th>
                </tr>
              </thead>
              <tbody>
                {savings_breakdown.map((row, i) => {
                  const rate = (row.used + row.saved) > 0 ? row.saved / (row.used + row.saved) : 0;
                  const cost = (row.saved / 1_000_000) * COST_PER_1M;
                  return (
                    <tr key={i} className="border-b border-slate-100 last:border-0 hover:bg-slate-50">
                      <td className="px-4 py-2.5 font-mono text-xs text-slate-700">{row.label}</td>
                      <td className="px-4 py-2.5 tabular-nums text-right text-blue-600">{row.used.toLocaleString()}</td>
                      <td className="px-4 py-2.5 tabular-nums text-right text-emerald-600 font-medium">{row.saved.toLocaleString()}</td>
                      <td className="px-4 py-2.5 tabular-nums text-right">
                        <span className={rate > 0.25 ? "text-emerald-600 font-semibold" : rate > 0.1 ? "text-amber-600" : "text-slate-600"}>
                          {(rate * 100).toFixed(1)}%
                        </span>
                      </td>
                      <td className="px-4 py-2.5 tabular-nums text-right text-slate-500">${cost.toFixed(4)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}
