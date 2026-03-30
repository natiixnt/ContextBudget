/**
 * Redcon CLI wrapper - spawns subprocess, parses JSON output.
 */

import { spawn } from 'child_process';
import * as vscode from 'vscode';
import type {
  RunReport,
  AgentPlanReport,
  DoctorReport,
  SimulationReport,
  BenchmarkReport,
  DriftReport,
} from './types';

export interface RedconCliOptions {
  cwd: string;
  timeout?: number;
}

interface CliResult {
  stdout: string;
  stderr: string;
  exitCode: number;
}

function getCliCommand(): string {
  return vscode.workspace
    .getConfiguration('redcon')
    .get<string>('cliCommand', 'redcon');
}

function getConfigFlag(): string[] {
  const configPath = vscode.workspace
    .getConfiguration('redcon')
    .get<string>('configPath', '');
  return configPath ? ['--config', configPath] : [];
}

async function exec(
  args: string[],
  opts: RedconCliOptions,
): Promise<CliResult> {
  const cmd = getCliCommand();
  const timeout = opts.timeout ?? 120_000;

  return new Promise((resolve, reject) => {
    const pathDirs = ['/usr/local/bin', '/opt/homebrew/bin', process.env.HOME + '/.local/bin'].join(':');
    const env = { ...process.env, PYTHONUNBUFFERED: '1' };
    env.PATH = pathDirs + ':' + (process.env.PATH ?? '');

    const proc = spawn(cmd, args, {
      cwd: opts.cwd,
      env,
      stdio: ['ignore', 'pipe', 'pipe'],
    });

    let stdout = '';
    let stderr = '';

    proc.stdout.on('data', (data: Buffer) => {
      stdout += data.toString();
    });
    proc.stderr.on('data', (data: Buffer) => {
      stderr += data.toString();
    });

    const timer = setTimeout(() => {
      proc.kill('SIGTERM');
      reject(new Error(`Redcon command timed out after ${timeout}ms`));
    }, timeout);

    proc.on('close', (code) => {
      clearTimeout(timer);
      resolve({ stdout, stderr, exitCode: code ?? 1 });
    });

    proc.on('error', (err) => {
      clearTimeout(timer);
      if ((err as NodeJS.ErrnoException).code === 'ENOENT') {
        reject(
          new Error(
            `Redcon CLI not found. Install with: pip install redcon\n` +
              `Or set "redcon.cliCommand" in VS Code settings.`,
          ),
        );
      } else if ((err as NodeJS.ErrnoException).code === 'EACCES') {
        reject(new Error('Permission denied running redcon CLI. Check file permissions.'));
      } else {
        reject(err);
      }
    });
  });
}

function parseJson<T>(result: CliResult): T {
  try {
    return JSON.parse(result.stdout) as T;
  } catch {
    throw new Error(
      `Failed to parse Redcon output as JSON.\n` +
        `Exit code: ${result.exitCode}\n` +
        `stderr: ${result.stderr}\n` +
        `stdout (first 1000 chars): ${result.stdout.slice(0, 1000)}`,
    );
  }
}

// --- Public API ---

export async function pack(
  task: string,
  opts: RedconCliOptions & {
    maxTokens?: number;
    topFiles?: number;
    delta?: string;
    strict?: boolean;
    policy?: string;
  },
): Promise<RunReport> {
  const args = ['pack', task, '--format', 'json', ...getConfigFlag()];
  if (opts.maxTokens) {
    args.push('--max-tokens', String(opts.maxTokens));
  }
  if (opts.topFiles) {
    args.push('--top-files', String(opts.topFiles));
  }
  if (opts.delta) {
    args.push('--delta', opts.delta);
  }
  if (opts.strict) {
    args.push('--strict');
  }
  if (opts.policy) {
    args.push('--policy', opts.policy);
  }
  const result = await exec(args, opts);
  return parseJson<RunReport>(result);
}

export async function plan(
  task: string,
  opts: RedconCliOptions & { topFiles?: number },
): Promise<{ ranked_files: RunReport['ranked_files']; scanned_files: number }> {
  const args = ['plan', task, ...getConfigFlag()];
  if (opts.topFiles) {
    args.push('--top-files', String(opts.topFiles));
  }
  const result = await exec(args, opts);
  return parseJson(result);
}

export async function planAgent(
  task: string,
  opts: RedconCliOptions & { topFiles?: number },
): Promise<AgentPlanReport> {
  const args = ['plan-agent', task, ...getConfigFlag()];
  if (opts.topFiles) {
    args.push('--top-files', String(opts.topFiles));
  }
  const result = await exec(args, opts);
  return parseJson<AgentPlanReport>(result);
}

export async function simulate(
  task: string,
  opts: RedconCliOptions & {
    model?: string;
    contextMode?: string;
  },
): Promise<SimulationReport> {
  const args = [
    'simulate-agent',
    task,
    '--format',
    'json',
    ...getConfigFlag(),
  ];
  if (opts.model) {
    args.push('--model', opts.model);
  }
  if (opts.contextMode) {
    args.push('--context-mode', opts.contextMode);
  }
  const result = await exec(args, opts);
  return parseJson<SimulationReport>(result);
}

export async function doctor(opts: RedconCliOptions): Promise<DoctorReport> {
  const result = await exec(['doctor', '--format', 'json'], opts);
  return parseJson<DoctorReport>(result);
}

export async function init(
  opts: RedconCliOptions & { force?: boolean },
): Promise<string> {
  const args = ['init'];
  if (opts.force) {
    args.push('--force');
  }
  const result = await exec(args, opts);
  return result.stdout || result.stderr;
}

export async function benchmark(
  task: string,
  opts: RedconCliOptions & {
    maxTokens?: number;
    topFiles?: number;
  },
): Promise<BenchmarkReport> {
  const args = ['benchmark', task, ...getConfigFlag()];
  if (opts.maxTokens) {
    args.push('--max-tokens', String(opts.maxTokens));
  }
  if (opts.topFiles) {
    args.push('--top-files', String(opts.topFiles));
  }
  const result = await exec(args, opts);
  return parseJson<BenchmarkReport>(result);
}

export async function exportContext(
  runJsonPath: string,
  opts: RedconCliOptions,
): Promise<string> {
  const result = await exec(['export', runJsonPath], opts);
  return result.stdout;
}

export async function drift(
  opts: RedconCliOptions & {
    task?: string;
    window?: number;
    threshold?: number;
  },
): Promise<DriftReport> {
  const args = ['drift', '--format', 'json', ...getConfigFlag()];
  if (opts.task) {
    args.push('--task', opts.task);
  }
  if (opts.window) {
    args.push('--window', String(opts.window));
  }
  if (opts.threshold) {
    args.push('--threshold', String(opts.threshold));
  }
  const result = await exec(args, opts);
  return parseJson<DriftReport>(result);
}

export async function checkInstalled(cwd: string): Promise<boolean> {
  try {
    const result = await exec(['--help'], { cwd, timeout: 10_000 });
    return result.exitCode === 0;
  } catch {
    return false;
  }
}
