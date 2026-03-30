/**
 * Chat-style WebviewView - single conversational UI replacing the 4 sidebar panels.
 * Messages flow as a conversation: user types task -> system shows analyzing -> results appear as rich cards.
 */

import * as vscode from 'vscode';
import { state as appState } from '../state';
import { getNonce, getSharedStyles, escapeHtml, formatTokens } from './theme';
import type {
  RunReport,
  DoctorReport,
  BenchmarkReport,
  SimulationReport,
  DriftReport,
  AgentPlanReport,
  CompressedFileJson,
} from '../types';

/* ------------------------------------------------------------------ */
/*  Message types                                                      */
/* ------------------------------------------------------------------ */

interface ChatMessage {
  id: string;
  role: 'user' | 'system' | 'result';
  type: string;
  html: string;
  timestamp: number;
}

let msgCounter = 0;
function nextId(): string {
  return `msg-${Date.now()}-${++msgCounter}`;
}

/* ------------------------------------------------------------------ */
/*  Strategy helpers                                                   */
/* ------------------------------------------------------------------ */

function pillClass(s: string): string {
  if (s === 'full') return 'pill-full';
  if (s === 'snippet') return 'pill-snippet';
  if (s === 'symbol_extraction') return 'pill-symbol';
  if (s === 'summary') return 'pill-summary';
  if (s === 'cache_reuse') return 'pill-cache';
  return 'pill-slicing';
}

function riskBadge(risk: string): string {
  if (risk === 'low') return '<span class="badge badge-success">low risk</span>';
  if (risk === 'medium') return '<span class="badge badge-warning">medium risk</span>';
  return '<span class="badge badge-error">high risk</span>';
}

/* ------------------------------------------------------------------ */
/*  Card renderers                                                     */
/* ------------------------------------------------------------------ */

function renderWelcome(): string {
  return `
    <div class="welcome animate-in">
      <div class="welcome-icon">&#9889;</div>
      <div class="welcome-title">Redcon</div>
      <div class="welcome-sub">Context budgeting for AI coding agents</div>
      <div class="welcome-hint">Type a task below to analyze and pack your repository context.</div>
      <div class="welcome-actions">
        <button class="btn btn-sm btn-primary" data-send="doctor">Run Doctor</button>
        <button class="btn btn-sm btn-primary" data-send="config">Open Config</button>
      </div>
    </div>`;
}

function renderTutorial(): string {
  return `
    <div class="result-card animate-in">
      <div class="result-section-title">Quick Start Guide</div>
      <div class="tutorial-steps">
        <div class="tutorial-step">
          <span class="tutorial-num">1</span>
          <div class="tutorial-body">
            <div class="tutorial-step-title">Initialize config</div>
            <div class="card-sub">Create a <code>redcon.toml</code> config in your project root to set token budgets and compression strategies.</div>
            <div class="tutorial-actions">
              <button class="btn btn-sm" data-send="config">Open Config</button>
            </div>
          </div>
        </div>
        <div class="tutorial-step">
          <span class="tutorial-num">2</span>
          <div class="tutorial-body">
            <div class="tutorial-step-title">Describe your task</div>
            <div class="card-sub">Type what you're working on in the input bar below (e.g. "add user authentication") and hit send. Redcon will rank and compress the most relevant files to fit your token budget.</div>
          </div>
        </div>
        <div class="tutorial-step">
          <span class="tutorial-num">3</span>
          <div class="tutorial-body">
            <div class="tutorial-step-title">Review and copy</div>
            <div class="card-sub">See which files were included, compression strategies used, and token budget consumed. Copy the packed context to paste into your AI agent.</div>
          </div>
        </div>
        <div class="tutorial-step">
          <span class="tutorial-num">4</span>
          <div class="tutorial-body">
            <div class="tutorial-step-title">Explore more</div>
            <div class="card-sub">Check environment health, view detailed analytics, or run diagnostics.</div>
            <div class="tutorial-actions">
              <button class="btn btn-sm" data-send="doctor">Doctor</button>
              <button class="btn btn-sm" data-action="dashboard">Dashboard</button>
            </div>
          </div>
        </div>
      </div>
    </div>`;
}

function renderUserTask(task: string): string {
  return `<div class="msg-user-bubble">${escapeHtml(task)}</div>`;
}

function renderAnalyzing(label: string): string {
  return `
    <div class="msg-system analyzing">
      <span class="dot-pulse"></span>
      <span>${escapeHtml(label)}</span>
      <span class="analyzing-timer" style="margin-left:auto;font-size:10px;color:var(--muted);">0s</span>
    </div>`;
}

