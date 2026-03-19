/**
 * Redcon VS Code Extension - entry point.
 *
 * Provides context budgeting tools for AI coding agents directly in the editor.
 */

import * as vscode from 'vscode';
import { state } from './state';
import { StatusBar } from './statusBar';
import { ChatViewProvider } from './webview/chatView';
import { RedconDecorationProvider } from './providers/decorationProvider';
import { RedconCodeLensProvider } from './providers/codelensProvider';
import { DashboardPanel } from './webview/dashboardPanel';
import * as commands from './commands';
import * as redcon from './redcon';

export async function activate(
  context: vscode.ExtensionContext,
): Promise<void> {
  const output = vscode.window.createOutputChannel('Redcon');
  output.appendLine('Redcon extension activating...');

  // Check if redcon CLI is installed
  const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  if (workspaceRoot) {
    const installed = await redcon.checkInstalled(workspaceRoot);
    if (!installed) {
      const action = await vscode.window.showWarningMessage(
        'Redcon CLI not found. Install it to use context budgeting features.',
        'Install with pip',
        'Configure Path',
      );
      if (action === 'Install with pip') {
        const terminal = vscode.window.createTerminal('Redcon Install');
        terminal.show();
        terminal.sendText('pip install redcon');
      } else if (action === 'Configure Path') {
        vscode.commands.executeCommand(
          'workbench.action.openSettings',
          'redcon.cliCommand',
        );
      }
    }
  }

  // --- Chat View (single sidebar panel) ---

  const chatView = new ChatViewProvider(context.extensionUri);
  commands.setChatView(chatView);

  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider(ChatViewProvider.viewType, chatView, {
      webviewOptions: { retainContextWhenHidden: true },
    }),
  );

  // --- Status Bar ---

  const statusBar = new StatusBar();
  context.subscriptions.push(statusBar);

  // --- File Decorations ---

  const decorationProvider = new RedconDecorationProvider();
  context.subscriptions.push(
    vscode.window.registerFileDecorationProvider(decorationProvider),
  );

  // --- CodeLens ---

  const codeLensProvider = new RedconCodeLensProvider();
  context.subscriptions.push(
    vscode.languages.registerCodeLensProvider({ scheme: 'file' }, codeLensProvider),
  );

  // --- Commands ---

  context.subscriptions.push(
    vscode.commands.registerCommand('redcon.pack', commands.cmdPack),
    vscode.commands.registerCommand('redcon.plan', commands.cmdPlan),
    vscode.commands.registerCommand('redcon.planAgent', commands.cmdPlanAgent),
    vscode.commands.registerCommand('redcon.doctor', commands.cmdDoctor),
    vscode.commands.registerCommand('redcon.init', commands.cmdInit),
    vscode.commands.registerCommand('redcon.export', commands.cmdExport),
    vscode.commands.registerCommand('redcon.benchmark', commands.cmdBenchmark),
    vscode.commands.registerCommand('redcon.simulate', commands.cmdSimulate),
    vscode.commands.registerCommand('redcon.drift', commands.cmdDrift),
    vscode.commands.registerCommand('redcon.openConfig', commands.cmdOpenConfig),
    vscode.commands.registerCommand('redcon.copyContext', commands.cmdCopyContext),
    vscode.commands.registerCommand('redcon.revealFile', commands.cmdRevealFile),
    vscode.commands.registerCommand('redcon.loadRun', commands.cmdLoadRun),

    vscode.commands.registerCommand('redcon.openDashboard', () => {
      DashboardPanel.show(context.extensionUri);
    }),

    vscode.commands.registerCommand('redcon.refresh', () => {
      chatView.refresh();
      codeLensProvider.refresh();
      if (workspaceRoot) {
        state.loadHistory(workspaceRoot);
      }
    }),
  );

  // --- Auto-refresh on save ---

  context.subscriptions.push(
    vscode.workspace.onDidSaveTextDocument(() => {
      const config = vscode.workspace.getConfiguration('redcon');
      if (config.get<boolean>('autoRefreshOnSave', false) && state.state.lastTask) {
        chatView.addInfo('File saved - re-analyzing...');
        vscode.commands.executeCommand('redcon.pack', state.state.lastTask);
      }
    }),
  );

  // --- Load history on startup ---

  if (workspaceRoot) {
    state.loadHistory(workspaceRoot);
  }

  // --- Cleanup ---

  context.subscriptions.push({
    dispose: () => {
      state.dispose();
      decorationProvider.dispose();
      codeLensProvider.dispose();
      output.dispose();
    },
  });

  output.appendLine('Redcon extension activated');
}

export function deactivate(): void {
  // Cleanup handled by subscriptions
}
