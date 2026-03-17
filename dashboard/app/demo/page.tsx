"use client";

import { useState } from "react";
import DemoTour from "@/components/DemoTour";

const EXAMPLE_TASKS = [
  "Add rate limiting middleware to the gateway server",
  "Add new scoring signal based on file modification time",
  "Refactor the telemetry store to use SQLite instead of JSON",
  "Add webhook retry logic with exponential backoff",
];

type CompressedFile = {
  path: string;
  strategy: string;
  chunk_strategy: string;
  original_tokens: number;
  compressed_tokens: number;
  text: string;
  symbols?: { name: string; symbol_type: string }[];
  selected_ranges?: { kind: string; symbol?: string }[];
};

type RunResult = {
  budget: {
    estimated_input_tokens: number;
    estimated_saved_tokens: number;
    quality_risk_estimate: string;
  };
  compressed_context: CompressedFile[];
  files_included: string[];
  files_skipped: string[];
  ranked_files?: { path: string; score: number; reasons: string[] }[];
  task: string;
};

function strategyColor(strategy: string): string {
  if (strategy === "full") return "bg-white/10 text-white/60";
  if (strategy === "snippet") return "bg-amber-900/40 text-amber-300";
  if (strategy === "symbol") return "bg-accent/20 text-accent-light";
  if (strategy === "summary") return "bg-violet-900/40 text-violet-300";
  return "bg-white/10 text-white/50";
}

function strategyLabel(strategy: string): string {
  if (strategy === "full") return "full file";
  if (strategy === "snippet") return "snippet";
  if (strategy === "symbol") return "symbol extract";
  if (strategy === "summary") return "summary";
  return strategy;
}

function riskColor(risk: string): string {
  if (risk === "low") return "text-emerald-400";
  if (risk === "medium") return "text-amber-400";
  if (risk === "high") return "text-red-400";
  return "text-white/40";
}

