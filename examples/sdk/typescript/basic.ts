/**
 * Basic ContextBudget TypeScript SDK usage.
 *
 * Demonstrates all three primary SDK entry points:
 *
 *   prepareContext  — pack repository context under a token budget
 *   simulateAgent   — estimate token use and API cost before packing
 *   profileRun      — pack and return compression profiling metrics
 *
 * Prerequisites:
 *   pip install contextbudget   (or: pip install -e . from repo root)
 *   npm install                 (from this directory)
 *
 * Run:
 *   npx ts-node basic.ts
 */

import { ContextBudgetSDK } from "./contextbudget-sdk";

const TASK = "add Redis caching to the session store";
const REPO = "examples/small-feature/repo";

const sdk = new ContextBudgetSDK({ maxTokens: 30_000 });

// -----------------------------------------------------------------------
// 1. simulateAgent — check token and cost estimates before packing
// -----------------------------------------------------------------------
console.log("=== simulateAgent ===");

const plan = sdk.simulateAgent(TASK, REPO, { model: "claude-sonnet-4-6" });

console.log(`Model:          ${plan.model}`);
console.log(`Total tokens:   ${plan.total_tokens}`);
console.log(`Estimated cost: $${plan.cost_estimate.total_cost_usd.toFixed(4)}`);
console.log();
for (const step of plan.steps) {
  console.log(`  ${step.id.padEnd(14)} ${String(step.step_total_tokens).padStart(6)} tokens`);
}
console.log();

// -----------------------------------------------------------------------
// 2. prepareContext — pack context and get the middleware result
// -----------------------------------------------------------------------
console.log("=== prepareContext ===");

const result = sdk.prepareContext(TASK, REPO);
const meta = result.agent_middleware.metadata;

console.log(`Tokens used:    ${meta.estimated_input_tokens}`);
console.log(`Tokens saved:   ${meta.estimated_saved_tokens}`);
console.log(`Files included: ${meta.files_included_count}`);
console.log(`Quality risk:   ${meta.quality_risk_estimate}`);
console.log(`Cache hits:     ${meta.cache.hits}`);
console.log();

// Build a prompt string from the compressed context entries
const prompt = result.compressed_context
  .map((entry) => `# File: ${entry.path}\n${entry.text}`)
  .join("\n\n");

console.log(`Prompt length:  ${prompt.length} chars`);
console.log();

// -----------------------------------------------------------------------
// 3. profileRun — pack and return timing + compression metrics
// -----------------------------------------------------------------------
console.log("=== profileRun ===");

const prof = sdk.profileRun(TASK, REPO);
const p = prof.profile;

console.log(`Elapsed:            ${p.elapsed_ms} ms`);
console.log(`Compression ratio:  ${(p.compression_ratio * 100).toFixed(1)}%`);
console.log(`Files included:     ${p.files_included_count}`);
console.log(`Files skipped:      ${p.files_skipped_count}`);
console.log(`Quality risk:       ${p.quality_risk_estimate}`);
