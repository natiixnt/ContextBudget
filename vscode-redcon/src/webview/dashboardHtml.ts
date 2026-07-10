/**
 * Pure HTML renderer for the dashboard webview.
 *
 * No dependency on the vscode module, so it can be unit tested and
 * rendered standalone. Data colors come from a validated palette with
 * separate light and dark steps (switched on VS Code's body theme
 * class); UI chrome uses the editor's own theme tokens.
 */

import type { RunReport, RunHistoryEntry } from '../types';

export interface DashboardData {
  run: RunReport;
  history: RunHistoryEntry[];
  costPerMillionTokens: number;
}

/**
 * Fixed strategy -> palette slot assignment. Entity keyed, never cycled,
 * so a strategy keeps its color across runs regardless of which
 * strategies appear or how many files use them.
 */
const STRATEGY_SLOT: Record<string, number> = {
  full: 1,
  snippet: 2,
  symbol: 3,
  symbol_extraction: 3,
  slicing: 4,
  summary: 5,
  cache_reuse: 6,
};

function stratColor(s: string): string {
  const slot = STRATEGY_SLOT[s] ?? 6;
  return `var(--cat-${slot})`;
}

function fmt(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function fmtUsd(usd: number): string {
  if (usd >= 100) return `$${Math.round(usd).toLocaleString()}`;
  if (usd >= 1) return `$${usd.toFixed(2)}`;
  return `$${usd.toFixed(3)}`;
}

function esc(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function utilizationStatus(pct: number): string {
  if (pct > 90) return 'var(--status-crit)';
  if (pct > 70) return 'var(--status-warn)';
  return 'var(--status-good)';
}

export function renderDashboardHtml(data: DashboardData | null, nonce: string): string {
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
      --border: var(--vscode-panel-border, rgba(128,128,128,0.25));
      --card-bg: var(--vscode-sideBar-background, var(--bg));
      --muted: var(--vscode-descriptionForeground);
      --input-bg: var(--vscode-input-background);
      --link: var(--vscode-textLink-foreground);
      --radius: 8px;
    }

    /* Data palette - validated for the light surface. */
    body {
      --cat-1: #2a78d6;
      --cat-2: #1baf7a;
      --cat-3: #eda100;
      --cat-4: #008300;
      --cat-5: #4a3aa7;
      --cat-6: #e34948;
      --delta-good: #006300;
      --track: rgba(0, 0, 0, 0.08);
      /* Status colors are fixed across modes and reserved for state. */
      --status-good: #0ca30c;
      --status-warn: #fab219;
      --status-crit: #d03b3b;
      /* Brand red - chrome and identity only, never a data mark. */
      --brand: #d32f2f;
      --brand-strong: #c62828;
      --brand-hover: #b71c1c;
    }
    /* Same hues re-stepped for the dark surface (validated separately). */
    body.vscode-dark, body.vscode-high-contrast {
      --cat-1: #3987e5;
      --cat-2: #199e70;
      --cat-3: #c98500;
      --cat-4: #008300;
      --cat-5: #9085e9;
      --cat-6: #e66767;
      --delta-good: #0ca30c;
      --track: rgba(255, 255, 255, 0.10);
      --brand: #ef5350;
      --brand-strong: #d32f2f;
      --brand-hover: #e53935;
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
      margin-bottom: 24px;
      padding-bottom: 14px;
      border-bottom: 1px solid var(--border);
    }
    .header-left { flex: 1; min-width: 0; }
    .header h1 { font-size: 1.35em; font-weight: 700; display: flex; align-items: center; gap: 10px; }
    .header h1 svg { color: var(--brand); flex-shrink: 0; }
    .header .task {
      color: var(--muted); font-size: 0.85em; margin-top: 2px;
      white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }
    .header-actions { display: flex; gap: 8px; flex-shrink: 0; }

    .btn {
      display: inline-flex; align-items: center; gap: 6px;
      padding: 6px 14px; border: 1px solid var(--border); border-radius: var(--radius);
      background: var(--vscode-button-secondaryBackground, var(--input-bg));
      color: var(--vscode-button-secondaryForeground, var(--fg));
      cursor: pointer; font-size: 0.85em; font-family: inherit;
    }
    .btn:hover { background: var(--vscode-button-secondaryHoverBackground, var(--input-bg)); }
    .btn-primary {
      background: var(--brand-strong);
      color: #ffffff;
      border-color: transparent;
    }
    .btn-primary:hover { background: var(--brand-hover); }

    .row { display: grid; gap: 16px; margin-bottom: 20px; }
    .row-2 { grid-template-columns: 1fr 1fr; }
    .row-4 { grid-template-columns: repeat(4, 1fr); }
    @media (max-width: 800px) {
      .row-2, .row-4 { grid-template-columns: 1fr; }
    }

    .card {
      background: var(--card-bg); border: 1px solid var(--border);
      border-radius: var(--radius); padding: 16px 18px;
    }
    .card-title {
      font-size: 0.7em; text-transform: uppercase; letter-spacing: 0.08em;
      color: var(--muted); margin-bottom: 6px; font-weight: 600;
    }
    .card-value { font-size: 1.9em; font-weight: 700; line-height: 1.15; }
    .card-sub { font-size: 0.8em; color: var(--muted); margin-top: 3px; }

    .section { margin-bottom: 24px; }
    .section-header {
      font-size: 0.95em; font-weight: 600; margin-bottom: 10px;
      display: flex; align-items: center; gap: 8px;
    }
    .section-header::before {
      content: ''; width: 3px; height: 14px; border-radius: 2px;
      background: var(--brand); flex-shrink: 0;
    }
    .section-header .hint { font-size: 0.8em; font-weight: 400; color: var(--muted); }

    /* Savings hero */
    .savings-hero { display: flex; align-items: baseline; gap: 14px; flex-wrap: wrap; margin-bottom: 12px; }
    .savings-hero .big { font-size: 2.3em; font-weight: 700; line-height: 1.1; }
    .savings-hero .usd { font-size: 1.15em; font-weight: 600; color: var(--delta-good); }
    .savings-hero .scope { font-size: 0.8em; color: var(--muted); }

    /* Savings trend bars */
    .trend { display: flex; align-items: flex-end; gap: 3px; height: 96px; padding-top: 16px; }
    .trend-bar-wrap { flex: 1; display: flex; flex-direction: column; align-items: center; height: 100%; justify-content: flex-end; min-width: 6px; }
    .trend-bar {
      width: 100%; max-width: 26px;
      background: var(--cat-1);
      border-radius: 3px 3px 0 0;
      min-height: 2px;
    }
    .trend-bar-wrap:hover .trend-bar { opacity: 0.75; }
    .trend-baseline { border-top: 1px solid var(--border); margin-top: 0; }
    .trend-label { font-size: 0.72em; color: var(--muted); text-align: right; margin-top: 4px; }

    /* Donut / pie */
    .donut-wrap { display: flex; align-items: center; justify-content: center; gap: 24px; padding: 10px 0; flex-wrap: wrap; }
    .donut-svg { transform: rotate(-90deg); }
    .donut-container { position: relative; display: flex; align-items: center; justify-content: center; }
    .donut-center { position: absolute; text-align: center; }
    .donut-val { font-size: 26px; font-weight: 700; line-height: 1; }
    .donut-label { font-size: 11px; color: var(--muted); margin-top: 2px; }
    .legend { display: flex; flex-direction: column; gap: 7px; }
    .legend-item { display: flex; align-items: center; gap: 8px; font-size: 0.85em; }
    .legend-dot { width: 10px; height: 10px; border-radius: 3px; flex-shrink: 0; }
    .legend-val { font-weight: 600; margin-left: auto; padding-left: 14px; }

    /* Horizontal bars */
    .hbar { display: flex; flex-direction: column; gap: 6px; }
    .hbar-row { display: flex; align-items: center; gap: 10px; }
    .hbar-label {
      width: 150px; font-size: 0.8em; white-space: nowrap;
      overflow: hidden; text-overflow: ellipsis; flex-shrink: 0; text-align: right;
      color: var(--muted);
    }
    .hbar-track { flex: 1; height: 18px; position: relative; }
    .hbar-fill-orig {
      position: absolute; top: 0; left: 0; height: 100%;
      background: var(--track); border-radius: 3px;
    }
    .hbar-fill-comp {
      position: absolute; top: 0; left: 0; height: 100%;
      background: var(--cat-1); border-radius: 3px;
      border-right: 2px solid var(--card-bg);
    }
    .hbar-val { width: 52px; font-size: 0.78em; color: var(--delta-good); font-weight: 600; flex-shrink: 0; }

    .badge {
      display: inline-block; padding: 3px 10px; border-radius: 10px;
      font-size: 0.72em; font-weight: 700; letter-spacing: 0.02em;
      color: var(--bg);
    }
    .badge-low { background: var(--status-good); }
    .badge-medium { background: var(--status-warn); color: #1a1a19; }
    .badge-high { background: var(--status-crit); }

    table { width: 100%; border-collapse: collapse; font-size: 0.85em; }
    th {
      text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--border);
      color: var(--muted); font-weight: 600; font-size: 0.75em;
      text-transform: uppercase; letter-spacing: 0.05em;
    }
    td { padding: 7px 12px; border-bottom: 1px solid var(--track); font-variant-numeric: tabular-nums; }
    tr:last-child td { border-bottom: none; }
    tr:hover td { background: var(--track); }

    .file-link { color: var(--link); cursor: pointer; text-decoration: none; }
    .file-link:hover { text-decoration: underline; }

    .strategy-pill {
      display: inline-flex; align-items: center; gap: 6px;
      font-size: 0.82em; white-space: nowrap;
    }
    .strategy-pill .legend-dot { width: 8px; height: 8px; }

    .score-track { flex: 1; height: 6px; background: var(--track); border-radius: 3px; overflow: hidden; }
    .score-fill { height: 100%; background: var(--cat-1); border-radius: 3px; }

    .empty-state { text-align: center; padding: 80px 20px; color: var(--muted); }
    .empty-state h2 { font-size: 1.4em; margin-bottom: 16px; color: var(--fg); }
    .empty-state p { margin-bottom: 24px; max-width: 450px; margin-left: auto; margin-right: auto; line-height: 1.6; }

    ::-webkit-scrollbar { width: 6px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }

    #tooltip {
      position: fixed; display: none; pointer-events: none; z-index: 10;
      background: var(--vscode-editorHoverWidget-background, var(--card-bg));
      color: var(--vscode-editorHoverWidget-foreground, var(--fg));
      border: 1px solid var(--vscode-editorHoverWidget-border, var(--border));
      border-radius: 4px; padding: 6px 10px; font-size: 0.8em;
      max-width: 320px; white-space: pre-line;
    }
  </style>
</head>
<body>
  ${data ? renderBody(data) : renderEmpty()}
  <div id="tooltip"></div>
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
    const tooltip = document.getElementById('tooltip');
    document.addEventListener('mouseover', (e) => {
      const el = e.target.closest('[data-tip]');
      if (!el) { tooltip.style.display = 'none'; return; }
      tooltip.textContent = el.dataset.tip;
      tooltip.style.display = 'block';
    });
    document.addEventListener('mousemove', (e) => {
      if (tooltip.style.display !== 'block') return;
      const pad = 12;
      let x = e.clientX + pad;
      let y = e.clientY + pad;
      const r = tooltip.getBoundingClientRect();
      if (x + r.width > window.innerWidth - 8) x = e.clientX - r.width - pad;
      if (y + r.height > window.innerHeight - 8) y = e.clientY - r.height - pad;
      tooltip.style.left = x + 'px';
      tooltip.style.top = y + 'px';
    });
  </script>
</body>
</html>`;
}

function renderEmpty(): string {
  return `
    <div class="empty-state">
      <svg width="48" height="48" viewBox="0 0 24 24" fill="none" style="margin-bottom:16px;opacity:0.4;">
        <path d="M3 3v18h18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
        <path d="M7 16l4-6 4 4 5-8" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
      </svg>
      <h2>Redcon Analytics</h2>
      <p>No analysis data yet. Describe a task in the Redcon sidebar and send it. The dashboard will visualize token savings, budget usage, file rankings, and strategy breakdown.</p>
      <button class="btn btn-primary" data-action="run-pack">Analyze Context</button>
    </div>
  `;
}

function renderBody(data: DashboardData): string {
  const { run, history, costPerMillionTokens } = data;
  const budget = run.budget;
  const used = budget.estimated_input_tokens;
  const max = run.max_tokens;
  const saved = budget.estimated_saved_tokens;
  const pct = max > 0 ? Math.round((used / max) * 100) : 0;
  const available = Math.max(0, max - used);

  const totalOriginal = run.compressed_context.reduce((s, f) => s + f.original_tokens, 0);
  const totalCompressed = run.compressed_context.reduce((s, f) => s + f.compressed_tokens, 0);
  const compressionPct = totalOriginal > 0
    ? Math.round(((totalOriginal - totalCompressed) / totalOriginal) * 100)
    : 0;

  const stratCounts: Record<string, { count: number; tokens: number }> = {};
  for (const f of run.compressed_context) {
    if (!stratCounts[f.strategy]) stratCounts[f.strategy] = { count: 0, tokens: 0 };
    stratCounts[f.strategy].count++;
    stratCounts[f.strategy].tokens += f.compressed_tokens;
  }

  const riskClass = `badge-${budget.quality_risk_estimate}`;
  const usedColor = utilizationStatus(pct);
  const savedUsd = (saved / 1_000_000) * costPerMillionTokens;

  return `
    <div class="header">
      <div class="header-left">
        <h1>
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
            <path d="M3 3v18h18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
            <path d="M7 16l4-6 4 4 5-8" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
          Redcon Analytics
        </h1>
        <div class="task" title="${esc(run.task)}">${esc(run.task)}</div>
      </div>
      <div class="header-actions">
        <button class="btn" data-action="copy-context">Copy Context</button>
        <button class="btn btn-primary" data-action="run-pack">Re-analyze</button>
      </div>
    </div>

    ${renderSavingsSection(run, history, costPerMillionTokens)}

    <div class="row row-4">
      <div class="card">
        <div class="card-title">Budget Used</div>
        <div class="card-value" style="color:${usedColor};">${pct}%</div>
        <div class="card-sub">${fmt(used)} of ${fmt(max)} tokens</div>
      </div>
      <div class="card">
        <div class="card-title">Saved This Run</div>
        <div class="card-value" style="color:var(--delta-good);">${fmt(saved)}</div>
        <div class="card-sub">${compressionPct}% compression &middot; &asymp; ${fmtUsd(savedUsd)}</div>
      </div>
      <div class="card">
        <div class="card-title">Files Packed</div>
        <div class="card-value">${run.files_included.length}</div>
        <div class="card-sub">${run.files_skipped.length} skipped / ${run.ranked_files.length} scanned</div>
      </div>
      <div class="card">
        <div class="card-title">Quality Risk</div>
        <div class="card-value"><span class="badge ${riskClass}">${budget.quality_risk_estimate}</span></div>
        <div class="card-sub">${budget.quality_risk_estimate === 'low' ? 'Good context coverage' : budget.quality_risk_estimate === 'medium' ? 'Some content compressed away' : 'Context may be incomplete'}</div>
      </div>
    </div>

    <div class="row row-2">
      <div class="card">
        <div class="card-title">Budget Allocation</div>
        <div class="donut-wrap">
          <div class="donut-container">
            ${renderDonut(132, 16, [
              { value: used, color: usedColor },
              { value: available, color: 'var(--track)' },
            ], `${pct}%`, 'used')}
          </div>
          <div class="legend">
            <div class="legend-item">
              <span class="legend-dot" style="background:${usedColor};"></span>
              <span>Used</span>
              <span class="legend-val">${fmt(used)}</span>
            </div>
            <div class="legend-item">
              <span class="legend-dot" style="background:var(--track);"></span>
              <span>Available</span>
              <span class="legend-val">${fmt(available)}</span>
            </div>
            <div class="legend-item">
              <span class="legend-dot" style="background:var(--delta-good);"></span>
              <span>Saved by compression</span>
              <span class="legend-val">${fmt(saved)}</span>
            </div>
          </div>
        </div>
      </div>
      <div class="card">
        <div class="card-title">Strategy Distribution</div>
        <div class="donut-wrap">
          ${renderPie(132, Object.entries(stratCounts).map(([s, d]) => ({
            value: d.count, color: stratColor(s),
          })))}
          <div class="legend">
            ${Object.entries(stratCounts)
              .sort((a, b) => b[1].count - a[1].count)
              .map(([s, d]) => `
                <div class="legend-item">
                  <span class="legend-dot" style="background:${stratColor(s)};"></span>
                  <span>${s.replace(/_/g, ' ')}</span>
                  <span class="legend-val">${d.count}</span>
                </div>
              `).join('')}
          </div>
        </div>
      </div>
    </div>

    <div class="section">
      <div class="section-header">Token Impact by File <span class="hint">top 15, original vs packed</span></div>
      <div class="card">
        ${renderHBars(run)}
      </div>
    </div>

    <div class="section">
      <div class="section-header">Packed Context <span class="hint">${run.compressed_context.length} files</span></div>
      <div class="card" style="overflow-x:auto;padding:0;">
        <table>
          <thead>
            <tr>
              <th>File</th>
              <th>Strategy</th>
              <th>Original</th>
              <th>Packed</th>
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
                <td><span class="file-link" data-action="open-file" data-path="${esc(fullPath)}">${esc(f.path)}</span></td>
                <td><span class="strategy-pill"><span class="legend-dot" style="background:${stratColor(f.strategy)};"></span>${f.strategy.replace(/_/g, ' ')}</span></td>
                <td>${f.original_tokens.toLocaleString()}</td>
                <td>${f.compressed_tokens.toLocaleString()}</td>
                <td style="color:var(--delta-good);font-weight:600;">${savedT > 0 ? '-' + savedT.toLocaleString() : '0'}</td>
                <td>${ratio}%</td>
              </tr>`;
            }).join('')}
          </tbody>
        </table>
      </div>
    </div>

    <div class="section">
      <div class="section-header">File Rankings <span class="hint">${run.ranked_files.length} scanned</span></div>
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
                <td><span class="file-link" data-action="open-file" data-path="${esc(fullPath)}">${esc(f.path)}</span></td>
                <td style="width:180px;">
                  <div style="display:flex;align-items:center;gap:8px;">
                    <span style="min-width:36px;font-weight:600;">${f.score.toFixed(1)}</span>
                    <div class="score-track"><div class="score-fill" style="width:${barW}%;"></div></div>
                  </div>
                </td>
                <td>${f.line_count}</td>
                <td>${included ? '<span style="color:var(--delta-good);font-weight:600;">included</span>' : '<span style="color:var(--muted);">skipped</span>'}</td>
                <td style="color:var(--muted);font-size:0.8em;max-width:250px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${esc(f.reasons.join('; '))}">${esc(f.reasons.slice(0, 3).join('; '))}</td>
              </tr>`;
            }).join('')}
          </tbody>
        </table>
        ${run.ranked_files.length > 50 ? `<div class="card-sub" style="padding:12px;text-align:center;">Showing top 50 of ${run.ranked_files.length} files</div>` : ''}
      </div>
    </div>

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

