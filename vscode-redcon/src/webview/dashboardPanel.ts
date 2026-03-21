/**
 * Dashboard Webview - analytics center with SVG charts.
 */

import * as vscode from 'vscode';
import { state } from '../state';

const STRATEGY_COLORS: Record<string, string> = {
  full: '#4ec9b0',
  snippet: '#dcdcaa',
  symbol_extraction: '#e53935',
  summary: '#888',
  slicing: '#6a9955',
  cache_reuse: '#c678dd',
};

function stratColor(s: string): string {
  return STRATEGY_COLORS[s] ?? '#888';
}

export class DashboardPanel {
  private static instance: DashboardPanel | undefined;
  private panel: vscode.WebviewPanel;
  private disposables: vscode.Disposable[] = [];

  private constructor(extensionUri: vscode.Uri) {
    this.panel = vscode.window.createWebviewPanel(
      'redconDashboard',
      'Redcon Dashboard',
      vscode.ViewColumn.Active,
      {
        enableScripts: true,
        retainContextWhenHidden: true,
        localResourceRoots: [extensionUri],
      },
    );

    this.panel.iconPath = new vscode.ThemeIcon('graph');
    this.update();

    const stateListener = state.onDidChange(() => this.update());
    this.disposables.push(stateListener);

    this.panel.webview.onDidReceiveMessage(
      (msg) => {
        switch (msg.command) {
          case 'openFile':
            vscode.commands.executeCommand('vscode.open', vscode.Uri.file(msg.path));
            break;
          case 'runPack':
            vscode.commands.executeCommand('redcon.pack');
            break;
          case 'copyContext':
            vscode.commands.executeCommand('redcon.copyContext');
            break;
        }
      },
      null,
      this.disposables,
    );

    this.panel.onDidDispose(() => {
      DashboardPanel.instance = undefined;
      this.disposables.forEach((d) => d.dispose());
    });
  }

  static show(extensionUri: vscode.Uri): void {
    if (DashboardPanel.instance) {
      DashboardPanel.instance.panel.reveal(vscode.ViewColumn.Active);
      return;
    }
    DashboardPanel.instance = new DashboardPanel(extensionUri);
  }

  private update(): void {
    this.panel.webview.html = this.getHtml();
  }

