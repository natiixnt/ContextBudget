from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import ValidationError

from app import auth, cp_queries, cp_store, db, queries, store, webhook_store
from app.models import (
    AgentRunResponse,
    ApiKeyCreate,
    ApiKeyIssued,
    ApiKeyResponse,
    AuditEntry,
    AuditEntryCreate,
    AuditLogResponse,
    CostByDateResponse,
    CostByDateRow,
    CostByRepoResponse,
    CostByRepoRow,
    CostByRunResponse,
    CostByRunRow,
    CostByStageResponse,
    CostSummaryResponse,
    DashboardHeatmap,
    DashboardOverview,
    DashboardRepositories,
    DashboardROI,
    DashboardSavings,
    IncomingEvent,
    IngestResponse,
    OrgCreate,
    OrgResponse,
    PolicyVersionCreate,
    PolicyVersionResponse,
    ProjectCreate,
    ProjectResponse,
    RepoCreate,
    RepoResponse,
    ROIRepoRow,
    StageDetail,
    WebhookCreate,
    WebhookResponse,
)


def _serializable_errors(exc: ValidationError) -> list[dict]:
    result = []
    for e in exc.errors(include_url=False):
        entry = {k: v for k, v in e.items() if k != "ctx"}
        if "ctx" in e:
            entry["ctx"] = {k: str(v) for k, v in e["ctx"].items()}
        result.append(entry)
    return result


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_pool()
    yield
    await db.close_pool()


app = FastAPI(
    title="ContextBudget Cloud",
    version="1.0.0b1",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Auth dependencies
# ---------------------------------------------------------------------------

async def _api_key_ctx(
    authorization: str | None = Header(default=None),
) -> dict:
    """Require a valid Bearer API key; return org context."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="API key required")
    raw = authorization.removeprefix("Bearer ")
    ctx = await auth.verify_api_key(db.get_pool(), raw)
    if ctx is None:
        raise HTTPException(status_code=401, detail="Invalid or revoked API key")
    return ctx


async def _optional_api_key_ctx(
    authorization: str | None = Header(default=None),
) -> dict | None:
    """Accept an optional Bearer API key.  Returns org context or ``None``."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    raw = authorization.removeprefix("Bearer ")
    return await auth.verify_api_key(db.get_pool(), raw)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": "1.0.0b1"}


# ---------------------------------------------------------------------------
# Event ingestion
# Follow-up #1: optional Bearer auth scopes events to an org
# ---------------------------------------------------------------------------

@app.post("/events", response_model=IngestResponse, status_code=201)
async def ingest_events(
    request: Request,
    authorization: str | None = Header(default=None),
) -> IngestResponse:
    # Extract org_id from auth header if provided (no error on missing/invalid)
    org_id: int | None = None
    if authorization and authorization.startswith("Bearer "):
        raw = authorization.removeprefix("Bearer ")
        ctx = await auth.verify_api_key(db.get_pool(), raw)
        if ctx is not None:
            org_id = ctx["org_id"]

    body: Any = await request.json()

    if isinstance(body, dict):
        raw_events = [body]
    elif isinstance(body, list):
        raw_events = body
    else:
        raise HTTPException(status_code=422, detail="Body must be a JSON object or array of events")

    if not raw_events:
        raise HTTPException(status_code=422, detail="At least one event is required")

    events: list[IncomingEvent] = []
    for i, raw in enumerate(raw_events):
        try:
            events.append(IncomingEvent.model_validate(raw))
        except ValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail={"index": i, "errors": _serializable_errors(exc)},
            )

    pool = db.get_pool()
    ids = await store.insert_events_batch(pool, events, org_id=org_id)
    return IngestResponse(accepted=len(ids), event_ids=ids)


# ---------------------------------------------------------------------------
# Analytics (Follow-up #3: auto-scope to caller's org when authenticated)
# ---------------------------------------------------------------------------