function renderSavingsSection(
  run: RunReport,
  history: RunHistoryEntry[],
  costPerMillionTokens: number,
): string {
  const totalSaved = history.reduce((s, e) => s + (e.tokensSaved ?? 0), 0);
  const runCount = history.length;
  // History scans persisted artifacts; when none exist yet, fall back to
  // the in-memory run so the section is never a dead zero.
  const effectiveTotal = totalSaved > 0 ? totalSaved : run.budget.estimated_saved_tokens;
  const effectiveRuns = totalSaved > 0 ? runCount : 1;
  const totalUsd = (effectiveTotal / 1_000_000) * costPerMillionTokens;

  // Chronological trend of the most recent runs (history is newest first).
  const trendEntries = history
    .filter((e) => (e.tokensSaved ?? 0) >= 0)
    .slice(0, 20)
    .reverse();
  const maxSaved = Math.max(...trendEntries.map((e) => e.tokensSaved ?? 0), 1);

  const trend = trendEntries.length >= 2
    ? `
      <div class="trend">
        ${trendEntries.map((e) => {
          const v = e.tokensSaved ?? 0;
          const h = Math.max(2, Math.round((v / maxSaved) * 80));
          const when = e.generatedAt ? e.generatedAt.slice(0, 16).replace('T', ' ') : '';
          const tip = `${e.task}\n${fmt(v)} tokens saved (${fmtUsd((v / 1_000_000) * costPerMillionTokens)})\n${when}`;
          return `
            <div class="trend-bar-wrap" data-tip="${esc(tip)}">
              <div class="trend-bar" style="height:${h}px;"></div>
            </div>`;
        }).join('')}
      </div>
      <div class="trend-baseline"></div>
      <div class="trend-label">last ${trendEntries.length} runs, tokens saved per run</div>`
    : `<div class="card-sub">Savings trend appears after a few runs.</div>`;

  return `
    <div class="section">
      <div class="section-header">Savings <span class="hint">what redcon kept out of your context window</span></div>
      <div class="card">
        <div class="savings-hero">
          <span class="big">${fmt(effectiveTotal)}</span>
          <span>tokens saved</span>
          <span class="usd">&asymp; ${fmtUsd(totalUsd)}</span>
          <span class="scope">across ${effectiveRuns} run${effectiveRuns === 1 ? '' : 's'} &middot; $/M tokens configurable in settings</span>
        </div>
        ${trend}
      </div>
    </div>
  `;
}