  private getHtml(): string {
    const run = state.state.lastRun;
    const nonce = getNonce();

    return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'nonce-${nonce}'; script-src 'nonce-${nonce}';">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Redcon Dashboard</title>
  <style nonce="${nonce}">
    :root {
      --bg: var(--vscode-editor-background);
      --fg: var(--vscode-editor-foreground);
      --border: var(--vscode-panel-border);
      --card-bg: var(--vscode-sideBar-background);
      --accent: #e53935;
      --accent-dim: rgba(229, 57, 53, 0.15);
      --success: #4ec9b0;
      --warning: #dcdcaa;
      --error: #f14c4c;
      --muted: var(--vscode-descriptionForeground);
      --input-bg: var(--vscode-input-background);
      --radius: 10px;
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      font-family: var(--vscode-font-family, system-ui, -apple-system, sans-serif);
      font-size: 13px;
      color: var(--fg);
      background: var(--bg);
      padding: 24px;
      line-height: 1.5;
      max-width: 1200px;
      margin: 0 auto;
    }

    .header {
      display: flex;
      align-items: center;
      gap: 16px;
      margin-bottom: 28px;
      padding-bottom: 16px;
      border-bottom: 1px solid var(--border);
    }
    .header-left { flex: 1; }
    .header h1 { font-size: 1.5em; font-weight: 700; display: flex; align-items: center; gap: 10px; }
    .header h1 svg { color: var(--accent); }
    .header .task { color: var(--muted); font-size: 0.85em; margin-top: 4px; }
    .header-actions { display: flex; gap: 8px; }

    .btn {
      display: inline-flex; align-items: center; gap: 6px;
      padding: 7px 16px; border: 1px solid var(--border); border-radius: var(--radius);
      background: var(--input-bg); color: var(--fg); cursor: pointer;
      font-size: 0.85em; transition: all 0.2s; font-family: inherit;
    }
    .btn:hover { border-color: var(--accent); }
    .btn-primary {
      background: var(--accent); color: #fff; border-color: var(--accent);
      box-shadow: 0 0 8px rgba(229, 57, 53, 0.3);
    }
    .btn-primary:hover { opacity: 0.9; box-shadow: 0 0 14px rgba(229, 57, 53, 0.5); }

    /* Layout */
    .row { display: grid; gap: 20px; margin-bottom: 24px; }
    .row-2 { grid-template-columns: 1fr 1fr; }
    .row-3 { grid-template-columns: 1fr 1fr 1fr; }
    .row-4 { grid-template-columns: 1fr 1fr 1fr 1fr; }
    .row-1-2 { grid-template-columns: 1fr 2fr; }
    .row-2-1 { grid-template-columns: 2fr 1fr; }

    @media (max-width: 800px) {
      .row-2, .row-3, .row-4, .row-1-2, .row-2-1 { grid-template-columns: 1fr; }
    }

    .card {
      background: var(--card-bg); border: 1px solid var(--border);
      border-radius: var(--radius); padding: 20px;
      transition: border-color 0.2s;
    }
    .card:hover { border-color: color-mix(in srgb, var(--accent) 40%, var(--border)); }
    .card-title {
      font-size: 0.7em; text-transform: uppercase; letter-spacing: 0.1em;
      color: var(--muted); margin-bottom: 8px; font-weight: 600;
    }
    .card-value { font-size: 2em; font-weight: 700; line-height: 1.1; }
    .card-sub { font-size: 0.8em; color: var(--muted); margin-top: 4px; }

    .section { margin-bottom: 28px; }
    .section-header {
      font-size: 1em; font-weight: 600; margin-bottom: 14px;
      display: flex; align-items: center; gap: 8px;
      padding-bottom: 8px; border-bottom: 1px solid var(--border);
    }

    /* Donut chart */
    .donut-wrap { display: flex; align-items: center; justify-content: center; gap: 24px; padding: 12px 0; }
    .donut-svg { transform: rotate(-90deg); }
    .donut-center {
      position: absolute; text-align: center;
      transform: rotate(90deg);
    }
    .donut-container { position: relative; display: flex; align-items: center; justify-content: center; }
    .donut-val { font-size: 28px; font-weight: 700; line-height: 1; }
    .donut-label { font-size: 11px; color: var(--muted); margin-top: 2px; }
    .donut-legend { display: flex; flex-direction: column; gap: 8px; }
    .donut-legend-item { display: flex; align-items: center; gap: 8px; font-size: 0.85em; }
    .donut-legend-dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
    .donut-legend-val { font-weight: 600; margin-left: auto; }

    /* Horizontal bar chart */
    .hbar { display: flex; flex-direction: column; gap: 6px; }
    .hbar-row { display: flex; align-items: center; gap: 10px; }
    .hbar-label {
      width: 140px; font-size: 0.8em; white-space: nowrap;
      overflow: hidden; text-overflow: ellipsis; flex-shrink: 0; text-align: right;
    }
    .hbar-track {
      flex: 1; height: 20px; background: var(--input-bg);
      border-radius: 4px; overflow: hidden; position: relative;
    }
    .hbar-fill {
      height: 100%; border-radius: 4px;
      transition: width 0.6s cubic-bezier(0.4, 0, 0.2, 1);
    }
    .hbar-fill-orig {
      position: absolute; top: 0; left: 0; height: 100%;
      background: var(--border); border-radius: 4px; opacity: 0.5;
    }
    .hbar-fill-comp {
      position: absolute; top: 0; left: 0; height: 100%;
      background: var(--accent); border-radius: 4px;
    }
    .hbar-val { width: 60px; font-size: 0.75em; color: var(--muted); flex-shrink: 0; }

    /* Strategy pie */
    .pie-wrap { display: flex; align-items: center; justify-content: center; gap: 24px; padding: 12px 0; }
    .pie-legend { display: flex; flex-direction: column; gap: 6px; }
    .pie-legend-item { display: flex; align-items: center; gap: 8px; font-size: 0.85em; }
    .pie-legend-dot { width: 10px; height: 10px; border-radius: 3px; flex-shrink: 0; }
    .pie-legend-count { font-weight: 600; margin-left: auto; min-width: 24px; text-align: right; }

    /* Badge */
    .badge {
      display: inline-block; padding: 3px 10px; border-radius: 12px;
      font-size: 0.75em; font-weight: 600;
    }
    .badge-low { background: rgba(78, 201, 176, 0.15); color: var(--success); }
    .badge-medium { background: rgba(220, 220, 170, 0.15); color: var(--warning); }
    .badge-high { background: rgba(241, 76, 76, 0.15); color: var(--error); }

    /* Table */
    table { width: 100%; border-collapse: collapse; font-size: 0.85em; }
    th {
      text-align: left; padding: 8px 12px; border-bottom: 2px solid var(--border);
      color: var(--muted); font-weight: 600; font-size: 0.75em;
      text-transform: uppercase; letter-spacing: 0.05em;
    }
    td { padding: 8px 12px; border-bottom: 1px solid color-mix(in srgb, var(--border) 50%, transparent); }
    tr:hover td { background: var(--input-bg); }

    .file-link { color: var(--accent); cursor: pointer; text-decoration: none; }
    .file-link:hover { text-decoration: underline; }

    .strategy-pill {
      display: inline-block; padding: 2px 8px; border-radius: 10px;
      font-size: 0.75em; font-weight: 500; border: 1px solid;
    }

    .empty-state {
      text-align: center; padding: 80px 20px; color: var(--muted);
    }
    .empty-state h2 { font-size: 1.4em; margin-bottom: 16px; color: var(--fg); }
    .empty-state p { margin-bottom: 24px; max-width: 450px; margin-left: auto; margin-right: auto; line-height: 1.6; }

    /* Scrollbar */
    ::-webkit-scrollbar { width: 6px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }

    /* Animation */
    @keyframes fadeIn { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: translateY(0); } }
    .fade-in { animation: fadeIn 0.4s ease forwards; }
    .fade-in-1 { animation-delay: 0.05s; opacity: 0; }
    .fade-in-2 { animation-delay: 0.1s; opacity: 0; }
    .fade-in-3 { animation-delay: 0.15s; opacity: 0; }
    .fade-in-4 { animation-delay: 0.2s; opacity: 0; }
  </style>
</head>
<body>
  ${run ? this.renderDashboard(run, nonce) : this.renderEmpty()}
  <script nonce="${nonce}">
    const vscode = acquireVsCodeApi();
    document.addEventListener('click', (e) => {
      const el = e.target.closest('[data-action]');
      if (!el) return;
      const action = el.dataset.action;
      if (action === 'open-file') {
        vscode.postMessage({ command: 'openFile', path: el.dataset.path });
      } else if (action === 'run-pack') {
        vscode.postMessage({ command: 'runPack' });
      } else if (action === 'copy-context') {
        vscode.postMessage({ command: 'copyContext' });
      }
    });
  </script>
</body>
</html>`;
  }

  private renderEmpty(): string {
    return `
      <div class="empty-state">
        <svg width="48" height="48" viewBox="0 0 24 24" fill="none" style="margin-bottom:16px;opacity:0.4;">
          <path d="M3 3v18h18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
          <path d="M7 16l4-6 4 4 5-8" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
        <h2>Redcon Analytics</h2>
        <p>No analysis data yet. Describe a task in the Redcon sidebar and send it. The dashboard will visualize token budget, compression efficiency, file rankings, and strategy breakdown.</p>
        <button class="btn btn-primary" data-action="run-pack">Analyze Context</button>
      </div>
    `;
  }

  private renderDashboard(run: NonNullable<typeof state.state.lastRun>, _nonce: string): string {
    const budget = run.budget;
    const used = budget.estimated_input_tokens;
    const max = run.max_tokens;
    const saved = budget.estimated_saved_tokens;
    const pct = max > 0 ? Math.round((used / max) * 100) : 0;
    const available = max - used;

    const totalOriginal = run.compressed_context.reduce((s, f) => s + f.original_tokens, 0);
    const totalCompressed = run.compressed_context.reduce((s, f) => s + f.compressed_tokens, 0);
    const compressionPct = totalOriginal > 0
      ? Math.round(((totalOriginal - totalCompressed) / totalOriginal) * 100)
      : 0;

    // Strategy counts and tokens
    const stratCounts: Record<string, { count: number; tokens: number }> = {};
    for (const f of run.compressed_context) {
      if (!stratCounts[f.strategy]) stratCounts[f.strategy] = { count: 0, tokens: 0 };
      stratCounts[f.strategy].count++;
      stratCounts[f.strategy].tokens += f.compressed_tokens;
    }

    const riskClass = `badge-${budget.quality_risk_estimate}`;

    return `
      <!-- Header -->
      <div class="header fade-in">
        <div class="header-left">
          <h1>
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none">
              <path d="M3 3v18h18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
              <path d="M7 16l4-6 4 4 5-8" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
            Redcon Analytics
          </h1>
          <div class="task">${this.esc(run.task)}</div>
        </div>
        <div class="header-actions">
          <button class="btn" data-action="copy-context">Copy Context</button>
          <button class="btn btn-primary" data-action="run-pack">Re-analyze</button>
        </div>
      </div>

