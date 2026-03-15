/**
 * Explicit agent → Redcon → model middleware pipeline (TypeScript).
 *
 * Shows the middleware flow in two styles:
 *
 *   Style A  SDK class, inline policy check
 *   Style B  module-level convenience functions
 *
 * The middleware layer intercepts every agent task, compresses the repository
 * context to fit under the token budget, and forwards the compact prompt to
 * the model.  The agent and the model are never directly coupled to the file
 * system or to Redcon internals.
 *
 * Prerequisites:
 *   pip install redcon     (Python side)
 *   npm install                   (from this directory)
 *
 * Run:
 *   npx ts-node middleware_pattern.ts
 */

import { RedconSDK, prepareContext, profileRun, simulateAgent } from "./redcon-sdk";
import type { PrepareContextResult } from "./types";

const TASK = "refactor auth middleware token validation";
const REPO = "examples/risky-auth-change/repo";

// -----------------------------------------------------------------------
// Shared model stub — replace with a real LLM call in production
// -----------------------------------------------------------------------
function callModel(prompt: string): string {
  return `[model response — prompt was ${prompt.length} chars]`;
}

// -----------------------------------------------------------------------
// Style A — SDK class with inline policy guard
//
// agent task → prepareContext() → policy check → callModel()
// -----------------------------------------------------------------------
console.log("=== Style A: RedconSDK ===");

const sdk = new RedconSDK({ maxTokens: 28_000 });

// Step 1 — simulate cost before committing
const plan = sdk.simulateAgent(TASK, REPO, { model: "claude-sonnet-4-6" });
console.log(`  Pre-flight:   ${plan.total_tokens} tokens, $${plan.cost_estimate.total_cost_usd.toFixed(4)}`);

// Step 2 — prepare context (scan → rank → compress → cache)
const result: PrepareContextResult = sdk.prepareContext(TASK, REPO);
const meta = result.agent_middleware.metadata;
console.log(`  Tokens used:  ${meta.estimated_input_tokens}`);
console.log(`  Tokens saved: ${meta.estimated_saved_tokens}`);
console.log(`  Files:        ${meta.files_included_count} included, ${meta.files_skipped_count} skipped`);
console.log(`  Risk:         ${meta.quality_risk_estimate}`);

// Step 3 — inline policy guard (TypeScript side)
const TOKEN_LIMIT = 28_000;
if (meta.estimated_input_tokens > TOKEN_LIMIT) {
  throw new Error(
    `Context exceeds budget: ${meta.estimated_input_tokens} > ${TOKEN_LIMIT} tokens`,
  );
}
if (meta.quality_risk_estimate === "high") {
  console.warn("  WARNING: high compression risk — context quality may be degraded");
}

// Step 4 — build prompt and call model
const promptA = result.compressed_context
  .map((f) => `# File: ${f.path}\n${f.text}`)
  .join("\n\n");

const responseA = callModel(promptA);
console.log(`  Response:     ${responseA}`);
console.log();

// -----------------------------------------------------------------------
// Style B — module-level convenience functions
//
// Minimises boilerplate; shares no state across calls.
// -----------------------------------------------------------------------
console.log("=== Style B: module-level functions ===");

// Simulate first
const planB = simulateAgent(TASK, REPO, { model: "claude-sonnet-4-6" });
console.log(`  Pre-flight:   $${planB.cost_estimate.total_cost_usd.toFixed(4)}`);

// Profile run — includes timing and compression metrics in one call
const profB = profileRun(TASK, REPO, { maxTokens: 28_000 });
const p = profB.profile;
console.log(`  Elapsed:      ${p.elapsed_ms} ms`);
console.log(`  Compression:  ${(p.compression_ratio * 100).toFixed(1)}%`);
console.log(`  Files:        ${p.files_included_count} included`);

// Build prompt from compressed context
const promptB = profB.compressed_context
  .map((f) => `# File: ${f.path}\n${f.text}`)
  .join("\n\n");

const responseB = callModel(promptB);
console.log(`  Response:     ${responseB}`);
console.log();

// -----------------------------------------------------------------------
// Multi-turn loop with delta context
//
// Re-pack only changed files on each subsequent turn.
// -----------------------------------------------------------------------
console.log("=== Multi-turn delta loop ===");

const TASKS = [
  "refactor auth middleware token validation",
  "add unit tests for the auth middleware",
  "update OpenAPI spec for auth endpoints",
];

for (let i = 0; i < TASKS.length; i++) {
  const turn = i + 1;
  const task = TASKS[i];

  // On turn 2+ pass the previous run JSON path to get a delta context.
  // In production: write each result to a temp file and pass the path here.
  const ctx = sdk.prepareContext(task, REPO);
  const m = ctx.agent_middleware.metadata;

  console.log(`  [turn ${turn}] ${task}`);
  console.log(`    tokens: ${m.estimated_input_tokens}, delta: ${m.delta_enabled}, risk: ${m.quality_risk_estimate}`);

  const prompt = ctx.compressed_context.map((f) => `# ${f.path}\n${f.text}`).join("\n\n");
  callModel(prompt); // fire and forget in this demo
}
