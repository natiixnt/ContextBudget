/**
 * Pure HTML renderer for the sidebar control panel.
 *
 * Replaces the old chat-styled sidebar: no message bubbles, no
 * pretend conversation. Two things, top to bottom: a hero card with
 * cumulative savings plus the last run (the single click-through to
 * the dashboard) and the run feed. Runs arrive on their own through
 * the artifact watcher whenever an agent uses redcon; a manual pack
 * lives in the view title bar together with the other utility
 * actions (copy context, sync, doctor, config). The setup checklist
 * only appears while setup is incomplete. Sections are toggleable
 * via redcon.views.*.
 */

import type { RunReport, RunHistoryEntry } from '../types';

export interface ControlSections {
  miniDashboard: boolean;
  recentRuns: boolean;
  setup: boolean;
}

export interface ControlNotice {
  kind: 'info' | 'success' | 'error';
  text: string;
}

export interface ControlViewData {
  run: RunReport | null;
  history: RunHistoryEntry[];
  busyLabel: string | null;
  notice: ControlNotice | null;
  setup: { cliInstalled: boolean; mcpConfigured: boolean } | null;
  sections: ControlSections;
  costPerMillionTokens: number;
}

function esc(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function fmtK(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 100) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function fmtUsd(usd: number): string {
  if (usd >= 100) return `$${Math.round(usd).toLocaleString('en-US')}`;
  if (usd >= 1) return `$${usd.toFixed(2)}`;
  return `$${usd.toFixed(usd >= 0.01 ? 2 : 3)}`;
}

/* Injected below: canonical white logo lockup (same as the dashboard). */
const LOGO_SVG = `<svg class="logo" xmlns="http://www.w3.org/2000/svg" width="328" height="60" viewBox="0 0 327.5 60"><polygon fill="#ffffff" points="0.00,14.50 12.25,14.50 31.05,37.20 12.25,59.90 0.00,59.90 18.80,37.20"></polygon><polygon fill="#ffffff" points="19.12,14.50 31.37,14.50 50.17,37.20 31.37,59.90 19.12,59.90 37.92,37.20"></polygon><polygon fill="#ffffff" points="38.24,14.50 50.49,14.50 69.29,37.20 50.49,59.90 38.24,59.90 57.04,37.20"></polygon><g transform="translate(76.5,0)"><g fill="#ffffff" fill-opacity="1"><g transform="translate(0.98232, 57.495631)"><g><path d="M 27.125 -38.828125 L 29.65625 -38.828125 L 29.65625 -27.8125 L 25.90625 -27.8125 C 22.132812 -27.8125 19.5 -26.765625 18 -24.671875 C 16.5 -22.585938 15.75 -19.382812 15.75 -15.0625 L 15.75 0 L 4.4375 0 L 4.4375 -38.21875 L 15.4375 -38.21875 L 15.4375 -30.03125 C 16.351562 -32.726562 17.726562 -34.867188 19.5625 -36.453125 C 21.394531 -38.035156 23.914062 -38.828125 27.125 -38.828125 Z M 27.125 -38.828125 "></path></g></g></g><g fill="#ffffff" fill-opacity="1"><g transform="translate(28.951449, 57.495631)"><g><path d="M 23.609375 -6.578125 C 26.109375 -6.578125 28.046875 -7.046875 29.421875 -7.984375 C 30.796875 -8.929688 31.738281 -10.117188 32.25 -11.546875 L 43.109375 -11.546875 C 42.742188 -9.503906 41.882812 -7.5 40.53125 -5.53125 C 39.1875 -3.570312 37.140625 -1.957031 34.390625 -0.6875 C 31.640625 0.582031 27.972656 1.21875 23.390625 1.21875 C 18.441406 1.21875 14.453125 0.265625 11.421875 -1.640625 C 8.390625 -3.554688 6.195312 -6.066406 4.84375 -9.171875 C 3.5 -12.273438 2.828125 -15.613281 2.828125 -19.1875 C 2.828125 -22.90625 3.550781 -26.289062 5 -29.34375 C 6.457031 -32.40625 8.710938 -34.851562 11.765625 -36.6875 C 14.828125 -38.519531 18.703125 -39.4375 23.390625 -39.4375 C 28.222656 -39.4375 32.078125 -38.503906 34.953125 -36.640625 C 37.835938 -34.785156 39.925781 -32.328125 41.21875 -29.265625 C 42.519531 -26.210938 43.171875 -22.878906 43.171875 -19.265625 C 43.171875 -18.703125 43.171875 -18.148438 43.171875 -17.609375 C 43.171875 -17.078125 43.125 -16.609375 43.03125 -16.203125 L 13.90625 -16.203125 C 14.257812 -12.585938 15.289062 -10.078125 17 -8.671875 C 18.707031 -7.273438 20.910156 -6.578125 23.609375 -6.578125 Z M 23.53125 -31.796875 C 20.832031 -31.796875 18.679688 -31.15625 17.078125 -29.875 C 15.472656 -28.601562 14.441406 -26.335938 13.984375 -23.078125 L 32.40625 -23.078125 C 32.050781 -28.890625 29.09375 -31.796875 23.53125 -31.796875 Z M 23.53125 -31.796875 "></path></g></g></g><g fill="#ffffff" fill-opacity="1"><g transform="translate(71.363813, 57.495631)"><g><path d="M 34.078125 -53.5 L 45.390625 -53.5 L 45.390625 0 L 34.078125 0 L 34.078125 -5.65625 C 32.753906 -3.46875 30.972656 -1.773438 28.734375 -0.578125 C 26.492188 0.617188 23.71875 1.21875 20.40625 1.21875 C 16.28125 1.21875 12.914062 0.289062 10.3125 -1.5625 C 7.71875 -3.425781 5.820312 -5.910156 4.625 -9.015625 C 3.425781 -12.117188 2.828125 -15.507812 2.828125 -19.1875 C 2.828125 -22.800781 3.425781 -26.148438 4.625 -29.234375 C 5.820312 -32.316406 7.71875 -34.785156 10.3125 -36.640625 C 12.914062 -38.503906 16.28125 -39.4375 20.40625 -39.4375 C 23.71875 -39.4375 26.492188 -38.820312 28.734375 -37.59375 C 30.972656 -36.375 32.753906 -34.671875 34.078125 -32.484375 Z M 23.921875 -7.640625 C 27.484375 -7.640625 30.066406 -8.582031 31.671875 -10.46875 C 33.273438 -12.351562 34.078125 -15.234375 34.078125 -19.109375 C 34.078125 -23.023438 33.273438 -25.910156 31.671875 -27.765625 C 30.066406 -29.628906 27.484375 -30.5625 23.921875 -30.5625 C 20.359375 -30.5625 17.835938 -29.644531 16.359375 -27.8125 C 14.878906 -25.976562 14.140625 -23.078125 14.140625 -19.109375 C 14.140625 -15.179688 14.878906 -12.285156 16.359375 -10.421875 C 17.835938 -8.566406 20.359375 -7.640625 23.921875 -7.640625 Z M 23.921875 -7.640625 "></path></g></g></g><g fill="#ffffff" fill-opacity="1"><g transform="translate(117.520717, 57.495631)"><g><path d="M 2.828125 -19.109375 C 2.828125 -22.671875 3.539062 -25.992188 4.96875 -29.078125 C 6.394531 -32.160156 8.648438 -34.65625 11.734375 -36.5625 C 14.816406 -38.476562 18.828125 -39.4375 23.765625 -39.4375 C 28.203125 -39.4375 31.867188 -38.695312 34.765625 -37.21875 C 37.671875 -35.738281 39.875 -33.738281 41.375 -31.21875 C 42.882812 -28.695312 43.691406 -25.859375 43.796875 -22.703125 L 32.78125 -22.703125 C 32.582031 -25.097656 31.796875 -27.003906 30.421875 -28.421875 C 29.046875 -29.847656 26.90625 -30.5625 24 -30.5625 C 20.9375 -30.5625 18.539062 -29.722656 16.8125 -28.046875 C 15.082031 -26.367188 14.21875 -23.390625 14.21875 -19.109375 C 14.21875 -14.773438 15.054688 -11.78125 16.734375 -10.125 C 18.421875 -8.46875 20.789062 -7.640625 23.84375 -7.640625 C 26.851562 -7.640625 29.082031 -8.414062 30.53125 -9.96875 C 31.976562 -11.519531 32.804688 -13.445312 33.015625 -15.75 L 44.015625 -15.75 C 43.960938 -12.789062 43.195312 -10.007812 41.71875 -7.40625 C 40.25 -4.8125 38.046875 -2.722656 35.109375 -1.140625 C 32.179688 0.429688 28.476562 1.21875 24 1.21875 C 19.050781 1.21875 15.007812 0.265625 11.875 -1.640625 C 8.75 -3.554688 6.457031 -6.050781 5 -9.125 C 3.550781 -12.207031 2.828125 -15.535156 2.828125 -19.109375 Z M 2.828125 -19.109375 "></path></g></g></g><g fill="#ffffff" fill-opacity="1"><g transform="translate(160.620862, 57.495631)"><g><path d="M 24 1.21875 C 18.957031 1.21875 14.878906 0.265625 11.765625 -1.640625 C 8.660156 -3.554688 6.394531 -6.066406 4.96875 -9.171875 C 3.539062 -12.273438 2.828125 -15.613281 2.828125 -19.1875 C 2.828125 -22.695312 3.539062 -25.992188 4.96875 -29.078125 C 6.394531 -32.160156 8.660156 -34.65625 11.765625 -36.5625 C 14.878906 -38.476562 18.957031 -39.4375 24 -39.4375 C 29.09375 -39.4375 33.191406 -38.476562 36.296875 -36.5625 C 39.410156 -34.65625 41.664062 -32.160156 43.0625 -29.078125 C 44.46875 -25.992188 45.171875 -22.695312 45.171875 -19.1875 C 45.171875 -15.664062 44.46875 -12.347656 43.0625 -9.234375 C 41.664062 -6.128906 39.410156 -3.609375 36.296875 -1.671875 C 33.191406 0.253906 29.09375 1.21875 24 1.21875 Z M 14.21875 -19.109375 C 14.21875 -14.878906 14.992188 -11.910156 16.546875 -10.203125 C 18.097656 -8.492188 20.582031 -7.640625 24 -7.640625 C 27.457031 -7.640625 29.960938 -8.492188 31.515625 -10.203125 C 33.078125 -11.910156 33.859375 -14.878906 33.859375 -19.109375 C 33.859375 -23.335938 33.078125 -26.300781 31.515625 -28 C 29.960938 -29.707031 27.457031 -30.5625 24 -30.5625 C 20.582031 -30.5625 18.097656 -29.707031 16.546875 -28 C 14.992188 -26.300781 14.21875 -23.335938 14.21875 -19.109375 Z M 14.21875 -19.109375 "></path></g></g></g><g fill="#ffffff" fill-opacity="1"><g transform="translate(205.172961, 57.495631)"><g><path d="M 29.5 -39.4375 C 34.488281 -39.4375 38.101562 -38.019531 40.34375 -35.1875 C 42.59375 -32.363281 43.71875 -28.457031 43.71875 -23.46875 L 43.71875 0 L 32.40625 0 L 32.40625 -21.703125 C 32.40625 -24.765625 31.78125 -26.957031 30.53125 -28.28125 C 29.28125 -29.601562 27.484375 -30.265625 25.140625 -30.265625 C 22.140625 -30.265625 19.820312 -29.242188 18.1875 -27.203125 C 16.5625 -25.171875 15.75 -21.882812 15.75 -17.34375 L 15.75 0 L 4.4375 0 L 4.4375 -38.21875 L 15.75 -38.21875 L 15.75 -30.71875 C 16.96875 -33.320312 18.695312 -35.425781 20.9375 -37.03125 C 23.175781 -38.632812 26.03125 -39.4375 29.5 -39.4375 Z M 29.5 -39.4375 "></path></g></g></g></g></svg>`;

/* Brand triple-chevron section marker, same as the dashboard. */
const CHEVRON_SVG =
  '<svg width="12.2" height="8" viewBox="0 14.5 69.3 45.4" fill="currentColor" aria-hidden="true">' +
  '<polygon points="0,14.5 12.25,14.5 31.05,37.2 12.25,59.9 0,59.9 18.8,37.2"></polygon>' +
  '<polygon points="19.12,14.5 31.37,14.5 50.17,37.2 31.37,59.9 19.12,59.9 37.92,37.2"></polygon>' +
  '<polygon points="38.24,14.5 50.49,14.5 69.29,37.2 50.49,59.9 38.24,59.9 57.04,37.2"></polygon>' +
  '</svg>';

export function renderControlViewHtml(data: ControlViewData, nonce: string): string {
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'nonce-${nonce}'; script-src 'nonce-${nonce}';">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Redcon</title>
  <style nonce="${nonce}">
    body {
      --panel: #FFFFFF; --border: #E2E4EA; --border2: #CFD1D6;
      --text: #24272E; --text2: #5C6069; --text3: #8D9199;
      --track: #E8EAEE; --chip: #EEF0F3; --rowHover: rgba(15,30,60,0.045);
      --good: #178A5E; --goodBg: rgba(23,138,94,0.10);
      --warn: #9A7514; --warnBg: rgba(154,117,20,0.10);
      --bad: #E51414; --badBg: rgba(229,20,20,0.10);
      --red: #E51414; --redHov: #C51111;
      --delta: #178A5E;
      --mono: ui-monospace, 'SF Mono', 'Cascadia Mono', Menlo, Consolas, monospace;
      --display: 'Telegraf', -apple-system, 'Segoe UI', sans-serif;
    }
    body.vscode-dark, body.vscode-high-contrast {
      --panel: #242529; --border: #3A3B42; --border2: #4C4D55;
      --text: #D8D8DC; --text2: #9EA0A6; --text3: #6E7076;
      --track: #3A3B42; --chip: #313136; --rowHover: rgba(255,255,255,0.05);
      --good: #3FAE87; --goodBg: rgba(63,174,135,0.14);
      --warn: #D3A83C; --warnBg: rgba(211,168,60,0.14);
      --bad: #E51414; --badBg: rgba(229,20,20,0.15);
      --delta: #3FAE87;
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      font-size: 12px; line-height: 1.45;
      color: var(--text);
      background: var(--vscode-sideBar-background, transparent);
      padding: 10px 12px 16px;
      display: flex; flex-direction: column; gap: 14px;
    }
    .num { font-variant-numeric: tabular-nums; }

    .mini-dash {
      background: linear-gradient(122deg, #E51414 0%, #A31124 52%, #57102F 80%, #1D1038 100%);
      border-radius: 6px; padding: 11px 12px 12px; color: #fff;
      cursor: pointer; display: flex; flex-direction: column; gap: 8px;
    }
    .mini-dash:hover { filter: brightness(1.06); }
    .mini-dash .logo { height: 15px; width: auto; }
    .mini-head { display: flex; align-items: center; justify-content: space-between; }
    .mini-open { font-size: 10px; color: rgba(255,255,255,0.62); }
    .mini-label { font-size: 10px; font-weight: 700; color: rgba(255,255,255,0.62); }
    .mini-line { display: flex; align-items: baseline; gap: 7px; }
    .mini-value {
      font-family: var(--display); font-weight: 800; font-size: 27px;
      letter-spacing: -0.01em; line-height: 1.15; color: #fff;
    }
    .mini-unit { font-family: var(--display); font-size: 12px; color: rgba(255,255,255,0.82); }
    .mini-caption { font-size: 10.5px; color: rgba(255,255,255,0.62); }
    .mini-caption b { color: #fff; font-weight: 700; }
    .mini-trend { display: flex; align-items: flex-end; gap: 3px; height: 34px; }
    .mini-bar { flex: 1; border-radius: 1.5px 1.5px 0 0; background: rgba(255,255,255,0.38); min-height: 2px; }
    .mini-bar.last { background: #fff; }
    .mini-axis {
      border-top: 1px solid rgba(255,255,255,0.25); padding-top: 2px;
      display: flex; justify-content: space-between;
      font-family: var(--mono); font-size: 9px; color: rgba(255,255,255,0.55);
    }
    .mini-divider { border-top: 1px solid rgba(255,255,255,0.25); padding-top: 8px; }
    .mini-run-line {
      display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
      font-size: 11px; color: rgba(255,255,255,0.82);
    }
    .mini-run-line b { font-weight: 700; color: #fff; }
    .mini-risk { display: inline-flex; align-items: center; gap: 4px; }
    .mini-risk .dot { width: 6px; height: 6px; border-radius: 99px; }
    .mini-task {
      margin-top: 3px; font-size: 10.5px; color: rgba(255,255,255,0.55);
      overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }

    .section-title {
      display: inline-flex; align-items: center; gap: 2px;
      font-family: var(--display); font-weight: 800; font-size: 11.5px;
      letter-spacing: 0.01em; text-transform: lowercase; color: var(--text);
      margin-bottom: 7px;
    }
    .section-title svg { color: var(--red); }

    .btn {
      display: inline-flex; align-items: center; justify-content: center; gap: 6px;
      height: 26px; padding: 0 12px; border-radius: 5px; font-size: 12px;
      cursor: pointer; font-family: inherit; border: 1px solid var(--border);
      background: var(--chip); color: var(--text); transition: background 0.15s;
    }
    .btn:hover { background: var(--rowHover); }
    .btn:disabled { opacity: 0.55; cursor: default; }

    .notice {
      padding: 6px 9px; border-radius: 5px; font-size: 11.5px;
      border: 1px solid var(--border); color: var(--text2); background: var(--chip);
    }
    .notice.success { border-color: var(--good); color: var(--good); background: var(--goodBg); }
    .notice.error { border-color: var(--bad); color: var(--bad); background: var(--badBg); }

    .busy { display: flex; align-items: center; gap: 8px; color: var(--text2); font-size: 11.5px; }
    .spinner {
      width: 12px; height: 12px; border-radius: 50%; flex-shrink: 0;
      border: 2px solid var(--track); border-top-color: var(--red);
      animation: spin 0.8s linear infinite;
    }
    @keyframes spin { to { transform: rotate(360deg); } }

    .list-card { padding: 4px 6px; }
    .card {
      background: var(--panel); border: 1px solid var(--border);
      border-radius: 6px; padding: 10px 12px;
    }
    .run-row {
      display: flex; align-items: flex-start; gap: 9px;
      padding: 7px 8px; border-radius: 4px; cursor: pointer;
      transition: background 0.12s;
    }
    .run-row:hover { background: var(--rowHover); }
    .run-row + .run-row { border-top: 1px solid var(--border); }
    .run-idx {
      font-family: var(--mono); font-size: 9.5px; font-weight: 600;
      color: var(--red); padding-top: 2.5px; flex-shrink: 0;
    }
    .run-main { flex: 1; min-width: 0; }
    .run-top { display: flex; align-items: baseline; gap: 8px; }
    .run-task { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 11.5px; }
    .run-saved { font-family: var(--mono); font-size: 11px; color: var(--red); font-weight: 600; flex-shrink: 0; }
    .run-sub { display: flex; align-items: center; gap: 7px; margin-top: 4px; }
    .run-track { flex: 1; height: 3px; border-radius: 2px; background: var(--track); overflow: hidden; }
    .run-fill { display: block; height: 100%; border-radius: 2px; background: var(--red); }
    .run-when { font-family: var(--mono); font-size: 9.5px; color: var(--text3); flex-shrink: 0; }
    .run-dot { width: 6px; height: 6px; border-radius: 99px; flex-shrink: 0; }
    .empty-hint { font-size: 11px; color: var(--text3); padding: 2px 6px; }

    .setup-step { display: flex; align-items: flex-start; gap: 8px; padding: 5px 0; }
    .setup-icon {
      width: 16px; height: 16px; border-radius: 99px; flex-shrink: 0;
      display: inline-flex; align-items: center; justify-content: center;
      font-size: 10px; font-weight: 700;
      background: var(--chip); color: var(--text2); border: 1px solid var(--border);
    }
    .setup-step.done .setup-icon { background: var(--goodBg); color: var(--good); border-color: transparent; }
    .setup-body { flex: 1; min-width: 0; }
    .setup-name { font-size: 11.5px; font-weight: 600; }
    .setup-step.done .setup-name { color: var(--text3); text-decoration: line-through; }
    .setup-body .btn { height: 22px; font-size: 11px; margin-top: 4px; padding: 0 9px; }
  </style>
</head>
<body>
  ${data.sections.miniDashboard ? renderMiniDash(data) : ''}
  ${renderStatus(data)}
  ${data.sections.recentRuns ? renderRecentRuns(data.history) : ''}
  ${data.sections.setup ? renderSetup(data.setup) : ''}
  <script nonce="${nonce}">
    const vscode = acquireVsCodeApi();
    document.addEventListener('click', (e) => {
      const el = e.target.closest('[data-action]');
      if (!el) return;
      const action = el.dataset.action;
      if (action === 'open-run') vscode.postMessage({ command: 'openRun', path: el.dataset.path });
      else vscode.postMessage({ command: 'exec', action });
    });
  </script>
</body>
</html>`;
}

function renderMiniDash(data: ControlViewData): string {
  const totalSaved = data.history.reduce((s, e) => s + (e.tokensSaved ?? 0), 0)
    || (data.run ? data.run.budget.estimated_saved_tokens : 0);
  const runCount = data.history.length || (data.run ? 1 : 0);
  const usd = (totalSaved / 1_000_000) * data.costPerMillionTokens;

  const entries = data.history.slice(0, 12).reverse();
  const maxSaved = Math.max(...entries.map((e) => e.tokensSaved ?? 0), 1);
  const bars = entries.map((e, i) => {
    const h = Math.max(2, Math.round(((e.tokensSaved ?? 0) / maxSaved) * 34));
    return `<div class="mini-bar${i === entries.length - 1 ? ' last' : ''}" style="height:${h}px"></div>`;
  }).join('');
  const trend = entries.length >= 2
    ? `<div><div class="mini-trend">${bars}</div><div class="mini-axis"><span>run 1</span><span>run ${entries.length}</span></div></div>`
    : '';

  const caption = runCount > 0
    ? `<div class="mini-caption num"><b>${fmtUsd(usd)}</b> saved &middot; across ${runCount} run${runCount === 1 ? '' : 's'}</div>`
    : '<div class="mini-caption">no runs yet. they land here automatically.</div>';

  return `
    <div class="mini-dash" data-action="dashboard" title="Open the full dashboard">
      <div class="mini-head">${LOGO_SVG}<span class="mini-open">open dashboard &raquo;</span></div>
      <div>
        <div class="mini-label">cumulative savings</div>
        <div class="mini-line">
          <span class="mini-value num">${fmtK(totalSaved)}</span>
          <span class="mini-unit">tokens saved.</span>
        </div>
        ${caption}
      </div>
      ${trend}
      ${renderLastRunLine(data.run)}
    </div>
  `;
}

/* Compact last-run summary inside the hero card. Dot colors are picked
 * for contrast on the brand gradient, not the standard status ramp. */
function renderLastRunLine(run: RunReport | null): string {
  if (!run) return '';
  const pct = run.max_tokens > 0
    ? Math.round((run.budget.estimated_input_tokens / run.max_tokens) * 100)
    : 0;
  const risk = run.budget.quality_risk_estimate;
  const dot = risk === 'high' ? '#FFFFFF' : risk === 'medium' ? '#F2C14E' : '#4ADE9C';
  return `
      <div class="mini-divider">
        <div class="mini-label">last run</div>
        <div class="mini-run-line num">
          <b>${fmtK(run.budget.estimated_saved_tokens)} saved</b>
          <span>${pct}% budget</span>
          <span>${run.files_included.length} files</span>
          <span class="mini-risk"><span class="dot" style="background:${dot}"></span>${esc(risk)} risk</span>
        </div>
        <div class="mini-task" title="${esc(run.task)}">${esc(run.task)}</div>
      </div>`;
}

/* Slim progress / result strip. Only rendered while a command runs or
 * right after one finishes; the rest of the time it takes no space. */
function renderStatus(data: ControlViewData): string {
  if (data.busyLabel) {
    return `<div class="busy"><span class="spinner"></span>${esc(data.busyLabel)}</div>`;
  }
  if (data.notice) {
    return `<div class="notice ${data.notice.kind}">${esc(data.notice.text)}</div>`;
  }
  return '';
}

function fmtAgo(iso: string): string {
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return '';
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return 'now';
  if (s < 3600) return `${Math.round(s / 60)}m`;
  if (s < 86400) return `${Math.round(s / 3600)}h`;
  return `${Math.round(s / 86400)}d`;
}

function renderRecentRuns(history: RunHistoryEntry[]): string {
  const entries = history.slice(0, 8);
  const maxSaved = Math.max(...entries.map((e) => e.tokensSaved ?? 0), 1);
  const dotByRisk: Record<string, string> = {
    low: 'var(--good)',
    medium: 'var(--warn)',
    high: 'var(--bad)',
  };
  const rows = entries.map((e, i) => {
    const saved = e.tokensSaved ?? 0;
    const width = Math.max(3, Math.round((saved / maxSaved) * 100));
    const pct = e.maxTokens > 0 ? Math.round((e.tokens / e.maxTokens) * 100) : 0;
    const tip = `${e.task} - ${pct}% budget - ${e.filesIncluded} files - ${e.risk} risk`;
    return `
    <div class="run-row" data-action="open-run" data-path="${esc(e.path)}" title="${esc(tip)}">
      <span class="run-idx num">${String(i + 1).padStart(2, '0')}</span>
      <div class="run-main">
        <div class="run-top">
          <span class="run-task">${esc(e.task)}</span>
          <span class="run-saved num">${fmtK(saved)}</span>
        </div>
        <div class="run-sub">
          <span class="run-track"><span class="run-fill" style="width:${width}%"></span></span>
          <span class="run-when num">${fmtAgo(e.generatedAt)}</span>
          <span class="run-dot" style="background:${dotByRisk[e.risk] ?? 'var(--text3)'}"></span>
        </div>
      </div>
    </div>`;
  });
  return `
    <div>
      <div class="section-title">${CHEVRON_SVG}<span>recent runs</span></div>
      <div class="card list-card">${rows.length ? rows.join('') : '<div class="empty-hint">Runs land here automatically when your agent uses redcon.</div>'}</div>
    </div>
  `;
}

function renderSetup(setup: { cliInstalled: boolean; mcpConfigured: boolean } | null): string {
  // Only surface setup while something is missing. A completed
  // checklist is dead weight in a panel this small.
  if (!setup || (setup.cliInstalled && setup.mcpConfigured)) return '';
  return `
    <div>
      <div class="section-title">${CHEVRON_SVG}<span>setup</span></div>
      <div class="card">
      <div class="setup-step ${setup.cliInstalled ? 'done' : ''}">
        <span class="setup-icon">${setup.cliInstalled ? '&#10003;' : '1'}</span>
        <div class="setup-body">
          <div class="setup-name">Install Redcon CLI</div>
          ${setup.cliInstalled ? '' : '<button class="btn" data-action="setupInstall">Install</button>'}
        </div>
      </div>
      <div class="setup-step ${setup.mcpConfigured ? 'done' : ''}">
        <span class="setup-icon">${setup.mcpConfigured ? '&#10003;' : '2'}</span>
        <div class="setup-body">
          <div class="setup-name">Register MCP for your agents</div>
          ${setup.mcpConfigured ? '' : '<button class="btn" data-action="setupMcp">Register</button>'}
        </div>
      </div>
      </div>
    </div>
  `;
}