function FileRow({ file, baseline }: { file: CompressedFile; baseline: number }) {
  const [open, setOpen] = useState(false);
  const reduction = file.original_tokens > 0
    ? ((1 - file.compressed_tokens / file.original_tokens) * 100).toFixed(0)
    : "0";
  const barWidth = Math.max(2, (file.compressed_tokens / Math.max(baseline, 1)) * 100);

  return (
    <div className="border border-white/10 rounded-lg overflow-hidden">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-3 px-4 py-3 bg-card hover:bg-white/5 text-left"
      >
        <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-semibold flex-shrink-0 ${strategyColor(file.strategy)}`}>
          {strategyLabel(file.strategy)}
        </span>

        <span className="text-sm text-white/80 font-mono flex-1 truncate">{file.path}</span>

        <div className="hidden sm:flex items-center gap-2 flex-shrink-0 w-40">
          <div className="flex-1 bg-white/10 rounded-full h-1.5">
            <div className="bg-accent h-1.5 rounded-full" style={{ width: `${barWidth}%` }} />
          </div>
          <span className="text-xs tabular-nums text-white/40 w-12 text-right">{file.compressed_tokens} tok</span>
        </div>

        <span className="text-xs tabular-nums text-emerald-400 font-semibold flex-shrink-0 w-14 text-right">
          -{reduction}%
        </span>

        <svg
          className={`w-4 h-4 text-white/30 flex-shrink-0 transition-transform ${open ? "rotate-180" : ""}`}
          fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {open && (
        <div className="border-t border-white/5 bg-page-bg px-4 py-3">
          <div className="flex flex-wrap gap-3 mb-3 text-xs text-white/40">
            <span>Original: <strong className="text-white/70">{file.original_tokens} tokens</strong></span>
            <span>Compressed: <strong className="text-accent-light">{file.compressed_tokens} tokens</strong></span>
            <span>Strategy: <strong className="text-white/70">{file.chunk_strategy}</strong></span>
          </div>

          {file.symbols && file.symbols.length > 0 && (
            <div className="mb-3 flex flex-wrap gap-1.5">
              {file.symbols.map((s, i) => (
                <span
                  key={i}
                  className="inline-flex items-center gap-1 px-2 py-0.5 bg-accent/10 border border-accent/30 rounded text-xs text-accent-light font-mono"
                >
                  <span className="text-accent/70">{s.symbol_type}</span>
                  {s.name}
                </span>
              ))}
            </div>
          )}

          <pre className="text-xs text-white/60 bg-card border border-white/10 rounded p-3 overflow-x-auto whitespace-pre-wrap max-h-64">
            {file.text}
          </pre>
        </div>
      )}
    </div>
  );
}

function isTaskMeaningful(t: string): boolean {
  const words = t.trim().split(/\s+/).filter(Boolean);
  return t.trim().length >= 15 && words.length >= 3;
}

export default function DemoPage() {
  const [task, setTask] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<RunResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tourOpen, setTourOpen] = useState(false);
  const [taskWarning, setTaskWarning] = useState(false);

  async function runDemo(taskText: string) {
    if (!taskText.trim()) return;
    if (!isTaskMeaningful(taskText)) {
      setTaskWarning(true);
      return;
    }
    setTaskWarning(false);
    setLoading(true);
    setResult(null);
    setError(null);
    try {
      const res = await fetch("/api/demo/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task: taskText.trim() }),
      });
      const data = await res.json();
      if (!res.ok || data.error) throw new Error(data.error ?? "Run failed");
      setResult(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  const baseline = result ? result.compressed_context.reduce((s, f) => s + f.original_tokens, 0) : 0;
  const packed = result?.budget.estimated_input_tokens ?? 0;
  const saved = result?.budget.estimated_saved_tokens ?? 0;
  const reductionPct = baseline > 0 ? ((saved / baseline) * 100).toFixed(1) : "0";
  const barPacked = baseline > 0 ? (packed / baseline) * 100 : 0;

  return (
    <div className="max-w-3xl">
      {tourOpen && <DemoTour onClose={() => setTourOpen(false)} />}

      <div className="mb-8 flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-white">Live Demo</h1>
          <p className="text-white/50 text-sm mt-1">
            Wpisz zadanie i zobacz jak Redcon wybiera i kompresuje kontekst
            z prawdziwego repozytorium (89 plikow, ~252k tokenow baseline).
          </p>
        </div>
        <button
          onClick={() => setTourOpen(true)}
          className="flex-shrink-0 flex items-center gap-2 px-3 py-2 text-sm text-white/60 border border-white/10 rounded-lg hover:bg-white/5 hover:border-white/20"
        >
          <svg className="w-4 h-4 text-accent" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          Jak to dziala?
        </button>
      </div>

      <div className="bg-card border border-white/10 rounded-xl p-5 mb-6">
        <label className="block text-xs font-semibold text-white/40 uppercase tracking-wide mb-2">Task</label>
        <div className="flex gap-2">
          <input
            type="text"
            value={task}
            onChange={(e) => { setTask(e.target.value); if (isTaskMeaningful(e.target.value)) setTaskWarning(false); }}
            onKeyDown={(e) => e.key === "Enter" && runDemo(task)}
            placeholder="Describe what you want to do..."
            className="flex-1 px-3 py-2.5 text-sm bg-page-bg border border-white/10 rounded-lg text-white placeholder-white/30 focus:outline-none focus:ring-2 focus:ring-accent/50 focus:border-accent/50"
          />
          <button
            onClick={() => runDemo(task)}
            disabled={loading || !task.trim()}
            className="px-4 py-2.5 bg-accent text-white text-sm font-semibold rounded-lg hover:bg-accent-dark disabled:opacity-40 disabled:cursor-not-allowed flex items-center gap-2"
            title={task.trim() && !isTaskMeaningful(task) ? "Task must be at least 3 words (15 chars)" : undefined}
          >
            {loading ? (
              <>
                <svg className="animate-spin w-4 h-4" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
                Running...
              </>
            ) : "Run"}
          </button>
        </div>

        {taskWarning && (
          <p className="mt-2 text-xs text-amber-400">
            Task is too vague - enter at least 3 words describing what you want to do. Try one of the examples below.
          </p>
        )}

        <div className="mt-3 flex flex-wrap gap-2">
          {EXAMPLE_TASKS.map((t) => (
            <button
              key={t}
              onClick={() => { setTask(t); runDemo(t); }}
              disabled={loading}
              className="text-xs px-2.5 py-1 bg-white/5 hover:bg-white/10 text-white/50 hover:text-white/70 rounded-full disabled:opacity-40 border border-white/10"
            >
              {t}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div className="mb-6 p-4 bg-red-900/30 border border-red-500/30 rounded-xl text-sm text-red-400">
          {error}
        </div>
      )}

      {result && (
        <>
          <div className="grid grid-cols-3 gap-4 mb-6">
            <div className="col-span-3 bg-card border border-white/10 rounded-xl p-5">
              <div className="flex items-baseline justify-between mb-3">
                <span className="text-xs font-semibold text-white/40 uppercase tracking-wide">Token reduction</span>
                <span className={`text-xs font-semibold ${riskColor(result.budget.quality_risk_estimate)}`}>
                  quality risk: {result.budget.quality_risk_estimate}
                </span>
              </div>
              <div className="flex items-center gap-4">
                <div className="flex-1">
                  <div className="flex items-center gap-2 mb-1.5">
                    <span className="text-xs text-white/30 w-16">Baseline</span>
                    <div className="flex-1 bg-white/10 rounded-full h-3">
                      <div className="bg-white/30 h-3 rounded-full w-full" />
                    </div>
                    <span className="text-xs tabular-nums text-white/40 w-20 text-right">{baseline.toLocaleString()} tok</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-white/30 w-16">Redcon</span>
                    <div className="flex-1 bg-white/10 rounded-full h-3">
                      <div
                        className="bg-accent h-3 rounded-full transition-all duration-700"
                        style={{ width: `${Math.max(barPacked, 0.5)}%` }}
                      />
                    </div>
                    <span className="text-xs tabular-nums text-accent-light font-semibold w-20 text-right">{packed.toLocaleString()} tok</span>
                  </div>
                </div>
                <div className="flex-shrink-0 text-right">
                  <div className="text-3xl font-bold text-emerald-400">-{reductionPct}%</div>
                  <div className="text-xs text-white/30 mt-0.5">{saved.toLocaleString()} tokens saved</div>
                </div>
              </div>
            </div>

            <div className="bg-card border border-white/10 rounded-xl p-4 text-center">
              <div className="text-2xl font-bold text-white">{result.files_included.length}</div>
              <div className="text-xs text-white/40 mt-1">files included</div>
            </div>
            <div className="bg-card border border-white/10 rounded-xl p-4 text-center">
              <div className="text-2xl font-bold text-white/40">{result.files_skipped.length}</div>
              <div className="text-xs text-white/40 mt-1">files skipped</div>
            </div>
            <div className="bg-card border border-white/10 rounded-xl p-4 text-center">
              <div className="flex justify-center gap-1.5 flex-wrap">
                {Object.entries(
                  result.compressed_context.reduce<Record<string, number>>((acc, f) => {
                    acc[f.strategy] = (acc[f.strategy] ?? 0) + 1;
                    return acc;
                  }, {})
                ).map(([s, n]) => (
                  <span key={s} className={`text-xs px-2 py-0.5 rounded-full font-semibold ${strategyColor(s)}`}>
                    {n} {strategyLabel(s)}
                  </span>
                ))}
              </div>
              <div className="text-xs text-white/40 mt-2">compression strategies</div>
            </div>
          </div>

          <div className="mb-2 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-white/70">Compressed context</h2>
            <span className="text-xs text-white/30">kliknij plik aby zobaczyc zawartosc</span>
          </div>
          <div className="space-y-2">
            {result.compressed_context.map((f) => (
              <FileRow key={f.path} file={f} baseline={baseline} />
            ))}
          </div>
        </>
      )}

      {!result && !loading && !error && (
        <div className="text-center py-16 text-white/20 text-sm">
          Enter a task above or click an example to run Redcon on the demo repo.
        </div>
      )}
    </div>
  );
}
