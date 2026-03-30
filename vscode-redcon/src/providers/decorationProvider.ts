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

  private scoreMap = new Map<string, { score: number; included: boolean; strategy?: string; reason?: string }>();
  private showDecorations: boolean;

  constructor() {
    const config = vscode.workspace.getConfiguration('redcon');
    this.showDecorations = config.get<boolean>('showFileDecorations', true);

    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration('redcon.showFileDecorations')) {
        this.showDecorations = vscode.workspace
          .getConfiguration('redcon')
          .get<boolean>('showFileDecorations', true);
        this._onDidChange.fire(undefined);
      }
    });

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
    if (!this.showDecorations) {
      return undefined;
    }

    const run = state.state.lastRun;
    if (!run) {
      return undefined;
    }

    // Match by relative path (cross-platform)
    const repoRoot = run.repo ?? '';
    const relPath = repoRoot
      ? path.relative(repoRoot, uri.fsPath).split(path.sep).join('/')
      : uri.fsPath;

    const entry = this.scoreMap.get(relPath);
    if (!entry) {
      return undefined;
    }

    if (entry.included) {
      const badge = entry.score >= 5 ? '\u2605' : '\u2713';
      const reasonPart = entry.reason ? ` - ${entry.reason}` : '';
      return {
        badge,
        tooltip: `Redcon: score ${entry.score.toFixed(1)}${entry.strategy ? ` (${entry.strategy})` : ''}${reasonPart}`,
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
      const firstReason = rf.reasons?.[0];
      this.scoreMap.set(rf.path, {
        score: rf.score,
        included: included.has(rf.path),
        strategy: strategyMap.get(rf.path),
        reason: firstReason,
      });
    }
  }

  dispose(): void {
    this._onDidChange.dispose();
  }
}
