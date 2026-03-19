/**
 * File Decoration Provider - adds score badges to files in the explorer.
 */

import * as vscode from 'vscode';
import * as path from 'path';
import { state } from '../state';

export class RedconDecorationProvider
  implements vscode.FileDecorationProvider
{
  private readonly _onDidChange =
    new vscode.EventEmitter<vscode.Uri | vscode.Uri[] | undefined>();
  readonly onDidChangeFileDecorations = this._onDidChange.event;

  private scoreMap = new Map<string, { score: number; included: boolean; strategy?: string }>();

  constructor() {
    state.onDidChange((key) => {
      if (key === 'lastRun') {
        this.rebuildMap();
        this._onDidChange.fire(undefined);
      }
    });
  }

  provideFileDecoration(
    uri: vscode.Uri,
  ): vscode.FileDecoration | undefined {
    const config = vscode.workspace.getConfiguration('redcon');
    if (!config.get<boolean>('showFileDecorations', true)) {
      return undefined;
    }

    const run = state.state.lastRun;
    if (!run) {
      return undefined;
    }

    // Match by relative path
    const repoRoot = run.repo ?? '';
    let relPath = uri.fsPath;
    if (repoRoot && relPath.startsWith(repoRoot)) {
      relPath = relPath.slice(repoRoot.length).replace(/^[/\\]/, '');
    }

    const entry = this.scoreMap.get(relPath);
    if (!entry) {
      return undefined;
    }

    if (entry.included) {
      const badge = entry.score >= 5 ? '\u2605' : '\u2713';
      return {
        badge,
        tooltip: `Redcon: score ${entry.score.toFixed(1)}${entry.strategy ? ` (${entry.strategy})` : ''}`,
        color: new vscode.ThemeColor(
          entry.score >= 5
            ? 'redcon.scoreHigh'
            : entry.score >= 2
              ? 'redcon.scoreMedium'
              : 'redcon.scoreLow',
        ),
      };
    } else {
      return {
        badge: '\u2212',
        tooltip: `Redcon: skipped (score ${entry.score.toFixed(1)})`,
        color: new vscode.ThemeColor('redcon.scoreSkipped'),
      };
    }
  }

  private rebuildMap(): void {
    this.scoreMap.clear();
    const run = state.state.lastRun;
    if (!run) {
      return;
    }

    const included = new Set(run.files_included);
    const strategyMap = new Map<string, string>();

    for (const cf of run.compressed_context ?? []) {
      strategyMap.set(cf.path, cf.strategy);
    }

    for (const rf of run.ranked_files ?? []) {
      this.scoreMap.set(rf.path, {
        score: rf.score,
        included: included.has(rf.path),
        strategy: strategyMap.get(rf.path),
      });
    }
  }

  dispose(): void {
    this._onDidChange.dispose();
  }
}
