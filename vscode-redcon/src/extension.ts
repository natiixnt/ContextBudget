/**
 * Redcon VS Code Extension - entry point.
 *
 * Provides context budgeting tools for AI coding agents directly in the editor.
 */

import * as vscode from 'vscode';
import * as path from 'path';
import * as fs from 'fs';
import { state } from './state';
import { StatusBar } from './statusBar';
import { ChatViewProvider } from './webview/chatView';
import { RedconDecorationProvider } from './providers/decorationProvider';
import { RedconCodeLensProvider } from './providers/codelensProvider';
import * as commands from './commands';
import * as redcon from './redcon';
import { runSetup, registerMcp } from './setup';

export async function activate(
  context: vscode.ExtensionContext,
): Promise<void> {
  if (!vscode.workspace.isTrusted) {
    return;
  }

  const output = vscode.window.createOutputChannel('Redcon');
  output.appendLine('Redcon extension activating...');

  // Check if redcon CLI is installed and MCP is configured
  const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;

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
      chatView.showDashboard();
    }),

    vscode.commands.registerCommand('redcon.syncContext', commands.cmdSyncContext),

    vscode.commands.registerCommand('redcon.clearHistory', () => {
      state.setHistory([]);
    }),

    vscode.commands.registerCommand('redcon.help', () => {
      chatView.showTutorial();
    }),

    vscode.commands.registerCommand('redcon.refresh', () => {
      chatView.refresh();
      codeLensProvider.refresh();
      if (workspaceRoot) {
        state.loadHistory(workspaceRoot);
      }
    }),
  );

  // --- Setup commands (install + MCP registration) ---

  context.subscriptions.push(
    vscode.commands.registerCommand('redcon.setupInstall', async () => {
      if (!workspaceRoot) {
        vscode.window.showErrorMessage('Redcon: open a workspace folder first.');
        return;
      }
      const result = await runSetup(workspaceRoot);
      chatView.setSetupState(await detectSetupState(workspaceRoot));
      if (result.ok) {
        const action = await vscode.window.showInformationMessage(
          result.message,
          'Reload Window',
        );
        if (action === 'Reload Window') {
          vscode.commands.executeCommand('workbench.action.reloadWindow');
        }
      } else {
        vscode.window.showErrorMessage(`Redcon setup: ${result.message}`);
      }
    }),
    vscode.commands.registerCommand('redcon.setupMcp', async () => {
      if (!workspaceRoot) {
        vscode.window.showErrorMessage('Redcon: open a workspace folder first.');
        return;
      }
      const result = await registerMcp(workspaceRoot);
      chatView.setSetupState(await detectSetupState(workspaceRoot));
      if (result.ok) {
        const action = await vscode.window.showInformationMessage(
          result.message,
          'Reload Window',
        );
        if (action === 'Reload Window') {
          vscode.commands.executeCommand('workbench.action.reloadWindow');
        }
      } else {
        vscode.window.showErrorMessage(`Redcon MCP: ${result.message}`);
      }
    }),
  );

  // Detect setup state and push it to the chat view
  if (workspaceRoot) {
    detectSetupState(workspaceRoot).then((s) => chatView.setSetupState(s));
  }

  // --- Auto-refresh on save (debounced) ---

  let autoRefreshTimer: ReturnType<typeof setTimeout> | undefined;
  context.subscriptions.push(
    vscode.workspace.onDidSaveTextDocument(() => {
      const config = vscode.workspace.getConfiguration('redcon');
      if (config.get<boolean>('autoRefreshOnSave', false) && state.state.lastTask) {
        if (autoRefreshTimer) {
          clearTimeout(autoRefreshTimer);
        }
        autoRefreshTimer = setTimeout(() => {
          autoRefreshTimer = undefined;
          chatView.addInfo('File saved - re-analyzing...');
          vscode.commands.executeCommand('redcon.pack', state.state.lastTask);
        }, 2000);
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

export interface SetupState {
  cliInstalled: boolean;
  mcpConfigured: boolean;
}

async function detectSetupState(workspaceRoot: string): Promise<SetupState> {
  const cliInstalled = await redcon.checkInstalled(workspaceRoot);
  let mcpConfigured = false;
  try {
    const mcpPath = path.join(workspaceRoot, '.mcp.json');
    if (fs.existsSync(mcpPath)) {
      const data = JSON.parse(fs.readFileSync(mcpPath, 'utf-8'));
      mcpConfigured = !!data?.mcpServers?.redcon;
    }
  } catch {
    mcpConfigured = false;
  }
  return { cliInstalled, mcpConfigured };
}