function renderPackResult(run: RunReport): string {
  const b = run.budget;
  const used = b.estimated_input_tokens;
  const max = run.max_tokens;
  const saved = b.estimated_saved_tokens;
  const pct = max > 0 ? Math.round((used / max) * 100) : 0;
  const totalRaw = used + saved;
  const comprPct = totalRaw > 0 ? Math.round((saved / totalRaw) * 100) : 0;

  // Gauge
  const r = 40;
  const circ = 2 * Math.PI * r;
  const offset = circ - (circ * Math.min(pct, 100)) / 100;
  const gaugeColor = pct > 90 ? 'var(--error)' : pct > 70 ? 'var(--warning)' : 'var(--success)';

  // Strategy counts
  const stratCounts: Record<string, number> = {};
  for (const f of run.compressed_context ?? []) {
    stratCounts[f.strategy] = (stratCounts[f.strategy] ?? 0) + 1;
  }

  const stratPills = Object.entries(stratCounts)
    .sort((a, b) => b[1] - a[1])
    .map(([s, c]) => `<span class="pill ${pillClass(s)}">${s.replace('_', ' ')} ${c}</span>`)
    .join(' ');

  // Top files (max 8)
  const topFiles = (run.compressed_context ?? []).slice(0, 8);
  const totalCompressed = run.compressed_context?.length ?? 0;

  const fileRows = topFiles.map((f: CompressedFileJson) => {
    const savedPct = f.original_tokens > 0
      ? Math.round(((f.original_tokens - f.compressed_tokens) / f.original_tokens) * 100)
      : 0;
    const name = f.path.split('/').pop() ?? f.path;
    const dir = f.path.split('/').slice(0, -1).join('/');
    return `
      <div class="file-item" data-action="openFile" data-data="${escapeHtml(f.path)}">
        <div class="file-item-body">
          <div class="file-item-name">${escapeHtml(name)}</div>
          <div class="file-item-meta">
            <span>${escapeHtml(dir)}</span>
            <span class="pill ${pillClass(f.strategy)}" style="padding:1px 5px;font-size:9px;">${f.strategy.replace('_', ' ')}</span>
          </div>
        </div>
        <div class="file-item-right">
          <div style="font-size:11px;font-weight:600;">${formatTokens(f.compressed_tokens)}</div>
          ${savedPct > 0 ? `<div style="font-size:9px;color:var(--success);">-${savedPct}%</div>` : ''}
        </div>
      </div>`;
  }).join('');

  const moreFiles = totalCompressed > 8
    ? `<div class="more-link" data-action="dashboard">${totalCompressed - 8} more files - open dashboard</div>`
    : '';

  return `
    <div class="result-card animate-in">
      <!-- Header with gauge -->
      <div class="result-header">
        <div class="gauge-ring" style="width:90px;height:90px;">
          <svg width="90" height="90" viewBox="0 0 90 90">
            <circle class="gauge-ring-bg" cx="45" cy="45" r="${r}" stroke-width="6"/>
            <circle class="gauge-ring-fill" cx="45" cy="45" r="${r}" stroke-width="6"
              stroke="${gaugeColor}"
              stroke-dasharray="${circ}"
              stroke-dashoffset="${offset}"/>
          </svg>
          <div class="gauge-ring-center">
            <div class="gauge-ring-value" style="color:${gaugeColor};font-size:16px;">${pct}%</div>
            <div class="gauge-ring-label">used</div>
          </div>
        </div>
        <div class="result-kpis">
          <div class="kpi"><span class="kpi-val">${formatTokens(used)}<span class="kpi-dim">/${formatTokens(max)}</span></span><span class="kpi-label">tokens</span></div>
          <div class="kpi"><span class="kpi-val" style="color:var(--success);">${formatTokens(saved)}</span><span class="kpi-label">${comprPct}% saved</span></div>
          <div class="kpi"><span class="kpi-val">${run.files_included.length}</span><span class="kpi-label">files (${run.files_skipped.length} skipped)</span></div>
          <div class="kpi">${riskBadge(b.quality_risk_estimate)}</div>
        </div>
      </div>

      <!-- Strategies -->
      <div class="result-strats">${stratPills}</div>

      <!-- File list -->
      <div class="result-files">${fileRows}${moreFiles}</div>

      <!-- Actions -->
      <div class="actions-row">
        <button class="btn btn-sm" data-action="copy">Copy Context</button>
        <button class="btn btn-sm" data-action="export">Export</button>
        <button class="btn btn-sm btn-primary" data-action="dashboard">Dashboard</button>
      </div>
    </div>`;
}

function renderDoctorResult(doc: DoctorReport): string {
  const checks = doc.checks.map((c) => {
    const icon = c.status === 'ok' ? '&#10003;' : c.status === 'warn' ? '&#9888;' : '&#10007;';
    const cls = c.status === 'ok' ? 'check-ok' : c.status === 'warn' ? 'check-warn' : 'check-fail';
    return `<div class="check-item ${cls}"><span class="check-icon">${icon}</span><span>${escapeHtml(c.name)}: ${escapeHtml(c.message)}</span></div>`;
  }).join('');

  const statusLine = doc.failures > 0
    ? `<span class="badge badge-error">${doc.failures} failed</span>`
    : doc.warnings > 0
      ? `<span class="badge badge-warning">${doc.warnings} warnings</span>`
      : '<span class="badge badge-success">all passed</span>';

  return `
    <div class="result-card animate-in">
      <div class="result-section-title">Doctor ${statusLine}</div>
      <div class="card-sub" style="margin-bottom:8px;">v${escapeHtml(doc.redcon_version)} - Python ${escapeHtml(doc.python_version)} - ${escapeHtml(doc.platform)}</div>
      <div class="check-list">${checks}</div>
    </div>`;
}

