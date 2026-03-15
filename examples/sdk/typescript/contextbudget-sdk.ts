/**
 * Redcon TypeScript SDK.
 *
 * Architecture
 * ────────────
 *   agent → RedconSDK → Python bridge → Redcon pipeline → model
 *
 * The SDK spawns a lightweight Python bridge process (_bridge.py) for each
 * call.  The bridge runs the full Redcon pipeline in-process and
 * returns a JSON artifact over stdout.
 *
 * Three primary entry points
 * ──────────────────────────
 *   prepareContext()   pack repository context under a token budget
 *   simulateAgent()    estimate token use and API cost before packing
 *   profileRun()       pack and return compression profiling metrics
 *
 * Quick-start
 * ──────────
 *   import { RedconSDK } from "./redcon-sdk";
 *
 *   const sdk = new RedconSDK({ maxTokens: 32_000 });
 *   const result = await sdk.prepareContext("add caching", { repo: "." });
 *   const prompt  = result.agent_middleware.metadata.estimated_input_tokens;
 *
 * Prerequisites
 * ─────────────
 *   pip install redcon   (or `pip install -e .` from repo root)
 */

import { spawnSync } from "child_process";
import * as path from "path";
import * as fs from "fs";

import type {
  PrepareContextOptions,
  PrepareContextResult,
  ProfileRunOptions,
  ProfileRunResult,
  SDKOptions,
  SimulateAgentOptions,
  SimulateAgentResult,
} from "./types";

// Path to the Python bridge script bundled alongside this file.
const BRIDGE_SCRIPT = path.resolve(__dirname, "_bridge.py");

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

function callBridge<T>(
  pythonBin: string,
  method: string,
  params: Record<string, unknown>,
): T {
  if (!fs.existsSync(BRIDGE_SCRIPT)) {
    throw new Error(
      `Redcon bridge script not found at ${BRIDGE_SCRIPT}. ` +
        "Ensure _bridge.py is in the same directory as redcon-sdk.ts.",
    );
  }

  const input = JSON.stringify({ method, params });

  const proc = spawnSync(pythonBin, [BRIDGE_SCRIPT], {
    input,
    encoding: "utf-8",
    maxBuffer: 32 * 1024 * 1024, // 32 MB — generous for large context artifacts
  });

  if (proc.error) {
    throw new Error(`Failed to spawn bridge process: ${proc.error.message}`);
  }

  if (proc.status !== 0) {
    const stderr = (proc.stderr ?? "").trim();
    throw new Error(
      `Redcon bridge exited with code ${proc.status}${stderr ? `: ${stderr}` : ""}`,
    );
  }

  const stdout = (proc.stdout ?? "").trim();
  if (!stdout) {
    throw new Error("Redcon bridge returned empty output");
  }

  try {
    return JSON.parse(stdout) as T;
  } catch {
    throw new Error(`Redcon bridge returned invalid JSON: ${stdout.slice(0, 200)}`);
  }
}

// ---------------------------------------------------------------------------
// RedconSDK
// ---------------------------------------------------------------------------

/**
 * TypeScript SDK entry point for Redcon agent framework integration.
 *
 * Each method call spawns the Python bridge once and returns a typed result.
 * Calls are synchronous (spawnSync) so they can be used inside agent loops
 * without additional async wiring.
 */
export class RedconSDK {
  private readonly pythonBin: string;
  private readonly maxTokens: number | undefined;
  private readonly topFiles: number | undefined;

  constructor(options: SDKOptions = {}) {
    this.pythonBin = options.pythonBin ?? "python3";
    this.maxTokens = options.maxTokens;
    this.topFiles = options.topFiles;
  }

  /**
   * Pack repository context for a task under the configured token budget.
   *
   * Runs the full Redcon pipeline — scan, rank, compress, cache —
   * and returns a structured result containing the compressed context and
   * additive middleware metadata.
   *
   * @param task  Natural-language description of the agent's current task.
   * @param repo  Path to the repository to scan.
   * @param opts  Optional overrides (maxTokens, deltaFrom, workspace, …).
   *
   * @example
   * const result = sdk.prepareContext("refactor auth", ".");
   * const prompt = result.compressed_context
   *   .map(f => `# ${f.path}\n${f.text}`)
   *   .join("\n\n");
   * console.log(result.agent_middleware.metadata.estimated_input_tokens, "tokens");
   */
  prepareContext(
    task: string,
    repo: string = ".",
    opts: PrepareContextOptions = {},
  ): PrepareContextResult {
    return callBridge<PrepareContextResult>(this.pythonBin, "prepareContext", {
      task,
      repo,
      workspace: opts.workspace,
      maxTokens: opts.maxTokens ?? this.maxTokens,
      topFiles: opts.topFiles ?? this.topFiles,
      deltaFrom: opts.deltaFrom,
      metadata: opts.metadata,
      configPath: opts.configPath,
    });
  }

