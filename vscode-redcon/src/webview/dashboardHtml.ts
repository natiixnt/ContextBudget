/**
 * Pure HTML renderer for the dashboard webview.
 *
 * Implements the "Redcon Analytics" design handoff (boards 2a/2b):
 * brand banner with savings hero and per-run trend, four KPI cards,
 * two donut panels, token impact chart and two detail tables. Colors,
 * typography and geometry follow the handoff's design tokens; light
 * and dark palettes switch on VS Code's body theme class.
 *
 * No dependency on the vscode module, so it can be rendered and
 * screenshotted standalone.
 */

import type { RunReport, RunHistoryEntry } from '../types';

export type PrimaryMetric = 'tokens' | 'dollars';
export type BudgetPolicy = 'auto-raise' | 'strict-cap' | 'ask-first';
export type DataAccent = 'red' | 'blue' | 'violet' | 'crimson' | 'wine' | 'gradient';

export interface DashboardSections {
  kpis: boolean;
  donuts: boolean;
  impact: boolean;
  tables: boolean;
}

export interface DashboardData {
  run: RunReport;
  history: RunHistoryEntry[];
  costPerMillionTokens: number;
  primaryMetric: PrimaryMetric;
  budgetPolicy: BudgetPolicy;
  dataAccent: DataAccent;
  sections?: DashboardSections;
}

/* ------------------------------------------------------------------ */
/* Design tokens (from the handoff README)                             */
/* ------------------------------------------------------------------ */

const BRAND_GRADIENT =
  'linear-gradient(102deg, #E51414 0%, #A31124 46%, #57102F 72%, #1D1038 100%)';

interface AccentTokens {
  d1: string;
  d1soft: string;
  acc: string;
  s: [string, string, string, string, string, string];
  budgetArc?: string;
  barFill?: string;
  barTrack?: string;
  barBgSize?: string;
}

const ACCENTS: Record<DataAccent, { dark: AccentTokens; light: AccentTokens }> = {
  red: {
    dark: {
      d1: '#E51414', d1soft: 'rgba(229,20,20,0.16)', acc: '#EC5252',
      s: ['#7A0B0B', '#A50F0F', '#E51414', '#EC5252', '#F28C8C', '#F8C4C4'],
    },
    light: {
      d1: '#E51414', d1soft: 'rgba(229,20,20,0.10)', acc: '#A50F0F',
      s: ['#5E0808', '#9E0E0E', '#E51414', '#EA4A4A', '#F18989', '#F7C2C2'],
    },
  },
  blue: {
    dark: {
      d1: '#5B9BD5', d1soft: 'rgba(91,155,213,0.18)', acc: '#86AEDC',
      s: ['#2C5B8C', '#3E77B0', '#5B9BD5', '#82B6E4', '#ABCFEF', '#D6E8F8'],
    },
    light: {
      d1: '#2E6FB2', d1soft: 'rgba(46,111,178,0.12)', acc: '#2A4E7E',
      s: ['#16385C', '#24568A', '#3572AE', '#5E96CB', '#92BCE0', '#C9DFF1'],
    },
  },
  violet: {
    dark: {
      d1: '#8B68D4', d1soft: 'rgba(139,104,212,0.20)', acc: '#A48FE0',
      s: ['#3E2277', '#5230A0', '#6D46C4', '#8B68D4', '#AC93E2', '#D2C4F0'],
    },
    light: {
      d1: '#513097', d1soft: 'rgba(81,48,151,0.11)', acc: '#3A2270',
      s: ['#1D1038', '#33205F', '#4A2F87', '#6C51B2', '#9781D0', '#C7BBE8'],
    },
  },
  crimson: {
    dark: {
      d1: '#B01C34', d1soft: 'rgba(176,28,52,0.26)', acc: '#CE5D6C',
      s: ['#5E0D1A', '#851326', '#B01C34', '#CE4A59', '#E18B95', '#F2C4C9'],
    },
    light: {
      d1: '#A31124', d1soft: 'rgba(163,17,36,0.12)', acc: '#7A1020',
      s: ['#4A0A14', '#7A1020', '#A31124', '#C24955', '#DA8A92', '#EFC4C8'],
    },
  },
  wine: {
    dark: {
      d1: '#8E1B42', d1soft: 'rgba(142,27,66,0.28)', acc: '#C05C7E',
      s: ['#4E0C20', '#6E1230', '#8E1B42', '#B03A5E', '#CC6E88', '#E8AFC0'],
    },
    light: {
      d1: '#7A1230', d1soft: 'rgba(122,18,48,0.13)', acc: '#57102F',
      s: ['#3A0918', '#57102F', '#7A1230', '#9C3A56', '#C07690', '#E0B4C4'],
    },
  },
  // The handoff specifies the gradient's bar fills, track and budget
  // arc; its d1 / strategy ramp is not in the delivered files, so this
  // preset borrows the wine ramp (the gradient's tonal midpoint).
  gradient: {
    dark: {
      d1: '#8E1B42', d1soft: 'rgba(142,27,66,0.28)', acc: '#C05C7E',
      s: ['#4E0C20', '#6E1230', '#8E1B42', '#B03A5E', '#CC6E88', '#E8AFC0'],
      budgetArc: '#E51414',
      barFill: 'linear-gradient(90deg,#E51414 0%,#8E1B42 45%,#8B68D4 100%)',
      barTrack:
        'linear-gradient(90deg,rgba(229,20,20,0.17) 0%,rgba(142,27,66,0.17) 45%,rgba(139,104,212,0.17) 100%)',
      barBgSize: '820px 100%',
    },
    light: {
      d1: '#7A1230', d1soft: 'rgba(122,18,48,0.13)', acc: '#57102F',
      s: ['#3A0918', '#57102F', '#7A1230', '#9C3A56', '#C07690', '#E0B4C4'],
      budgetArc: '#E51414',
      barFill: 'linear-gradient(90deg,#E51414 0%,#57102F 55%,#1D1038 100%)',
      barTrack:
        'linear-gradient(90deg,rgba(229,20,20,0.11) 0%,rgba(87,16,47,0.11) 55%,rgba(29,16,56,0.11) 100%)',
      barBgSize: '820px 100%',
    },
  },
};

function accentCss(tokens: AccentTokens): string {
  const d1grad = `linear-gradient(0deg,${tokens.d1},${tokens.d1})`;
  const softgrad = `linear-gradient(0deg,${tokens.d1soft},${tokens.d1soft})`;
  return [
    `--d1:${tokens.d1}`,
    `--d1soft:${tokens.d1soft}`,
    `--acc:${tokens.acc}`,
    `--budgetArc:${tokens.budgetArc ?? tokens.d1}`,
    `--barFill:${tokens.barFill ?? d1grad}`,
    `--barTrack:${tokens.barTrack ?? softgrad}`,
    `--barBgSize:${tokens.barBgSize ?? 'auto'}`,
    ...tokens.s.map((c, i) => `--s${i + 1}:${c}`),
  ].join(';');
}