function renderBenchmarkResult(bench: BenchmarkReport): string {
  const rows = bench.strategies.map((s) => `
    <tr>
      <td><span class="pill ${pillClass(s.strategy)}" style="padding:1px 5px;font-size:9px;">${s.strategy}</span></td>
      <td>${formatTokens(s.estimated_input_tokens)}</td>
      <td style="color:var(--success);">${formatTokens(s.estimated_saved_tokens)}</td>
      <td>${s.files_included.length}</td>
      <td>${riskBadge(s.quality_risk_estimate)}</td>
    </tr>`).join('');

  return `
    <div class="result-card animate-in">
      <div class="result-section-title">Benchmark</div>
      <table class="mini-table">
        <thead><tr><th>Strategy</th><th>Tokens</th><th>Saved</th><th>Files</th><th>Risk</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

function renderSimulateResult(sim: SimulationReport): string {
  const steps = sim.steps.map((s, i) => {
    const cost = sim.cost_estimate.steps_cost[i];
    return `<div class="sim-step">
      <span class="sim-step-name">${escapeHtml(s.title)}</span>
      <span>${formatTokens(s.context_tokens)} tok</span>
      <span style="color:var(--success);">$${cost?.total_cost_usd?.toFixed(4) ?? '?'}</span>
    </div>`;
  }).join('');

  return `
    <div class="result-card animate-in">
      <div class="result-section-title">Cost Simulation - ${escapeHtml(sim.model)}</div>
      <div class="grid grid-3" style="margin-bottom:8px;">
        <div class="card" style="text-align:center;padding:8px;">
          <div class="card-title">Total</div>
          <div style="font-size:16px;font-weight:700;color:var(--success);">$${sim.cost_estimate.total_cost_usd.toFixed(4)}</div>
        </div>
        <div class="card" style="text-align:center;padding:8px;">
          <div class="card-title">Input</div>
          <div style="font-size:12px;">$${sim.cost_estimate.total_input_cost_usd.toFixed(4)}</div>
        </div>
        <div class="card" style="text-align:center;padding:8px;">
          <div class="card-title">Output</div>
          <div style="font-size:12px;">$${sim.cost_estimate.total_output_cost_usd.toFixed(4)}</div>
        </div>
      </div>
      <div class="sim-steps">${steps}</div>
    </div>`;
}

function renderDriftResult(drift: DriftReport): string {
  const cls = drift.drift_detected ? 'badge-warning' : 'badge-success';
  const icon = drift.drift_detected ? '&#9888;' : '&#10003;';
  return `
    <div class="result-card animate-in">
      <div class="result-section-title">${icon} Drift Check</div>
      <span class="badge ${cls}">${drift.drift_detected ? `${drift.drift_pct.toFixed(1)}% growth` : 'no drift'}</span>
      <div class="card-sub" style="margin-top:6px;">${escapeHtml(drift.message)}</div>
      ${drift.drift_detected ? `<div class="card-sub">Trend: ${escapeHtml(drift.trend)} | Mean: ${formatTokens(drift.mean_tokens)} | Latest: ${formatTokens(drift.latest_tokens)}</div>` : ''}
    </div>`;
}

function renderPlanAgentResult(plan: AgentPlanReport): string {
  const steps = plan.steps.map((s) => `
    <div class="plan-step card" style="padding:8px;margin-bottom:4px;">
      <div style="font-weight:600;font-size:11px;">${escapeHtml(s.id)}. ${escapeHtml(s.title)}</div>
      <div class="card-sub">${escapeHtml(s.objective)}</div>
      <div class="file-item-meta" style="margin-top:4px;">
        <span>${formatTokens(s.estimated_tokens)} tok</span>
        <span>${s.context.length} files</span>
      </div>
    </div>`).join('');

  return `
    <div class="result-card animate-in">
      <div class="result-section-title">Agent Workflow - ${plan.steps.length} steps</div>
      <div class="grid grid-2" style="margin-bottom:8px;">
        <div class="card" style="text-align:center;padding:8px;">
          <div class="card-title">Total</div>
          <div style="font-size:14px;font-weight:700;">${formatTokens(plan.total_estimated_tokens)}</div>
        </div>
        <div class="card" style="text-align:center;padding:8px;">
          <div class="card-title">Reused</div>
          <div style="font-size:14px;font-weight:700;color:var(--success);">${formatTokens(plan.reused_context_tokens)}</div>
        </div>
      </div>
      ${steps}
    </div>`;
}

function renderError(message: string): string {
  return `<div class="msg-error animate-in"><span>&#10007;</span> ${escapeHtml(message)}<button class="btn btn-sm" data-action="retry" style="margin-left:auto;font-size:10px;">Retry</button></div>`;
}

function renderInfo(message: string): string {
  return `<div class="msg-info animate-in">${escapeHtml(message)}</div>`;
}

const STRAT_COLORS: Record<string, string> = {
  full: '#4ec9b0', snippet: '#dcdcaa', symbol_extraction: '#1e3a5f',
  summary: '#888', slicing: '#6a9955', cache_reuse: '#c678dd',
};

