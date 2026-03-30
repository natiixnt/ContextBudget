/**
 * All command handlers for the extension.
 * Routes output to the chat view when available, falls back to notifications.
 */

import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import * as redcon from './redcon';
import { state } from './state';
import type { RunReport } from './types';
import type { ChatViewProvider } from './webview/chatView';
import { syncContextFiles, type SyncTarget } from './contextSync';

let chatView: ChatViewProvider | null = null;

export function setChatView(cv: ChatViewProvider): void {
  chatView = cv;
}

function getWorkspaceRoot(): string {
  const folders = vscode.workspace.workspaceFolders;
  if (!folders?.length) {
    throw new Error('No workspace folder open');
  }
  return folders[0].uri.fsPath;
}

function guardRunning(): boolean {
  if (state.state.isRunning) {
    vscode.window.showWarningMessage('A Redcon command is already running.');
    return true;
  }
  return false;
}

async function askTask(placeholder?: string): Promise<string | undefined> {
  const lastTask = state.state.lastTask;
  const result = await vscode.window.showInputBox({
    prompt: 'Describe the task for context analysis',
    placeHolder: placeholder ?? 'e.g. add user authentication',
    value: lastTask || undefined,
    ignoreFocusOut: true,
  });
  if (result !== undefined && result.trim() === '') {
    return undefined;
  }
  return result?.trim();
}

// --- Pack ---

export async function cmdPack(taskFromChat?: string): Promise<void> {
  if (guardRunning()) return;

  const task = typeof taskFromChat === 'string' ? taskFromChat : await askTask();
  if (!task) {
    return;
  }

  const cwd = getWorkspaceRoot();
  const config = vscode.workspace.getConfiguration('redcon');
  const maxTokens = config.get<number>('defaultMaxTokens', 30000);
  const topFiles = config.get<number>('defaultTopFiles', 25);

  if (maxTokens <= 0) {
    vscode.window.showErrorMessage('Redcon: maxTokens must be greater than 0');
    return;
  }

  // Post to chat
  chatView?.addUserMessage(task);
  const analyzingId = chatView?.addAnalyzing('Packing context...');

  state.setRunning(true);
  try {
    const result = await redcon.pack(task, { cwd, maxTokens, topFiles });
    state.setRun(result);
    await state.loadHistory(cwd);

    // Auto-sync context files
    await autoSync(result, cwd);

    if (analyzingId) {
      chatView?.replaceWithPackResult(analyzingId, result);
    } else {
      // Fallback: no chat view
      const pct = result.max_tokens > 0
        ? Math.round((result.budget.estimated_input_tokens / result.max_tokens) * 100)
        : 0;
      vscode.window.showInformationMessage(
        `Redcon: Packed ${result.files_included.length} files | ${pct}% budget | Risk: ${result.budget.quality_risk_estimate}`,
      );
    }
  } catch (err: unknown) {
    if (analyzingId) {
      chatView?.replaceWithError(analyzingId, err);
    } else {
      const msg = err instanceof Error ? err.message : String(err);
      vscode.window.showErrorMessage(`Redcon: ${msg}`);
    }
  } finally {
    state.setRunning(false);
  }
}

// --- Plan ---

export async function cmdPlan(): Promise<void> {
  const task = await askTask();
  if (!task) {
    return;
  }

  const cwd = getWorkspaceRoot();
  const config = vscode.workspace.getConfiguration('redcon');
  const topFiles = config.get<number>('defaultTopFiles', 25);

  chatView?.addUserMessage(`plan: ${task}`);
  const analyzingId = chatView?.addAnalyzing('Ranking files...');

  state.setRunning(true);
  try {
    const result = await redcon.pack(task, { cwd, topFiles });
    state.setRun(result);

    if (analyzingId) {
      chatView?.replaceWithPackResult(analyzingId, result);
    } else {
      vscode.window.showInformationMessage(
        `Redcon: Ranked ${result.ranked_files.length} files for "${task}"`,
      );
    }
  } catch (err: unknown) {
    if (analyzingId) {
      chatView?.replaceWithError(analyzingId, err);
    } else {
      const msg = err instanceof Error ? err.message : String(err);
      vscode.window.showErrorMessage(`Redcon: ${msg}`);
    }
  } finally {
    state.setRunning(false);
  }
}