@app.get("/analytics/tokens-per-repo")
async def get_tokens_per_repo(
    ctx: dict | None = Depends(_optional_api_key_ctx),
):
    org_id = ctx["org_id"] if ctx else None
    return await queries.tokens_per_repo(db.get_pool(), org_id=org_id)


@app.get("/analytics/tokens-per-task")
async def get_tokens_per_task(
    ctx: dict | None = Depends(_optional_api_key_ctx),
):
    org_id = ctx["org_id"] if ctx else None
    return await queries.tokens_per_task(db.get_pool(), org_id=org_id)


@app.get("/analytics/cache-hit-rate")
async def get_cache_hit_rate(
    ctx: dict | None = Depends(_optional_api_key_ctx),
):
    org_id = ctx["org_id"] if ctx else None
    return await queries.cache_hit_rate(db.get_pool(), org_id=org_id)


@app.get("/dashboard/overview", response_model=DashboardOverview)
async def get_dashboard_overview(
    ctx: dict | None = Depends(_optional_api_key_ctx),
) -> DashboardOverview:
    org_id = ctx["org_id"] if ctx else None
    return await queries.dashboard_overview(db.get_pool(), org_id=org_id)


@app.get("/dashboard/repositories", response_model=DashboardRepositories)
async def get_dashboard_repositories(
    ctx: dict | None = Depends(_optional_api_key_ctx),
) -> DashboardRepositories:
    org_id = ctx["org_id"] if ctx else None
    return await queries.dashboard_repositories(db.get_pool(), org_id=org_id)


@app.get("/dashboard/savings", response_model=DashboardSavings)
async def get_dashboard_savings(
    ctx: dict | None = Depends(_optional_api_key_ctx),
) -> DashboardSavings:
    org_id = ctx["org_id"] if ctx else None
    return await queries.dashboard_savings(db.get_pool(), org_id=org_id)


@app.get("/dashboard/heatmap", response_model=DashboardHeatmap)
async def get_dashboard_heatmap(
    ctx: dict | None = Depends(_optional_api_key_ctx),
) -> DashboardHeatmap:
    org_id = ctx["org_id"] if ctx else None
    return await queries.dashboard_heatmap(db.get_pool(), org_id=org_id)


# ---------------------------------------------------------------------------
# Organizations (provisioning — protect /orgs POST at network level in prod)
# ---------------------------------------------------------------------------

@app.post("/orgs", response_model=OrgResponse, status_code=201)
async def create_org(body: OrgCreate) -> OrgResponse:
    try:
        record = await cp_store.create_org(db.get_pool(), body.slug, body.display_name)
    except Exception as exc:
        if "unique" in str(exc).lower():
            raise HTTPException(status_code=409, detail=f"Org slug '{body.slug}' already exists")
        raise HTTPException(status_code=500, detail=str(exc))
    return OrgResponse(**record)


@app.get("/orgs", response_model=list[OrgResponse])
async def list_orgs(_ctx: dict = Depends(_api_key_ctx)) -> list[OrgResponse]:
    records = await cp_store.list_orgs(db.get_pool())
    return [OrgResponse(**r) for r in records]