function renderDashboardCard(run: RunReport): string {
  const used = run.budget.estimated_input_tokens;
  const max = run.max_tokens;
  const saved = run.budget.estimated_saved_tokens;
  const pct = max > 0 ? Math.round((used / max) * 100) : 0;
  const available = max - used;

  const totalOrig = run.compressed_context.reduce((s, f) => s + f.original_tokens, 0);
  const totalComp = run.compressed_context.reduce((s, f) => s + f.compressed_tokens, 0);
  const comprPct = totalOrig > 0 ? Math.round(((totalOrig - totalComp) / totalOrig) * 100) : 0;

  // Strategy counts
  const stratCounts: Record<string, number> = {};
  for (const f of run.compressed_context) {
    stratCounts[f.strategy] = (stratCounts[f.strategy] ?? 0) + 1;
  }

  // Donut SVG for budget
  const r = 44;
  const circ = 2 * Math.PI * r;
  const usedLen = max > 0 ? (used / max) * circ : 0;
  const gaugeColor = pct > 90 ? '#f14c4c' : pct > 70 ? '#dcdcaa' : '#4ec9b0';

  // Strategy pie segments
  const stratEntries = Object.entries(stratCounts).sort((a, b) => b[1] - a[1]);
  const totalFiles = stratEntries.reduce((s, [, c]) => s + c, 0);
  let pieOffset = 0;
  const piePaths = stratEntries.map(([s, c]) => {
    const len = totalFiles > 0 ? (c / totalFiles) * circ : 0;
    const html = `<circle cx="50" cy="50" r="${r}" fill="none"
      stroke="${STRAT_COLORS[s] ?? '#888'}" stroke-width="10"
      stroke-dasharray="${Math.max(0, len - 1.5)} ${circ - Math.max(0, len - 1.5)}"
      stroke-dashoffset="${-pieOffset}" stroke-linecap="round"/>`;
    pieOffset += len;
    return html;
  }).join('');

  // Top files horizontal bars
  const topFiles = run.compressed_context.slice(0, 10);
  const maxTok = Math.max(...topFiles.map((f) => f.original_tokens), 1);
  const fileBars = topFiles.map((f) => {
    const name = f.path.split('/').pop() ?? f.path;
    const origW = Math.round((f.original_tokens / maxTok) * 100);
    const compW = Math.round((f.compressed_tokens / maxTok) * 100);
    const savedPct = f.original_tokens > 0 ? Math.round(((f.original_tokens - f.compressed_tokens) / f.original_tokens) * 100) : 0;
    return `
      <div style="display:flex;align-items:center;gap:6px;margin-bottom:3px;">
        <div style="width:100px;font-size:9px;text-align:right;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--muted);" title="${escapeHtml(f.path)}">${escapeHtml(name)}</div>
        <div style="flex:1;height:14px;background:var(--input);border-radius:3px;overflow:hidden;position:relative;">
          <div style="position:absolute;top:0;left:0;height:100%;width:${origW}%;background:var(--card-border);border-radius:3px;opacity:0.5;"></div>
          <div style="position:absolute;top:0;left:0;height:100%;width:${compW}%;background:var(--accent);border-radius:3px;"></div>
        </div>
        <div style="width:36px;font-size:9px;color:var(--success);">${savedPct > 0 ? `-${savedPct}%` : '0%'}</div>
      </div>`;
  }).join('');

  const stratLegend = stratEntries.map(([s, c]) => `
    <div style="display:flex;align-items:center;gap:5px;font-size:10px;">
      <span style="width:8px;height:8px;border-radius:2px;background:${STRAT_COLORS[s] ?? '#888'};flex-shrink:0;"></span>
      <span style="flex:1;">${s.replace(/_/g, ' ')}</span>
      <span style="font-weight:600;">${c}</span>
    </div>`).join('');

  return `
    <div class="result-card animate-in">
      <div class="result-section-title" style="margin-bottom:12px;">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" style="color:var(--accent);">
          <path d="M3 3v18h18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
          <path d="M7 16l4-6 4 4 5-8" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
        Analytics - ${escapeHtml(run.task)}
      </div>

      <!-- KPI row -->
      <div class="grid grid-2" style="margin-bottom:10px;">
        <div class="card" style="text-align:center;padding:8px;">
          <div class="card-title">Budget</div>
          <div style="font-size:18px;font-weight:700;color:${gaugeColor};">${pct}%</div>
          <div class="card-sub">${formatTokens(used)} / ${formatTokens(max)}</div>
        </div>
        <div class="card" style="text-align:center;padding:8px;">
          <div class="card-title">Saved</div>
          <div style="font-size:18px;font-weight:700;color:var(--success);">${formatTokens(saved)}</div>
          <div class="card-sub">${comprPct}% compression</div>
        </div>
      </div>

      <!-- Charts row -->
      <div class="grid grid-2" style="margin-bottom:10px;">
        <div class="card" style="padding:10px;">
          <div class="card-title">Budget Usage</div>
          <div style="display:flex;align-items:center;justify-content:center;gap:12px;padding:6px 0;">
            <div style="position:relative;display:flex;align-items:center;justify-content:center;">
              <svg width="100" height="100" viewBox="0 0 100 100" style="transform:rotate(-90deg);">
                <circle cx="50" cy="50" r="${r}" fill="none" stroke="var(--input)" stroke-width="10"/>
                <circle cx="50" cy="50" r="${r}" fill="none" stroke="${gaugeColor}" stroke-width="10"
                  stroke-dasharray="${usedLen} ${circ - usedLen}" stroke-linecap="round"/>
              </svg>
              <div style="position:absolute;text-align:center;">
                <div style="font-size:16px;font-weight:700;color:${gaugeColor};">${pct}%</div>
              </div>
            </div>
            <div style="display:flex;flex-direction:column;gap:3px;font-size:10px;">
              <div><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${gaugeColor};vertical-align:middle;margin-right:4px;"></span>Used: ${formatTokens(used)}</div>
              <div><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--input);vertical-align:middle;margin-right:4px;"></span>Free: ${formatTokens(available > 0 ? available : 0)}</div>
            </div>
          </div>
        </div>
        <div class="card" style="padding:10px;">
          <div class="card-title">Strategies</div>
          <div style="display:flex;align-items:center;justify-content:center;gap:12px;padding:6px 0;">
            <svg width="100" height="100" viewBox="0 0 100 100" style="transform:rotate(-90deg);">
              <circle cx="50" cy="50" r="${r}" fill="none" stroke="var(--input)" stroke-width="10"/>
              ${piePaths}
            </svg>
            <div style="display:flex;flex-direction:column;gap:3px;">
              ${stratLegend}
            </div>
          </div>
        </div>
      </div>

      <!-- File bars -->
      <div class="card" style="padding:10px;margin-bottom:10px;">
        <div class="card-title">Token Impact (top ${topFiles.length})</div>
        <div style="padding:4px 0;">${fileBars}</div>
        <div style="display:flex;gap:12px;justify-content:center;font-size:9px;color:var(--muted);padding-top:4px;">
          <span><span style="display:inline-block;width:8px;height:8px;background:var(--card-border);border-radius:2px;opacity:0.5;vertical-align:middle;margin-right:3px;"></span>Original</span>
          <span><span style="display:inline-block;width:8px;height:8px;background:var(--accent);border-radius:2px;vertical-align:middle;margin-right:3px;"></span>Compressed</span>
        </div>
      </div>

      <!-- Actions -->
      <div class="actions-row">
        <button class="btn btn-sm" data-action="copy">Copy Context</button>
        <button class="btn btn-sm" data-action="export">Export</button>
      </div>
    </div>`;
}