function renderDonut(
  size: number,
  stroke: number,
  segments: { value: number; color: string }[],
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

function renderPie(
  size: number,
  segments: { value: number; color: string }[],
): string {
  const cx = size / 2;
  const cy = size / 2;
  const r = size / 2 - 4;
  const total = segments.reduce((s, seg) => s + seg.value, 0);
  if (total === 0) return '';

  // Single category: a full circle (arc math degenerates at 100%).
  if (segments.length === 1) {
    return `
      <svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">
        <circle cx="${cx}" cy="${cy}" r="${r}" fill="${segments[0].color}"/>
        <circle cx="${cx}" cy="${cy}" r="${r * 0.55}" fill="var(--card-bg)"/>
      </svg>`;
  }

  let startAngle = -Math.PI / 2;
  const paths = segments.map((seg) => {
    const angle = (seg.value / total) * 2 * Math.PI;
    const endAngle = startAngle + angle;
    const largeArc = angle > Math.PI ? 1 : 0;
    const x1 = cx + r * Math.cos(startAngle);
    const y1 = cy + r * Math.sin(startAngle);
    const x2 = cx + r * Math.cos(endAngle);
    const y2 = cy + r * Math.sin(endAngle);
    // 2px surface-colored stroke keeps adjacent slices separated for CVD.
    const html = `<path d="M${cx},${cy} L${x1},${y1} A${r},${r} 0 ${largeArc},1 ${x2},${y2} Z"
      fill="${seg.color}" stroke="var(--card-bg)" stroke-width="2"/>`;
    startAngle = endAngle;
    return html;
  }).join('');

  const innerR = r * 0.55;
  return `
    <svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">
      ${paths}
      <circle cx="${cx}" cy="${cy}" r="${innerR}" fill="var(--card-bg)"/>
    </svg>`;
}

function renderHBars(run: RunReport): string {
  const files = run.compressed_context.slice(0, 15);
  const maxOrig = Math.max(...files.map((f) => f.original_tokens), 1);

  return `<div class="hbar" style="padding:6px 0;">
    ${files.map((f) => {
      const name = f.path.split('/').pop() ?? f.path;
      const origW = (f.original_tokens / maxOrig) * 100;
      const compW = (f.compressed_tokens / maxOrig) * 100;
      const savedPct = f.original_tokens > 0
        ? Math.round(((f.original_tokens - f.compressed_tokens) / f.original_tokens) * 100)
        : 0;
      const tip = `${f.path}\noriginal ${f.original_tokens.toLocaleString()} -> packed ${f.compressed_tokens.toLocaleString()} tokens`;
      return `
        <div class="hbar-row" data-tip="${esc(tip)}">
          <div class="hbar-label">${esc(name)}</div>
          <div class="hbar-track">
            <div class="hbar-fill-orig" style="width:${origW}%;"></div>
            <div class="hbar-fill-comp" style="width:${compW}%;"></div>
          </div>
          <div class="hbar-val"${savedPct > 0 ? '' : ' style="color:var(--muted);font-weight:400;"'}>${savedPct > 0 ? `-${savedPct}%` : '0%'}</div>
        </div>`;
    }).join('')}
    <div style="display:flex;gap:16px;justify-content:center;padding:6px 0 0;font-size:0.75em;color:var(--muted);">
      <span><span style="display:inline-block;width:10px;height:10px;background:var(--track);border-radius:2px;vertical-align:middle;margin-right:4px;"></span>Original</span>
      <span><span style="display:inline-block;width:10px;height:10px;background:var(--cat-1);border-radius:2px;vertical-align:middle;margin-right:4px;"></span>Packed</span>
    </div>
  </div>`;
}