// --- Plan Agent ---

export async function cmdPlanAgent(): Promise<void> {
  if (guardRunning()) return;

  const task = await askTask('e.g. implement shopping cart with checkout');
  if (!task) {
    return;
  }

  const cwd = getWorkspaceRoot();

  chatView?.addUserMessage(`plan-agent: ${task}`);
  const analyzingId = chatView?.addAnalyzing('Planning agent workflow...');

  state.setRunning(true);
  try {
    const result = await redcon.planAgent(task, { cwd });
    state.setPlan(result);

    if (analyzingId) {
      chatView?.replaceWithPlanAgentResult(analyzingId, result);
    } else {
      vscode.window.showInformationMessage(
        `Redcon: Agent plan with ${result.steps.length} steps`,
      );
    }
  } catch (err: unknown) {
    if (analyzingId) {
      chatView?.replaceWithError(analyzingId, err);
    } else {
      const msg = err instanceof Error ? err.message : String(err);
      vscode.window.showErrorMessage(`Redcon: ${msg}`);
    }
  } finally {
    state.setRunning(false);
  }
}

// --- Doctor ---

export async function cmdDoctor(): Promise<void> {
  if (guardRunning()) return;

  const cwd = getWorkspaceRoot();

  const analyzingId = chatView?.addAnalyzing('Running diagnostics...');

  state.setRunning(true);
  try {
    const result = await redcon.doctor({ cwd });
    state.setDoctor(result);

    if (analyzingId) {
      chatView?.replaceWithDoctorResult(analyzingId, result);
    } else {
      if (result.failures > 0) {
        vscode.window.showWarningMessage(`Redcon Doctor: ${result.failures} check(s) failed`);
      } else {
        vscode.window.showInformationMessage('Redcon Doctor: All checks passed');
      }
    }
  } catch (err: unknown) {
    if (analyzingId) {
      chatView?.replaceWithError(analyzingId, err);
    } else {
      const msg = err instanceof Error ? err.message : String(err);
      vscode.window.showErrorMessage(`Redcon: ${msg}`);
    }
  } finally {
    state.setRunning(false);
  }
}

// --- Init ---

export async function cmdInit(): Promise<void> {
  const cwd = getWorkspaceRoot();
  const configPath = path.join(cwd, 'redcon.toml');

  if (fs.existsSync(configPath)) {
    const overwrite = await vscode.window.showWarningMessage(
      'redcon.toml already exists. Overwrite?',
      'Yes',
      'No',
    );
    if (overwrite !== 'Yes') {
      return;
    }
  }

  state.setRunning(true);
  try {
    await redcon.init({ cwd, force: fs.existsSync(configPath) });
    chatView?.addInfo('Configuration initialized - redcon.toml created');
    const doc = await vscode.workspace.openTextDocument(configPath);
    await vscode.window.showTextDocument(doc);
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    vscode.window.showErrorMessage(`Redcon: ${msg}`);
  } finally {
    state.setRunning(false);
  }
}

// --- Export ---

export async function cmdExport(): Promise<void> {
  const cwd = getWorkspaceRoot();

  const files = fs
    .readdirSync(cwd)
    .filter(
      (f) =>
        (f.endsWith('.json') && f.startsWith('redcon-')) ||
        f === 'run.json',
    )
    .map((f) => path.join(cwd, f));

  if (files.length === 0) {
    chatView?.addInfo('No run.json artifacts found');
    return;
  }

  const selected = await vscode.window.showQuickPick(
    files.map((f) => ({
      label: path.basename(f),
      description: f,
      detail: '',
    })),
    { placeHolder: 'Select run artifact to export' },
  );

  if (!selected) {
    return;
  }

  state.setRunning(true);
  try {
    const result = await redcon.exportContext(selected.description!, { cwd });

    if (result !== undefined) {
      await vscode.env.clipboard.writeText(result);
      chatView?.addInfo(`Exported ${result.length} characters to clipboard`);
    }
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    vscode.window.showErrorMessage(`Redcon: ${msg}`);
  } finally {
    state.setRunning(false);
  }
}