/* ------------------------------------------------------------------ */
/*  ChatViewProvider                                                   */
/* ------------------------------------------------------------------ */

export class ChatViewProvider implements vscode.WebviewViewProvider {
  static readonly viewType = 'redcon.chat';

  private view?: vscode.WebviewView;
  private messages: ChatMessage[] = [];

  constructor(private readonly extensionUri: vscode.Uri) {}

  resolveWebviewView(webviewView: vscode.WebviewView): void {
    this.view = webviewView;
    webviewView.webview.options = {
      enableScripts: true,
      localResourceRoots: [this.extensionUri],
    };

    webviewView.webview.onDidReceiveMessage((msg) => this.handleMessage(msg));
    this.renderShell();

    // Show welcome
    if (this.messages.length === 0) {
      this.addMessage('system', 'welcome', renderWelcome());
    }

    // Restore previous messages
    this.view.webview.postMessage({ command: 'setMessages', messages: this.messages });

    // Update description with savings if available
    this.updateDescription();
  }

  updateDescription(): void {
    if (!this.view) return;
    const run = appState.state.lastRun;
    if (run?.budget) {
      const saved = run.budget.estimated_saved_tokens;
      const total = run.budget.estimated_input_tokens + saved;
      const pct = total > 0 ? Math.round((saved / total) * 100) : 0;
      this.view.description = pct > 0 ? `${pct}% saved` : '';
    }
  }

  /* -- Public API for commands.ts -- */

  addUserMessage(task: string): void {
    this.addMessage('user', 'task', renderUserTask(task));
  }

  addAnalyzing(label: string): string {
    const id = this.addMessage('system', 'analyzing', renderAnalyzing(label));
    return id;
  }

  replaceWithPackResult(analyzingId: string, run: RunReport): void {
    this.replaceMessage(analyzingId, 'result', 'pack-result', renderPackResult(run));
    this.updateDescription();
  }

  replaceWithDoctorResult(analyzingId: string, doc: DoctorReport): void {
    this.replaceMessage(analyzingId, 'result', 'doctor-result', renderDoctorResult(doc));
  }

  replaceWithBenchmarkResult(analyzingId: string, bench: BenchmarkReport): void {
    this.replaceMessage(analyzingId, 'result', 'benchmark-result', renderBenchmarkResult(bench));
  }

  replaceWithSimulateResult(analyzingId: string, sim: SimulationReport): void {
    this.replaceMessage(analyzingId, 'result', 'simulate-result', renderSimulateResult(sim));
  }

  replaceWithDriftResult(analyzingId: string, drift: DriftReport): void {
    this.replaceMessage(analyzingId, 'result', 'drift-result', renderDriftResult(drift));
  }

  replaceWithPlanAgentResult(analyzingId: string, plan: AgentPlanReport): void {
    this.replaceMessage(analyzingId, 'result', 'plan-agent-result', renderPlanAgentResult(plan));
  }

  replaceWithError(analyzingId: string, err: unknown): void {
    const msg = err instanceof Error ? err.message : String(err);
    this.replaceMessage(analyzingId, 'result', 'error', renderError(msg));
  }

  addInfo(message: string): void {
    this.addMessage('system', 'info', renderInfo(message));
  }

  showTutorial(): void {
    this.addMessage('result', 'tutorial', renderTutorial());
  }

  showDashboard(): void {
    const run = appState.state.lastRun;
    if (run) {
      this.addMessage('result', 'dashboard', renderDashboardCard(run));
    } else {
      this.addInfo('No analysis data yet. Send a task first.');
    }
  }

  refresh(): void {
    // Re-render the shell if the view is available
    if (this.view) {
      this.renderShell();
      this.view.webview.postMessage({ command: 'setMessages', messages: this.messages });
    }
  }

  /* -- Internal -- */

  private addMessage(role: ChatMessage['role'], type: string, html: string): string {
    const id = nextId();
    const msg: ChatMessage = { id, role, type, html, timestamp: Date.now() };
    this.messages.push(msg);

    // Enforce message count limit - remove oldest non-welcome message
    if (this.messages.length > 100) {
      const removeIdx = this.messages.findIndex((m) => m.type !== 'welcome');
      if (removeIdx >= 0) {
        const removed = this.messages.splice(removeIdx, 1)[0];
        this.view?.webview.postMessage({ command: 'removeMessage', id: removed.id });
      }
    }

    this.view?.webview.postMessage({ command: 'addMessage', message: msg });
    return id;
  }

  private replaceMessage(id: string, role: ChatMessage['role'], type: string, html: string): void {
    const idx = this.messages.findIndex((m) => m.id === id);
    if (idx >= 0) {
      this.messages[idx] = { ...this.messages[idx], role, type, html };
    }
    this.view?.webview.postMessage({ command: 'updateMessage', id, html });
  }

