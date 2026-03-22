/**
 * Context Sync - generates short, imperative AI agent context from Redcon results.
 * Writes to .claude/redcon-context.md, .cursorrules, .github/copilot-instructions.md
 */

import * as fs from 'fs';
import * as path from 'path';
import type { RunReport } from './types';

const MARKER = '<!-- redcon:auto-generated -->';

export type SyncTarget = 'claude' | 'cursor' | 'copilot';

export interface SyncResult {
  filesWritten: string[];
  filesSkipped: string[];
  errors: string[];
}

function groupByDir(paths: string[]): Record<string, string[]> {
  const groups: Record<string, string[]> = {};
  for (const p of paths) {
    const parts = p.split('/');
    const dir = parts.length > 1 ? parts.slice(0, -1).join('/') : '.';
    const name = parts.pop() ?? p;
    if (!groups[dir]) groups[dir] = [];
    groups[dir].push(name);
  }
  return groups;
}

export function generateContextMarkdown(run: RunReport, maxFiles: number = 30): string {
  const lines: string[] = [];
  const pct = run.max_tokens > 0
    ? Math.round((run.budget.estimated_input_tokens / run.max_tokens) * 100)
    : 0;
  const totalOrig = run.compressed_context.reduce((s, f) => s + f.original_tokens, 0);
  const totalComp = run.compressed_context.reduce((s, f) => s + f.compressed_tokens, 0);
  const savedPct = totalOrig > 0 ? Math.round(((totalOrig - totalComp) / totalOrig) * 100) : 0;

  lines.push(MARKER);
  lines.push('# Redcon Context');
  lines.push('');
  lines.push(`> Auto-generated. Do not edit. Task: "${run.task}"`);
  lines.push('');

  // Rule 1: Budget status
  lines.push(`**Budget:** ${pct}% used (${fmtTokens(run.budget.estimated_input_tokens)}/${fmtTokens(run.max_tokens)}) | ${savedPct}% saved by compression | Risk: ${run.budget.quality_risk_estimate}`);
  lines.push('');

  // Rule 2: Module map - short, grouped by directory
  const dirs = groupByDir(run.files_included);
  const dirEntries = Object.entries(dirs).sort((a, b) => b[1].length - a[1].length);

  lines.push('## Where to look');
  lines.push('');
  for (const [dir, files] of dirEntries.slice(0, 10)) {
    lines.push(`- \`${dir}/\` - ${files.slice(0, 4).join(', ')}${files.length > 4 ? ` (+${files.length - 4})` : ''}`);
  }
  lines.push('');

  // Rule 3: Critical files - top ranked with one-line purpose
  const top = run.ranked_files.slice(0, Math.min(maxFiles, 15));
  lines.push('## Critical files (by relevance)');
  lines.push('');
  for (const f of top) {
    const reason = f.reasons[0] ?? '';
    const short = reason.length > 60 ? reason.slice(0, 57) + '...' : reason;
    lines.push(`- \`${f.path}\` (${f.score.toFixed(1)}) - ${short}`);
  }
  lines.push('');

  // Rule 4: Key exports - what's available to import/use
  const exports: string[] = [];
  for (const f of run.compressed_context) {
    for (const s of f.symbols) {
      if (s.exported && exports.length < 20) {
        const fname = f.path.split('/').pop() ?? f.path;
        exports.push(`\`${s.name}\` (${s.kind}) from \`${fname}\``);
      }
    }
  }
  if (exports.length > 0) {
    lines.push('## Key exports');
    lines.push('');
    for (const e of exports) {
      lines.push(`- ${e}`);
    }
    lines.push('');
  }

  // Rule 5: Imperative instructions
  lines.push('## Rules');
  lines.push('');
  lines.push('- ALWAYS read the actual file before modifying it');
  lines.push('- Check the critical files list above before searching broadly');
  lines.push('- Files not listed here were ranked low-relevance for this task');
  if (run.files_skipped.length > 0) {
    lines.push(`- ${run.files_skipped.length} files were excluded (low relevance or over budget)`);
  }
  lines.push('');

  return lines.join('\n');
}

function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function wrapForAgent(markdown: string): string {
  return markdown;
}

export async function syncContextFiles(
  run: RunReport,
  workspaceRoot: string,
  targets: SyncTarget[],
  maxFiles: number = 30,
): Promise<SyncResult> {
  const result: SyncResult = { filesWritten: [], filesSkipped: [], errors: [] };
  const markdown = generateContextMarkdown(run, maxFiles);

  for (const target of targets) {
    try {
      let filePath: string;

      switch (target) {
        case 'claude': {
          const dir = path.join(workspaceRoot, '.claude');
          fs.mkdirSync(dir, { recursive: true });
          filePath = path.join(dir, 'redcon-context.md');
          break;
        }
        case 'cursor': {
          filePath = path.join(workspaceRoot, '.cursorrules');
          // If existing file without our marker, don't overwrite
          if (fs.existsSync(filePath)) {
            const existing = fs.readFileSync(filePath, 'utf-8');
            if (!existing.includes(MARKER)) {
              result.filesSkipped.push(filePath);
              continue;
            }
          }
          break;
        }
        case 'copilot': {
          const dir = path.join(workspaceRoot, '.github');
          fs.mkdirSync(dir, { recursive: true });
          filePath = path.join(dir, 'copilot-instructions.md');
          break;
        }
        default:
          continue;
      }

      fs.writeFileSync(filePath, wrapForAgent(markdown), 'utf-8');
      result.filesWritten.push(filePath);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      result.errors.push(`${target}: ${msg}`);
    }
  }

  return result;
}
