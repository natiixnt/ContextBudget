/**
 * Status bar - shows budget gauge and risk in the bottom bar.
 */

import * as vscode from 'vscode';
import { state } from './state';
import { formatTokens } from './webview/theme';

export class StatusBar {
  private readonly budgetItem: vscode.StatusBarItem;
  private readonly riskItem: vscode.StatusBarItem;

  constructor() {
    this.budgetItem = vscode.window.createStatusBarItem(
      vscode.StatusBarAlignment.Left,
      50,
    );
    this.budgetItem.command = 'redcon.openDashboard';
    this.budgetItem.name = 'Redcon Budget';

    this.riskItem = vscode.window.createStatusBarItem(
      vscode.StatusBarAlignment.Left,
      49,
    );
    this.riskItem.command = 'redcon.pack';
    this.riskItem.name = 'Redcon Risk';

    state.onDidChange((key) => {
      if (key === 'lastRun' || key === 'isRunning') {
        this.update();
      }
    });

    this.update();
  }

  update(): void {
    const config = vscode.workspace.getConfiguration('redcon');
    if (!config.get<boolean>('showStatusBar', true)) {
      this.budgetItem.hide();
      this.riskItem.hide();
      return;
    }

    if (state.state.isRunning) {
      this.budgetItem.text = '$(loading~spin) Redcon...';
      this.budgetItem.tooltip = 'Running redcon command...';
      this.budgetItem.show();
      this.riskItem.hide();
      return;
    }

    const run = state.state.lastRun;
    if (!run) {
      this.budgetItem.text = '$(package) Redcon';
      this.budgetItem.tooltip = 'No run data. Click to open dashboard.';
      this.budgetItem.show();
      this.riskItem.hide();
      return;
    }

    const used = run.budget.estimated_input_tokens;
    const max = run.max_tokens;
    const pct = max > 0 ? Math.round((used / max) * 100) : 0;

    // Budget item
    const icon = pct > 90 ? '$(warning)' : pct > 70 ? '$(info)' : '$(package)';
    this.budgetItem.text = `${icon} ${formatTokens(used)}/${formatTokens(max)}`;
    this.budgetItem.tooltip = new vscode.MarkdownString(
      [
        `**Redcon Budget**`,
        '',
        `${this.progressBar(pct)} ${pct}%`,
        '',
        `Used: ${used.toLocaleString()} tokens`,
        `Budget: ${max.toLocaleString()} tokens`,
        `Saved: ${run.budget.estimated_saved_tokens.toLocaleString()} tokens`,
        `Files: ${run.files_included.length} included, ${run.files_skipped.length} skipped`,
        '',
        `Task: ${run.task}`,
      ].join('\n'),
    );
    this.budgetItem.backgroundColor =
      pct > 90
        ? new vscode.ThemeColor('statusBarItem.warningBackground')
        : undefined;
    this.budgetItem.show();

    // Risk item
    const risk = run.budget.quality_risk_estimate;
    const riskIcons: Record<string, string> = {
      low: '$(shield)',
      medium: '$(alert)',
      high: '$(error)',
    };
    this.riskItem.text = `${riskIcons[risk] ?? '$(question)'} ${risk}`;
    this.riskItem.tooltip = `Quality risk: ${risk}`;
    this.riskItem.backgroundColor =
      risk === 'high'
        ? new vscode.ThemeColor('statusBarItem.errorBackground')
        : risk === 'medium'
          ? new vscode.ThemeColor('statusBarItem.warningBackground')
          : undefined;
    this.riskItem.show();
  }

  private progressBar(pct: number): string {
    const filled = Math.max(0, Math.min(20, Math.round(pct / 5)));
    const empty = 20 - filled;
    return '\u2588'.repeat(filled) + '\u2591'.repeat(empty);
  }

  dispose(): void {
    this.budgetItem.dispose();
    this.riskItem.dispose();
  }
}