      <!-- KPI row -->
      <div class="row row-4 fade-in fade-in-1">
        <div class="card">
          <div class="card-title">Budget Used</div>
          <div class="card-value" style="color:${pct > 90 ? 'var(--error)' : pct > 70 ? 'var(--warning)' : 'var(--success)'};">${pct}%</div>
          <div class="card-sub">${this.fmt(used)} of ${this.fmt(max)} tokens</div>
        </div>
        <div class="card">
          <div class="card-title">Tokens Saved</div>
          <div class="card-value" style="color:var(--success);">${this.fmt(saved)}</div>
          <div class="card-sub">${compressionPct}% compression ratio</div>
        </div>
        <div class="card">
          <div class="card-title">Files Packed</div>
          <div class="card-value">${run.files_included.length}</div>
          <div class="card-sub">${run.files_skipped.length} skipped / ${run.ranked_files.length} scanned</div>
        </div>
        <div class="card">
          <div class="card-title">Quality Risk</div>
          <div class="card-value"><span class="badge ${riskClass}">${budget.quality_risk_estimate}</span></div>
          <div class="card-sub">${budget.quality_risk_estimate === 'low' ? 'Good context coverage' : budget.quality_risk_estimate === 'medium' ? 'Some data compressed' : 'Context may be incomplete'}</div>
        </div>
      </div>