  private handleMessage(msg: { command: string; text?: string; action?: string; data?: string }): void {
    switch (msg.command) {
      case 'submit':
        if (msg.text?.trim()) {
          vscode.commands.executeCommand('redcon.pack', msg.text.trim());
        }
        break;
      case 'action':
        this.handleAction(msg.action ?? '', msg.data);
        break;
      case 'send':
        // Quick actions from welcome screen
        if (msg.text === 'doctor') {
          vscode.commands.executeCommand('redcon.doctor');
        } else if (msg.text === 'config') {
          vscode.commands.executeCommand('redcon.openConfig');
        } else if (msg.text === 'help') {
          this.addMessage('result', 'tutorial', renderTutorial());
        }
        break;
    }
  }

  private handleAction(action: string, data?: string): void {
    switch (action) {
      case 'copy':
        vscode.commands.executeCommand('redcon.copyContext');
        break;
      case 'export':
        vscode.commands.executeCommand('redcon.export');
        break;
      case 'dashboard':
        this.showDashboard();
        break;
      case 'sync':
        vscode.commands.executeCommand('redcon.syncContext');
        break;
      case 'retry': {
        // Re-send the last user task
        const lastUser = [...this.messages].reverse().find((m) => m.role === 'user' && m.type === 'task');
        if (lastUser) {
          // Extract raw text from the user bubble html
          const match = lastUser.html.match(/<div class="msg-user-bubble">(.*?)<\/div>/s);
          const text = match ? match[1].replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"') : '';
          if (text) {
            vscode.commands.executeCommand('redcon.pack', text);
          }
        }
        break;
      }
      case 'openFile':
        if (data) {
          const folders = vscode.workspace.workspaceFolders;
          if (folders?.length) {
            const uri = vscode.Uri.joinPath(folders[0].uri, data);
            vscode.window.showTextDocument(uri);
          }
        }
        break;
    }
  }

