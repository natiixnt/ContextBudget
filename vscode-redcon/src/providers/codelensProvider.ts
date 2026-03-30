/**
 * CodeLens Provider - shows compression strategy and token count at the top of files.
 */

import * as vscode from 'vscode';
import * as path from 'path';
import { state } from '../state';

function getRelativePath(fsPath: string, repoRoot: string): string {
  if (!repoRoot) return fsPath;
  return path.relative(repoRoot, fsPath).split(path.sep).join('/');
}

export class RedconCodeLensProvider implements vscode.CodeLensProvider {
  private readonly _onDidChange = new vscode.EventEmitter<void>();
  readonly onDidChangeCodeLenses = this._onDidChange.event;

  constructor() {
    state.onDidChange((key) => {
      if (key === 'lastRun') {
        this._onDidChange.fire();
      }
    });
  }

  provideCodeLenses(document: vscode.TextDocument): vscode.CodeLens[] {
    const config = vscode.workspace.getConfiguration('redcon');
    if (!config.get<boolean>('showCodeLens', true)) {
      return [];
    }

    const run = state.state.lastRun;
    if (!run) {
      return [];
    }

    const repoRoot = run.repo ?? '';
    const relPath = getRelativePath(document.uri.fsPath, repoRoot);

    // Find in compressed context
    const compressed = run.compressed_context?.find(
      (f) => f.path === relPath,
    );
    if (!compressed) {
      // Check if it's in ranked but skipped
      const ranked = run.ranked_files?.find((f) => f.path === relPath);
      if (ranked) {
        return [
          new vscode.CodeLens(new vscode.Range(0, 0, 0, 0), {
            title: `$(close) Redcon: skipped (score ${ranked.score.toFixed(1)})`,
            command: 'redcon.plan',
            tooltip: 'File was ranked but not included in context',
          }),
        ];
      }
      return [];
    }

    const ratio =
      compressed.original_tokens > 0
        ? Math.round(
            ((compressed.original_tokens - compressed.compressed_tokens) /
              compressed.original_tokens) *
              100,
          )
        : 0;

    const strategyIcons: Record<string, string> = {
      full: '$(file-code)',
      snippet: '$(selection)',
      symbol_extraction: '$(symbol-method)',
      summary: '$(note)',
      slicing: '$(split-horizontal)',
      cache_reuse: '$(database)',
    };

    const icon = strategyIcons[compressed.strategy] ?? '$(file)';

    const lenses: vscode.CodeLens[] = [
      new vscode.CodeLens(new vscode.Range(0, 0, 0, 0), {
        title: `${icon} Redcon: ${compressed.strategy} | ${compressed.compressed_tokens}/${compressed.original_tokens} tok (-${ratio}%)`,
        command: 'redcon.openDashboard',
        tooltip: `Strategy: ${compressed.strategy}\nOriginal: ${compressed.original_tokens} tokens\nCompressed: ${compressed.compressed_tokens} tokens\nSaved: ${ratio}%`,
      }),
    ];

    // Show selected ranges as additional lenses
    if (compressed.selected_ranges?.length && compressed.strategy === 'snippet') {
      for (const range of (compressed.selected_ranges ?? []).slice(0, 3)) {
        const startLine = Math.max(0, (range.start as number) - 1);
        lenses.push(
          new vscode.CodeLens(new vscode.Range(startLine, 0, startLine, 0), {
            title: `$(selection) included: L${range.start}-${range.end} (${range.type})`,
            command: '',
            tooltip: `This range is included in the packed context`,
          }),
        );
      }
      if (compressed.selected_ranges.length > 3) {
        lenses.push(
          new vscode.CodeLens(new vscode.Range(0, 0, 0, 0), {
            title: `+${compressed.selected_ranges.length - 3} more ranges`,
            command: 'redcon.openDashboard',
            tooltip: `${compressed.selected_ranges.length} total ranges included`,
          }),
        );
      }
    }

    return lenses;
  }

  refresh(): void {
    this._onDidChange.fire();
  }

  dispose(): void {
    this._onDidChange.dispose();
  }
}
