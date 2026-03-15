/**
 * Redcon Cloud API client.
 *
 * Covers the full control-plane and analytics surface:
 *   - Org / Project / Repo management
 *   - API key issuance and revocation
 *   - Event ingestion
 *   - Policy version management
 *   - Webhooks
 *   - Audit log
 *   - Cost analytics and ROI dashboard
 *   - Usage quotas
 *   - Billing summary
 *
 * @example
 * ```ts
 * import { CloudClient } from "@redcon/sdk/cloud";
 *
 * const client = new CloudClient({
 *   baseUrl: "https://cloud.example.com",
 *   apiKey: "rck_...",
 * });
 *
 * const roi = await client.getDashboardROI();
 * console.log(`Saved $${roi.estimated_dollars_saved.toFixed(2)} this month`);
 * ```
 */

import { request, HttpError } from "./http";
import type {
  ApiKeyCreate,
  ApiKeyIssued,
  ApiKeyResponse,
  AuditEntry,
  AuditLogResponse,
  CloudClientOptions,
  CostByRepoRow,
  CostSummaryResponse,
  DashboardROI,
  IngestEvent,
  IngestResponse,
  OrgCreate,
  OrgResponse,
  PolicyVersionCreate,
  PolicyVersionResponse,
  ProjectCreate,
  ProjectResponse,
  QuotaConfig,
  RepoCreate,
  RepoResponse,
  WebhookCreate,
  WebhookResponse,
} from "./types";

export { HttpError };

export class CloudClient {
  private readonly baseUrl: string;
  private readonly headers: Record<string, string>;
  private readonly timeoutMs: number;

  constructor(opts: CloudClientOptions) {
    this.baseUrl = opts.baseUrl.replace(/\/$/, "");
    this.headers = {
      Authorization: `Bearer ${opts.apiKey}`,
    };
    this.timeoutMs = opts.timeoutMs ?? 30_000;
  }

  private get<T>(path: string): Promise<T> {
    return request<T>({
      method: "GET",
      url: `${this.baseUrl}${path}`,
      headers: this.headers,
      timeoutMs: this.timeoutMs,
    });
  }

  private post<T>(path: string, body?: unknown): Promise<T> {
    return request<T>({
      method: "POST",
      url: `${this.baseUrl}${path}`,
      headers: this.headers,
      body,
      timeoutMs: this.timeoutMs,
    });
  }

  private put<T>(path: string, body?: unknown): Promise<T> {
    return request<T>({
      method: "PUT",
      url: `${this.baseUrl}${path}`,
      headers: this.headers,
      body,
      timeoutMs: this.timeoutMs,
    });
  }

  private delete(path: string): Promise<void> {
    return request<void>({
      method: "DELETE",
      url: `${this.baseUrl}${path}`,
      headers: this.headers,
      timeoutMs: this.timeoutMs,
    });
  }

  // ── Health ────────────────────────────────────────────────────────────────

  async health(): Promise<{ status: string; version: string }> {
    return this.get("/health");
  }

  // ── Orgs ─────────────────────────────────────────────────────────────────

  async listOrgs(): Promise<OrgResponse[]> {
    return this.get("/orgs");
  }

  async getOrg(orgId: number): Promise<OrgResponse> {
    return this.get(`/orgs/${orgId}`);
  }

  async deleteOrg(orgId: number): Promise<void> {
    return this.delete(`/orgs/${orgId}`);
  }

  // ── Projects ─────────────────────────────────────────────────────────────

  async createProject(orgId: number, body: ProjectCreate): Promise<ProjectResponse> {
    return this.post(`/orgs/${orgId}/projects`, body);
  }

  async listProjects(orgId: number): Promise<ProjectResponse[]> {
    return this.get(`/orgs/${orgId}/projects`);
  }

  async deleteProject(orgId: number, projectId: number): Promise<void> {
    return this.delete(`/orgs/${orgId}/projects/${projectId}`);
  }

  // ── Repositories ─────────────────────────────────────────────────────────

  async createRepo(orgId: number, projectId: number, body: RepoCreate): Promise<RepoResponse> {
    return this.post(`/orgs/${orgId}/projects/${projectId}/repos`, body);
  }

  async listRepos(orgId: number, projectId: number): Promise<RepoResponse[]> {
    return this.get(`/orgs/${orgId}/projects/${projectId}/repos`);
  }

  async deleteRepo(orgId: number, projectId: number, repoId: number): Promise<void> {
    return this.delete(`/orgs/${orgId}/projects/${projectId}/repos/${repoId}`);
  }

  // ── API keys ─────────────────────────────────────────────────────────────

  async issueApiKey(orgId: number, body: ApiKeyCreate = {}): Promise<ApiKeyIssued> {
    return this.post(`/orgs/${orgId}/api-keys`, body);
  }