      <!-- Charts row: Budget donut + Strategy pie -->
      <div class="row row-2 fade-in fade-in-2">
        <div class="card">
          <div class="card-title">Budget Allocation</div>
          <div class="donut-wrap">
            <div class="donut-container">
              ${this.renderDonut(140, 18, [
                { value: used, color: pct > 90 ? '#f14c4c' : pct > 70 ? '#dcdcaa' : '#4ec9b0', label: 'Used' },
                { value: available > 0 ? available : 0, color: 'var(--input-bg)', label: 'Available' },
              ], `${pct}%`, 'used')}
            </div>
            <div class="donut-legend">
              <div class="donut-legend-item">
                <span class="donut-legend-dot" style="background:${pct > 90 ? '#f14c4c' : pct > 70 ? '#dcdcaa' : '#4ec9b0'};"></span>
                <span>Used</span>
                <span class="donut-legend-val">${this.fmt(used)}</span>
              </div>
              <div class="donut-legend-item">
                <span class="donut-legend-dot" style="background:var(--border);"></span>
                <span>Available</span>
                <span class="donut-legend-val">${this.fmt(available > 0 ? available : 0)}</span>
              </div>
              <div class="donut-legend-item">
                <span class="donut-legend-dot" style="background:var(--success);"></span>
                <span>Saved by compression</span>
                <span class="donut-legend-val">${this.fmt(saved)}</span>
              </div>
            </div>
          </div>
        </div>
        <div class="card">
          <div class="card-title">Strategy Distribution</div>
          <div class="pie-wrap">
            ${this.renderPie(140, Object.entries(stratCounts).map(([s, d]) => ({
              value: d.count, color: stratColor(s), label: s.replace(/_/g, ' '),
            })))}
            <div class="pie-legend">
              ${Object.entries(stratCounts)
                .sort((a, b) => b[1].count - a[1].count)
                .map(([s, d]) => `
                  <div class="pie-legend-item">
                    <span class="pie-legend-dot" style="background:${stratColor(s)};"></span>
                    <span>${s.replace(/_/g, ' ')}</span>
                    <span class="pie-legend-count">${d.count}</span>
                  </div>
                `).join('')}
            </div>
          </div>
        </div>
      </div>

      <!-- Horizontal bar chart: top files by token impact -->
      <div class="section fade-in fade-in-3">
        <div class="section-header">Token Impact by File (top 15)</div>
        <div class="card">
          ${this.renderHBars(run)}
        </div>
      </div>

