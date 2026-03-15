from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import ValidationError

from app import db, store, queries
from app.models import IncomingEvent, IngestResponse


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
                detail={"index": i, "errors": exc.errors()},
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