  /**
   * Simulate a multi-step agent workflow with token and cost estimates.
   *
   * Returns a step-by-step breakdown across lifecycle steps (inspect,
   * implement, test, validate, document) *before* any pack run.
   *
   * @param task   Natural-language task description.
   * @param repo   Repository path to analyse.
   * @param opts   Optional overrides (model, topFiles, custom prices, …).
   *
   * @example
   * const plan = sdk.simulateAgent("add caching", ".", { model: "claude-sonnet-4-6" });
   * console.log(`Estimated cost: $${plan.cost_estimate.total_cost_usd.toFixed(4)}`);
   * for (const step of plan.steps) {
   *   console.log(`  ${step.id.padEnd(14)} ${step.step_total_tokens} tokens`);
   * }
   */
  simulateAgent(
    task: string,
    repo: string = ".",
    opts: SimulateAgentOptions = {},
  ): SimulateAgentResult {
    return callBridge<SimulateAgentResult>(this.pythonBin, "simulateAgent", {
      task,
      repo,
      workspace: opts.workspace,
      model: opts.model ?? "claude-sonnet-4-6",
      topFiles: opts.topFiles ?? this.topFiles,
      pricePerMillionInput: opts.pricePerMillionInput,
      pricePerMillionOutput: opts.pricePerMillionOutput,
      configPath: opts.configPath,
    });
  }

  /**
   * Pack context and return the run artifact augmented with profiling data.
   *
   * Measures wall-clock time and derives compression metrics, making it easy
   * for agent frameworks to log a one-stop run summary.
   *
   * @param task  Natural-language task description.
   * @param repo  Repository path.
   * @param opts  Optional overrides.
   *
   * @example
   * const prof = sdk.profileRun("add caching", ".");
   * const p = prof.profile;
   * console.log(`packed in ${p.elapsed_ms} ms, ${(p.compression_ratio * 100).toFixed(1)}% compression`);
   * console.log(`${p.files_included_count} files included, ${p.files_skipped_count} skipped`);
   */
  profileRun(
    task: string,
    repo: string = ".",
    opts: ProfileRunOptions = {},
  ): ProfileRunResult {
    return callBridge<ProfileRunResult>(this.pythonBin, "profileRun", {
      task,
      repo,
      workspace: opts.workspace,
      maxTokens: opts.maxTokens ?? this.maxTokens,
      topFiles: opts.topFiles ?? this.topFiles,
      configPath: opts.configPath,
    });
  }
}

// ---------------------------------------------------------------------------
// Module-level convenience functions
// ---------------------------------------------------------------------------

let _defaultSdk: RedconSDK | undefined;

function getDefaultSdk(): RedconSDK {
  _defaultSdk ??= new RedconSDK();
  return _defaultSdk;
}

/**
 * Prepare packed context for a task (module-level convenience).
 *
 * @example
 * import { prepareContext } from "./redcon-sdk";
 * const result = prepareContext("add caching", ".", { maxTokens: 28_000 });
 */
export function prepareContext(
  task: string,
  repo: string = ".",
  opts: PrepareContextOptions = {},
): PrepareContextResult {
  return getDefaultSdk().prepareContext(task, repo, opts);
}

/**
 * Simulate agent token and cost estimates (module-level convenience).
 *
 * @example
 * import { simulateAgent } from "./redcon-sdk";
 * const plan = simulateAgent("add caching", ".", { model: "claude-sonnet-4-6" });
 */
export function simulateAgent(
  task: string,
  repo: string = ".",
  opts: SimulateAgentOptions = {},
): SimulateAgentResult {
  return getDefaultSdk().simulateAgent(task, repo, opts);
}

/**
 * Pack and return profiling metrics (module-level convenience).
 *
 * @example
 * import { profileRun } from "./redcon-sdk";
 * const prof = profileRun("add caching", ".");
 */
export function profileRun(
  task: string,
  repo: string = ".",
  opts: ProfileRunOptions = {},
): ProfileRunResult {
  return getDefaultSdk().profileRun(task, repo, opts);
}
