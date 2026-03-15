/**
 * Redcon Runtime Gateway client.
 *
 * Wraps the three gateway endpoints:
 *   - POST /prepare-context    — stateless context optimization
 *   - POST /run-agent-step     — multi-turn agent sessions
 *   - POST /report-run         — run outcome telemetry
 *
 * @example
 * ```ts
 * import { GatewayClient } from "@redcon/sdk/gateway";
 *
 * const client = new GatewayClient({
 *   baseUrl: "http://localhost:8787",
 *   apiKey: process.env.RC_GATEWAY_API_KEY,
 * });
 *
 * const ctx = await client.prepareContext({
 *   task: "refactor authentication module",
 *   repo: "/workspace/myapp",
 *   max_tokens: 60000,
 * });
 *
 * console.log(`Optimized to ${ctx.token_estimate} tokens (saved ${ctx.tokens_saved})`);
 * console.log(ctx.optimized_context.prompt_text);
 * ```
 */

import { request } from "./http";
import type {
  GatewayClientOptions,
  PrepareContextRequest,
  PrepareContextResponse,
  ReportRunRequest,
  ReportRunResponse,
  RunAgentStepRequest,
  RunAgentStepResponse,
} from "./types";

export class GatewayClient {
  private readonly baseUrl: string;
  private readonly headers: Record<string, string>;
  private readonly timeoutMs: number;

  constructor(opts: GatewayClientOptions) {
    this.baseUrl = opts.baseUrl.replace(/\/$/, "");
    this.headers = opts.apiKey
      ? { Authorization: `Bearer ${opts.apiKey}` }
      : {};
    this.timeoutMs = opts.timeoutMs ?? 30_000;
  }

  private post<T>(path: string, body: unknown): Promise<T> {
    return request<T>({
      method: "POST",
      url: `${this.baseUrl}${path}`,
      headers: this.headers,
      body,
      timeoutMs: this.timeoutMs,
    });
  }

  // ── Health ────────────────────────────────────────────────────────────────

  async health(): Promise<{ status: string; version: string }> {
    return request({
      method: "GET",
      url: `${this.baseUrl}/health`,
      headers: this.headers,
      timeoutMs: this.timeoutMs,
    });
  }

  // ── Context optimization ──────────────────────────────────────────────────

  /**
   * Optimize the context for a single LLM call (stateless).
   *
   * Returns token estimates, compressed file contents, and policy status.
   * No session state is created — use {@link runAgentStep} for multi-turn.
   */
  async prepareContext(req: PrepareContextRequest): Promise<PrepareContextResponse> {
    return this.post<PrepareContextResponse>("/prepare-context", req);
  }

  // ── Multi-turn agent sessions ─────────────────────────────────────────────

  /**
   * Run one step of a multi-turn agent session.
   *
   * On the first call, omit `session_id`; the response will include a new
   * `session_id`.  Pass that `session_id` on all subsequent calls to continue
   * the same session with delta-context propagation.
   */
  async runAgentStep(req: RunAgentStepRequest): Promise<RunAgentStepResponse> {
    return this.post<RunAgentStepResponse>("/run-agent-step", req);
  }

  // ── Run outcome reporting ─────────────────────────────────────────────────

  /**
   * Report the outcome of an agent run.
   *
   * Call this after the LLM response is received to record the final token
   * count and status in the gateway telemetry stream.
   */
  async reportRun(req: ReportRunRequest): Promise<ReportRunResponse> {
    return this.post<ReportRunResponse>("/report-run", req);
  }

  // ── Convenience: full agent session ──────────────────────────────────────

  /**
   * Run multiple turns of an agent session, yielding each step's response.
   *
   * @example
   * ```ts
   * const tasks = [
   *   "understand the auth module",
   *   "identify security issues",
   *   "propose fixes",
   * ];
   *
   * for await (const step of client.runSession({ repo: "/workspace", tasks })) {
   *   console.log(`Turn ${step.turn}: ${step.token_estimate} tokens`);
   * }
   * ```
   */
  async *runSession(opts: {
    tasks: string[];
    repo?: string;
    workspace?: string;
    maxTokens?: number;
  }): AsyncGenerator<RunAgentStepResponse> {
    let sessionId: string | undefined;
    for (const task of opts.tasks) {
      const resp = await this.runAgentStep({
        task,
        repo: opts.repo,
        workspace: opts.workspace,
        max_tokens: opts.maxTokens,
        session_id: sessionId,
      });
      sessionId = resp.session_id;
      yield resp;
    }
  }
}
