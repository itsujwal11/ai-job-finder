"""Endpoints called by the n8n workflows (bearer-token protected)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..config import ConfigError
from ..profile import ProfileError
from ..services import fetching, processor, runs
from .auth import require_pipeline_token

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"], dependencies=[Depends(require_pipeline_token)])


class StartRunBody(BaseModel):
    trigger: str = "manual"
    n8n_execution_id: str | None = None


class FetchBody(BaseModel):
    max_tasks: int = Field(default=10, ge=1, le=100)
    max_seconds: int = Field(default=90, ge=5, le=600)


class ProcessBody(BaseModel):
    max_items: int = Field(default=6, ge=1, le=50)
    max_seconds: int = Field(default=240, ge=10, le=900)


class ErrorBody(BaseModel):
    workflow: str | None = None
    node: str | None = None
    message: str | None = None
    execution_id: str | None = None
    execution_url: str | None = None
    run_id: int | None = None
    details: dict[str, Any] | None = None


@router.post("/runs")
def start_run(body: StartRunBody) -> dict[str, Any]:
    try:
        return runs.start_run(body.trigger, body.n8n_execution_id)
    except (ProfileError, ConfigError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except runs.RunConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _guard(fn, *args):
    try:
        return fn(*args)
    except runs.RunNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ProfileError, ConfigError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/runs/{run_id}/fetch-next")
def fetch_next(run_id: int, body: FetchBody | None = None) -> dict[str, Any]:
    body = body or FetchBody()
    return _guard(fetching.fetch_next, run_id, body.max_tasks, body.max_seconds)


@router.post("/runs/{run_id}/process-next")
def process_next(run_id: int, body: ProcessBody | None = None) -> dict[str, Any]:
    body = body or ProcessBody()
    return _guard(processor.process_next, run_id, body.max_items, body.max_seconds)


@router.post("/runs/{run_id}/finish")
def finish(run_id: int) -> dict[str, Any]:
    return _guard(runs.finish_run, run_id)


@router.post("/errors")
def report_error(body: ErrorBody) -> dict[str, Any]:
    return runs.record_error(body.model_dump())