  async listApiKeys(orgId: number): Promise<ApiKeyResponse[]> {
    return this.get(`/orgs/${orgId}/api-keys`);
  }

  async revokeApiKey(orgId: number, keyId: number): Promise<void> {
    return this.delete(`/orgs/${orgId}/api-keys/${keyId}`);
  }

  // ── Events ───────────────────────────────────────────────────────────────

  async ingestEvents(events: IngestEvent | IngestEvent[]): Promise<IngestResponse> {
    return this.post("/events", events);
  }

  // ── Policies ─────────────────────────────────────────────────────────────

  async createPolicy(orgId: number, body: PolicyVersionCreate): Promise<PolicyVersionResponse> {
    return this.post(`/orgs/${orgId}/policies`, body);
  }

  async listPolicies(orgId: number): Promise<PolicyVersionResponse[]> {
    return this.get(`/orgs/${orgId}/policies`);
  }

  async activatePolicy(orgId: number, policyId: number): Promise<PolicyVersionResponse> {
    return this.put(`/orgs/${orgId}/policies/${policyId}/activate`);
  }

  async getActivePolicy(
    orgId: number,
    opts: { repoId?: number; projectId?: number } = {}
  ): Promise<PolicyVersionResponse | null> {
    const params = new URLSearchParams({ org_id: String(orgId) });
    if (opts.repoId !== undefined) params.append("repo_id", String(opts.repoId));
    if (opts.projectId !== undefined) params.append("project_id", String(opts.projectId));
    try {
      return await this.get<PolicyVersionResponse>(`/policies/active?${params}`);
    } catch (err) {
      if (err instanceof HttpError && err.statusCode === 404) return null;
      throw err;
    }
  }

  // ── Webhooks ──────────────────────────────────────────────────────────────

  async createWebhook(orgId: number, body: WebhookCreate): Promise<WebhookResponse> {
    return this.post(`/orgs/${orgId}/webhooks`, body);
  }

  async listWebhooks(orgId: number): Promise<WebhookResponse[]> {
    return this.get(`/orgs/${orgId}/webhooks`);
  }

  async deleteWebhook(orgId: number, webhookId: number): Promise<void> {
    return this.delete(`/orgs/${orgId}/webhooks/${webhookId}`);
  }

  // ── Audit log ─────────────────────────────────────────────────────────────

  async getAuditLog(orgId: number, opts: { limit?: number; offset?: number } = {}): Promise<AuditLogResponse> {
    const params = new URLSearchParams();
    if (opts.limit !== undefined) params.append("limit", String(opts.limit));
    if (opts.offset !== undefined) params.append("offset", String(opts.offset));
    const qs = params.toString() ? `?${params}` : "";
    return this.get(`/orgs/${orgId}/audit-log${qs}`);
  }

  // ── Cost analytics ────────────────────────────────────────────────────────

  async getCostSummary(opts: {
    repositoryId?: string;
    fromDate?: string;
    toDate?: string;
  } = {}): Promise<CostSummaryResponse> {
    const params = new URLSearchParams();
    if (opts.repositoryId) params.append("repository_id", opts.repositoryId);
    if (opts.fromDate) params.append("from_date", opts.fromDate);
    if (opts.toDate) params.append("to_date", opts.toDate);
    const qs = params.toString() ? `?${params}` : "";
    return this.get(`/analytics/cost${qs}`);
  }

  async getCostByRepo(opts: { fromDate?: string; toDate?: string } = {}): Promise<{ repositories: CostByRepoRow[] }> {
    const params = new URLSearchParams();
    if (opts.fromDate) params.append("from_date", opts.fromDate);
    if (opts.toDate) params.append("to_date", opts.toDate);
    const qs = params.toString() ? `?${params}` : "";
    return this.get(`/analytics/cost/by-repo${qs}`);
  }

  async getDashboardROI(pricePerMillion: number = 15.0): Promise<DashboardROI> {
    return this.get(`/dashboard/roi?price_per_1m=${pricePerMillion}`);
  }

  // ── Quotas ────────────────────────────────────────────────────────────────

  async getQuota(orgId: number): Promise<{ org_id: number; quota: QuotaConfig | null; monthly_usage: { tokens_used: number; events_count: number } }> {
    return this.get(`/orgs/${orgId}/quota`);
  }

  // ── Billing ───────────────────────────────────────────────────────────────

  async getBillingSummary(orgId: number): Promise<{
    org_id: number;
    stripe_customer_id: string | null;
    billing_events: number;
    total_tokens_reported: number;
    first_reported_at: string | null;
    last_reported_at: string | null;
  }> {
    return this.get(`/orgs/${orgId}/billing`);
  }
}
