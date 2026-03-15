"use client";

import { useMemo } from "react";
import { useData } from "@/hooks/useData";
import MetricCard from "@/components/MetricCard";

interface RepoStats {
  name: string;
  runs: number;
  input_tokens: number;
  saved_tokens: number;
  savings_rate: number;
  last_run: string;
  commands: Record<string, number>;
}

function deriveRepoName(artifact: string, task: string): string {
  // Try artifact path: extract meaningful directory name
  if (artifact) {
    const parts = artifact.split(/[\\/]/);
    // Find a non-trivial directory name
    for (let i = parts.length - 2; i >= 0; i--) {
      const p = parts[i];
      if (p && !p.startsWith(".") && p !== "artifacts" && p !== "output" && p !== "results") {
        return p;
      }
    }
  }
  // Fall back to first word(s) of task
  if (task) {
    const cleaned = task.replace(/^(fix|add|update|refactor|implement|build|test|review)\s+/i, "");
    const words = cleaned.split(/\s+/).slice(0, 3).join(" ");
    return words || "unknown";
  }
  return "unknown";
}

export default function RepositoriesPage() {
  const { data, loading } = useData();

  const repos = useMemo<RepoStats[]>(() => {
    const map = new Map<string, RepoStats>();
    for (const r of data.run_history) {
      const name = deriveRepoName(r.artifact, r.task);
      if (!map.has(name)) {
        map.set(name, {
          name,
          runs: 0,
          input_tokens: 0,
          saved_tokens: 0,
          savings_rate: 0,
          last_run: "",
          commands: {},
        });
      }
      const repo = map.get(name)!;
      repo.runs++;
      repo.input_tokens += r.input_tokens;
      repo.saved_tokens += r.saved_tokens;
      repo.commands[r.command] = (repo.commands[r.command] || 0) + 1;
      if (!repo.last_run || r.generated_at > repo.last_run) {
        repo.last_run = r.generated_at;
      }
    }
    const repoList = Array.from(map.values());
    for (const repo of repoList) {
      const denom = repo.input_tokens + repo.saved_tokens;
      repo.savings_rate = denom > 0 ? repo.saved_tokens / denom : 0;
    }
    return repoList.sort((a, b) => b.input_tokens - a.input_tokens);
  }, [data.run_history]);

  const totalRepos = repos.length;
  const totalRuns = repos.reduce((s, r) => s + r.runs, 0);
  const totalSaved = repos.reduce((s, r) => s + r.saved_tokens, 0);

  return (
    <div>
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-slate-900">Repositories</h1>
        <p className="text-slate-500 text-sm mt-1">
          Workspace usage aggregated from run history.
          {!data.connected && !loading && (
            <span className="text-amber-600 font-medium ml-1">
              Connect to live data via <code className="font-mono bg-amber-50 px-1 rounded">contextbudget dashboard</code>.
            </span>
          )}
        </p>
      </div>

      {/* Summary */}
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-4 mb-8">
        <MetricCard label="Workspaces" value={totalRepos.toLocaleString()} color="blue" />
        <MetricCard label="Total Runs" value={totalRuns.toLocaleString()} />
        <MetricCard
          label="Total Tokens Saved"
          value={totalSaved >= 1_000_000
            ? (totalSaved / 1_000_000).toFixed(1) + "M"
            : totalSaved.toLocaleString()}
          color="green"
        />
      </div>

      {/* Repo Cards */}
      {repos.length === 0 ? (
        <div className="rounded-xl border border-slate-200 bg-white p-12 text-center">
          <p className="text-slate-400 text-sm">No run history found. Run some commands first.</p>
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {repos.map((repo) => {
            const fmtTok = (n: number) =>
              n >= 1_000_000 ? (n / 1_000_000).toFixed(1) + "M" : n >= 1_000 ? (n / 1_000).toFixed(0) + "k" : String(n);
            return (
              <div
                key={repo.name}
                className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm hover:shadow-md transition-shadow"
              >
                <div className="flex items-start gap-3 mb-4">
                  <div className="w-9 h-9 rounded-lg bg-slate-100 flex items-center justify-center flex-shrink-0">
                    <svg className="w-5 h-5 text-slate-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
                    </svg>
                  </div>
                  <div className="min-w-0">
                    <div className="font-semibold text-slate-900 truncate" title={repo.name}>
                      {repo.name}
                    </div>
                    <div className="text-xs text-slate-400 mt-0.5">
                      {repo.last_run ? repo.last_run.slice(0, 10) : "—"}
                    </div>
                  </div>
                </div>

                <div className="grid grid-cols-3 gap-2 text-center mb-4">
                  <div>
                    <div className="text-lg font-bold text-slate-800">{repo.runs}</div>
                    <div className="text-xs text-slate-400">Runs</div>
                  </div>
                  <div>
                    <div className="text-lg font-bold text-blue-600">{fmtTok(repo.input_tokens)}</div>
                    <div className="text-xs text-slate-400">Input</div>
                  </div>
                  <div>
                    <div className="text-lg font-bold text-emerald-600">{fmtTok(repo.saved_tokens)}</div>
                    <div className="text-xs text-slate-400">Saved</div>
                  </div>
                </div>

                {/* Savings bar */}
                <div className="mb-3">
                  <div className="flex justify-between text-xs text-slate-500 mb-1">
                    <span>Savings rate</span>
                    <span className="font-semibold">{(repo.savings_rate * 100).toFixed(1)}%</span>
                  </div>
                  <div className="h-1.5 bg-slate-100 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-emerald-500 rounded-full transition-all"
                      style={{ width: `${(repo.savings_rate * 100).toFixed(1)}%` }}
                    />
                  </div>
                </div>

                {/* Command breakdown */}
                <div className="flex flex-wrap gap-1.5">
                  {Object.entries(repo.commands).map(([cmd, count]) => (
                    <span
                      key={cmd}
                      className="text-xs px-2 py-0.5 rounded-full bg-slate-100 text-slate-600"
                    >
                      {cmd} × {count}
                    </span>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
