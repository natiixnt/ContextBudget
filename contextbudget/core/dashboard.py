from __future__ import annotations

"""Local dashboard server for ContextBudget analytics."""

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from contextbudget.core.heatmap import build_heatmap_report, heatmap_as_dict

# ---------------------------------------------------------------------------
# Artifact scanning
# ---------------------------------------------------------------------------

_KNOWN_COMMANDS = {
    "pack", "benchmark", "simulate-agent", "plan", "plan-agent",
    "heatmap", "profile", "report",
}


def _scan_artifacts(paths: list[Path]) -> list[dict[str, Any]]:
    """Return JSON run artifacts found under the given paths, newest first."""
    found: list[dict[str, Any]] = []
    visited: set[Path] = set()

    def _load(p: Path) -> None:
        if p in visited:
            return
        visited.add(p)
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return
        if isinstance(data, dict) and data.get("command") in _KNOWN_COMMANDS:
            data["_artifact_path"] = str(p)
            found.append(data)

    for path in paths:
        if path.is_file():
            _load(path)
        elif path.is_dir():
            for p in sorted(path.rglob("*.json")):
                if not any(part.startswith(".") for part in p.parts):
                    _load(p)

    return sorted(found, key=lambda d: d.get("generated_at", ""), reverse=True)


def _load_history_entries(paths: list[Path]) -> list[dict[str, Any]]:
    """Load run entries from .contextbudget/history.json files in search paths."""
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in paths:
        root = path if path.is_dir() else path.parent
        history_file = root / ".contextbudget" / "history.json"
        if not history_file.exists():
            continue
        try:
            data = json.loads(history_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        for entry in data.get("entries", []):
            if not isinstance(entry, dict):
                continue
            ts = entry.get("generated_at", "")
            if not ts or ts in seen:
                continue
            seen.add(ts)
            entries.append(entry)
    return sorted(entries, key=lambda e: e.get("generated_at", ""), reverse=True)


# ---------------------------------------------------------------------------
# Data aggregation
# ---------------------------------------------------------------------------

def build_dashboard_data(paths: list[Path]) -> dict[str, Any]:
    """Aggregate artifact data for all dashboard sections."""
    artifacts = _scan_artifacts(paths)
    pack_artifacts = [a for a in artifacts if a.get("command") == "pack"]
    sim_artifacts = [a for a in artifacts if a.get("command") == "simulate-agent"]
    bench_artifacts = [a for a in artifacts if a.get("command") == "benchmark"]

    # ── run history from artifacts ────────────────────────────────────────────
    artifact_timestamps: set[str] = set()
    run_history: list[dict[str, Any]] = []
    for a in artifacts:
        cmd = a.get("command", "")
        budget = a.get("budget", {}) or {}
        ce = a.get("cost_estimate", {}) or {}
        ts = a.get("generated_at", "")
        artifact_timestamps.add(ts)

        if cmd == "pack":
            input_tok = budget.get("estimated_input_tokens", 0)
            saved_tok = budget.get("estimated_saved_tokens", 0)
            files = len(a.get("files_included", []))
            risk = budget.get("quality_risk_estimate", "")
            cost = None
        elif cmd == "simulate-agent":
            input_tok = a.get("total_tokens", 0)
            saved_tok = 0
            files = len(a.get("steps", []))
            risk = ""
            cost = ce.get("total_cost_usd")
        elif cmd == "benchmark":
            strategies = a.get("strategies", [])
            best = next(
                (s for s in strategies if s.get("strategy") == "compressed_pack"),
                strategies[0] if strategies else {},
            )
            input_tok = best.get("estimated_input_tokens", a.get("baseline_full_context_tokens", 0))
            saved_tok = best.get("estimated_saved_tokens", 0)
            files = 0
            risk = best.get("quality_risk_estimate", "")
            cost = None
        else:
            input_tok = 0
            saved_tok = 0
            files = 0
            risk = ""
            cost = None

        entry: dict[str, Any] = {
            "command": cmd,
            "task": a.get("task", ""),
            "generated_at": ts,
            "artifact": a.get("_artifact_path", ""),
            "input_tokens": input_tok,
            "saved_tokens": saved_tok,
            "files": files,
            "risk": risk,
            "source": "artifact",
        }
        if cost is not None:
            entry["cost_usd"] = cost
        run_history.append(entry)

    # ── merge persistent history.json entries ─────────────────────────────────
    history_entries = _load_history_entries(paths)
    for h in history_entries:
        ts = h.get("generated_at", "")
        if ts in artifact_timestamps:
            continue  # already represented by a scanned artifact
        tu = h.get("token_usage", {}) or {}
        run_history.append({
            "command": "pack",
            "task": h.get("task", ""),
            "generated_at": ts,
            "artifact": (h.get("result_artifacts") or {}).get("json", ""),
            "input_tokens": int(tu.get("estimated_input_tokens", 0) or 0),
            "saved_tokens": int(tu.get("estimated_saved_tokens", 0) or 0),
            "files": len(h.get("selected_files", [])),
            "risk": str(tu.get("quality_risk_estimate", "") or ""),
            "source": "history",
        })

    # Sort merged history newest-first
    run_history.sort(key=lambda r: r.get("generated_at", ""), reverse=True)

    # ── token chart (pack runs, chronological, up to 30) ─────────────────────
    pack_history = [r for r in reversed(run_history) if r["command"] == "pack"][-30:]
    token_chart: list[dict[str, Any]] = []
    for r in pack_history:
        label = r.get("task", "")
        if len(label) > 28:
            label = label[:27] + "…"
        date = (r.get("generated_at", "") or "")[:10]
        token_chart.append({
            "label": f"{label} ({date})" if date else label,
            "input_tokens": r["input_tokens"],
            "saved_tokens": r["saved_tokens"],
        })

    # ── run trend (all pack runs chronological for line chart) ────────────────
    run_trend: list[dict[str, Any]] = []
    for r in pack_history:
        run_trend.append({
            "date": (r.get("generated_at", "") or "")[:10],
            "label": (r.get("task", "") or "")[:24],
            "input_tokens": r["input_tokens"],
            "saved_tokens": r["saved_tokens"],
        })

    # ── summary stats ────────────────────────────────────────────────────────
    total_input = sum(r["input_tokens"] for r in run_history)
    total_saved = sum(r["saved_tokens"] for r in run_history)
    denom = total_input + total_saved
    savings_rate = round(total_saved / denom, 4) if denom > 0 else 0.0

    # ── savings breakdown by command ──────────────────────────────────────────
    breakdown_map: dict[str, dict[str, int]] = {}
    for r in run_history:
        cmd = r["command"]
        if cmd not in breakdown_map:
            breakdown_map[cmd] = {"used": 0, "saved": 0}
        breakdown_map[cmd]["used"] += r["input_tokens"]
        breakdown_map[cmd]["saved"] += r["saved_tokens"]
    savings_breakdown = [
        {"label": cmd, "used": v["used"], "saved": v["saved"]}
        for cmd, v in breakdown_map.items()
    ]

    # ── heatmap ───────────────────────────────────────────────────────────────
    heatmap: dict[str, Any] = {}
    if pack_artifacts:
        artifact_paths = [Path(a["_artifact_path"]) for a in pack_artifacts if "_artifact_path" in a]
        try:
            report = build_heatmap_report(artifact_paths)
            heatmap = heatmap_as_dict(report)
        except Exception:
            heatmap = {}

    # ── simulation summary ────────────────────────────────────────────────────
    simulations: list[dict[str, Any]] = []
    for a in sim_artifacts[:20]:
        ce = a.get("cost_estimate", {}) or {}
        steps = a.get("steps", [])
        simulations.append({
            "task": a.get("task", ""),
            "model": a.get("model", ""),
            "total_tokens": a.get("total_tokens", 0),
            "steps": len(steps),
            "context_mode": a.get("context_mode", ""),
            "cost_usd": ce.get("total_cost_usd", 0),
            "generated_at": a.get("generated_at", ""),
        })

    # ── benchmark comparison ─────────────────────────────────────────────────
    benchmarks: list[dict[str, Any]] = []
    for a in bench_artifacts[:10]:
        baseline = a.get("baseline_full_context_tokens", 0)
        rows = []
        for s in a.get("strategies", []):
            saved = s.get("estimated_saved_tokens", 0)
            rows.append({
                "strategy": s.get("strategy", ""),
                "input_tokens": s.get("estimated_input_tokens", baseline),
                "saved_tokens": saved,
                "risk": s.get("quality_risk_estimate", ""),
                "runtime_ms": s.get("runtime_ms", 0),
            })
        benchmarks.append({
            "task": a.get("task", ""),
            "baseline_tokens": baseline,
            "generated_at": a.get("generated_at", ""),
            "strategies": rows,
        })

    return {
        "summary": {
            "total_runs": len(run_history),
            "pack_runs": len([r for r in run_history if r["command"] == "pack"]),
            "sim_runs": len(sim_artifacts),
            "benchmark_runs": len(bench_artifacts),
            "total_input_tokens": total_input,
            "total_saved_tokens": total_saved,
            "savings_rate": savings_rate,
        },
        "run_history": run_history,
        "token_chart": token_chart,
        "run_trend": run_trend,
        "savings_breakdown": savings_breakdown,
        "heatmap": heatmap,
        "simulations": simulations,
        "benchmarks": benchmarks,
    }


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>ContextBudget Dashboard</title>
<style>
:root {
  --bg: #f8f9fa; --surface: #fff; --border: #dee2e6;
  --text: #212529; --muted: #6c757d; --accent: #0d6efd;
  --green: #198754; --orange: #fd7e14; --red: #dc3545;
  --chart-a: #0d6efd; --chart-b: #20c997;
  --card-shadow: 0 1px 4px rgba(0,0,0,.08);
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: system-ui,-apple-system,sans-serif; background: var(--bg);
       color: var(--text); font-size: 14px; line-height: 1.5; }
header { background: var(--surface); border-bottom: 1px solid var(--border);
         padding: 14px 24px; display: flex; align-items: center; gap: 12px; }
header h1 { font-size: 18px; font-weight: 700; }
header .subtitle { color: var(--muted); font-size: 12px; }
main { max-width: 1280px; margin: 0 auto; padding: 24px; }
section { margin-bottom: 32px; }
h2 { font-size: 15px; font-weight: 600; margin-bottom: 12px;
     padding-bottom: 6px; border-bottom: 1px solid var(--border); }
.cards { display: flex; flex-wrap: wrap; gap: 16px; margin-bottom: 24px; }
.card { background: var(--surface); border: 1px solid var(--border);
        border-radius: 8px; padding: 16px 20px; flex: 1; min-width: 160px;
        box-shadow: var(--card-shadow); }
.card .label { font-size: 11px; font-weight: 600; text-transform: uppercase;
               letter-spacing: .05em; color: var(--muted); margin-bottom: 4px; }
.card .value { font-size: 26px; font-weight: 700; }
.card .value.green { color: var(--green); }
.card .value.blue { color: var(--accent); }
.card .value.orange { color: var(--orange); }
.chart-wrap { background: var(--surface); border: 1px solid var(--border);
              border-radius: 8px; padding: 20px; box-shadow: var(--card-shadow); }
.chart-grid { display: grid; grid-template-columns: 1fr 2fr; gap: 16px; }
@media (max-width: 860px) { .chart-grid { grid-template-columns: 1fr; } }
.chart-wrap canvas { max-height: 300px; }
.donut-wrap { display: flex; flex-direction: column; align-items: center;
              justify-content: center; }
.donut-wrap canvas { max-height: 260px; max-width: 260px; }
canvas { max-height: 320px; }
table { width: 100%; border-collapse: collapse; background: var(--surface);
        border-radius: 8px; overflow: hidden; box-shadow: var(--card-shadow); }
th { background: #f1f3f5; font-size: 11px; font-weight: 600;
     text-transform: uppercase; letter-spacing: .04em; color: var(--muted);
     padding: 10px 12px; text-align: left; border-bottom: 1px solid var(--border);
     white-space: nowrap; cursor: pointer; user-select: none; }
th:hover { background: #e9ecef; }
th.sorted-asc::after  { content: " ▲"; }
th.sorted-desc::after { content: " ▼"; }
td { padding: 9px 12px; border-bottom: 1px solid var(--border); vertical-align: top; }
tr:last-child td { border-bottom: none; }
tr:hover td { background: #f8f9fa; }
.badge { display: inline-block; padding: 2px 7px; border-radius: 10px;
         font-size: 11px; font-weight: 600; }
.badge-low  { background: #d1e7dd; color: #0a3622; }
.badge-medium { background: #fff3cd; color: #664d03; }
.badge-high { background: #f8d7da; color: #58151c; }
.badge-pack { background: #cfe2ff; color: #084298; }
.badge-benchmark { background: #e2d9f3; color: #432874; }
.badge-simulate { background: #d1ecf1; color: #0c5460; }
.badge-other { background: #e9ecef; color: #495057; }
.num { text-align: right; font-variant-numeric: tabular-nums; }
.muted { color: var(--muted); }
.empty { text-align: center; padding: 32px; color: var(--muted); }
.filter-row { display: flex; align-items: center; gap: 12px; margin-bottom: 10px; }
.filter-row input { flex: 1; max-width: 300px; padding: 6px 10px;
                    border: 1px solid var(--border); border-radius: 6px;
                    font-size: 13px; background: var(--surface); }
.filter-row input:focus { outline: 2px solid var(--accent); }
.heat-cell { position: relative; }
.heat-bar { position: absolute; left: 0; top: 0; bottom: 0;
            opacity: .12; pointer-events: none; border-radius: 2px; }
</style>
</head>
<body>
<header>
  <svg width="28" height="28" viewBox="0 0 28 28" fill="none">
    <rect width="28" height="28" rx="6" fill="#0d6efd"/>
    <rect x="5" y="18" width="4" height="5" rx="1" fill="white"/>
    <rect x="12" y="12" width="4" height="11" rx="1" fill="white"/>
    <rect x="19" y="7" width="4" height="16" rx="1" fill="white"/>
  </svg>
  <div>
    <h1>ContextBudget Dashboard</h1>
    <div class="subtitle" id="gen-time"></div>
  </div>
</header>
<main>

<!-- SUMMARY CARDS -->
<section>
  <h2>Summary</h2>
  <div class="cards" id="cards"></div>
</section>

<!-- SAVINGS BREAKDOWN + RUN TREND -->
<section id="sec-savings">
  <h2>Savings Breakdown</h2>
  <div class="chart-grid">
    <div class="chart-wrap donut-wrap">
      <canvas id="savingsDonut"></canvas>
      <p class="empty" id="donut-empty" style="display:none">No token data yet.</p>
    </div>
    <div class="chart-wrap">
      <canvas id="trendChart"></canvas>
      <p class="empty" id="trend-empty" style="display:none">No pack run history yet.</p>
    </div>
  </div>
</section>

<!-- TOKEN USAGE CHART -->
<section id="sec-chart">
  <h2>Token Usage — Pack Runs</h2>
  <div class="chart-wrap">
    <canvas id="tokenChart"></canvas>
    <p class="empty" id="chart-empty" style="display:none">No pack run artifacts found.</p>
  </div>
</section>

<!-- CONTEXT HEATMAP -->
<section id="sec-heatmap">
  <h2>Context Heatmap</h2>
  <div class="filter-row">
    <input type="text" id="heatmap-filter" placeholder="Filter files…">
  </div>
  <table id="heatmap-table">
    <thead><tr>
      <th data-col="path">File</th>
      <th data-col="total_compressed_tokens" class="num">Tokens (compressed)</th>
      <th data-col="total_original_tokens" class="num">Tokens (original)</th>
      <th data-col="total_saved_tokens" class="num">Saved</th>
      <th data-col="inclusion_count" class="num">Inclusions</th>
      <th data-col="inclusion_rate" class="num">Rate</th>
    </tr></thead>
    <tbody id="heatmap-body"></tbody>
  </table>
</section>

<!-- SIMULATION RUNS -->
<section id="sec-sims">
  <h2>Simulation Runs</h2>
  <table id="sim-table">
    <thead><tr>
      <th data-col="task">Task</th>
      <th data-col="model">Model</th>
      <th data-col="total_tokens" class="num">Total Tokens</th>
      <th data-col="steps" class="num">Steps</th>
      <th data-col="context_mode">Mode</th>
      <th data-col="cost_usd" class="num">Cost (USD)</th>
      <th data-col="generated_at">Date</th>
    </tr></thead>
    <tbody id="sim-body"></tbody>
  </table>
</section>

<!-- BENCHMARK COMPARISON -->
<section id="sec-bench">
  <h2>Benchmark Comparisons</h2>
  <div id="bench-body"></div>
</section>

<!-- RUN HISTORY -->
<section>
  <h2>Run History</h2>
  <div class="filter-row">
    <input type="text" id="history-filter" placeholder="Filter by task or command…">
  </div>
  <table id="history-table">
    <thead><tr>
      <th data-col="command">Command</th>
      <th data-col="task">Task</th>
      <th data-col="input_tokens" class="num">Input Tokens</th>
      <th data-col="saved_tokens" class="num">Saved Tokens</th>
      <th data-col="files" class="num">Files</th>
      <th data-col="risk">Risk</th>
      <th data-col="generated_at">Date</th>
    </tr></thead>
    <tbody id="history-body"></tbody>
  </table>
</section>

</main>

<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<script>
const DATA = DASHBOARD_DATA_PLACEHOLDER;

// ── helpers ────────────────────────────────────────────────────────────────
const fmt = n => (n == null ? "-" : Number(n).toLocaleString());
const pct = r => r == null ? "-" : (r * 100).toFixed(1) + "%";
const usd = v => v == null ? "-" : "$" + Number(v).toFixed(4);
const dateStr = s => s ? s.slice(0, 16).replace("T", " ") : "-";
const esc = s => String(s ?? "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");

function riskBadge(r) {
  if (!r) return "";
  const cls = r === "low" ? "badge-low" : r === "medium" ? "badge-medium" : "badge-high";
  return `<span class="badge ${cls}">${esc(r)}</span>`;
}
function cmdBadge(c) {
  const cls = c === "pack" ? "badge-pack"
            : c === "benchmark" ? "badge-benchmark"
            : c === "simulate-agent" ? "badge-simulate"
            : "badge-other";
  return `<span class="badge ${cls}">${esc(c)}</span>`;
}

// ── summary cards ─────────────────────────────────────────────────────────
document.getElementById("gen-time").textContent =
  "Generated " + new Date().toLocaleString();

const s = DATA.summary;
const cardDefs = [
  { label: "Total Runs", value: fmt(s.total_runs), cls: "" },
  { label: "Pack Runs", value: fmt(s.pack_runs), cls: "blue" },
  { label: "Simulation Runs", value: fmt(s.sim_runs), cls: "" },
  { label: "Benchmark Runs", value: fmt(s.benchmark_runs), cls: "" },
  { label: "Total Input Tokens", value: fmt(s.total_input_tokens), cls: "blue" },
  { label: "Total Tokens Saved", value: fmt(s.total_saved_tokens), cls: "green" },
  { label: "Overall Savings Rate", value: pct(s.savings_rate), cls: s.savings_rate > 0.2 ? "green" : "orange" },
];
document.getElementById("cards").innerHTML = cardDefs.map(c =>
  `<div class="card"><div class="label">${c.label}</div><div class="value ${c.cls}">${c.value}</div></div>`
).join("");

// ── savings donut chart ────────────────────────────────────────────────────
const totalUsed = s.total_input_tokens;
const totalSaved = s.total_saved_tokens;
if (totalUsed + totalSaved === 0) {
  document.getElementById("donut-empty").style.display = "";
  document.getElementById("savingsDonut").style.display = "none";
} else {
  new Chart(document.getElementById("savingsDonut"), {
    type: "doughnut",
    data: {
      labels: ["Tokens Used", "Tokens Saved"],
      datasets: [{
        data: [totalUsed, totalSaved],
        backgroundColor: ["rgba(13,110,253,.75)", "rgba(32,201,151,.75)"],
        borderWidth: 2,
        borderColor: "#fff",
      }],
    },
    options: {
      responsive: true,
      cutout: "60%",
      plugins: {
        legend: { position: "bottom", labels: { font: { size: 12 } } },
        tooltip: {
          callbacks: {
            label: ctx => {
              const v = ctx.parsed;
              const total = ctx.dataset.data.reduce((a, b) => a + b, 0);
              return ` ${ctx.label}: ${v.toLocaleString()} (${(v/total*100).toFixed(1)}%)`;
            },
          },
        },
      },
    },
  });
}

// ── run trend line chart ───────────────────────────────────────────────────
const trend = DATA.run_trend || [];
if (trend.length === 0) {
  document.getElementById("trend-empty").style.display = "";
  document.getElementById("trendChart").style.display = "none";
} else {
  new Chart(document.getElementById("trendChart"), {
    type: "line",
    data: {
      labels: trend.map(r => r.date || r.label || ""),
      datasets: [
        {
          label: "Input Tokens",
          data: trend.map(r => r.input_tokens),
          borderColor: "rgba(13,110,253,.9)",
          backgroundColor: "rgba(13,110,253,.08)",
          fill: true,
          tension: 0.3,
          pointRadius: 3,
        },
        {
          label: "Tokens Saved",
          data: trend.map(r => r.saved_tokens),
          borderColor: "rgba(32,201,151,.9)",
          backgroundColor: "rgba(32,201,151,.08)",
          fill: true,
          tension: 0.3,
          pointRadius: 3,
        },
      ],
    },
    options: {
      responsive: true,
      plugins: { legend: { position: "top" } },
      scales: {
        x: { ticks: { maxRotation: 45, font: { size: 11 } } },
        y: { beginAtZero: true, ticks: { font: { size: 11 } } },
      },
    },
  });
}

// ── token usage chart ──────────────────────────────────────────────────────
const tc = DATA.token_chart;
if (tc.length === 0) {
  document.getElementById("chart-empty").style.display = "";
  document.getElementById("tokenChart").style.display = "none";
} else {
  new Chart(document.getElementById("tokenChart"), {
    type: "bar",
    data: {
      labels: tc.map(r => r.label),
      datasets: [
        { label: "Input Tokens", data: tc.map(r => r.input_tokens),
          backgroundColor: "rgba(13,110,253,.75)", borderRadius: 4 },
        { label: "Tokens Saved", data: tc.map(r => r.saved_tokens),
          backgroundColor: "rgba(32,201,151,.75)", borderRadius: 4 },
      ],
    },
    options: {
      responsive: true, plugins: { legend: { position: "top" } },
      scales: {
        x: { stacked: false, ticks: { maxRotation: 45, font: { size: 11 } } },
        y: { beginAtZero: true, ticks: { font: { size: 11 } } },
      },
    },
  });
}

// ── generic sortable table ─────────────────────────────────────────────────
function makeSortable(tableId, rows, renderRow, filterInputId) {
  const table = document.getElementById(tableId);
  if (!table) return;
  const tbody = table.querySelector("tbody");
  let sortCol = null, sortDir = 1;
  let filterText = "";

  function render() {
    let data = rows.slice();
    if (filterText) {
      const q = filterText.toLowerCase();
      data = data.filter(r => Object.values(r).some(v => String(v ?? "").toLowerCase().includes(q)));
    }
    if (sortCol != null) {
      data.sort((a, b) => {
        const av = a[sortCol] ?? "", bv = b[sortCol] ?? "";
        if (typeof av === "number" && typeof bv === "number") return (av - bv) * sortDir;
        return String(av).localeCompare(String(bv)) * sortDir;
      });
    }
    tbody.innerHTML = data.length
      ? data.map(renderRow).join("")
      : `<tr><td colspan="99" class="empty">No data.</td></tr>`;
  }

  table.querySelectorAll("th[data-col]").forEach(th => {
    th.addEventListener("click", () => {
      const col = th.dataset.col;
      if (sortCol === col) sortDir = -sortDir;
      else { sortCol = col; sortDir = -1; }
      table.querySelectorAll("th").forEach(h => h.classList.remove("sorted-asc","sorted-desc"));
      th.classList.add(sortDir === 1 ? "sorted-asc" : "sorted-desc");
      render();
    });
  });

  if (filterInputId) {
    const inp = document.getElementById(filterInputId);
    if (inp) inp.addEventListener("input", e => { filterText = e.target.value; render(); });
  }

  render();
}

// ── heatmap table with heat colors ────────────────────────────────────────
const heatFiles = (DATA.heatmap.top_token_heavy_files || DATA.heatmap.files || []);
const maxTok = heatFiles.reduce((m, r) => Math.max(m, r.total_compressed_tokens || 0), 1);

makeSortable("heatmap-table", heatFiles, r => {
  const heat = Math.round((r.total_compressed_tokens || 0) / maxTok * 100);
  const heatStyle = `style="background:linear-gradient(90deg,rgba(220,53,69,${heat/200}) ${heat}%,transparent ${heat}%)"`;
  return `<tr>
    <td ${heatStyle}>${esc(r.path)}</td>
    <td class="num">${fmt(r.total_compressed_tokens)}</td>
    <td class="num">${fmt(r.total_original_tokens)}</td>
    <td class="num">${fmt(r.total_saved_tokens)}</td>
    <td class="num">${fmt(r.inclusion_count)}</td>
    <td class="num">${pct(r.inclusion_rate)}</td>
  </tr>`;
}, "heatmap-filter");

if (!heatFiles.length) {
  document.getElementById("sec-heatmap").querySelector("table").innerHTML =
    `<tr><td class="empty">No pack artifacts found — run <code>contextbudget pack</code> first.</td></tr>`;
}

// ── simulation table ───────────────────────────────────────────────────────
makeSortable("sim-table", DATA.simulations, r => `
  <tr>
    <td>${esc(r.task)}</td>
    <td>${esc(r.model)}</td>
    <td class="num">${fmt(r.total_tokens)}</td>
    <td class="num">${fmt(r.steps)}</td>
    <td>${esc(r.context_mode)}</td>
    <td class="num">${usd(r.cost_usd)}</td>
    <td class="muted">${dateStr(r.generated_at)}</td>
  </tr>`);

if (!DATA.simulations.length) {
  document.getElementById("sec-sims").style.display = "none";
}

// ── benchmark cards ────────────────────────────────────────────────────────
const benchBody = document.getElementById("bench-body");
if (!DATA.benchmarks.length) {
  document.getElementById("sec-bench").style.display = "none";
} else {
  benchBody.innerHTML = DATA.benchmarks.map(b => {
    const rows = b.strategies.map(s => `
      <tr>
        <td>${esc(s.strategy)}</td>
        <td class="num">${fmt(s.input_tokens)}</td>
        <td class="num">${fmt(s.saved_tokens)}</td>
        <td>${riskBadge(s.risk)}</td>
        <td class="num muted">${fmt(s.runtime_ms)} ms</td>
      </tr>`).join("");
    return `
      <div style="margin-bottom:20px">
        <div style="font-size:13px;font-weight:600;margin-bottom:6px">${esc(b.task)}
          <span class="muted" style="font-weight:400"> — baseline: ${fmt(b.baseline_tokens)} tokens</span>
        </div>
        <table>
          <thead><tr>
            <th>Strategy</th><th class="num">Input Tokens</th>
            <th class="num">Saved</th><th>Risk</th><th class="num">Runtime</th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;
  }).join("");
}

// ── run history table ──────────────────────────────────────────────────────
makeSortable("history-table", DATA.run_history, r => `
  <tr>
    <td>${cmdBadge(r.command)}</td>
    <td>${esc(r.task)}</td>
    <td class="num">${fmt(r.input_tokens)}</td>
    <td class="num">${fmt(r.saved_tokens)}</td>
    <td class="num">${fmt(r.files)}</td>
    <td>${riskBadge(r.risk)}</td>
    <td class="muted">${dateStr(r.generated_at)}</td>
  </tr>`, "history-filter");
</script>
</body>
</html>
"""


def _build_html(data: dict[str, Any]) -> str:
    data_json = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return _HTML_TEMPLATE.replace("DASHBOARD_DATA_PLACEHOLDER", data_json)


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    dashboard_html: str = ""
    dashboard_data: dict[str, Any] = {}

    def log_message(self, fmt: str, *args: object) -> None:  # silence access log
        pass

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            body = self.dashboard_html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/data":
            body = json.dumps(self.dashboard_data, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(204)
            self.end_headers()


def serve_dashboard(
    data: dict[str, Any],
    port: int = 7842,
    no_open: bool = False,
) -> None:
    """Start a local HTTP server and serve the dashboard.

    Blocks until the user presses Ctrl-C.
    """
    html = _build_html(data)

    # Inject data into the handler class (simple approach for single-threaded server)
    _Handler.dashboard_html = html
    _Handler.dashboard_data = data

    server = HTTPServer(("127.0.0.1", port), _Handler)
    url = f"http://127.0.0.1:{port}/"

    print(f"Dashboard running at {url}")
    print("Press Ctrl-C to stop.")

    if not no_open:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
    finally:
        server.server_close()