@app.get("/orgs/{org_id}", response_model=OrgResponse)
async def get_org(org_id: int, _ctx: dict = Depends(_api_key_ctx)) -> OrgResponse:
    record = await cp_store.get_org(db.get_pool(), org_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Org not found")
    return OrgResponse(**record)


@app.delete("/orgs/{org_id}", status_code=204)
async def delete_org(
    org_id: int,
    ctx: dict = Depends(_api_key_ctx),
) -> None:
    """Delete org and all children (projects, repos, runs, keys, policies, audit entries)."""
    if ctx["org_id"] != org_id:
        raise HTTPException(status_code=403, detail="Cannot delete another org")
    deleted = await cp_store.delete_org(db.get_pool(), org_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Org not found")


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

@app.post("/orgs/{org_id}/projects", response_model=ProjectResponse, status_code=201)
async def create_project(
    org_id: int,
    body: ProjectCreate,
    _ctx: dict = Depends(_api_key_ctx),
) -> ProjectResponse:
    try:
        record = await cp_store.create_project(
            db.get_pool(), org_id, body.slug, body.display_name
        )
    except Exception as exc:
        if "unique" in str(exc).lower():
            raise HTTPException(
                status_code=409,
                detail=f"Project slug '{body.slug}' already exists in org {org_id}",
            )
        raise HTTPException(status_code=500, detail=str(exc))
    return ProjectResponse(**record)


@app.get("/orgs/{org_id}/projects", response_model=list[ProjectResponse])
async def list_projects(
    org_id: int, _ctx: dict = Depends(_api_key_ctx)
) -> list[ProjectResponse]:
    records = await cp_store.list_projects(db.get_pool(), org_id)
    return [ProjectResponse(**r) for r in records]


@app.delete("/orgs/{org_id}/projects/{project_id}", status_code=204)
async def delete_project(
    org_id: int,
    project_id: int,
    _ctx: dict = Depends(_api_key_ctx),
) -> None:
    deleted = await cp_store.delete_project(db.get_pool(), project_id, org_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Project not found")


# ---------------------------------------------------------------------------
# Repositories
# ---------------------------------------------------------------------------

@app.post(
    "/orgs/{org_id}/projects/{project_id}/repos",
    response_model=RepoResponse,
    status_code=201,
)
async def create_repo(
    org_id: int,
    project_id: int,
    body: RepoCreate,
    _ctx: dict = Depends(_api_key_ctx),
) -> RepoResponse:
    project = await cp_store.get_project(db.get_pool(), project_id)
    if project is None or project["org_id"] != org_id:
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        record = await cp_store.create_repo(
            db.get_pool(),
            project_id,
            body.slug,
            body.display_name,
            body.repository_id,
        )
    except Exception as exc:
        if "unique" in str(exc).lower():
            raise HTTPException(
                status_code=409,
                detail=f"Repo slug '{body.slug}' already exists in project {project_id}",
            )
        raise HTTPException(status_code=500, detail=str(exc))
    return RepoResponse(**record)


@app.get(
    "/orgs/{org_id}/projects/{project_id}/repos",
    response_model=list[RepoResponse],
)
async def list_repos(
    org_id: int,
    project_id: int,
    _ctx: dict = Depends(_api_key_ctx),
) -> list[RepoResponse]:
    project = await cp_store.get_project(db.get_pool(), project_id)
    if project is None or project["org_id"] != org_id:
        raise HTTPException(status_code=404, detail="Project not found")
    records = await cp_store.list_repos(db.get_pool(), project_id)
    return [RepoResponse(**r) for r in records]


@app.delete(
    "/orgs/{org_id}/projects/{project_id}/repos/{repo_id}",
    status_code=204,
)
async def delete_repo(
    org_id: int,
    project_id: int,
    repo_id: int,
    _ctx: dict = Depends(_api_key_ctx),
) -> None:
    project = await cp_store.get_project(db.get_pool(), project_id)
    if project is None or project["org_id"] != org_id:
        raise HTTPException(status_code=404, detail="Project not found")
    deleted = await cp_store.delete_repo(db.get_pool(), repo_id, project_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Repo not found")


# ---------------------------------------------------------------------------
# API keys
# ---------------------------------------------------------------------------

@app.post("/orgs/{org_id}/api-keys", response_model=ApiKeyIssued, status_code=201)
async def issue_api_key(
    org_id: int,
    body: ApiKeyCreate,
) -> ApiKeyIssued:
    """Issue a new API key for *org_id*.  ``raw_key`` is shown once; store it immediately."""
    org = await cp_store.get_org(db.get_pool(), org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Org not found")
    raw, record = await cp_store.issue_api_key(
        db.get_pool(), org_id, body.label, body.expires_at
    )
    return ApiKeyIssued(raw_key=raw, **record)


@app.get("/orgs/{org_id}/api-keys", response_model=list[ApiKeyResponse])
async def list_api_keys(
    org_id: int, _ctx: dict = Depends(_api_key_ctx)
) -> list[ApiKeyResponse]:
    records = await cp_store.list_api_keys(db.get_pool(), org_id)
    return [ApiKeyResponse(**r) for r in records]


@app.delete("/orgs/{org_id}/api-keys/{key_id}", status_code=204)
async def revoke_api_key(
    org_id: int,
    key_id: int,
    _ctx: dict = Depends(_api_key_ctx),
) -> None:
    revoked = await cp_store.revoke_api_key(db.get_pool(), key_id, org_id)
    if not revoked:
        raise HTTPException(status_code=404, detail="Key not found or already revoked")


# ---------------------------------------------------------------------------
# Audit log
# Follow-up #4: POST endpoint for gateway to push entries
# ---------------------------------------------------------------------------

@app.post("/orgs/{org_id}/audit-log", status_code=201)
async def append_audit_log_entry(
    org_id: int,
    body: AuditEntryCreate,
    _ctx: dict = Depends(_api_key_ctx),
) -> dict[str, int]:
    """Append one audit entry.  Used by the runtime gateway after each request."""
    entry_id = await cp_store.append_audit_entry(
        db.get_pool(),
        org_id=org_id,
        repository_id=body.repository_id,
        run_id=body.run_id,
        task_hash=body.task_hash,
        endpoint=body.endpoint,
        policy_version=body.policy_version,
        tokens_used=body.tokens_used,
        tokens_saved=body.tokens_saved,
        violation_count=body.violation_count,
        policy_passed=body.policy_passed,
        status_code=body.status_code,
    )
    return {"id": entry_id}


@app.get("/orgs/{org_id}/audit-log", response_model=AuditLogResponse)
async def get_audit_log(
    org_id: int,
    limit: int = 100,
    offset: int = 0,
    _ctx: dict = Depends(_api_key_ctx),
) -> AuditLogResponse:
    entries = await cp_store.list_audit_log(
        db.get_pool(), org_id, limit=limit, offset=offset
    )
    return AuditLogResponse(
        entries=[AuditEntry(**e) for e in entries],
        total=len(entries),
    )


# ---------------------------------------------------------------------------
# Policy versions
# ---------------------------------------------------------------------------

@app.post("/orgs/{org_id}/policies", response_model=PolicyVersionResponse, status_code=201)
async def create_policy_version(
    org_id: int,
    body: PolicyVersionCreate,
    _ctx: dict = Depends(_api_key_ctx),
) -> PolicyVersionResponse:
    record = await cp_store.create_policy_version(
        db.get_pool(),
        org_id=org_id,
        project_id=body.project_id,
        repo_id=body.repo_id,
        version=body.version,
        spec=body.spec,
    )
    await cp_store.append_audit_entry(
        db.get_pool(),
        org_id=org_id,
        endpoint="POST /orgs/{org_id}/policies",
        policy_version=body.version,
        status_code=201,
    )
    return PolicyVersionResponse(**record)


@app.get("/orgs/{org_id}/policies", response_model=list[PolicyVersionResponse])
async def list_policy_versions(
    org_id: int, _ctx: dict = Depends(_api_key_ctx)
) -> list[PolicyVersionResponse]:
    records = await cp_store.list_policy_versions(db.get_pool(), org_id)
    return [PolicyVersionResponse(**r) for r in records]


@app.put(
    "/orgs/{org_id}/policies/{policy_id}/activate",
    response_model=PolicyVersionResponse,
)
async def activate_policy_version(
    org_id: int,
    policy_id: int,
    _ctx: dict = Depends(_api_key_ctx),
) -> PolicyVersionResponse:
    ok = await cp_store.activate_policy_version(db.get_pool(), policy_id, org_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Policy version not found")
    records = await cp_store.list_policy_versions(db.get_pool(), org_id)
    record = next((r for r in records if r["id"] == policy_id), None)
    if record is None:
        raise HTTPException(status_code=404, detail="Policy version not found")
    await cp_store.append_audit_entry(
        db.get_pool(),
        org_id=org_id,
        endpoint="PUT /orgs/{org_id}/policies/{policy_id}/activate",
        policy_version=record.get("version"),
        status_code=200,
    )
    return PolicyVersionResponse(**record)


@app.get("/policies/active", response_model=PolicyVersionResponse | None)
async def get_active_policy(
    org_id: int,
    repo_id: int | None = None,
    project_id: int | None = None,
    _ctx: dict = Depends(_api_key_ctx),
) -> PolicyVersionResponse | None:
    record = await cp_store.get_active_policy(
        db.get_pool(), org_id=org_id, repo_id=repo_id, project_id=project_id
    )
    if record is None:
        return None
    return PolicyVersionResponse(**record)


# ---------------------------------------------------------------------------
# Cost analytics
# ---------------------------------------------------------------------------

@app.get("/analytics/cost", response_model=CostSummaryResponse)
async def get_cost_summary(
    repository_id: str | None = None,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    ctx: dict = Depends(_api_key_ctx),
) -> CostSummaryResponse:
    result = await cp_queries.cost_summary(
        db.get_pool(),
        repository_id=repository_id,
        from_date=from_date,
        to_date=to_date,
    )
    return CostSummaryResponse(**result)


@app.get("/analytics/cost/by-repo", response_model=CostByRepoResponse)
async def get_cost_by_repo(
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    _ctx: dict = Depends(_api_key_ctx),
) -> CostByRepoResponse:
    rows = await cp_queries.cost_by_repo(
        db.get_pool(), from_date=from_date, to_date=to_date
    )
    return CostByRepoResponse(repositories=[CostByRepoRow(**r) for r in rows])


@app.get("/analytics/cost/by-date", response_model=CostByDateResponse)
async def get_cost_by_date(
    repository_id: str | None = None,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    _ctx: dict = Depends(_api_key_ctx),
) -> CostByDateResponse:
    rows = await cp_queries.cost_by_date(
        db.get_pool(),
        repository_id=repository_id,
        from_date=from_date,
        to_date=to_date,
    )
    return CostByDateResponse(days=[CostByDateRow(**r) for r in rows])


@app.get("/analytics/cost/by-run", response_model=CostByRunResponse)
async def get_cost_by_run(
    repository_id: str | None = None,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    limit: int = 100,
    ctx: dict = Depends(_api_key_ctx),
) -> CostByRunResponse:
    """Per-run cost attribution — useful for chargeback and agent-level ROI reporting."""
    rows = await cp_queries.cost_by_run(
        db.get_pool(),
        repository_id=repository_id,
        from_date=from_date,
        to_date=to_date,
        org_id=ctx["org_id"],
        limit=limit,
    )
    return CostByRunResponse(runs=[CostByRunRow(**r) for r in rows])


@app.get("/analytics/cost/by-stage", response_model=CostByStageResponse)
async def get_cost_by_stage(
    repository_id: str | None = None,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    ctx: dict = Depends(_api_key_ctx),
) -> CostByStageResponse:
    """Savings broken down by optimization stage (compression/ranking vs cache)."""
    result = await cp_queries.cost_by_stage(
        db.get_pool(),
        repository_id=repository_id,
        from_date=from_date,
        to_date=to_date,
        org_id=ctx["org_id"],
    )
    stages = {
        k: StageDetail(**v) for k, v in result["stages"].items()
    }
    return CostByStageResponse(
        run_count=result["run_count"],
        total_baseline_tokens=result["total_baseline_tokens"],
        total_optimized_tokens=result["total_optimized_tokens"],
        total_tokens_saved=result["total_tokens_saved"],
        overall_savings_rate=result["overall_savings_rate"],
        stages=stages,
    )


@app.get("/dashboard/roi", response_model=DashboardROI)
async def get_dashboard_roi(
    price_per_1m: float = 15.0,
    ctx: dict | None = Depends(_optional_api_key_ctx),
) -> DashboardROI:
    """ROI summary: tokens saved, dollars saved, cache hit rate, top repos.

    Default pricing: $15/MTok (GPT-4o input rate).  Pass ``?price_per_1m=X``
    to use a custom rate (e.g. Claude Sonnet = 3.0, GPT-4.1-mini = 0.4).
    """
    org_id = ctx["org_id"] if ctx else None
    result = await cp_queries.roi_summary(
        db.get_pool(),
        org_id=org_id,
        price_per_1m=price_per_1m,
    )
    top_repos = [ROIRepoRow(**r) for r in result["top_repos"]]
    return DashboardROI(
        total_tokens_used=result["total_tokens_used"],
        total_tokens_saved=result["total_tokens_saved"],
        total_baseline_tokens=result["total_baseline_tokens"],
        savings_rate=result["savings_rate"],
        estimated_dollars_saved=result["estimated_dollars_saved"],
        cache_hit_rate_pct=result["cache_hit_rate_pct"],
        total_runs=result["total_runs"],
        runs_with_cache_hits=result["runs_with_cache_hits"],
        price_per_1m_tokens=result["price_per_1m_tokens"],
        top_repos=top_repos,
        note=result["note"],
    )


# ---------------------------------------------------------------------------
# Agent runs
# ---------------------------------------------------------------------------

@app.post("/orgs/{org_id}/webhooks", response_model=WebhookResponse, status_code=201)
async def create_webhook(
    org_id: int,
    body: WebhookCreate,
    _ctx: dict = Depends(_api_key_ctx),
) -> WebhookResponse:
    """Register a webhook URL for push notifications (policy violations, budget overruns, drift)."""
    record = await webhook_store.create_webhook(
        db.get_pool(),
        org_id=org_id,
        url=body.url,
        secret=body.secret,
        events=body.events,
    )
    return WebhookResponse(**record)


@app.get("/orgs/{org_id}/webhooks", response_model=list[WebhookResponse])
async def list_webhooks(
    org_id: int,
    _ctx: dict = Depends(_api_key_ctx),
) -> list[WebhookResponse]:
    records = await webhook_store.list_webhooks(db.get_pool(), org_id)
    return [WebhookResponse(**r) for r in records]


@app.delete("/orgs/{org_id}/webhooks/{webhook_id}", status_code=204)
async def delete_webhook(
    org_id: int,
    webhook_id: int,
    _ctx: dict = Depends(_api_key_ctx),
) -> None:
    deleted = await webhook_store.delete_webhook(db.get_pool(), webhook_id, org_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Webhook not found")


@app.get(
    "/orgs/{org_id}/projects/{project_id}/repos/{repo_id}/runs",
    response_model=list[AgentRunResponse],
)
async def list_agent_runs(
    org_id: int,
    project_id: int,
    repo_id: int,
    limit: int = 50,
    offset: int = 0,
    _ctx: dict = Depends(_api_key_ctx),
) -> list[AgentRunResponse]:
    project = await cp_store.get_project(db.get_pool(), project_id)
    if project is None or project["org_id"] != org_id:
        raise HTTPException(status_code=404, detail="Project not found")
    repo = await cp_store.get_repo(db.get_pool(), repo_id)
    if repo is None or repo["project_id"] != project_id:
        raise HTTPException(status_code=404, detail="Repo not found")
    records = await cp_store.list_agent_runs(
        db.get_pool(), repo_id, limit=limit, offset=offset
    )
    return [AgentRunResponse(**r) for r in records]