/* Canonical strategy order: fixed name -> ramp slot (darkest first). */
const STRATEGY_ORDER: [string, string][] = [
  ['full', 'full'],
  ['snippet', 'snippet'],
  ['symbol', 'symbol extraction'],
  ['symbol_extraction', 'symbol extraction'],
  ['slicing', 'slicing'],
  ['summary', 'summary'],
  ['cache_reuse', 'cache reuse'],
];
const STRATEGY_SLOT: Record<string, number> = {
  full: 1,
  snippet: 2,
  symbol: 3,
  symbol_extraction: 3,
  slicing: 4,
  summary: 5,
  cache_reuse: 6,
};

/* ------------------------------------------------------------------ */
/* Formatting                                                          */
/* ------------------------------------------------------------------ */

function fmtK(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 100) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function fmtInt(n: number): string {
  return n.toLocaleString('en-US');
}

function fmtUsd(usd: number): string {
  if (usd >= 100) return `$${Math.round(usd).toLocaleString('en-US')}`;
  if (usd >= 1) return `$${usd.toFixed(2)}`;
  return `$${usd.toFixed(usd >= 0.01 ? 2 : 3)}`;
}

function esc(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/* Injected by the build below: canonical white logo lockup. */
const LOGO_SVG = `<svg class="logo" xmlns="http://www.w3.org/2000/svg" width="328" height="60" viewBox="0 0 327.5 60"><polygon fill="#ffffff" points="0.00,14.50 12.25,14.50 31.05,37.20 12.25,59.90 0.00,59.90 18.80,37.20"></polygon><polygon fill="#ffffff" points="19.12,14.50 31.37,14.50 50.17,37.20 31.37,59.90 19.12,59.90 37.92,37.20"></polygon><polygon fill="#ffffff" points="38.24,14.50 50.49,14.50 69.29,37.20 50.49,59.90 38.24,59.90 57.04,37.20"></polygon><g transform="translate(76.5,0)"><g fill="#ffffff" fill-opacity="1"><g transform="translate(0.98232, 57.495631)"><g><path d="M 27.125 -38.828125 L 29.65625 -38.828125 L 29.65625 -27.8125 L 25.90625 -27.8125 C 22.132812 -27.8125 19.5 -26.765625 18 -24.671875 C 16.5 -22.585938 15.75 -19.382812 15.75 -15.0625 L 15.75 0 L 4.4375 0 L 4.4375 -38.21875 L 15.4375 -38.21875 L 15.4375 -30.03125 C 16.351562 -32.726562 17.726562 -34.867188 19.5625 -36.453125 C 21.394531 -38.035156 23.914062 -38.828125 27.125 -38.828125 Z M 27.125 -38.828125 "></path></g></g></g><g fill="#ffffff" fill-opacity="1"><g transform="translate(28.951449, 57.495631)"><g><path d="M 23.609375 -6.578125 C 26.109375 -6.578125 28.046875 -7.046875 29.421875 -7.984375 C 30.796875 -8.929688 31.738281 -10.117188 32.25 -11.546875 L 43.109375 -11.546875 C 42.742188 -9.503906 41.882812 -7.5 40.53125 -5.53125 C 39.1875 -3.570312 37.140625 -1.957031 34.390625 -0.6875 C 31.640625 0.582031 27.972656 1.21875 23.390625 1.21875 C 18.441406 1.21875 14.453125 0.265625 11.421875 -1.640625 C 8.390625 -3.554688 6.195312 -6.066406 4.84375 -9.171875 C 3.5 -12.273438 2.828125 -15.613281 2.828125 -19.1875 C 2.828125 -22.90625 3.550781 -26.289062 5 -29.34375 C 6.457031 -32.40625 8.710938 -34.851562 11.765625 -36.6875 C 14.828125 -38.519531 18.703125 -39.4375 23.390625 -39.4375 C 28.222656 -39.4375 32.078125 -38.503906 34.953125 -36.640625 C 37.835938 -34.785156 39.925781 -32.328125 41.21875 -29.265625 C 42.519531 -26.210938 43.171875 -22.878906 43.171875 -19.265625 C 43.171875 -18.703125 43.171875 -18.148438 43.171875 -17.609375 C 43.171875 -17.078125 43.125 -16.609375 43.03125 -16.203125 L 13.90625 -16.203125 C 14.257812 -12.585938 15.289062 -10.078125 17 -8.671875 C 18.707031 -7.273438 20.910156 -6.578125 23.609375 -6.578125 Z M 23.53125 -31.796875 C 20.832031 -31.796875 18.679688 -31.15625 17.078125 -29.875 C 15.472656 -28.601562 14.441406 -26.335938 13.984375 -23.078125 L 32.40625 -23.078125 C 32.050781 -28.890625 29.09375 -31.796875 23.53125 -31.796875 Z M 23.53125 -31.796875 "></path></g></g></g><g fill="#ffffff" fill-opacity="1"><g transform="translate(71.363813, 57.495631)"><g><path d="M 34.078125 -53.5 L 45.390625 -53.5 L 45.390625 0 L 34.078125 0 L 34.078125 -5.65625 C 32.753906 -3.46875 30.972656 -1.773438 28.734375 -0.578125 C 26.492188 0.617188 23.71875 1.21875 20.40625 1.21875 C 16.28125 1.21875 12.914062 0.289062 10.3125 -1.5625 C 7.71875 -3.425781 5.820312 -5.910156 4.625 -9.015625 C 3.425781 -12.117188 2.828125 -15.507812 2.828125 -19.1875 C 2.828125 -22.800781 3.425781 -26.148438 4.625 -29.234375 C 5.820312 -32.316406 7.71875 -34.785156 10.3125 -36.640625 C 12.914062 -38.503906 16.28125 -39.4375 20.40625 -39.4375 C 23.71875 -39.4375 26.492188 -38.820312 28.734375 -37.59375 C 30.972656 -36.375 32.753906 -34.671875 34.078125 -32.484375 Z M 23.921875 -7.640625 C 27.484375 -7.640625 30.066406 -8.582031 31.671875 -10.46875 C 33.273438 -12.351562 34.078125 -15.234375 34.078125 -19.109375 C 34.078125 -23.023438 33.273438 -25.910156 31.671875 -27.765625 C 30.066406 -29.628906 27.484375 -30.5625 23.921875 -30.5625 C 20.359375 -30.5625 17.835938 -29.644531 16.359375 -27.8125 C 14.878906 -25.976562 14.140625 -23.078125 14.140625 -19.109375 C 14.140625 -15.179688 14.878906 -12.285156 16.359375 -10.421875 C 17.835938 -8.566406 20.359375 -7.640625 23.921875 -7.640625 Z M 23.921875 -7.640625 "></path></g></g></g><g fill="#ffffff" fill-opacity="1"><g transform="translate(117.520717, 57.495631)"><g><path d="M 2.828125 -19.109375 C 2.828125 -22.671875 3.539062 -25.992188 4.96875 -29.078125 C 6.394531 -32.160156 8.648438 -34.65625 11.734375 -36.5625 C 14.816406 -38.476562 18.828125 -39.4375 23.765625 -39.4375 C 28.203125 -39.4375 31.867188 -38.695312 34.765625 -37.21875 C 37.671875 -35.738281 39.875 -33.738281 41.375 -31.21875 C 42.882812 -28.695312 43.691406 -25.859375 43.796875 -22.703125 L 32.78125 -22.703125 C 32.582031 -25.097656 31.796875 -27.003906 30.421875 -28.421875 C 29.046875 -29.847656 26.90625 -30.5625 24 -30.5625 C 20.9375 -30.5625 18.539062 -29.722656 16.8125 -28.046875 C 15.082031 -26.367188 14.21875 -23.390625 14.21875 -19.109375 C 14.21875 -14.773438 15.054688 -11.78125 16.734375 -10.125 C 18.421875 -8.46875 20.789062 -7.640625 23.84375 -7.640625 C 26.851562 -7.640625 29.082031 -8.414062 30.53125 -9.96875 C 31.976562 -11.519531 32.804688 -13.445312 33.015625 -15.75 L 44.015625 -15.75 C 43.960938 -12.789062 43.195312 -10.007812 41.71875 -7.40625 C 40.25 -4.8125 38.046875 -2.722656 35.109375 -1.140625 C 32.179688 0.429688 28.476562 1.21875 24 1.21875 C 19.050781 1.21875 15.007812 0.265625 11.875 -1.640625 C 8.75 -3.554688 6.457031 -6.050781 5 -9.125 C 3.550781 -12.207031 2.828125 -15.535156 2.828125 -19.109375 Z M 2.828125 -19.109375 "></path></g></g></g><g fill="#ffffff" fill-opacity="1"><g transform="translate(160.620862, 57.495631)"><g><path d="M 24 1.21875 C 18.957031 1.21875 14.878906 0.265625 11.765625 -1.640625 C 8.660156 -3.554688 6.394531 -6.066406 4.96875 -9.171875 C 3.539062 -12.273438 2.828125 -15.613281 2.828125 -19.1875 C 2.828125 -22.695312 3.539062 -25.992188 4.96875 -29.078125 C 6.394531 -32.160156 8.660156 -34.65625 11.765625 -36.5625 C 14.878906 -38.476562 18.957031 -39.4375 24 -39.4375 C 29.09375 -39.4375 33.191406 -38.476562 36.296875 -36.5625 C 39.410156 -34.65625 41.664062 -32.160156 43.0625 -29.078125 C 44.46875 -25.992188 45.171875 -22.695312 45.171875 -19.1875 C 45.171875 -15.664062 44.46875 -12.347656 43.0625 -9.234375 C 41.664062 -6.128906 39.410156 -3.609375 36.296875 -1.671875 C 33.191406 0.253906 29.09375 1.21875 24 1.21875 Z M 14.21875 -19.109375 C 14.21875 -14.878906 14.992188 -11.910156 16.546875 -10.203125 C 18.097656 -8.492188 20.582031 -7.640625 24 -7.640625 C 27.457031 -7.640625 29.960938 -8.492188 31.515625 -10.203125 C 33.078125 -11.910156 33.859375 -14.878906 33.859375 -19.109375 C 33.859375 -23.335938 33.078125 -26.300781 31.515625 -28 C 29.960938 -29.707031 27.457031 -30.5625 24 -30.5625 C 20.582031 -30.5625 18.097656 -29.707031 16.546875 -28 C 14.992188 -26.300781 14.21875 -23.335938 14.21875 -19.109375 Z M 14.21875 -19.109375 "></path></g></g></g><g fill="#ffffff" fill-opacity="1"><g transform="translate(205.172961, 57.495631)"><g><path d="M 29.5 -39.4375 C 34.488281 -39.4375 38.101562 -38.019531 40.34375 -35.1875 C 42.59375 -32.363281 43.71875 -28.457031 43.71875 -23.46875 L 43.71875 0 L 32.40625 0 L 32.40625 -21.703125 C 32.40625 -24.765625 31.78125 -26.957031 30.53125 -28.28125 C 29.28125 -29.601562 27.484375 -30.265625 25.140625 -30.265625 C 22.140625 -30.265625 19.820312 -29.242188 18.1875 -27.203125 C 16.5625 -25.171875 15.75 -21.882812 15.75 -17.34375 L 15.75 0 L 4.4375 0 L 4.4375 -38.21875 L 15.75 -38.21875 L 15.75 -30.71875 C 16.96875 -33.320312 18.695312 -35.425781 20.9375 -37.03125 C 23.175781 -38.632812 26.03125 -39.4375 29.5 -39.4375 Z M 29.5 -39.4375 "></path></g></g></g></g></svg>`;

/* Triple-chevron section marker (spec: 12.2x8px, currentColor). */
const CHEVRON_SVG =
  '<svg width="12.2" height="8" viewBox="0 14.5 69.3 45.4" fill="currentColor" aria-hidden="true">' +
  '<polygon points="0,14.5 12.25,14.5 31.05,37.2 12.25,59.9 0,59.9 18.8,37.2"></polygon>' +
  '<polygon points="19.12,14.5 31.37,14.5 50.17,37.2 31.37,59.9 19.12,59.9 37.92,37.2"></polygon>' +
  '<polygon points="38.24,14.5 50.49,14.5 69.29,37.2 50.49,59.9 38.24,59.9 57.04,37.2"></polygon>' +
  '</svg>';

/* ------------------------------------------------------------------ */
/* Entry                                                               */
/* ------------------------------------------------------------------ */

export function renderDashboardHtml(data: DashboardData | null, nonce: string): string {
  const accent = ACCENTS[data?.dataAccent ?? 'red'] ?? ACCENTS.red;

  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'nonce-${nonce}'; script-src 'nonce-${nonce}';">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Redcon Analytics</title>
  <style nonce="${nonce}">
    /* Theme tokens (handoff boards 2b light / 2a dark). */
    body {
      --bg: var(--vscode-editor-background, #FFFFFF);
      --panel: #FFFFFF; --panel2: #F3F4F6;
      --border: #E2E4EA; --border2: #CFD1D6;
      --text: #24272E; --text2: #5C6069; --text3: #8D9199;
      --track: #E8EAEE; --chip: #EEF0F3;
      --rowHover: rgba(15,30,60,0.045);
      --good: #178A5E; --goodBg: rgba(23,138,94,0.10);
      --warn: #9A7514; --warnBg: rgba(154,117,20,0.10);
      --bad: #E51414; --badBg: rgba(229,20,20,0.10);
      --riskWarnBorder: rgba(154,117,20,0.50); --riskWarnBg: rgba(154,117,20,0.05);
      --riskBadBorder: rgba(229,20,20,0.55); --riskBadBg: rgba(229,20,20,0.05);
      --red: #E51414; --redHov: #C51111;
      ${accentCss(accent.light)};
      --mono: ui-monospace, 'SF Mono', 'Cascadia Mono', Menlo, Consolas, monospace;
      --display: 'Telegraf', -apple-system, 'Segoe UI', sans-serif;
    }
    body.vscode-dark, body.vscode-high-contrast {
      --panel: #242529; --panel2: #2B2C31;
      --border: #3A3B42; --border2: #4C4D55;
      --text: #D8D8DC; --text2: #9EA0A6; --text3: #6E7076;
      --track: #3A3B42; --chip: #313136;
      --rowHover: rgba(255,255,255,0.035);
      --good: #3FAE87; --goodBg: rgba(63,174,135,0.14);
      --warn: #D3A83C; --warnBg: rgba(211,168,60,0.14);
      --bad: #E51414; --badBg: rgba(229,20,20,0.15);
      --riskWarnBorder: rgba(211,168,60,0.55); --riskWarnBg: rgba(211,168,60,0.07);
      --riskBadBorder: rgba(229,20,20,0.65); --riskBadBg: rgba(229,20,20,0.08);
      ${accentCss(accent.dark)};
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
      font-size: 13px; line-height: 1.45;
      color: var(--text); background: var(--bg);
      padding: 18px; max-width: 1240px; margin: 0 auto;
      display: flex; flex-direction: column; gap: 12px;
    }

    .num { font-variant-numeric: tabular-nums; }

    /* -------- brand banner -------- */
    .banner {
      background: ${BRAND_GRADIENT};
      border-radius: 8px; padding: 14px 16px 16px;
      display: flex; flex-direction: column; gap: 16px; color: #fff;
    }
    .banner-head { display: flex; align-items: center; gap: 12px; min-width: 0; }
    .lockup { display: flex; align-items: baseline; gap: 3px; min-width: 0; }
    .lockup .logo { height: 20px; width: auto; flex-shrink: 0; margin-bottom: -1px; }
    .lockup .app {
      font-family: var(--display); font-weight: 400; font-size: 15px;
      color: rgba(255,255,255,0.88); transform: translateY(-1px);
    }
    .lockup .divider {
      width: 1px; height: 16px; background: rgba(255,255,255,0.3);
      margin: 0 10px; align-self: center; flex-shrink: 0;
    }
    .lockup .task {
      align-self: center;
      font-family: var(--display); font-weight: 400; font-size: 17px;
      color: rgba(255,255,255,0.88);
      white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0;
    }
    .banner-actions { margin-left: auto; display: flex; gap: 8px; flex-shrink: 0; }
    .btn {
      height: 26px; padding: 0 12px; border-radius: 5px; font-size: 12px;
      display: inline-flex; align-items: center; cursor: pointer;
      font-family: inherit; transition: background 0.15s;
    }
    .btn-ghost {
      background: transparent; border: 1px solid rgba(255,255,255,0.42); color: #fff;
    }
    .btn-ghost:hover { background: rgba(255,255,255,0.12); }
    .btn-primary {
      background: #fff; border: none; color: #C1180F; font-weight: 700;
    }
    .btn-primary:hover { background: rgba(255,255,255,0.88); }

    .banner-hero { display: flex; justify-content: space-between; align-items: flex-end; gap: 28px; }
    .hero-left { min-width: 0; }
    .hero-label {
      font-size: 11px; font-weight: 700; color: rgba(255,255,255,0.62);
      letter-spacing: 0.02em;
    }
    .hero-line { display: flex; align-items: baseline; gap: 10px; }
    .hero-value {
      font-family: var(--display); font-weight: 800; font-size: 42px;
      letter-spacing: -0.01em; color: #fff; line-height: 1.15;
    }
    .hero-unit {
      font-family: var(--display); font-weight: 400; font-size: 15px;
      color: rgba(255,255,255,0.82);
    }
    .hero-caption { font-size: 12px; color: rgba(255,255,255,0.62); margin-top: 8px; }
    .hero-caption b { color: #fff; font-weight: 700; }

    .trend { width: 300px; flex-shrink: 0; }
    .trend-plot {
      display: flex; align-items: flex-end; gap: 5px; height: 70px;
      padding-top: 14px; position: relative;
    }
    .trend-bar {
      width: 15px; border-radius: 2px 2px 0 0;
      background: rgba(255,255,255,0.38); transition: background 0.15s;
      position: relative;
    }
    .trend-bar:hover { background: rgba(255,255,255,0.8); }
    .trend-bar.last { background: #fff; }
    .trend-lastlabel {
      position: absolute; top: -14px; left: 50%; transform: translateX(-50%);
      font-family: var(--mono); font-size: 10px; color: rgba(255,255,255,0.85);
      white-space: nowrap;
    }
    .trend-axis {
      border-top: 1px solid rgba(255,255,255,0.25);
      display: flex; justify-content: space-between; padding-top: 3px;
      font-family: var(--mono); font-size: 10px; color: rgba(255,255,255,0.55);
    }

    /* -------- KPI cards -------- */
    .kpis { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
    .panel {
      background: var(--panel); border: 1px solid var(--border);
      border-radius: 8px; padding: 14px 16px; transition: border-color 0.15s;
    }
    .kpi { padding: 13px 14px; display: flex; flex-direction: column; gap: 8px; min-height: 118px; }
    .kpi:hover { border-color: var(--border2); }
    .kpi.risk-medium { border-color: var(--riskWarnBorder); background: var(--riskWarnBg); }
    .kpi.risk-high { border-color: var(--riskBadBorder); background: var(--riskBadBg); }
    .kpi.risk-high:hover { border-color: var(--riskBadBorder); }
    .kpi.risk-medium:hover { border-color: var(--riskWarnBorder); }
    .kpi-label { font-size: 11px; font-weight: 700; color: var(--text2); text-transform: lowercase; }
    .kpi-value { font-family: var(--display); font-weight: 800; font-size: 25px; line-height: 1.2; }
    .kpi-sub { font-size: 11px; color: var(--text2); margin-top: auto; }
    .kpi-foot { font-size: 10.5px; color: var(--text3); }

    .meter { height: 6px; border-radius: 3px; background: var(--track); position: relative; }
    .meter-fill { height: 100%; border-radius: 3px; }
    .meter-tick {
      position: absolute; top: -2px; width: 1px; height: 10px; background: var(--border2);
    }

    .pill {
      display: inline-flex; align-items: center; gap: 6px;
      padding: 3px 10px; border-radius: 99px;
      font-size: 12.5px; font-weight: 650; width: fit-content;
    }
    .pill .dot { width: 7px; height: 7px; border-radius: 99px; background: currentColor; }
    .pill-low { background: var(--goodBg); color: var(--good); }
    .pill-medium { background: var(--warnBg); color: var(--warn); }
    .pill-high { background: var(--bad); color: #fff; }
    .risk-note { font-size: 11px; color: var(--text2); margin-top: auto; }
    .risk-note.warn { color: var(--warn); }
    .risk-note.bad { color: var(--bad); }

    /* -------- panels / titles -------- */
    .duo { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    .tables { display: grid; grid-template-columns: 1.12fr 1fr; gap: 12px; align-items: start; }
    .panel-title-row { display: flex; align-items: center; margin-bottom: 10px; }
    .panel-title {
      display: inline-flex; align-items: center; gap: 2px;
      font-family: var(--display); font-weight: 800; font-size: 12.5px;
      letter-spacing: 0.01em; text-transform: lowercase; color: var(--text);
    }
    .panel-note { margin-left: auto; font-size: 11px; font-weight: 500; color: var(--text3); }

    /* -------- donuts -------- */
    .donut-body { display: flex; align-items: center; gap: 18px; }
    .donut-holder { position: relative; width: 140px; height: 140px; flex-shrink: 0; }
    .donut-holder svg { display: block; }
    .donut-holder circle { transition: stroke-width 0.15s; }
    .donut-holder circle.seg:hover { stroke-width: 23; }
    .donut-center {
      position: absolute; inset: 0; display: flex; flex-direction: column;
      align-items: center; justify-content: center; pointer-events: none;
    }
    .donut-center .v { font-family: var(--display); font-weight: 800; font-size: 21px; }
    .donut-center .w { font-size: 10px; color: var(--text3); }
    .legend { flex: 1; display: flex; flex-direction: column; gap: 6px; min-width: 0; }
    .legend-row { display: flex; align-items: center; gap: 8px; font-size: 12px; }
    .legend-row .sw { width: 9px; height: 9px; border-radius: 2px; flex-shrink: 0; }
    .legend-row .val { margin-left: auto; font-family: var(--mono); font-size: 11px; color: var(--text2); }
    .panel-foot {
      border-top: 1px solid var(--border); margin-top: 10px; padding-top: 8px;
      font-size: 11px; color: var(--text3);
    }

    /* -------- token impact -------- */
    .impact-note { margin-left: auto; display: inline-flex; align-items: center; gap: 6px; }
    .impact-legend { display: inline-flex; align-items: center; gap: 5px; font-size: 11px; font-weight: 500; color: var(--text2); }
    .impact-legend .sw { width: 9px; height: 9px; border-radius: 2px; background-repeat: no-repeat; }
    .impact-rows { display: flex; flex-direction: column; gap: 7px; }
    .impact-row {
      display: grid; grid-template-columns: 172px 1fr 190px; gap: 12px;
      align-items: center; border-radius: 4px; transition: background 0.12s; cursor: pointer;
    }
    .impact-row:hover { background: var(--rowHover); }
    .impact-file {
      font-family: var(--mono); font-size: 11.5px;
      overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }
    .impact-lane { position: relative; height: 15px; }
    .impact-track, .impact-fill {
      position: absolute; left: 0; top: 0; bottom: 0; border-radius: 3px;
      background-repeat: no-repeat; background-size: var(--barBgSize);
    }
    .impact-track { background-image: var(--barTrack); }
    .impact-fill { background-image: var(--barFill); }
    .impact-nums {
      display: flex; justify-content: flex-end; align-items: baseline; gap: 8px;
      font-family: var(--mono); font-size: 11px; color: var(--text2);
    }
    .impact-delta { width: 38px; text-align: right; font-weight: 600; color: var(--d1); }

    /* -------- tables -------- */
    .tbl-panel { padding: 14px 16px 8px; }
    table { width: 100%; border-collapse: collapse; font-size: 12px; }
    th {
      text-align: left; font-size: 11px; font-weight: 600; color: var(--text3);
      text-transform: lowercase; padding: 5px; border-bottom: 1px solid var(--border);
    }
    th.r, td.r { text-align: right; }
    td { padding: 4px 5px; border-bottom: 1px solid var(--border); }
    tbody tr { transition: background 0.12s; cursor: pointer; }
    tbody tr:hover { background: var(--rowHover); }
    tbody tr.total { cursor: default; }
    td.file { font-family: var(--mono); font-size: 11.5px; }
    td.n { font-family: var(--mono); font-size: 11px; color: var(--text2); }
    td.ratio { font-family: var(--mono); font-size: 11px; font-weight: 600; color: var(--d1); }
    td.ratio.zero { color: var(--text3); font-weight: 400; }
    .strat { display: inline-flex; align-items: center; gap: 6px; font-size: 11.5px; color: var(--text2); }
    .strat .sw { width: 7px; height: 7px; border-radius: 2px; flex-shrink: 0; }
    tr.total td { border-top: 1px solid var(--border2); border-bottom: none; font-weight: 600; color: var(--text); }
    .score-cell { display: flex; align-items: center; gap: 7px; }
    .score-bar { width: 52px; height: 5px; border-radius: 3px; background: var(--track); overflow: hidden; flex-shrink: 0; }
    .score-fill { height: 100%; border-radius: 3px; background: var(--d1); display: block; }
    .score-val { font-family: var(--mono); font-size: 11px; }
    .status-pill {
      display: inline-block; padding: 1px 7px; border-radius: 99px;
      font-size: 10.5px; font-weight: 600;
    }
    .status-full { border: 1px solid var(--acc); color: var(--acc); }
    .status-packed { background: var(--d1soft); color: var(--d1); }
    .status-skipped { background: var(--chip); color: var(--text3); }
    tr.skipped td, tr.skipped .score-val { color: var(--text3); }
    tr.skipped .score-fill { background: var(--border2); }
    td.reasons { font-size: 11px; color: var(--text2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 170px; }
    tr.skipped td.reasons { color: var(--text3); }

    .empty-state { text-align: center; padding: 80px 20px; color: var(--text2); }
    .empty-state h2 { font-family: var(--display); font-weight: 800; font-size: 1.4em; margin-bottom: 14px; color: var(--text); }
    .empty-state p { margin: 0 auto 22px; max-width: 440px; line-height: 1.6; }
    .empty-state .btn { margin: 0 auto; }
    .empty-state .btn-primary { background: var(--red); color: #fff; }
    .empty-state .btn-primary:hover { background: var(--redHov); }

    #tooltip {
      position: fixed; display: none; pointer-events: none; z-index: 10;
      background: var(--panel2); color: var(--text);
      border: 1px solid var(--border2);
      border-radius: 4px; padding: 5px 9px; font-size: 11px;
      font-family: var(--mono); max-width: 320px; white-space: pre-line;
    }

    /* -------- responsive -------- */
    @media (max-width: 1000px) {
      .kpis { grid-template-columns: 1fr 1fr; }
      .duo, .tables { grid-template-columns: 1fr; }
      .banner-hero { flex-wrap: wrap; }
    }

    ::-webkit-scrollbar { width: 6px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
  </style>
</head>
<body>
  ${data ? renderBoard(data) : renderEmpty()}
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
      <h2>redcon analytics</h2>
      <p>No analysis data yet. Describe a task in the Redcon sidebar and send it. The dashboard will visualize savings, budget usage, packing strategies and file rankings.</p>
      <button class="btn btn-primary" data-action="run-pack">Analyze Context</button>
    </div>
  `;
}

/* ------------------------------------------------------------------ */
/* Board                                                               */
/* ------------------------------------------------------------------ */

function renderBoard(data: DashboardData): string {
  const s = data.sections ?? { kpis: true, donuts: true, impact: true, tables: true };
  return `
    ${renderBanner(data)}
    ${s.kpis ? renderKpis(data) : ''}
    ${s.donuts ? `<div class="duo">
      ${renderBudgetDonut(data)}
      ${renderStrategyDonut(data)}
    </div>` : ''}
    ${s.impact ? renderImpact(data) : ''}
    ${s.tables ? `<div class="tables">
      ${renderPackedTable(data)}
      ${renderRankingsTable(data)}
    </div>` : ''}
  `;
}

function savingsTotals(data: DashboardData): { totalSaved: number; runCount: number } {
  const totalSaved = data.history.reduce((s, e) => s + (e.tokensSaved ?? 0), 0);
  if (totalSaved > 0) return { totalSaved, runCount: data.history.length };
  return { totalSaved: data.run.budget.estimated_saved_tokens, runCount: 1 };
}

function renderBanner(data: DashboardData): string {
  const { run, costPerMillionTokens, primaryMetric } = data;
  const { totalSaved, runCount } = savingsTotals(data);
  const totalUsd = (totalSaved / 1_000_000) * costPerMillionTokens;
  const rate = `$${costPerMillionTokens.toFixed(2)} / M input`;

  let heroValue: string;
  let heroUnit: string;
  let caption: string;
  if (primaryMetric === 'dollars') {
    heroValue = fmtUsd(totalUsd);
    heroUnit = 'saved.';
    caption = `<b>${fmtK(totalSaved)}</b> tokens &middot; estimate at ${rate} &middot; across ${runCount} run${runCount === 1 ? '' : 's'}`;
  } else {
    heroValue = fmtK(totalSaved);
    heroUnit = 'tokens saved.';
    caption = `<b>${fmtUsd(totalUsd)}</b> saved &middot; at ${rate} &middot; across ${runCount} run${runCount === 1 ? '' : 's'}`;
  }

  // Chronological trend of up to the last 14 runs (history is newest first).
  const entries = data.history.slice(0, 14).reverse();
  const maxSaved = Math.max(...entries.map((e) => e.tokensSaved ?? 0), 1);
  const bars = entries
    .map((e, i) => {
      const v = e.tokensSaved ?? 0;
      const h = Math.max(3, Math.round((v / maxSaved) * 56));
      const last = i === entries.length - 1;
      const label = last ? `<span class="trend-lastlabel num">${fmtK(v)}</span>` : '';
      return `<div class="trend-bar${last ? ' last' : ''}" style="height:${h}px" data-tip="run ${i + 1} &middot; ${fmtK(v)} saved">${label}</div>`;
    })
    .join('');
  const trend = entries.length >= 2
    ? `
      <div class="trend">
        <div class="trend-plot">${bars}</div>
        <div class="trend-axis"><span>run 1</span><span>run ${entries.length}</span></div>
      </div>`
    : '';

  return `
    <div class="banner">
      <div class="banner-head">
        <div class="lockup">
          ${LOGO_SVG}
          <span class="app">analytics</span>
          <span class="divider"></span>
          <span class="task" title="${esc(run.task)}">task: ${esc(run.task)}</span>
        </div>
        <div class="banner-actions">
          <button class="btn btn-ghost" data-action="copy-context">Copy Context</button>
          <button class="btn btn-primary" data-action="run-pack">Re-analyze</button>
        </div>
      </div>
      <div class="banner-hero">
        <div class="hero-left">
          <div class="hero-label">cumulative savings</div>
          <div class="hero-line">
            <span class="hero-value num">${heroValue}</span>
            <span class="hero-unit">${heroUnit}</span>
          </div>
          <div class="hero-caption num">${caption}</div>
        </div>
        ${trend}
      </div>
    </div>
  `;
}

function policyFootnote(policy: BudgetPolicy, maxTokens: number): string {
  if (policy === 'strict-cap') return 'strict cap &middot; no auto-raise';
  if (policy === 'ask-first') return 'asks before raising budget';
  return `auto-raise on high risk &middot; cap ${fmtK(maxTokens * 2)}`;
}

function highRiskNote(policy: BudgetPolicy, maxTokens: number): string {
  if (policy === 'strict-cap') return 'critical file skipped &middot; raise budget manually';
  if (policy === 'ask-first') return 'critical file skipped &middot; approval pending';
  const next = Math.round((maxTokens * 4) / 3 / 100) * 100;
  return `quality floor hit &middot; next run ${fmtK(maxTokens)} &raquo; ${fmtK(next)} (auto)`;
}

function renderKpis(data: DashboardData): string {
  const { run, costPerMillionTokens, primaryMetric, budgetPolicy } = data;
  const used = run.budget.estimated_input_tokens;
  const max = run.max_tokens;
  const pct = max > 0 ? Math.round((used / max) * 100) : 0;
  const saved = run.budget.estimated_saved_tokens;
  const savedUsd = (saved / 1_000_000) * costPerMillionTokens;
  const totalOriginal = run.compressed_context.reduce((s, f) => s + f.original_tokens, 0);
  const compression = totalOriginal > 0 ? Math.round((saved / totalOriginal) * 100) : 0;
  // Budget bar: accent normally, status colors as warning states.
  const fillColor = pct >= 95 ? 'var(--bad)' : pct >= 80 ? 'var(--warn)' : 'var(--budgetArc)';

  let savedValue: string;
  let savedSub: string;
  if (primaryMetric === 'dollars') {
    savedValue = fmtUsd(savedUsd);
    savedSub = `${fmtK(saved)} tokens &middot; ${compression}% compression`;
  } else {
    savedValue = fmtK(saved);
    savedSub = `${compression}% compression &middot; ${fmtUsd(savedUsd)}`;
  }

  const risk = run.budget.quality_risk_estimate;
  const degraded = run.degraded_files?.length ?? 0;
  const triangle =
    '<svg width="10" height="9" viewBox="0 0 10 9" fill="currentColor" aria-hidden="true"><path d="M5 0 L10 9 L0 9 Z"/></svg>';
  let riskCard: string;
  if (risk === 'high') {
    riskCard = `
      <div class="panel kpi risk-high">
        <div class="kpi-label">quality risk</div>
        <span class="pill pill-high">${triangle}high</span>
        <div class="risk-note bad">${highRiskNote(budgetPolicy, max)}</div>
      </div>`;
  } else if (risk === 'medium') {
    const note = degraded > 0
      ? `${degraded} degraded file${degraded === 1 ? '' : 's'} &middot; review summaries`
      : 'borderline omissions &middot; review summaries';
    riskCard = `
      <div class="panel kpi risk-medium">
        <div class="kpi-label">quality risk</div>
        <span class="pill pill-medium">${triangle}medium</span>
        <div class="risk-note warn">${note}</div>
      </div>`;
  } else {
    riskCard = `
      <div class="panel kpi">
        <div class="kpi-label">quality risk</div>
        <span class="pill pill-low"><span class="dot"></span>low</span>
        <div class="risk-note">no critical files skipped</div>
      </div>`;
  }

  return `
    <div class="kpis">
      <div class="panel kpi">
        <div class="kpi-label">budget used</div>
        <div class="kpi-value num">${pct}%</div>
        <div class="meter">
          <div class="meter-fill" style="width:${Math.min(100, pct)}%;background:${fillColor}"></div>
          <span class="meter-tick" style="left:80%"></span>
          <span class="meter-tick" style="left:95%"></span>
        </div>
        <div class="kpi-sub num">${fmtK(used)} of ${fmtK(max)} tokens</div>
        <div class="kpi-foot">${policyFootnote(budgetPolicy, max)}</div>
      </div>
      <div class="panel kpi">
        <div class="kpi-label">saved this run</div>
        <div class="kpi-value num">${savedValue}</div>
        <div class="kpi-sub num">${savedSub}</div>
      </div>
      <div class="panel kpi">
        <div class="kpi-label">files packed</div>
        <div class="kpi-value num">${run.files_included.length}</div>
        <div class="kpi-sub num">${run.files_skipped.length} skipped &middot; ${run.ranked_files.length} scanned</div>
      </div>
      ${riskCard}
    </div>
  `;
}

function panelTitle(title: string, note = ''): string {
  return `
    <div class="panel-title-row">
      <span class="panel-title">${CHEVRON_SVG}<span>${title}</span></span>
      ${note ? `<span class="panel-note">${note}</span>` : ''}
    </div>`;
}

/* Donut geometry per spec: 140x140, r=52, stroke 20. */
const DONUT_CIRC = 2 * Math.PI * 52;

function donutSegments(
  segments: { pct: number; color: string; tip: string }[],
): string {
  let angle = -90;
  return segments
    .map((seg) => {
      const len = (seg.pct / 100) * DONUT_CIRC;
      const html = `<circle class="seg" cx="70" cy="70" r="52" fill="none"
        stroke="${seg.color}" stroke-width="20"
        stroke-dasharray="${len.toFixed(2)} ${(DONUT_CIRC - len).toFixed(2)}"
        transform="rotate(${angle.toFixed(2)} 70 70)"
        data-tip="${esc(seg.tip)}"></circle>`;
      angle += (seg.pct / 100) * 360;
      return html;
    })
    .join('');
}

function renderBudgetDonut(data: DashboardData): string {
  const { run } = data;
  const used = run.budget.estimated_input_tokens;
  const max = run.max_tokens;
  const pct = max > 0 ? Math.min(100, Math.round((used / max) * 100)) : 0;
  const available = Math.max(0, max - used);

  return `
    <div class="panel">
      ${panelTitle('budget allocation')}
      <div class="donut-body">
        <div class="donut-holder">
          <svg width="140" height="140" viewBox="0 0 140 140">
            <circle cx="70" cy="70" r="52" fill="none" stroke="var(--track)" stroke-width="20"></circle>
            ${donutSegments([
              { pct, color: 'var(--budgetArc)', tip: `used · ${fmtK(used)} · ${pct}%` },
            ])}
          </svg>
          <div class="donut-center"><span class="v num">${pct}%</span><span class="w">used</span></div>
        </div>
        <div class="legend">
          <div class="legend-row"><span class="sw" style="background:var(--budgetArc)"></span>used<span class="val">${fmtK(used)} &middot; ${pct}%</span></div>
          <div class="legend-row"><span class="sw" style="background:var(--track)"></span>available<span class="val">${fmtK(available)} &middot; ${100 - pct}%</span></div>
        </div>
      </div>
      <div class="panel-foot num">hard cap ${fmtK(max)} tokens per run</div>
    </div>
  `;
}

function renderStrategyDonut(data: DashboardData): string {
  const { run } = data;
  const tokensByStrategy = new Map<string, number>();
  for (const f of run.compressed_context) {
    tokensByStrategy.set(f.strategy, (tokensByStrategy.get(f.strategy) ?? 0) + f.compressed_tokens);
  }
  // Merge the two spellings of symbol extraction onto one slot.
  const symbolTotal = (tokensByStrategy.get('symbol') ?? 0) + (tokensByStrategy.get('symbol_extraction') ?? 0);
  if (symbolTotal > 0) {
    tokensByStrategy.delete('symbol_extraction');
    tokensByStrategy.set('symbol', symbolTotal);
  }
  const total = [...tokensByStrategy.values()].reduce((a, b) => a + b, 0);

  // Canonical order keeps each strategy on its fixed ramp slot.
  const present: { label: string; tokens: number; slot: number }[] = [];
  for (const [key, label] of STRATEGY_ORDER) {
    if (key === 'symbol_extraction') continue;
    const tokens = tokensByStrategy.get(key);
    if (tokens === undefined || tokens <= 0) continue;
    present.push({ label, tokens, slot: STRATEGY_SLOT[key] });
  }

  const segs = present.map((p) => {
    const pct = total > 0 ? (p.tokens / total) * 100 : 0;
    return {
      pct,
      color: `var(--s${p.slot})`,
      tip: `${p.label} · ${fmtK(p.tokens)} · ${Math.round(pct)}%`,
    };
  });

  const legend = present
    .map((p) => {
      const pct = total > 0 ? Math.round((p.tokens / total) * 100) : 0;
      return `<div class="legend-row"><span class="sw" style="background:var(--s${p.slot})"></span>${p.label}<span class="val">${fmtK(p.tokens)} &middot; ${pct}%</span></div>`;
    })
    .join('');

  return `
    <div class="panel">
      ${panelTitle('strategy distribution', 'share of packed tokens')}
      <div class="donut-body">
        <div class="donut-holder">
          <svg width="140" height="140" viewBox="0 0 140 140">
            ${donutSegments(segs)}
          </svg>
          <div class="donut-center"><span class="v num">${fmtK(total)}</span><span class="w">packed</span></div>
        </div>
        <div class="legend">${legend}</div>
      </div>
    </div>
  `;
}

function renderImpact(data: DashboardData): string {
  const { run } = data;
  const files = [...run.compressed_context]
    .sort((a, b) => b.original_tokens - a.original_tokens)
    .slice(0, 8);
  const maxOrig = Math.max(...files.map((f) => f.original_tokens), 1);

  const rows = files
    .map((f) => {
      const trackW = (f.original_tokens / maxOrig) * 100;
      const fillW = (f.compressed_tokens / maxOrig) * 100;
      const delta = f.original_tokens > 0
        ? Math.round(((f.original_tokens - f.compressed_tokens) / f.original_tokens) * 100)
        : 0;
      const fullPath = run.repo ? `${run.repo}/${f.path}` : f.path;
      return `
        <div class="impact-row" data-action="open-file" data-path="${esc(fullPath)}">
          <span class="impact-file" title="${esc(f.path)}">${esc(f.path)}</span>
          <div class="impact-lane">
            <div class="impact-track" style="width:${trackW.toFixed(1)}%"></div>
            <div class="impact-fill" style="width:${fillW.toFixed(1)}%"></div>
          </div>
          <div class="impact-nums">
            <span>${fmtInt(f.original_tokens)} &raquo; ${fmtInt(f.compressed_tokens)}</span>
            <span class="impact-delta">${delta > 0 ? `-${delta}%` : '0%'}</span>
          </div>
        </div>`;
    })
    .join('');

  return `
    <div class="panel">
      <div class="panel-title-row">
        <span class="panel-title">${CHEVRON_SVG}<span>token impact by file</span></span>
        <span class="impact-note">
          <span class="impact-legend"><span class="sw" style="background-image:var(--barTrack)"></span>original</span>
          <span class="impact-legend"><span class="sw" style="background-image:var(--barFill)"></span>packed</span>
          <span class="panel-note" style="margin-left:4px">&middot; top ${files.length} of ${run.compressed_context.length} &middot; shared scale</span>
        </span>
      </div>
      <div class="impact-rows">${rows}</div>
    </div>
  `;
}

function strategyLabel(s: string): string {
  return s.replace(/_/g, ' ');
}

function renderPackedTable(data: DashboardData): string {
  const { run } = data;
  const files = run.compressed_context;
  let totalOrig = 0;
  let totalPacked = 0;

  const rows = files
    .map((f) => {
      const savedT = f.original_tokens - f.compressed_tokens;
      const ratio = f.original_tokens > 0 ? Math.round((savedT / f.original_tokens) * 100) : 0;
      totalOrig += f.original_tokens;
      totalPacked += f.compressed_tokens;
      const slot = STRATEGY_SLOT[f.strategy] ?? 6;
      const fullPath = run.repo ? `${run.repo}/${f.path}` : f.path;
      return `
        <tr data-action="open-file" data-path="${esc(fullPath)}">
          <td class="file" title="${esc(f.path)}">${esc(f.path)}</td>
          <td><span class="strat"><span class="sw" style="background:var(--s${slot})"></span>${strategyLabel(f.strategy)}</span></td>
          <td class="n r">${fmtInt(f.original_tokens)}</td>
          <td class="n r">${fmtInt(f.compressed_tokens)}</td>
          <td class="n r">${fmtInt(savedT)}</td>
          <td class="ratio r${ratio === 0 ? ' zero' : ''}">${ratio > 0 ? `-${ratio}%` : '0%'}</td>
        </tr>`;
    })
    .join('');

  const totalSaved = totalOrig - totalPacked;
  const totalRatio = totalOrig > 0 ? Math.round((totalSaved / totalOrig) * 100) : 0;

  return `
    <div class="panel tbl-panel">
      ${panelTitle('packed context', `${files.length} files`)}
      <table>
        <thead><tr><th>file</th><th>strategy</th><th class="r">original</th><th class="r">packed</th><th class="r">saved</th><th class="r">ratio</th></tr></thead>
        <tbody>
          ${rows}
          <tr class="total"><td>total</td><td></td><td class="n r">${fmtInt(totalOrig)}</td><td class="n r">${fmtInt(totalPacked)}</td><td class="n r">${fmtInt(totalSaved)}</td><td class="ratio r">-${totalRatio}%</td></tr>
        </tbody>
      </table>
    </div>
  `;
}

function renderRankingsTable(data: DashboardData): string {
  const { run } = data;
  const top = run.ranked_files.slice(0, 8);
  const maxScore = Math.max(...run.ranked_files.map((r) => r.score), 0.001);
  const included = new Set(run.files_included);
  const fullStrategy = new Set(
    run.compressed_context.filter((f) => f.strategy === 'full').map((f) => f.path),
  );

  const rows = top
    .map((f, i) => {
      const isIncluded = included.has(f.path);
      // Scores are unbounded floats; display them normalized to the top
      // ranked file (top = 100) so the column reads like the design.
      const score = Math.round((f.score / maxScore) * 100);
      const status = !isIncluded
        ? '<span class="status-pill status-skipped">skipped</span>'
        : fullStrategy.has(f.path)
          ? '<span class="status-pill status-full">full</span>'
          : '<span class="status-pill status-packed">packed</span>';
      const reasons = f.reasons.slice(0, 2).join(' · ');
      const fullPath = run.repo ? `${run.repo}/${f.path}` : f.path;
      return `
        <tr class="${isIncluded ? '' : 'skipped'}" data-action="open-file" data-path="${esc(fullPath)}">
          <td class="n">${i + 1}</td>
          <td class="file" title="${esc(f.path)}">${esc(f.path)}</td>
          <td><span class="score-cell"><span class="score-bar"><span class="score-fill" style="width:${score}%"></span></span><span class="score-val">${score}</span></span></td>
          <td class="n r">${fmtInt(f.line_count)}</td>
          <td>${status}</td>
          <td class="reasons" title="${esc(f.reasons.join(' · '))}">${esc(reasons)}</td>
        </tr>`;
    })
    .join('');

  return `
    <div class="panel tbl-panel">
      ${panelTitle('file rankings', `top ${top.length} of ${run.ranked_files.length} scanned`)}
      <table>
        <thead><tr><th>#</th><th>file</th><th>score</th><th class="r">lines</th><th>status</th><th>reasons</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
}
