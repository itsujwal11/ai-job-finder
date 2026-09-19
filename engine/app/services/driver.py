"""Drives a whole pipeline run in-process.

n8n normally paces the run by calling fetch-next / process-next repeatedly. This module does
the same loop inside the engine so a run can also be started from the dashboard button or the
CLI, on machines where n8n is not set up. Only one run can be active at a time (enforced by
runs.start_run), so the background thread is safe to fire and forget.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

from . import fetching, processor, runs

log = logging.getLogger(__name__)

#: Upper bound on loop iterations; the wall-clock deadline is what normally stops a run.
MAX_BATCHES = 200

_thread: threading.Thread | None = None
_lock = threading.Lock()


def drive(run_id: int, max_seconds: int = 3600) -> dict[str, Any]:
    """Fetch everything, process as much as the deadline allows, then finish. Blocking.

    The backlog can be thousands of postings, so the loop is bounded by wall-clock time rather
    than by emptying the queue: whatever is left stays queued and the next run continues with it,
    best-first. Only the run's own AI cap decides how much gets analysed.
    """
    deadline = time.monotonic() + max_seconds
    for _ in range(MAX_BATCHES):
        if time.monotonic() > deadline:
            log.info("Run %s hit its fetch deadline; finishing with what was collected", run_id)
            break
        batch = fetching.fetch_next(run_id, max_tasks=15, max_seconds=120)
        if batch.get("remaining", 0) == 0 or batch.get("note"):
            break
    for _ in range(MAX_BATCHES):
        if time.monotonic() > deadline:
            log.info("Run %s hit its processing deadline; the rest stays queued for the next run", run_id)
            break
        batch = processor.process_next(run_id, max_items=10, max_seconds=240)
        if batch.get("remaining", 0) == 0 or batch.get("note"):
            break
    return runs.finish_run(run_id)


def is_driving() -> bool:
    with _lock:
        return _thread is not None and _thread.is_alive()


def start_background(run_id: int) -> None:
    """Run `drive` on a daemon thread; failures are recorded on the run itself."""
    global _thread

    def target() -> None:
        try:
            drive(run_id)
        except Exception as exc:  # noqa: BLE001 - the run row must never be left 'running'
            log.exception("Background run %s failed", run_id)
            runs.record_failure(run_id, f"{type(exc).__name__}: {exc}")

    with _lock:
        if _thread is not None and _thread.is_alive():
            raise RuntimeError("A run is already being driven by the engine")
        _thread = threading.Thread(target=target, name=f"run-{run_id}", daemon=True)
        _thread.start()
