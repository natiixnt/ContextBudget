/**
 * Dashboard Webview - panel wiring around the pure HTML renderer.
 */

import * as vscode from 'vscode';
import { state } from '../state';
import { renderDashboardHtml } from './dashboardHtml';

export class DashboardPanel {
  private static instance: DashboardPanel | undefined;
  private panel: vscode.WebviewPanel;
  private disposables: vscode.Disposable[] = [];

  private constructor(extensionUri: vscode.Uri) {
    this.panel = vscode.window.createWebviewPanel(
      'redconDashboard',
      'Redcon Dashboard',
      vscode.ViewColumn.Active,
      {
        enableScripts: true,
        retainContextWhenHidden: true,
        localResourceRoots: [extensionUri],
      },
    );

    this.panel.iconPath = new vscode.ThemeIcon('graph');
    this.update();

    const stateListener = state.onDidChange(() => this.update());
    this.disposables.push(stateListener);
    this.disposables.push(
      vscode.workspace.onDidChangeConfiguration((e) => {
        if (e.affectsConfiguration('redcon')) this.update();
      }),
    );

    this.panel.webview.onDidReceiveMessage(
      (msg) => {
        switch (msg.command) {
          case 'openFile':
            vscode.commands.executeCommand('vscode.open', vscode.Uri.file(msg.path));
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
      DashboardPanel.instance.panel.reveal(vscode.ViewColumn.Active);
      return;
    }
    DashboardPanel.instance = new DashboardPanel(extensionUri);
  }

  private update(): void {
    const run = state.state.lastRun;
    const cfg = vscode.workspace.getConfiguration('redcon');

    this.panel.webview.html = renderDashboardHtml(
      run
        ? {
            run,
            history: state.state.runHistory,
            costPerMillionTokens: cfg.get<number>('costPerMillionTokens', 3.0),
            primaryMetric: cfg.get('display.primaryMetric', 'tokens'),
            budgetPolicy: cfg.get('budget.policy', 'auto-raise'),
            dataAccent: cfg.get('display.dataAccent', 'red'),
            sections: {
              kpis: cfg.get<boolean>('dashboard.showKpis', true),
              donuts: cfg.get<boolean>('dashboard.showDonuts', true),
              impact: cfg.get<boolean>('dashboard.showImpact', true),
              tables: cfg.get<boolean>('dashboard.showTables', true),
            },
          }
        : null,
      getNonce(),
    );
  }
}

function getNonce(): string {
  let text = '';
  const possible = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
  for (let i = 0; i < 32; i++) {
    text += possible.charAt(Math.floor(Math.random() * possible.length));
  }
  return text;
}