  private renderShell(): void {
    if (!this.view) return;
    const nonce = getNonce();

    const chatStyles = `
      body { padding: 0; display: flex; flex-direction: column; height: 100vh; overflow: hidden; }

      /* Messages area */
      #messages-wrap {
        flex: 1; position: relative; overflow: hidden;
      }
      #messages-wrap::before, #messages-wrap::after {
        content: '';
        position: absolute; left: 0; right: 0; height: 24px;
        pointer-events: none; z-index: 2;
        transition: opacity 0.3s ease;
      }
      #messages-wrap::before {
        top: 0;
        background: linear-gradient(to bottom, var(--bg), transparent);
      }
      #messages-wrap::after {
        bottom: 0;
        background: linear-gradient(to top, var(--bg), transparent);
      }
      #messages-wrap.at-top::before { opacity: 0; }
      #messages-wrap.at-bottom::after { opacity: 0; }
      #messages {
        height: 100%; overflow-y: auto; padding: 8px;
        display: flex; flex-direction: column;
        scroll-behavior: smooth;
      }
      #messages > .msg { width: 100%; max-width: 600px; margin-left: auto; margin-right: auto; }

      /* Welcome */
      .msg-system:first-child:last-child {
        flex: 1;
        display: flex;
        align-items: center;
        justify-content: center;
      }
      .welcome {
        text-align: center;
        padding: 24px 12px;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        width: 100%;
        max-width: 600px;
      }
      .welcome-icon {
        font-size: 36px; margin-bottom: 10px;
        background: linear-gradient(135deg, #e53935, #1e3a5f);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        filter: drop-shadow(0 0 12px rgba(229, 57, 53, 0.4)) drop-shadow(0 0 24px rgba(30, 58, 95, 0.3));
      }
      .welcome-title { font-size: 16px; font-weight: 700; margin-bottom: 4px; color: var(--fg); }
      .welcome-sub { font-size: 12px; color: var(--muted); margin-bottom: 12px; }
      .welcome-hint { font-size: 11px; color: var(--muted); margin-bottom: 12px; }
      .welcome-actions { display: flex; gap: 6px; justify-content: center; }

      /* User message */
      .msg-user { display: flex; justify-content: flex-end; margin-bottom: 8px; }
      .msg-user-bubble {
        background: linear-gradient(135deg, rgba(229, 57, 53, 0.12), rgba(30, 58, 95, 0.18));
        border: 1px solid var(--card-border);
        border-radius: var(--radius) var(--radius) 4px var(--radius);
        padding: 8px 12px;
        font-size: 12px;
        max-width: 85%;
        word-break: break-word;
      }

      /* System messages */
      .msg-system {
        display: flex; align-items: center; gap: 8px;
        color: var(--muted); font-size: 11px;
        padding: 6px 0; margin-bottom: 4px;
      }
      .analyzing { animation: pulse 1.5s infinite; }

      /* Dot pulse animation */
      .dot-pulse {
        display: inline-flex; gap: 3px;
      }
      .dot-pulse::before, .dot-pulse::after, .dot-pulse {
        position: relative;
      }
      @keyframes dotPulse {
        0%, 80%, 100% { opacity: 0.3; }
        40% { opacity: 1; }
      }
      .dot-pulse::before {
        content: '';
        width: 5px; height: 5px;
        border-radius: 50%;
        background: var(--accent);
        animation: dotPulse 1.4s ease-in-out infinite;
      }

      /* Result cards */
      .result-card {
        background: var(--card);
        border: 1px solid var(--card-border);
        border-radius: var(--radius);
        padding: 12px;
        margin-bottom: 8px;
        backdrop-filter: blur(10px);
      }
      .result-header {
        display: flex; align-items: center; gap: 12px;
        margin-bottom: 10px;
      }
      .result-kpis { flex: 1; }
      .kpi { display: flex; flex-direction: column; margin-bottom: 4px; }
      .kpi-val { font-size: 13px; font-weight: 600; }
      .kpi-dim { font-size: 11px; font-weight: 400; color: var(--muted); }
      .kpi-label { font-size: 10px; color: var(--muted); }

      .result-strats { display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 8px; }

      .result-files { margin-bottom: 8px; }

      .result-section-title {
        font-size: 12px; font-weight: 600;
        margin-bottom: 8px;
        display: flex; align-items: center; gap: 6px;
      }

      .more-link {
        text-align: center; font-size: 10px; color: var(--accent);
        cursor: pointer; padding: 4px; margin-top: 4px;
      }
      .more-link:hover { text-decoration: underline; }

      /* Actions */
      .actions-row { display: flex; flex-wrap: wrap; gap: 4px; }

      /* Error / Info */
      .msg-error {
        background: var(--error-dim); border: 1px solid color-mix(in srgb, var(--error) 30%, transparent);
        border-radius: var(--radius); padding: 8px 12px; font-size: 11px; color: var(--error);
        margin-bottom: 8px;
      }
      .msg-info {
        color: var(--muted); font-size: 11px;
        padding: 4px 0; margin-bottom: 4px;
      }

      /* Doctor checks */
      .check-list { display: flex; flex-direction: column; gap: 4px; }
      .check-item {
        display: flex; align-items: flex-start; gap: 6px;
        font-size: 11px; padding: 4px 0;
      }
      .check-icon { flex-shrink: 0; }
      .check-ok .check-icon { color: var(--success); }
      .check-warn .check-icon { color: var(--warning); }
      .check-fail .check-icon { color: var(--error); }

      /* Benchmark table */
      .mini-table {
        width: 100%; border-collapse: collapse; font-size: 10px;
      }
      .mini-table th {
        text-align: left; padding: 4px 6px;
        border-bottom: 1px solid var(--card-border);
        color: var(--muted); font-weight: 600;
      }
      .mini-table td { padding: 4px 6px; }
      .mini-table tr:hover td { background: var(--card-hover); }

      /* Simulate steps */
      .sim-steps { display: flex; flex-direction: column; gap: 4px; }
      .sim-step {
        display: flex; align-items: center; gap: 8px;
        font-size: 11px; padding: 4px 0;
        border-bottom: 1px solid color-mix(in srgb, var(--card-border) 50%, transparent);
      }
      .sim-step-name { flex: 1; }

      /* Tutorial */
      .tutorial-steps { display: flex; flex-direction: column; gap: 4px; }
      .tutorial-step {
        display: flex; align-items: flex-start; gap: 10px;
        padding: 12px 0;
        border-bottom: 1px solid color-mix(in srgb, var(--card-border) 50%, transparent);
      }
      .tutorial-step:last-child { border-bottom: none; padding-bottom: 4px; }
      .tutorial-num {
        width: 24px; height: 24px;
        border-radius: 50%;
        background: linear-gradient(135deg, #e53935, #1e3a5f);
        color: #fff;
        font-size: 12px; font-weight: 700;
        display: flex; align-items: center; justify-content: center;
        flex-shrink: 0;
      }
      .tutorial-body { flex: 1; }
      .tutorial-step-title { font-size: 12px; font-weight: 600; margin-bottom: 2px; }
      .tutorial-actions {
        display: flex; gap: 6px; flex-wrap: wrap;
        padding-top: 5px;
      }
      .tutorial-step code {
        background: var(--input);
        padding: 1px 4px;
        border-radius: 3px;
        font-size: 10px;
      }

      /* Input bar */
      #input-wrap {
        background: var(--bg);
        padding: 8px;
      }
      #input-bar {
        display: flex; gap: 8px;
        align-items: center;
        width: 100%;
        max-width: 600px;
        margin: 0 auto;
        box-sizing: border-box;
        padding: 6px 6px 6px 14px;
        border: 1.5px solid rgba(229, 57, 53, 0.3);
        border-radius: 22px;
        background: var(--input);
        transition: border-color 0.5s cubic-bezier(0.4, 0, 0.2, 1), box-shadow 0.5s cubic-bezier(0.4, 0, 0.2, 1);
        box-shadow: 0 0 0 transparent;
      }
      #input-bar:focus-within {
        border-color: rgba(229, 57, 53, 0.6);
        box-shadow: 0 0 16px rgba(229, 57, 53, 0.2), 0 0 40px rgba(30, 58, 95, 0.15), 0 0 60px rgba(229, 57, 53, 0.06);
      }
      #task-input {
        flex: 1;
        padding: 6px 0;
        border: none;
        background: transparent;
        color: var(--fg);
        font-size: 12px;
        font-family: inherit;
        outline: none;
      }
      #task-input::placeholder { color: var(--muted); }
      #send-btn {
        width: 30px;
        height: 30px;
        padding: 0;
        border: none;
        border-radius: 50%;
        background: linear-gradient(135deg, #e53935, #1e3a5f);
        color: #fff;
        cursor: pointer;
        display: flex;
        align-items: center;
        justify-content: center;
        flex-shrink: 0;
        transition: all var(--transition);
        box-shadow: 0 0 6px rgba(229, 57, 53, 0.2), 0 0 12px rgba(30, 58, 95, 0.2);
      }
      #send-btn svg {
        display: block;
      }
      #send-btn:hover { opacity: 0.85; box-shadow: 0 0 10px rgba(229, 57, 53, 0.3), 0 0 20px rgba(30, 58, 95, 0.3); }
      #send-btn:disabled { opacity: 0.4; cursor: default; box-shadow: none; }

      /* Gradient border pseudo for input bar */
      #input-bar { position: relative; }
      #input-bar::before {
        content: '';
        position: absolute;
        inset: -1.5px;
        border-radius: 23px;
        padding: 1.5px;
        background: linear-gradient(135deg, #e53935, #1e3a5f);
        -webkit-mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
        -webkit-mask-composite: xor;
        mask-composite: exclude;
        opacity: 0.3;
        pointer-events: none;
        transition: opacity 0.5s cubic-bezier(0.4, 0, 0.2, 1);
      }
      #input-bar:focus-within::before {
        opacity: 0.6;
      }
    `;

    const script = `
      const vscode = acquireVsCodeApi();
      const messagesEl = document.getElementById('messages');
      const wrapEl = document.getElementById('messages-wrap');
      const inputEl = document.getElementById('task-input');
      const sendBtn = document.getElementById('send-btn');

      function updateScrollClasses() {
        const t = messagesEl.scrollTop;
        const h = messagesEl.scrollHeight - messagesEl.clientHeight;
        wrapEl.classList.toggle('at-top', t < 4);
        wrapEl.classList.toggle('at-bottom', t >= h - 4);
      }
      messagesEl.addEventListener('scroll', updateScrollClasses);

      function scrollToBottom() {
        messagesEl.scrollTop = messagesEl.scrollHeight;
        updateScrollClasses();
      }

      // Track analyzing timers so they can be cleared on replace
      const analyzingTimers = {};

      function appendMessage(msg) {
        const wrapper = document.createElement('div');
        wrapper.className = 'msg msg-' + msg.role;
        wrapper.id = msg.id;
        wrapper.innerHTML = msg.html;
        messagesEl.appendChild(wrapper);
        scrollToBottom();

        // Start elapsed timer for analyzing messages
        const analyzingEl = wrapper.querySelector('.analyzing');
        if (analyzingEl) {
          const timerSpan = analyzingEl.querySelector('.analyzing-timer');
          if (timerSpan) {
            let seconds = 0;
            analyzingTimers[msg.id] = setInterval(() => {
              seconds++;
              timerSpan.textContent = seconds + 's';
            }, 1000);
          }
        }
      }

      function removeMessage(id) {
        const el = document.getElementById(id);
        if (el) el.remove();
        if (analyzingTimers[id]) {
          clearInterval(analyzingTimers[id]);
          delete analyzingTimers[id];
        }
      }

      function submit() {
        const text = inputEl.value.trim();
        if (!text) return;
        inputEl.value = '';
        vscode.postMessage({ command: 'submit', text: text });
      }

      inputEl.addEventListener('keydown', function(e) {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          submit();
        }
      });

      sendBtn.addEventListener('click', submit);

      // Delegated click handler for data-send and data-action attributes
      document.addEventListener('click', function(e) {
        const target = e.target.closest('[data-send]');
        if (target) {
          vscode.postMessage({ command: 'send', text: target.dataset.send });
          return;
        }
        const actionEl = e.target.closest('[data-action]');
        if (actionEl) {
          vscode.postMessage({ command: 'action', action: actionEl.dataset.action, data: actionEl.dataset.data || '' });
        }
      });

      window.addEventListener('message', function(event) {
        const msg = event.data;
        if (msg.command === 'addMessage') {
          appendMessage(msg.message);
        } else if (msg.command === 'updateMessage') {
          // Clear analyzing timer if present
          if (analyzingTimers[msg.id]) {
            clearInterval(analyzingTimers[msg.id]);
            delete analyzingTimers[msg.id];
          }
          const el = document.getElementById(msg.id);
          if (el) {
            el.innerHTML = msg.html;
            el.className = el.className.replace(/msg-(user|system|result)/, 'msg-result');
            scrollToBottom();
          }
        } else if (msg.command === 'removeMessage') {
          removeMessage(msg.id);
        } else if (msg.command === 'setMessages') {
          messagesEl.innerHTML = '';
          for (const m of msg.messages) {
            appendMessage(m);
          }
        }
      });

      // Rotating placeholder text
      const placeholders = [
        'Describe your task...',
        'e.g. add user authentication',
        'e.g. refactor database layer',
        'e.g. fix payment webhook handler',
        'e.g. write API integration tests',
        'What are you working on?',
      ];
      let placeholderIdx = 0;
      setInterval(() => {
        placeholderIdx = (placeholderIdx + 1) % placeholders.length;
        inputEl.placeholder = placeholders[placeholderIdx];
      }, 5000);
    `;

    const body = `
      <div id="messages-wrap" class="at-top at-bottom">
        <div id="messages" role="log" aria-live="polite"></div>
      </div>
      <div id="input-wrap">
        <div id="input-bar">
          <input type="text" id="task-input" placeholder="Describe your task..." aria-label="Task description" />
          <button id="send-btn" title="Send" aria-label="Send message"><svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" style="margin-left:2px;"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/></svg></button>
        </div>
      </div>
    `;

    this.view.webview.html = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'nonce-${nonce}'; script-src 'nonce-${nonce}';">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  ${getSharedStyles(nonce)}
  <style nonce="${nonce}">${chatStyles}</style>
</head>
<body>
  ${body}
  <script nonce="${nonce}">${script}</script>
</body>
</html>`;
  }
}