      <!-- Packed files table -->
      <div class="section fade-in fade-in-4">
        <div class="section-header">Packed Context (${run.compressed_context.length} files)</div>
        <div class="card" style="overflow-x:auto;padding:0;">
          <table>
            <thead>
              <tr>
                <th>File</th>
                <th>Strategy</th>
                <th>Original</th>
                <th>Compressed</th>
                <th>Saved</th>
                <th>Ratio</th>
              </tr>
            </thead>
            <tbody>
              ${run.compressed_context.map((f) => {
                const savedT = f.original_tokens - f.compressed_tokens;
                const ratio = f.original_tokens > 0 ? Math.round((savedT / f.original_tokens) * 100) : 0;
                const fullPath = run.repo ? `${run.repo}/${f.path}` : f.path;
                return `
                <tr>
                  <td><span class="file-link" data-action="open-file" data-path="${this.esc(fullPath)}">${this.esc(f.path)}</span></td>
                  <td><span class="strategy-pill" style="border-color:${stratColor(f.strategy)};color:${stratColor(f.strategy)};">${f.strategy.replace(/_/g, ' ')}</span></td>
                  <td>${f.original_tokens.toLocaleString()}</td>
                  <td>${f.compressed_tokens.toLocaleString()}</td>
                  <td style="color:var(--success);">${savedT > 0 ? '-' + savedT.toLocaleString() : '0'}</td>
                  <td>${ratio}%</td>
                </tr>`;
              }).join('')}
            </tbody>
          </table>
        </div>
      </div>

      <!-- File Rankings -->
      <div class="section">
        <div class="section-header">File Rankings (${run.ranked_files.length} scanned)</div>
        <div class="card" style="overflow-x:auto;padding:0;">
          <table>
            <thead>
              <tr><th>#</th><th>File</th><th>Score</th><th>Lines</th><th>Status</th><th>Reasons</th></tr>
            </thead>
            <tbody>
              ${run.ranked_files.slice(0, 50).map((f, i) => {
                const included = run.files_included.includes(f.path);
                const maxScore = Math.max(...run.ranked_files.map((r) => r.score), 1);
                const barW = (f.score / maxScore) * 100;
                const fullPath = run.repo ? `${run.repo}/${f.path}` : f.path;
                return `
                <tr>
                  <td style="color:var(--muted);width:40px;">${i + 1}</td>
                  <td><span class="file-link" data-action="open-file" data-path="${this.esc(fullPath)}">${this.esc(f.path)}</span></td>
                  <td style="width:180px;">
                    <div style="display:flex;align-items:center;gap:8px;">
                      <span style="min-width:36px;font-weight:600;">${f.score.toFixed(1)}</span>
                      <div style="flex:1;height:6px;background:var(--input-bg);border-radius:3px;overflow:hidden;">
                        <div style="width:${barW}%;height:100%;background:${barW > 70 ? 'var(--success)' : barW > 40 ? 'var(--warning)' : 'var(--muted)'};border-radius:3px;transition:width 0.4s;"></div>
                      </div>
                    </div>
                  </td>
                  <td>${f.line_count}</td>
                  <td>${included ? '<span style="color:var(--success);">included</span>' : '<span style="color:var(--muted);">skipped</span>'}</td>
                  <td style="color:var(--muted);font-size:0.8em;max-width:250px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${this.esc(f.reasons.join('; '))}">${this.esc(f.reasons.slice(0, 3).join('; '))}</td>
                </tr>`;
              }).join('')}
            </tbody>
          </table>
          ${run.ranked_files.length > 50 ? `<div class="card-sub" style="padding:12px;text-align:center;">Showing top 50 of ${run.ranked_files.length} files</div>` : ''}
        </div>
      </div>

