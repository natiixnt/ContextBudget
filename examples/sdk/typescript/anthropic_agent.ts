/**
 * Anthropic agent integration with Redcon middleware (TypeScript).
 *
 * Architecture:
 *   agent task
 *     → Redcon (scan → rank → compress → cache → delta)
 *     → optimised prompt
 *     → Claude API
 *     → response
 *
 * Redcon sits transparently between the agent loop and the model.
 * For each turn it builds the smallest context that fits under the token
 * budget and forwards the compressed prompt to Claude.  Delta mode ensures
 * subsequent turns only resend files that changed.
 *
 * Prerequisites:
 *   pip install redcon     (Python side)
 *   npm install                   (from this directory)
 *   export ANTHROPIC_API_KEY=sk-ant-...
 *
 * Run:
 *   npx ts-node anthropic_agent.ts
 */

import Anthropic from "@anthropic-ai/sdk";
import { RedconSDK } from "./redcon-sdk";
import type { PrepareContextResult } from "./types";

// -----------------------------------------------------------------------
// Anthropic client
// -----------------------------------------------------------------------
const anthropic = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY });

async function callClaude(prompt: string): Promise<string> {
  const message = await anthropic.messages.create({
    model: "claude-sonnet-4-6",
    max_tokens: 1024,
    messages: [{ role: "user", content: prompt }],
  });
  const block = message.content[0];
  return block.type === "text" ? block.text : "";
}

// -----------------------------------------------------------------------
// Middleware helper — agent → Redcon → model
// -----------------------------------------------------------------------

/**
 * Build the prompt text from a prepareContext result.
 * Concatenates compressed file content with file-path headers.
 */
function buildPrompt(result: PrepareContextResult): string {
  return result.compressed_context
    .map((entry) => `# File: ${entry.path}\n${entry.text}`)
    .join("\n\n");
}

// -----------------------------------------------------------------------
// Agent loop
// -----------------------------------------------------------------------

const sdk = new RedconSDK({ maxTokens: 32_000 });
const REPO = "examples/small-feature/repo";

async function runAgentLoop(): Promise<void> {
  const tasks = [
    "add Redis caching to the session store",
    "add unit tests for the Redis cache layer",
  ];

  let previousRunJson: string | undefined;

  for (let i = 0; i < tasks.length; i++) {
    const task = tasks[i];
    console.log(`\n[turn ${i + 1}] ${task}`);

    // 1. Intercept the task — Redcon builds the optimised prompt
    const result = sdk.prepareContext(task, REPO, {
      deltaFrom: previousRunJson, // only resend changed files on turn 2+
    });

    const meta = result.agent_middleware.metadata;
    console.log(`  tokens:      ${meta.estimated_input_tokens} used, ${meta.estimated_saved_tokens} saved`);
    console.log(`  files:       ${result.files_included.join(", ")}`);
    console.log(`  delta:       ${meta.delta_enabled}`);
    console.log(`  quality:     ${meta.quality_risk_estimate}`);

    // 2. Forward the compressed prompt to Claude
    const prompt = buildPrompt(result);
    const response = await callClaude(prompt);

    console.log(`  response:    ${response.slice(0, 120)}...`);

    // 3. Persist the run artifact so the next turn can delta against it
    //    In a real agent loop you'd write this to a temp file and pass the path.
    //    Here we pass the JSON string directly via a temp path (demonstration).
    previousRunJson = undefined; // In production: write result to file, pass path
  }
}

runAgentLoop().catch((err) => {
  console.error("Agent loop failed:", err);
  process.exit(1);
});
