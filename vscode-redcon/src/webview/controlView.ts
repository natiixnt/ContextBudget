/**
 * Sidebar control panel provider.
 *
 * State-driven: re-renders on run/history changes and on redcon.*
 * configuration changes. Commands report progress through setBusy /
 * notify instead of injecting chat messages.
 */

import * as vscode from 'vscode';
import { state } from '../state';
import {
  renderControlViewHtml,
  type ControlNotice,
  type ControlSections,
} from './controlViewHtml';

export class ControlViewProvider implements vscode.WebviewViewProvider {
  static readonly viewType = 'redcon.control';

  private view?: vscode.WebviewView;
  private busyLabel: string | null = null;
  private notice: ControlNotice | null = null;
  private setupState: { cliInstalled: boolean; mcpConfigured: boolean } | null = null;
  private disposables: vscode.Disposable[] = [];

  constructor(private readonly extensionUri: vscode.Uri) {}

  resolveWebviewView(webviewView: vscode.WebviewView): void {
    this.view = webviewView;
    webviewView.webview.options = {
      enableScripts: true,
      localResourceRoots: [this.extensionUri],
    };

    webviewView.webview.onDidReceiveMessage((msg) => this.handleMessage(msg));

    this.disposables.push(
      state.onDidChange(() => this.render()),
      vscode.workspace.onDidChangeConfiguration((e) => {
        if (e.affectsConfiguration('redcon.views')) this.render();
      }),
    );
    webviewView.onDidDispose(() => {
      this.disposables.forEach((d) => d.dispose());
      this.disposables = [];
      this.view = undefined;
    });

    this.render();
  }

  /* -- Public API for commands.ts / extension.ts -- */

  setBusy(label: string): void {
    this.busyLabel = label;
    this.notice = null;
    this.render();
  }

  clearBusy(): void {
    this.busyLabel = null;
    this.render();
  }

  notify(kind: ControlNotice['kind'], text: string): void {
    this.busyLabel = null;
    this.notice = { kind, text };
    this.render();
  }

  setSetupState(setup: { cliInstalled: boolean; mcpConfigured: boolean }): void {
    this.setupState = setup;
    this.render();
  }

  refresh(): void {
    this.render();
  }

  /* -- Internal -- */

  private sections(): ControlSections {
    const cfg = vscode.workspace.getConfiguration('redcon.views');
    return {
      miniDashboard: cfg.get<boolean>('showMiniDashboard', true),
      recentRuns: cfg.get<boolean>('showRecentRuns', true),
      setup: cfg.get<boolean>('showSetup', true),
    };
  }

  private render(): void {
    if (!this.view) return;
    const run = state.state.lastRun;

    this.view.webview.html = renderControlViewHtml(
      {
        run,
        history: state.state.runHistory,
        busyLabel: this.busyLabel,
        notice: this.notice,
        setup: this.setupState,
        sections: this.sections(),
        costPerMillionTokens: vscode.workspace
          .getConfiguration('redcon')
          .get<number>('costPerMillionTokens', 3.0),
      },
      getNonce(),
    );

    if (run?.budget) {
      const saved = run.budget.estimated_saved_tokens;
      const total = run.budget.estimated_input_tokens + saved;
      const pct = total > 0 ? Math.round((saved / total) * 100) : 0;
      this.view.description = pct > 0 ? `${pct}% saved` : '';
    }
  }

  private handleMessage(msg: { command: string; text?: string; action?: string; path?: string }): void {
    switch (msg.command) {
      case 'analyze':
        if (msg.text?.trim()) {
          vscode.commands.executeCommand('redcon.pack', msg.text.trim());
        }
        break;
      case 'openRun':
        if (msg.path) {
          void vscode.commands
            .executeCommand('redcon.loadRun', msg.path)
            .then(() => vscode.commands.executeCommand('redcon.openDashboard'));
        }
        break;
      case 'exec':
        this.handleAction(msg.action ?? '');
        break;
    }
  }

  private handleAction(action: string): void {
    const commandByAction: Record<string, string> = {
      dashboard: 'redcon.openDashboard',
      setupInstall: 'redcon.setupInstall',
      setupMcp: 'redcon.setupMcp',
    };
    const command = commandByAction[action];
    if (command) {
      vscode.commands.executeCommand(command);
    }
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
