/**
 * One-click installer for Redcon CLI and MCP server configuration.
 *
 * Runs pip install + redcon init + redcon mcp install behind a progress
 * indicator so users don't need to touch pip or edit .mcp.json by hand.
 */

import * as vscode from 'vscode';
import { exec } from 'child_process';
import * as path from 'path';
import * as fs from 'fs';

export interface SetupResult {
  ok: boolean;
  message: string;
  pythonCmd?: string;
  mcpConfigured?: boolean;
}

function execAsync(cmd: string, cwd: string, timeoutMs = 120_000): Promise<{ stdout: string; stderr: string }> {
  return new Promise((resolve, reject) => {
    const child = exec(cmd, { cwd, timeout: timeoutMs, env: { ...process.env } }, (err, stdout, stderr) => {
      if (err) {
        const e = err as NodeJS.ErrnoException;
        (e as unknown as { stdout: string; stderr: string }).stdout = stdout;
        (e as unknown as { stdout: string; stderr: string }).stderr = stderr;
        reject(e);
        return;
      }
      resolve({ stdout, stderr });
    });
    // Ensure we don't leak zombie processes
    child.on('exit', () => { /* noop */ });
  });
}

async function detectPython(): Promise<string | null> {
  const candidates = ['python3', 'python'];
  for (const cmd of candidates) {
    try {
      const { stdout } = await execAsync(`${cmd} --version`, process.cwd(), 5000);
      if (stdout.toLowerCase().includes('python')) {
        return cmd;
      }
    } catch {
      continue;
    }
  }
  return null;
}

async function isRedconInstalled(pythonCmd: string): Promise<boolean> {
  try {
    await execAsync(`${pythonCmd} -m redcon --help`, process.cwd(), 10_000);
    return true;
  } catch {
    // Fallback: direct redcon binary on PATH
    try {
      await execAsync('redcon --help', process.cwd(), 5_000);
      return true;
    } catch {
      return false;
    }
  }
}

async function isMcpConfigured(workspaceRoot: string): Promise<boolean> {
  const mcpPath = path.join(workspaceRoot, '.mcp.json');
  if (!fs.existsSync(mcpPath)) return false;
  try {
    const data = JSON.parse(fs.readFileSync(mcpPath, 'utf-8'));
    return !!data?.mcpServers?.redcon;
  } catch {
    return false;
  }
}

/**
 * Run the full setup: install redcon[mcp] via pip, then register MCP config.
 */
export async function runSetup(workspaceRoot: string): Promise<SetupResult> {
  return vscode.window.withProgress(
    {
      location: vscode.ProgressLocation.Notification,
      title: 'Setting up Redcon',
      cancellable: false,
    },
    async (progress) => {
      progress.report({ message: 'Detecting Python...', increment: 0 });
      const pythonCmd = await detectPython();
      if (!pythonCmd) {
        return {
          ok: false,
          message: 'Python 3.10+ is required. Install Python, then retry.',
        };
      }

      progress.report({ message: 'Installing redcon[mcp] via pip...', increment: 20 });
      try {
        await execAsync(`${pythonCmd} -m pip install --user --upgrade "redcon[mcp]"`, workspaceRoot, 180_000);
      } catch (err) {
        const e = err as NodeJS.ErrnoException & { stderr?: string };
        return {
          ok: false,
          message: `pip install failed: ${e.stderr?.slice(0, 300) ?? e.message}`,
          pythonCmd,
        };
      }

      progress.report({ message: 'Verifying install...', increment: 40 });
      const installed = await isRedconInstalled(pythonCmd);
      if (!installed) {
        return {
          ok: false,
          message: 'Install succeeded but redcon CLI is not on PATH. You may need to add Python user bin to PATH.',
          pythonCmd,
        };
      }

      progress.report({ message: 'Registering MCP server...', increment: 70 });
      try {
        await execAsync(
          `${pythonCmd} -m redcon mcp install --target all --repo "${workspaceRoot}"`,
          workspaceRoot,
          30_000,
        );
      } catch (err) {
        const e = err as NodeJS.ErrnoException & { stderr?: string };
        return {
          ok: true,
          message: `Installed redcon, but MCP registration failed: ${e.stderr?.slice(0, 200) ?? e.message}`,
          pythonCmd,
          mcpConfigured: false,
        };
      }

      const mcpConfigured = await isMcpConfigured(workspaceRoot);
      progress.report({ message: 'Done', increment: 100 });

      return {
        ok: true,
        message: mcpConfigured
          ? 'Redcon installed and MCP configured for Claude Code, Cursor, and Windsurf.'
          : 'Redcon installed. Reload IDE to activate MCP.',
        pythonCmd,
        mcpConfigured,
      };
    },
  );
}

/**
 * Just register MCP config when redcon is already installed.
 */
export async function registerMcp(workspaceRoot: string): Promise<SetupResult> {
  return vscode.window.withProgress(
    {
      location: vscode.ProgressLocation.Notification,
      title: 'Registering Redcon MCP server',
      cancellable: false,
    },
    async (progress) => {
      progress.report({ message: 'Configuring MCP...', increment: 0 });
      const pythonCmd = (await detectPython()) ?? 'python3';
      try {
        await execAsync(
          `${pythonCmd} -m redcon mcp install --target all --repo "${workspaceRoot}"`,
          workspaceRoot,
          30_000,
        );
      } catch {
        try {
          await execAsync(
            `redcon mcp install --target all --repo "${workspaceRoot}"`,
            workspaceRoot,
            30_000,
          );
        } catch (err) {
          const e = err as NodeJS.ErrnoException & { stderr?: string };
          return {
            ok: false,
            message: `MCP registration failed: ${e.stderr?.slice(0, 200) ?? e.message}`,
          };
        }
      }

      progress.report({ message: 'Done', increment: 100 });
      return {
        ok: true,
        message: 'MCP registered. Reload the window to activate redcon tools in your agent.',
        mcpConfigured: true,
      };
    },
  );
}
