"use client";

import { useData } from "@/hooks/useData";
import MetricCard from "@/components/MetricCard";
import TokenTrendChart from "@/components/charts/TokenTrendChart";
import TokenBarChart from "@/components/charts/TokenBarChart";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mb-8">
      <h2 className="text-base font-semibold text-white mb-4 pb-2 border-b border-white/10">{title}</h2>
      {children}
    </section>
  );
}

function riskBadge(risk: string) {
  const map: Record<string, string> = {
    low: "bg-emerald-900/50 text-emerald-400",
    medium: "bg-amber-900/50 text-amber-400",
    high: "bg-red-900/50 text-red-400",
  };
  return map[risk] || "bg-white/10 text-white/60";
}

function cmdBadge(cmd: string) {
  const map: Record<string, string> = {
    pack: "bg-accent/20 text-accent",
    benchmark: "bg-violet-900/50 text-violet-400",
    "simulate-agent": "bg-cyan-900/50 text-cyan-400",
  };
  return map[cmd] || "bg-white/10 text-white/60";
}

function computeDrift(runTrend: { input_tokens: number }[]) {
  if (runTrend.length < 2) return null;
  const first = runTrend[0].input_tokens;
  const last = runTrend[runTrend.length - 1].input_tokens;
  if (!first) return null;
  return ((last - first) / first) * 100;
}

export default function OverviewPage() {
  const { data, loading } = useData();
  const { summary, run_history, run_trend, token_chart } = data;

  const drift = computeDrift(run_trend);
  const recentRuns = run_history.slice(0, 10);

  return (
    <div>
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-white">Overview</h1>
        <p className="text-white/50 text-sm mt-1">
          Agent context infrastructure at a glance.{" "}
          {!data.connected && !loading && (
            <span className="text-amber-400 font-medium">
              Not connected - run <code className="font-mono bg-white/5 px-1 rounded">redcon dashboard</code> to load live data.
            </span>
          )}
        </p>
      </div>

      <Section title="Summary">
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-4">
          <MetricCard
            label="Total Runs"
            value={summary.total_runs.toLocaleString()}
            sub={`${summary.pack_runs} pack · ${summary.sim_runs} sim · ${summary.benchmark_runs} bench`}
          />
          <MetricCard
            label="Input Tokens"
            value={summary.total_input_tokens >= 1_000_000
              ? (summary.total_input_tokens / 1_000_000).toFixed(1) + "M"
              : summary.total_input_tokens.toLocaleString()}
            color="blue"
          />
          <MetricCard
            label="Tokens Saved"
            value={summary.total_saved_tokens >= 1_000_000
              ? (summary.total_saved_tokens / 1_000_000).toFixed(1) + "M"
              : summary.total_saved_tokens.toLocaleString()}
            color="green"
            sub="via compression + cache"
          />
          <MetricCard
            label="Savings Rate"
            value={(summary.savings_rate * 100).toFixed(1) + "%"}
            color={summary.savings_rate > 0.2 ? "green" : summary.savings_rate > 0 ? "amber" : "default"}
          />
          <MetricCard
            label="Context Drift"
            value={
              drift === null
                ? "-"
                : (drift > 0 ? "+" : "") + drift.toFixed(1) + "%"
            }
            color={drift === null ? "default" : drift > 20 ? "red" : drift > 5 ? "amber" : "green"}
            sub="first -> latest pack run"
          />
          <MetricCard
            label="Cache Reuse"
            value={summary.total_saved_tokens > 0 && summary.total_input_tokens > 0
              ? (summary.total_saved_tokens / (summary.total_input_tokens + summary.total_saved_tokens) * 100).toFixed(1) + "%"
              : "-"}
            color="blue"
            sub="of total context avoided"
          />
        </div>
      </Section>

      <Section title="Token Usage Trend">
        <div className="bg-card rounded-xl border border-white/10 p-5">
          <TokenTrendChart data={run_trend} />
        </div>
      </Section>

      <Section title="Token Usage - Pack Runs">
        <div className="bg-card rounded-xl border border-white/10 p-5">
          <TokenBarChart data={token_chart} />
        </div>
      </Section>

      <Section title="Recent Runs">
        {recentRuns.length === 0 ? (
          <p className="text-white/30 text-sm py-6 text-center">No runs recorded yet.</p>
        ) : (
          <div className="overflow-x-auto rounded-xl border border-white/10">
            <table className="w-full text-sm bg-card">
              <thead className="border-b border-white/10">
                <tr>
                  <th className="px-3 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-white/40">Command</th>
                  <th className="px-3 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-white/40">Task</th>
                  <th className="px-3 py-2.5 text-right text-xs font-semibold uppercase tracking-wide text-white/40">Input Tokens</th>
                  <th className="px-3 py-2.5 text-right text-xs font-semibold uppercase tracking-wide text-white/40">Saved</th>
                  <th className="px-3 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-white/40">Risk</th>
                  <th className="px-3 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-white/40">Date</th>
                </tr>
              </thead>
              <tbody>
                {recentRuns.map((r, i) => (
                  <tr key={i} className="border-b border-white/5 last:border-0 hover:bg-white/5">
                    <td className="px-3 py-2.5">
                      <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-semibold ${cmdBadge(r.command)}`}>
                        {r.command}
                      </span>
                    </td>
                    <td className="px-3 py-2.5 text-white/70 max-w-xs truncate">{r.task || "-"}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums text-white/80">{r.input_tokens.toLocaleString()}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums text-emerald-400">{r.saved_tokens.toLocaleString()}</td>
                    <td className="px-3 py-2.5">
                      {r.risk ? (
                        <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-semibold ${riskBadge(r.risk)}`}>
                          {r.risk}
                        </span>
                      ) : "-"}
                    </td>
                    <td className="px-3 py-2.5 text-white/30 text-xs whitespace-nowrap">
                      {r.generated_at ? r.generated_at.slice(0, 16).replace("T", " ") : "-"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>
    </div>
  );
}
