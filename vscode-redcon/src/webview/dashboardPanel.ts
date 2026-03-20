/**
 * Dashboard Webview - rich visualization of run data with charts.
 */

import * as vscode from 'vscode';
import { state } from '../state';

export class DashboardPanel {
  private static instance: DashboardPanel | undefined;
  private panel: vscode.WebviewPanel;
  private disposables: vscode.Disposable[] = [];

  private constructor(extensionUri: vscode.Uri) {
    this.panel = vscode.window.createWebviewPanel(
      'redconDashboard',
      'Redcon Dashboard',
      vscode.ViewColumn.One,
      {
        enableScripts: true,
        retainContextWhenHidden: true,
        localResourceRoots: [extensionUri],
      },
    );

    this.panel.iconPath = new vscode.ThemeIcon('dashboard');
    this.update();

    // Listen for state changes
    const stateListener = state.onDidChange(() => this.update());
    this.disposables.push(stateListener);

    // Handle messages from webview
    this.panel.webview.onDidReceiveMessage(
      (msg) => {
        switch (msg.command) {
          case 'openFile':
            vscode.commands.executeCommand(
              'vscode.open',
              vscode.Uri.file(msg.path),
            );
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
      DashboardPanel.instance.panel.reveal(vscode.ViewColumn.One);
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
      --badge-bg: var(--vscode-badge-background);
      --badge-fg: var(--vscode-badge-foreground);
      --input-bg: var(--vscode-input-background);
      --radius: 8px;
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      font-family: var(--vscode-font-family);
      font-size: var(--vscode-font-size);
      color: var(--fg);
      background: var(--bg);
      padding: 20px;
      line-height: 1.5;
    }

    .header {
      display: flex;
      align-items: center;
      gap: 12px;
      margin-bottom: 24px;
      padding-bottom: 16px;
      border-bottom: 1px solid var(--border);
    }

    .header h1 {
      font-size: 1.4em;
      font-weight: 600;
      flex: 1;
    }

    .header .task {
      color: var(--muted);
      font-size: 0.9em;
    }

    .btn {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 6px 14px;
      border: 1px solid var(--border);
      border-radius: var(--radius);
      background: var(--input-bg);
      color: var(--fg);
      cursor: pointer;
      font-size: 0.85em;
      transition: background 0.15s;
    }
    .btn:hover { background: var(--badge-bg); }
    .btn-primary {
      background: var(--accent);
      color: #fff;
      border-color: var(--accent);
      box-shadow: 0 0 8px rgba(229, 57, 53, 0.3);
    }
    .btn-primary:hover { opacity: 0.9; box-shadow: 0 0 14px rgba(229, 57, 53, 0.5); }

    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 16px;
      margin-bottom: 24px;
    }

    .card {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 16px;
    }

    .card-title {
      font-size: 0.75em;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--muted);
      margin-bottom: 8px;
    }

    .card-value {
      font-size: 1.8em;
      font-weight: 700;
      line-height: 1.2;
    }

    .card-sub {
      font-size: 0.8em;
      color: var(--muted);
      margin-top: 4px;
    }

    .gauge-container {
      margin: 24px 0;
    }

    .gauge-bar {
      height: 28px;
      background: var(--input-bg);
      border-radius: 14px;
      overflow: hidden;
      position: relative;
    }

    .gauge-fill {
      height: 100%;
      border-radius: 14px;
      transition: width 0.6s ease;
      background: linear-gradient(90deg, var(--success), #e53935);
    }

    .gauge-fill.warn {
      background: linear-gradient(90deg, var(--warning), #e8a838);
    }

    .gauge-fill.danger {
      background: linear-gradient(90deg, var(--error), #d43535);
    }

    .gauge-label {
      position: absolute;
      right: 12px;
      top: 50%;
      transform: translateY(-50%);
      font-size: 0.8em;
      font-weight: 600;
      color: var(--fg);
    }

    .section {
      margin-bottom: 28px;
    }

    .section h2 {
      font-size: 1.1em;
      font-weight: 600;
      margin-bottom: 12px;
      display: flex;
      align-items: center;
      gap: 8px;
    }

    .badge {
      display: inline-block;
      padding: 2px 8px;
      border-radius: 10px;
      font-size: 0.75em;
      font-weight: 600;
    }
    .badge-low { background: var(--success); color: #000; }
    .badge-medium { background: var(--warning); color: #000; }
    .badge-high { background: var(--error); color: #fff; }

    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.85em;
    }

    th {
      text-align: left;
      padding: 8px 12px;
      border-bottom: 2px solid var(--border);
      color: var(--muted);
      font-weight: 600;
      font-size: 0.8em;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }

    td {
      padding: 8px 12px;
      border-bottom: 1px solid var(--border);
    }

    tr:hover td { background: var(--input-bg); }

    .file-link {
      color: var(--accent);
      cursor: pointer;
      text-decoration: none;
    }
    .file-link:hover { text-decoration: underline; }

    .strategy-pill {
      display: inline-block;
      padding: 2px 8px;
      border-radius: 10px;
      font-size: 0.75em;
      font-weight: 500;
      border: 1px solid var(--border);
    }
    .strategy-full { border-color: var(--success); color: var(--success); }
    .strategy-snippet { border-color: var(--warning); color: var(--warning); }
    .strategy-symbol_extraction { border-color: var(--accent); color: var(--accent); }
    .strategy-summary { border-color: var(--muted); color: var(--muted); }
    .strategy-slicing { border-color: var(--muted); color: var(--muted); }
    .strategy-cache_reuse { border-color: var(--accent); color: var(--accent); }

    .bar-chart {
      display: flex;
      align-items: end;
      gap: 4px;
      height: 80px;
      margin-top: 12px;
    }

    .bar-chart-col {
      flex: 1;
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 4px;
    }

    .bar-chart-bar {
      width: 100%;
      min-height: 2px;
      border-radius: 3px 3px 0 0;
      transition: height 0.4s ease;
    }

    .bar-chart-label {
      font-size: 0.6em;
      color: var(--muted);
      text-align: center;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      max-width: 60px;
    }

    .score-bar {
      display: inline-flex;
      gap: 1px;
      vertical-align: middle;
    }
    .score-bar span {
      width: 6px;
      height: 14px;
      border-radius: 1px;
    }

    .empty-state {
      text-align: center;
      padding: 60px 20px;
      color: var(--muted);
    }
    .empty-state h2 {
      justify-content: center;
      margin-bottom: 16px;
    }
    .empty-state p {
      margin-bottom: 20px;
      max-width: 400px;
      margin-left: auto;
      margin-right: auto;
    }

    .donut {
      width: 120px;
      height: 120px;
      margin: 0 auto 8px;
    }
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
        <h2>Redcon Dashboard</h2>
        <p>No run data yet. Pack your repository context to see budget analysis, file rankings, and compression details.</p>
        <button class="btn btn-primary" data-action="run-pack">Pack Context</button>
      </div>
    `;
  }

  private renderDashboard(run: NonNullable<typeof state.state.lastRun>, nonce: string): string {
    const budget = run.budget;
    const used = budget.estimated_input_tokens;
    const max = run.max_tokens;
    const saved = budget.estimated_saved_tokens;
    const pct = max > 0 ? Math.round((used / max) * 100) : 0;

    const totalOriginal = run.compressed_context.reduce(
      (s, f) => s + f.original_tokens, 0,
    );
    const totalCompressed = run.compressed_context.reduce(
      (s, f) => s + f.compressed_tokens, 0,
    );
    const compressionPct = totalOriginal > 0
      ? Math.round(((totalOriginal - totalCompressed) / totalOriginal) * 100)
      : 0;

    // Strategy counts
    const strategyCounts: Record<string, number> = {};
    for (const f of run.compressed_context) {
      strategyCounts[f.strategy] = (strategyCounts[f.strategy] ?? 0) + 1;
    }

    const riskClass = `badge-${budget.quality_risk_estimate}`;

    // Build file bars for chart
    const maxFileTokens = Math.max(
      ...run.compressed_context.map((f) => f.original_tokens),
      1,
    );

    return `
      <div class="header">
        <h1>Redcon Dashboard</h1>
        <span class="task">${this.escapeHtml(run.task)}</span>
        <button class="btn" data-action="copy-context">Copy Context</button>
        <button class="btn btn-primary" data-action="run-pack">Re-pack</button>
      </div>

      <!-- KPI Cards -->
      <div class="grid">
        <div class="card">
          <div class="card-title">Tokens Used</div>
          <div class="card-value">${this.fmtTokens(used)}</div>
          <div class="card-sub">of ${this.fmtTokens(max)} budget</div>
        </div>
        <div class="card">
          <div class="card-title">Tokens Saved</div>
          <div class="card-value" style="color:var(--success)">${this.fmtTokens(saved)}</div>
          <div class="card-sub">${compressionPct}% compression ratio</div>
        </div>
        <div class="card">
          <div class="card-title">Files</div>
          <div class="card-value">${run.files_included.length}</div>
          <div class="card-sub">${run.files_skipped.length} skipped / ${run.ranked_files.length} scanned</div>
        </div>
        <div class="card">
          <div class="card-title">Quality Risk</div>
          <div class="card-value"><span class="badge ${riskClass}">${budget.quality_risk_estimate}</span></div>
          <div class="card-sub">${budget.quality_risk_estimate === 'low' ? 'Good coverage' : budget.quality_risk_estimate === 'medium' ? 'Some compression' : 'Context may be missing'}</div>
        </div>
      </div>

      <!-- Budget Gauge -->
      <div class="section gauge-container">
        <h2>Budget Usage</h2>
        <div class="gauge-bar">
          <div class="gauge-fill ${pct > 90 ? 'danger' : pct > 70 ? 'warn' : ''}" style="width: ${Math.min(pct, 100)}%"></div>
          <span class="gauge-label">${pct}% - ${this.fmtTokens(used)} / ${this.fmtTokens(max)}</span>
        </div>
      </div>

      <!-- Strategy Breakdown -->
      <div class="section">
        <h2>Compression Strategies</h2>
        <div class="grid">
          ${Object.entries(strategyCounts)
            .map(
              ([strategy, count]) => `
            <div class="card">
              <div class="card-title">${strategy.replace('_', ' ')}</div>
              <div class="card-value">${count}</div>
              <div class="card-sub">files</div>
            </div>
          `,
            )
            .join('')}
        </div>
      </div>

      <!-- Token Distribution Chart -->
      <div class="section">
        <h2>Token Distribution</h2>
        <div class="card">
          <div class="bar-chart">
            ${run.compressed_context
              .slice(0, 20)
              .map((f) => {
                const origH = Math.max(2, (f.original_tokens / maxFileTokens) * 70);
                const compH = Math.max(2, (f.compressed_tokens / maxFileTokens) * 70);
                const name = f.path.split('/').pop() ?? f.path;
                return `
                <div class="bar-chart-col">
                  <div class="bar-chart-bar" style="height:${origH}px; background:var(--border);" title="Original: ${f.original_tokens}"></div>
                  <div class="bar-chart-bar" style="height:${compH}px; background:var(--accent); margin-top:-${compH}px;" title="Compressed: ${f.compressed_tokens}"></div>
                  <div class="bar-chart-label" title="${f.path}">${name}</div>
                </div>`;
              })
              .join('')}
          </div>
          <div class="card-sub" style="margin-top:8px; text-align:center;">
            Gray = original tokens, Blue = compressed tokens (showing top ${Math.min(20, run.compressed_context.length)} files)
          </div>
        </div>
      </div>

      <!-- Packed Files Table -->
      <div class="section">
        <h2>Packed Context (${run.compressed_context.length} files)</h2>
        <div class="card" style="overflow-x:auto;">
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
              ${run.compressed_context
                .map((f) => {
                  const savedTokens = f.original_tokens - f.compressed_tokens;
                  const ratio = f.original_tokens > 0
                    ? Math.round((savedTokens / f.original_tokens) * 100)
                    : 0;
                  const fullPath = run.repo
                    ? `${run.repo}/${f.path}`
                    : f.path;
                  return `
                  <tr>
                    <td><span class="file-link" data-action="open-file" data-path="${this.escapeHtml(fullPath)}">${this.escapeHtml(f.path)}</span></td>
                    <td><span class="strategy-pill strategy-${f.strategy}">${f.strategy}</span></td>
                    <td>${f.original_tokens.toLocaleString()}</td>
                    <td>${f.compressed_tokens.toLocaleString()}</td>
                    <td>${savedTokens.toLocaleString()}</td>
                    <td>${ratio}%</td>
                  </tr>`;
                })
                .join('')}
            </tbody>
          </table>
        </div>
      </div>

      <!-- File Ranking Table -->
      <div class="section">
        <h2>File Rankings (${run.ranked_files.length} files)</h2>
        <div class="card" style="overflow-x:auto;">
          <table>
            <thead>
              <tr>
                <th>#</th>
                <th>File</th>
                <th>Score</th>
                <th>Lines</th>
                <th>Status</th>
                <th>Reasons</th>
              </tr>
            </thead>
            <tbody>
              ${run.ranked_files
                .slice(0, 50)
                .map((f, i) => {
                  const included = run.files_included.includes(f.path);
                  const maxScore = Math.max(...run.ranked_files.map((r) => r.score));
                  const barWidth = maxScore > 0 ? (f.score / maxScore) * 100 : 0;
                  const fullPath = run.repo
                    ? `${run.repo}/${f.path}`
                    : f.path;
                  return `
                  <tr>
                    <td style="color:var(--muted)">${i + 1}</td>
                    <td><span class="file-link" data-action="open-file" data-path="${this.escapeHtml(fullPath)}">${this.escapeHtml(f.path)}</span></td>
                    <td>
                      <div style="display:flex;align-items:center;gap:8px;">
                        <span style="min-width:36px">${f.score.toFixed(1)}</span>
                        <div style="flex:1;height:6px;background:var(--input-bg);border-radius:3px;overflow:hidden;min-width:40px;">
                          <div style="width:${barWidth}%;height:100%;background:${barWidth > 70 ? 'var(--success)' : barWidth > 40 ? 'var(--warning)' : 'var(--muted)'};border-radius:3px;"></div>
                        </div>
                      </div>
                    </td>
                    <td>${f.line_count}</td>
                    <td>${included ? '<span style="color:var(--success)">included</span>' : '<span style="color:var(--muted)">skipped</span>'}</td>
                    <td style="color:var(--muted);font-size:0.8em;max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${this.escapeHtml(f.reasons.join('; '))}">${this.escapeHtml(f.reasons.slice(0, 3).join('; '))}</td>
                  </tr>`;
                })
                .join('')}
            </tbody>
          </table>
          ${run.ranked_files.length > 50 ? `<div class="card-sub" style="padding:8px 12px;">Showing top 50 of ${run.ranked_files.length} files</div>` : ''}
        </div>
      </div>

      <!-- Metadata -->
      <div class="section">
        <h2>Run Metadata</h2>
        <div class="grid">
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

  private fmtTokens(n: number): string {
    if (n >= 1_000_000) {
      return `${(n / 1_000_000).toFixed(1)}M`;
    }
    if (n >= 1_000) {
      return `${(n / 1_000).toFixed(1)}k`;
    }
    return String(n);
  }

  private escapeHtml(text: string): string {
    return text
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }
}

function getNonce(): string {
  let text = '';
  const possible =
    'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
  for (let i = 0; i < 32; i++) {
    text += possible.charAt(Math.floor(Math.random() * possible.length));
  }
  return text;
}