// --- Benchmark ---

export async function cmdBenchmark(): Promise<void> {
  if (guardRunning()) return;

  const task = await askTask();
  if (!task) {
    return;
  }

  const cwd = getWorkspaceRoot();
  const config = vscode.workspace.getConfiguration('redcon');
  const maxTokens = config.get<number>('defaultMaxTokens', 30000);

  chatView?.addUserMessage(`benchmark: ${task}`);
  const analyzingId = chatView?.addAnalyzing('Benchmarking strategies...');

  state.setRunning(true);
  try {
    const result = await redcon.benchmark(task, { cwd, maxTokens });
    state.setBenchmark(result);

    if (analyzingId) {
      chatView?.replaceWithBenchmarkResult(analyzingId, result);
    } else {
      vscode.window.showInformationMessage(
        `Redcon: Benchmarked ${result.strategies.length} strategies`,
      );
    }
  } catch (err: unknown) {
    if (analyzingId) {
      chatView?.replaceWithError(analyzingId, err);
    } else {
      const msg = err instanceof Error ? err.message : String(err);
      vscode.window.showErrorMessage(`Redcon: ${msg}`);
    }
  } finally {
    state.setRunning(false);
  }
}

// --- Simulate ---

export async function cmdSimulate(): Promise<void> {
  if (guardRunning()) return;

  const task = await askTask('e.g. refactor payment processing');
  if (!task) {
    return;
  }

  const cwd = getWorkspaceRoot();

  const model = await vscode.window.showQuickPick(
    [
      { label: 'gpt-4o', description: 'OpenAI GPT-4o' },
      { label: 'gpt-4o-mini', description: 'OpenAI GPT-4o Mini' },
      { label: 'claude-sonnet-4-20250514', description: 'Anthropic Claude Sonnet' },
      { label: 'claude-opus-4-20250514', description: 'Anthropic Claude Opus' },
      { label: 'claude-sonnet-4-20250514', description: 'Anthropic Claude 4 Sonnet' },
    ],
    { placeHolder: 'Select model for cost estimation' },
  );

  if (!model) {
    return;
  }

  chatView?.addUserMessage(`simulate: ${task} (${model.label})`);
  const analyzingId = chatView?.addAnalyzing('Simulating agent cost...');

  state.setRunning(true);
  try {
    const result = await redcon.simulate(task, { cwd, model: model.label });
    state.setSimulation(result);

    if (analyzingId) {
      chatView?.replaceWithSimulateResult(analyzingId, result);
    } else {
      vscode.window.showInformationMessage(
        `Redcon: Estimated cost $${result.cost_estimate.total_cost_usd.toFixed(4)}`,
      );
    }
  } catch (err: unknown) {
    if (analyzingId) {
      chatView?.replaceWithError(analyzingId, err);
    } else {
      const msg = err instanceof Error ? err.message : String(err);
      vscode.window.showErrorMessage(`Redcon: ${msg}`);
    }
  } finally {
    state.setRunning(false);
  }
}

// --- Drift ---

export async function cmdDrift(): Promise<void> {
  if (guardRunning()) return;

  const cwd = getWorkspaceRoot();

  const analyzingId = chatView?.addAnalyzing('Checking token drift...');

  state.setRunning(true);
  try {
    const result = await redcon.drift({ cwd });

    if (analyzingId) {
      chatView?.replaceWithDriftResult(analyzingId, result);
    } else {
      if (result.drift_detected) {
        vscode.window.showWarningMessage(
          `Redcon Drift: ${result.drift_pct.toFixed(1)}% growth`,
        );
      } else {
        vscode.window.showInformationMessage('Redcon: No drift detected');
      }
    }
  } catch (err: unknown) {
    if (analyzingId) {
      chatView?.replaceWithError(analyzingId, err);
    } else {
      const msg = err instanceof Error ? err.message : String(err);
      vscode.window.showErrorMessage(`Redcon: ${msg}`);
    }
  } finally {
    state.setRunning(false);
  }
}