      <!-- Metadata -->
      <div class="section">
        <div class="section-header">Run Metadata</div>
        <div class="row row-4">
          <div class="card">
            <div class="card-title">Token Estimator</div>
            <div class="card-sub">${run.token_estimator.effective_backend} (${run.token_estimator.uncertainty})</div>
          </div>
          <div class="card">
            <div class="card-title">Summarizer</div>
            <div class="card-sub">${run.summarizer.effective_backend}</div>
          </div>
          <div class="card">
            <div class="card-title">Cache</div>
            <div class="card-sub">${run.cache?.enabled ? `${run.cache.backend} - ${run.cache.hits} hits` : 'disabled'}</div>
          </div>
          <div class="card">
            <div class="card-title">Generated</div>
            <div class="card-sub">${run.generated_at}</div>
          </div>
        </div>
      </div>
    `;
  }

  /* --- SVG Chart helpers --- */

  private renderDonut(
    size: number,
    stroke: number,
    segments: { value: number; color: string; label: string }[],
    centerText: string,
    centerLabel: string,
  ): string {
    const r = (size - stroke) / 2;
    const circ = 2 * Math.PI * r;
    const total = segments.reduce((s, seg) => s + seg.value, 0);
    let offset = 0;

    const paths = segments.map((seg) => {
      const len = total > 0 ? (seg.value / total) * circ : 0;
      const gap = 2;
      const html = `<circle cx="${size / 2}" cy="${size / 2}" r="${r}"
        fill="none" stroke="${seg.color}" stroke-width="${stroke}"
        stroke-dasharray="${Math.max(0, len - gap)} ${circ - Math.max(0, len - gap)}"
        stroke-dashoffset="${-offset}"
        stroke-linecap="round"/>`;
      offset += len;
      return html;
    }).join('');

    return `
      <svg class="donut-svg" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">
        ${paths}
      </svg>
      <div class="donut-center">
        <div class="donut-val">${centerText}</div>
        <div class="donut-label">${centerLabel}</div>
      </div>`;
  }

  private renderPie(
    size: number,
    segments: { value: number; color: string; label: string }[],
  ): string {
    const cx = size / 2;
    const cy = size / 2;
    const r = size / 2 - 4;
    const total = segments.reduce((s, seg) => s + seg.value, 0);
    if (total === 0) return '';

    let startAngle = -Math.PI / 2;
    const paths = segments.map((seg) => {
      const angle = (seg.value / total) * 2 * Math.PI;
      const endAngle = startAngle + angle;
      const largeArc = angle > Math.PI ? 1 : 0;
      const x1 = cx + r * Math.cos(startAngle);
      const y1 = cy + r * Math.sin(startAngle);
      const x2 = cx + r * Math.cos(endAngle);
      const y2 = cy + r * Math.sin(endAngle);
      const html = `<path d="M${cx},${cy} L${x1},${y1} A${r},${r} 0 ${largeArc},1 ${x2},${y2} Z"
        fill="${seg.color}" opacity="0.85"/>`;
      startAngle = endAngle;
      return html;
    }).join('');

    // Inner circle for donut effect
    const innerR = r * 0.55;
    return `
      <svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">
        ${paths}
        <circle cx="${cx}" cy="${cy}" r="${innerR}" fill="var(--card-bg)"/>
      </svg>`;
  }

  private renderHBars(run: NonNullable<typeof state.state.lastRun>): string {
    const files = run.compressed_context.slice(0, 15);
    const maxOrig = Math.max(...files.map((f) => f.original_tokens), 1);

    return `<div class="hbar" style="padding:8px 0;">
      ${files.map((f) => {
        const name = f.path.split('/').pop() ?? f.path;
        const origW = (f.original_tokens / maxOrig) * 100;
        const compW = (f.compressed_tokens / maxOrig) * 100;
        const savedPct = f.original_tokens > 0
          ? Math.round(((f.original_tokens - f.compressed_tokens) / f.original_tokens) * 100)
          : 0;
        return `
          <div class="hbar-row">
            <div class="hbar-label" title="${this.esc(f.path)}">${this.esc(name)}</div>
            <div class="hbar-track">
              <div class="hbar-fill-orig" style="width:${origW}%;"></div>
              <div class="hbar-fill-comp" style="width:${compW}%;"></div>
            </div>
            <div class="hbar-val">${savedPct > 0 ? `-${savedPct}%` : '0%'}</div>
          </div>`;
      }).join('')}
      <div style="display:flex;gap:16px;justify-content:center;padding:8px 0;font-size:0.75em;color:var(--muted);">
        <span><span style="display:inline-block;width:10px;height:10px;background:var(--border);border-radius:2px;opacity:0.5;vertical-align:middle;margin-right:4px;"></span>Original</span>
        <span><span style="display:inline-block;width:10px;height:10px;background:var(--accent);border-radius:2px;vertical-align:middle;margin-right:4px;"></span>Compressed</span>
      </div>
    </div>`;
  }

  private fmt(n: number): string {
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
    if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
    return String(n);
  }

  private esc(text: string): string {
    return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
}

function getNonce(): string {
  let text = '';
  const possible = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
  for (let i = 0; i < 32; i++) {
    text += possible.charAt(Math.floor(Math.random() * possible.length));
  }
  return text;
}
