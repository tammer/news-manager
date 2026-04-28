"""In-process async jobs for source discovery API runs."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock, Thread
from typing import Any, Callable, Literal
from uuid import uuid4

from news_manager.source_discovery import discover_sources

logger = logging.getLogger(__name__)

JobStatus = Literal["queued", "running", "succeeded", "failed"]


@dataclass(frozen=True)
class SourceDiscoveryParams:
    user_id: str
    query: str
    locale: str | None
    max_results: int

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "query": self.query,
            "locale": self.locale,
            "max_results": self.max_results,
        }


@dataclass
class _SourceDiscoveryJob:
    job_id: str
    owner_user_id: str
    status: JobStatus
    params: SourceDiscoveryParams
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "job_id": self.job_id,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "params": self.params.to_json_dict(),
            "result": self.result,
            "error": self.error,
        }


_jobs_lock = Lock()
_jobs: dict[str, _SourceDiscoveryJob] = {}


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")


def _run_job(
    *,
    job_id: str,
    params: SourceDiscoveryParams,
    discovery_runner: Callable[..., dict[str, Any]],
) -> None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return
        job.status = "running"
        job.started_at = _now_iso()

    try:
        payload = discovery_runner(
            params.query,
            locale=params.locale,
            max_results=params.max_results,
        )
    except Exception as e:
        logger.exception("Source discovery job %s failed", job_id)
        with _jobs_lock:
            job = _jobs.get(job_id)
            if job is None:
                return
            job.status = "failed"
            job.finished_at = _now_iso()
            job.error = str(e)
        return

    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return
        job.status = "succeeded"
        job.finished_at = _now_iso()
        job.result = payload


def start_source_discovery_job(
    *,
    params: SourceDiscoveryParams,
    discovery_runner: Callable[..., dict[str, Any]] = discover_sources,
) -> dict[str, Any]:
    job_id = str(uuid4())
    job = _SourceDiscoveryJob(
        job_id=job_id,
        owner_user_id=params.user_id,
        status="queued",
        params=params,
        created_at=_now_iso(),
    )
    with _jobs_lock:
        _jobs[job_id] = job

    thread = Thread(
        target=_run_job,
        kwargs={
            "job_id": job_id,
            "params": params,
            "discovery_runner": discovery_runner,
        },
        daemon=True,
        name=f"source-discovery-job-{job_id[:8]}",
    )
    thread.start()
    return get_source_discovery_job(job_id) or {"ok": True, "job_id": job_id, "status": "queued"}


def get_source_discovery_job(job_id: str) -> dict[str, Any] | None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return None
        return job.to_json_dict()


def get_source_discovery_job_owner_user_id(job_id: str) -> str | None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return None
        return job.owner_user_id