// --- Open Config ---

export async function cmdOpenConfig(): Promise<void> {
  const cwd = getWorkspaceRoot();
  const configPath = path.join(cwd, 'redcon.toml');

  if (!fs.existsSync(configPath)) {
    const create = await vscode.window.showInformationMessage(
      'No redcon.toml found. Create one?',
      'Yes',
      'No',
    );
    if (create !== 'Yes') {
      return;
    }
    await cmdInit();
    if (!fs.existsSync(configPath)) {
      return;
    }
  }

  const doc = await vscode.workspace.openTextDocument(configPath);
  await vscode.window.showTextDocument(doc);
}

// --- Copy Context ---

export async function cmdCopyContext(): Promise<void> {
  const run = state.state.lastRun;
  if (!run?.compressed_context?.length) {
    chatView?.addInfo('No packed context to copy. Run Pack first.');
    return;
  }

  const text = run.compressed_context
    .map((f) => `# File: ${f.path}\n${f.text}`)
    .join('\n\n');

  await vscode.env.clipboard.writeText(text);
  vscode.window.setStatusBarMessage('Context copied to clipboard', 3000);
  chatView?.addInfo(`Copied context for ${run.compressed_context.length} files to clipboard`);
}

// --- Load Run (from history) ---

export async function cmdLoadRun(runPath: string): Promise<void> {
  try {
    const raw = fs.readFileSync(runPath, 'utf-8');
    const data = JSON.parse(raw) as RunReport;
    state.setRun(data);
    chatView?.addInfo(`Loaded run: "${data.task}" (${data.budget.estimated_input_tokens.toLocaleString()} tokens)`);
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    vscode.window.showErrorMessage(`Failed to load run: ${msg}`);
  }
}

// --- Reveal File ---

export async function cmdRevealFile(item: { resourceUri?: vscode.Uri }): Promise<void> {
  if (item?.resourceUri) {
    await vscode.window.showTextDocument(item.resourceUri);
  }
}

// --- Context Sync ---

async function autoSync(result: RunReport, cwd: string): Promise<void> {
  const config = vscode.workspace.getConfiguration('redcon');
  if (!config.get<boolean>('contextSync.enabled', false)) return;
  if (!config.get<boolean>('contextSync.autoSyncOnPack', true)) return;

  const targets = config.get<SyncTarget[]>('contextSync.targets', ['claude']);
  const maxFiles = config.get<number>('contextSync.maxFiles', 30);
  const syncResult = await syncContextFiles(result, cwd, targets, maxFiles);

  if (syncResult.filesWritten.length > 0) {
    const names = syncResult.filesWritten.map((p) => path.basename(p)).join(', ');
    chatView?.addInfo(`Context synced: ${names}`);
  }
  if (syncResult.errors.length > 0) {
    chatView?.addInfo(`Sync errors: ${syncResult.errors.join('; ')}`);
  }
}

export async function cmdSyncContext(): Promise<void> {
  const run = state.state.lastRun;
  if (!run) {
    chatView?.addInfo('No analysis data yet. Send a task first.');
    return;
  }

  const cwd = getWorkspaceRoot();
  const config = vscode.workspace.getConfiguration('redcon');
  const targets = config.get<SyncTarget[]>('contextSync.targets', ['claude']);
  const maxFiles = config.get<number>('contextSync.maxFiles', 30);

  const syncResult = await syncContextFiles(run, cwd, targets, maxFiles);

  if (syncResult.filesWritten.length > 0) {
    const names = syncResult.filesWritten.map((p) => path.basename(p)).join(', ');
    chatView?.addInfo(`Context synced: ${names}`);
  } else if (syncResult.errors.length > 0) {
    chatView?.addInfo(`Sync failed: ${syncResult.errors.join('; ')}`);
  } else {
    chatView?.addInfo('No sync targets configured. Enable in Settings > Redcon > Context Sync.');
  }
}
