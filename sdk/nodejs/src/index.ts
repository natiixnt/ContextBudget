/**
 * @contextbudget/sdk — TypeScript/Node.js SDK for ContextBudget
 *
 * @example Cloud API
 * ```ts
 * import { CloudClient } from "@contextbudget/sdk";
 *
 * const client = new CloudClient({
 *   baseUrl: "https://cloud.example.com",
 *   apiKey: process.env.CB_CLOUD_API_KEY!,
 * });
 *
 * const roi = await client.getDashboardROI();
 * ```
 *
 * @example Gateway
 * ```ts
 * import { GatewayClient } from "@contextbudget/sdk";
 *
 * const gateway = new GatewayClient({ baseUrl: "http://localhost:8787" });
 * const ctx = await gateway.prepareContext({ task: "...", repo: "." });
 * ```
 */

export { CloudClient } from "./cloud";
export { GatewayClient } from "./gateway";
export { HttpError } from "./http";
export type {
  // Cloud types
  OrgCreate,
  OrgResponse,
  ProjectCreate,
  ProjectResponse,
  RepoCreate,
  RepoResponse,
  ApiKeyCreate,
  ApiKeyIssued,
  ApiKeyResponse,
  IngestEvent,
  IngestResponse,
  PolicyVersionCreate,
  PolicyVersionResponse,
  WebhookCreate,
  WebhookResponse,
  AuditEntry,
  AuditLogResponse,
  CostSummaryResponse,
  CostByRepoRow,
  DashboardROI,
  ROIRepoRow,
  QuotaConfig,
  CloudClientOptions,
  // Gateway types
  PrepareContextRequest,
  PrepareContextResponse,
  OptimizedContext,
  OptimizedFile,
  PolicyStatus,
  RunAgentStepRequest,
  RunAgentStepResponse,
  ReportRunRequest,
  ReportRunResponse,
  GatewayClientOptions,
} from "./types";
