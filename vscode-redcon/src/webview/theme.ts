/**
 * Shared CSS theme and utilities for all webview panels.
 * Glassmorphism-inspired dark UI with smooth animations.
 */

export function getSharedStyles(nonce: string): string {
  return `<style nonce="${nonce}">
    :root {
      --bg: var(--vscode-editor-background, #1e1e1e);
      --fg: var(--vscode-editor-foreground, #d4d4d4);
      --border: var(--vscode-panel-border, #2d2d2d);
      --card: color-mix(in srgb, var(--vscode-sideBar-background, #252526) 80%, transparent);
      --card-hover: color-mix(in srgb, var(--vscode-sideBar-background, #252526) 95%, white 5%);
      --card-border: color-mix(in srgb, var(--vscode-panel-border, #2d2d2d) 60%, transparent);
      --accent-red: #e53935;
      --accent-navy: #1e3a5f;
      --accent: #1e3a5f;
      --accent-dim: rgba(30, 58, 95, 0.15);
      --accent-grad: linear-gradient(135deg, var(--accent-red), var(--accent-navy));
      --success: #4ec9b0;
      --success-dim: rgba(78, 201, 176, 0.15);
      --warning: #dcdcaa;
      --warning-dim: rgba(220, 220, 170, 0.15);
      --error: #f14c4c;
      --error-dim: rgba(241, 76, 76, 0.15);
      --muted: var(--vscode-descriptionForeground, #808080);
      --input: var(--vscode-input-background, #3c3c3c);
      --badge-bg: var(--vscode-badge-background, #4d4d4d);
      --badge-fg: var(--vscode-badge-foreground, #ffffff);
      --radius: 10px;
      --radius-sm: 6px;
      --radius-lg: 14px;
      --shadow: 0 2px 8px rgba(0,0,0,0.15);
      --shadow-lg: 0 4px 20px rgba(0,0,0,0.25);
      --transition: 0.2s cubic-bezier(0.4, 0, 0.2, 1);
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      font-family: var(--vscode-font-family, system-ui, -apple-system, sans-serif);
      font-size: 13px;
      color: var(--fg);
      background: transparent;
      padding: 8px;
      line-height: 1.5;
      overflow-x: hidden;
    }

    /* --- Cards --- */
    .card {
      background: var(--card);
      border: 1px solid var(--card-border);
      border-radius: var(--radius);
      padding: 12px;
      margin-bottom: 8px;
      backdrop-filter: blur(10px);
      transition: all var(--transition);
    }
    .card:hover {
      background: var(--card-hover);
      border-color: var(--accent-dim);
      box-shadow: var(--shadow);
    }
    .card-title {
      font-size: 10px;
      text-transform: uppercase;
      letter-spacing: 0.1em;
      color: var(--muted);
      margin-bottom: 6px;
      font-weight: 600;
    }

    .card-value {
      font-size: 22px;
      font-weight: 700;
      line-height: 1.1;
    }

    .card-sub {
      font-size: 11px;
      color: var(--muted);
      margin-top: 4px;
    }

    /* --- Grid --- */
    .grid { display: grid; gap: 8px; }
    .grid-2 { grid-template-columns: 1fr 1fr; }
    .grid-3 { grid-template-columns: 1fr 1fr 1fr; }

    /* --- Badges --- */
    .badge {
      display: inline-flex;
      align-items: center;
      padding: 2px 8px;
      border-radius: 20px;
      font-size: 10px;
      font-weight: 600;
      letter-spacing: 0.03em;
    }
    .badge-success { background: var(--success-dim); color: var(--success); }
    .badge-warning { background: var(--warning-dim); color: var(--warning); }
    .badge-error { background: var(--error-dim); color: var(--error); }
    .badge-accent { background: var(--accent-dim); color: var(--accent); }
    .badge-muted { background: var(--input); color: var(--muted); }

    /* --- Pills (strategy) --- */
    .pill {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 3px 8px;
      border-radius: 20px;
      font-size: 10px;
      font-weight: 500;
      border: 1px solid;
    }
    .pill-full { border-color: var(--success); color: var(--success); background: var(--success-dim); }
    .pill-snippet { border-color: var(--warning); color: var(--warning); background: var(--warning-dim); }
    .pill-symbol { border-color: var(--accent); color: var(--accent); background: var(--accent-dim); }
    .pill-summary { border-color: var(--muted); color: var(--muted); background: var(--input); }
    .pill-slicing { border-color: var(--muted); color: var(--muted); background: var(--input); }
    .pill-cache { border-color: var(--accent); color: var(--accent); background: var(--accent-dim); }

    /* --- Progress bars --- */
    .progress {
      height: 6px;
      background: var(--input);
      border-radius: 3px;
      overflow: hidden;
    }
    .progress-fill {
      height: 100%;
      border-radius: 3px;
      transition: width 0.6s cubic-bezier(0.4, 0, 0.2, 1);
    }
    .progress-success { background: linear-gradient(90deg, var(--success), color-mix(in srgb, var(--success) 70%, var(--accent))); }
    .progress-warning { background: linear-gradient(90deg, var(--warning), #e8a838); }
    .progress-error { background: linear-gradient(90deg, var(--error), #d43535); }
    .progress-accent { background: linear-gradient(90deg, var(--accent), color-mix(in srgb, var(--accent) 70%, var(--success))); }

    /* --- Buttons --- */
    .btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 5px;
      padding: 6px 12px;
      border: 1px solid var(--card-border);
      border-radius: var(--radius-sm);
      background: var(--input);
      color: var(--fg);
      cursor: pointer;
      font-size: 11px;
      font-weight: 500;
      transition: all var(--transition);
      font-family: inherit;
    }
    .btn:hover { background: var(--card-hover); border-color: var(--accent); }
    .btn-primary {
      background: var(--accent-grad);
      color: #fff;
      border-color: transparent;
    }
    .btn-primary:hover { opacity: 0.9; }
    .btn-sm { padding: 3px 8px; font-size: 10px; }
    .btn-block { width: 100%; }

    /* --- Search input --- */
    .search {
      width: 100%;
      padding: 6px 10px;
      border: 1px solid var(--card-border);
      border-radius: var(--radius-sm);
      background: var(--input);
      color: var(--fg);
      font-size: 11px;
      font-family: inherit;
      outline: none;
      transition: border-color var(--transition);
    }
    .search:focus { border-color: var(--accent); }
    .search::placeholder { color: var(--muted); }

    /* --- File list items --- */
    .file-item {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 8px 10px;
      border-radius: var(--radius-sm);
      cursor: pointer;
      transition: all var(--transition);
      border: 1px solid transparent;
    }
    .file-item:hover {
      background: var(--card-hover);
      border-color: var(--card-border);
    }
    .file-item-icon {
      width: 28px;
      height: 28px;
      border-radius: var(--radius-sm);
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 13px;
      flex-shrink: 0;
    }
    .file-item-body { flex: 1; min-width: 0; }
    .file-item-name {
      font-size: 12px;
      font-weight: 500;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .file-item-meta {
      font-size: 10px;
      color: var(--muted);
      display: flex;
      align-items: center;
      gap: 6px;
      margin-top: 2px;
    }
    .file-item-right {
      text-align: right;
      flex-shrink: 0;
    }

    /* --- Circular gauge --- */
    .gauge-ring {
      position: relative;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .gauge-ring svg { transform: rotate(-90deg); }
    .gauge-ring-bg {
      fill: none;
      stroke: var(--input);
    }
    .gauge-ring-fill {
      fill: none;
      stroke-linecap: round;
      transition: stroke-dashoffset 1s cubic-bezier(0.4, 0, 0.2, 1);
    }
    .gauge-ring-center {
      position: absolute;
      text-align: center;
    }
    .gauge-ring-value {
      font-size: 20px;
      font-weight: 700;
      line-height: 1;
    }
    .gauge-ring-label {
      font-size: 10px;
      color: var(--muted);
      margin-top: 2px;
    }

    /* --- Section headers --- */
    .section-title {
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--muted);
      margin: 12px 0 6px;
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .section-title::after {
      content: '';
      flex: 1;
      height: 1px;
      background: var(--card-border);
    }

    /* --- Divider --- */
    .divider {
      height: 1px;
      background: var(--card-border);
      margin: 10px 0;
    }

    /* --- Empty state --- */
    .empty {
      text-align: center;
      padding: 24px 12px;
      color: var(--muted);
    }
    .empty-icon { font-size: 28px; margin-bottom: 8px; opacity: 0.5; }
    .empty-text { font-size: 12px; margin-bottom: 12px; }

    /* --- Animations --- */
    @keyframes fadeIn {
      from { opacity: 0; transform: translateY(4px); }
      to { opacity: 1; transform: translateY(0); }
    }
    @keyframes slideIn {
      from { opacity: 0; transform: translateX(-8px); }
      to { opacity: 1; transform: translateX(0); }
    }
    @keyframes pulse {
      0%, 100% { opacity: 1; }
      50% { opacity: 0.6; }
    }
    .animate-in {
      animation: fadeIn 0.3s ease forwards;
    }
    .animate-slide {
      animation: slideIn 0.3s ease forwards;
    }

    @media (prefers-reduced-motion: reduce) {
      *, *::before, *::after {
        animation-duration: 0.01ms !important;
        animation-iteration-count: 1 !important;
        transition-duration: 0.01ms !important;
      }
    }

    /* --- Accessibility --- */
    .sr-only {
      position: absolute;
      width: 1px;
      height: 1px;
      overflow: hidden;
      clip: rect(0, 0, 0, 0);
      white-space: nowrap;
      border: 0;
    }

    /* --- Scrollbar --- */
    ::-webkit-scrollbar { width: 6px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb {
      background: var(--card-border);
      border-radius: 3px;
    }
    ::-webkit-scrollbar-thumb:hover {
      background: var(--muted);
    }

    /* --- Tooltip --- */
    .tip {
      position: relative;
    }
    .tip::after {
      content: attr(data-tip);
      position: absolute;
      bottom: 100%;
      left: 50%;
      transform: translateX(-50%);
      padding: 4px 8px;
      border-radius: var(--radius-sm);
      background: var(--badge-bg);
      color: var(--badge-fg);
      font-size: 10px;
      white-space: nowrap;
      pointer-events: none;
      opacity: 0;
      transition: opacity var(--transition);
      z-index: 10;
    }
    .tip:hover::after { opacity: 1; }
  </style>`;
}

export function getNonce(): string {
  let text = '';
  const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
  for (let i = 0; i < 32; i++) {
    text += chars.charAt(Math.floor(Math.random() * chars.length));
  }
  return text;
}

export function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

export function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

export function wrapHtml(
  nonce: string,
  body: string,
  extraStyles?: string,
  script?: string,
): string {
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'nonce-${nonce}'; script-src 'nonce-${nonce}';">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  ${getSharedStyles(nonce)}
  ${extraStyles ? `<style nonce="${nonce}">${extraStyles}</style>` : ''}
</head>
<body>
  ${body}
  ${script ? `<script nonce="${nonce}">${script}</script>` : ''}
</body>
</html>`;
}
