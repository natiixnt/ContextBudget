from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import ValidationError

from app import db, store, queries
from app.models import (
    DashboardHeatmap,
    DashboardOverview,
    DashboardRepositories,
    DashboardSavings,
    IncomingEvent,
    IngestResponse,
)


def _serializable_errors(exc: ValidationError) -> list[dict]:
    """Return validation errors with ctx values converted to strings."""
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
    title="ContextBudget Control Plane",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/events", response_model=IngestResponse, status_code=201)
async def ingest_events(request: Request) -> IngestResponse:
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
    ids = await store.insert_events_batch(pool, events)
    return IngestResponse(accepted=len(ids), event_ids=ids)


@app.get("/analytics/tokens-per-repo")
async def get_tokens_per_repo():
    return await queries.tokens_per_repo(db.get_pool())


@app.get("/analytics/tokens-per-task")
async def get_tokens_per_task():
    return await queries.tokens_per_task(db.get_pool())


@app.get("/analytics/cache-hit-rate")
async def get_cache_hit_rate():
    return await queries.cache_hit_rate(db.get_pool())


@app.get("/dashboard/overview", response_model=DashboardOverview)
async def get_dashboard_overview() -> DashboardOverview:
    return await queries.dashboard_overview(db.get_pool())


@app.get("/dashboard/repositories", response_model=DashboardRepositories)
async def get_dashboard_repositories() -> DashboardRepositories:
    return await queries.dashboard_repositories(db.get_pool())


@app.get("/dashboard/savings", response_model=DashboardSavings)
async def get_dashboard_savings() -> DashboardSavings:
    return await queries.dashboard_savings(db.get_pool())


@app.get("/dashboard/heatmap", response_model=DashboardHeatmap)
async def get_dashboard_heatmap() -> DashboardHeatmap:
    return await queries.dashboard_heatmap(db.get_pool())
