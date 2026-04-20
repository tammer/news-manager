"""In-process async jobs for pipeline API runs."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock, Thread
from typing import Any, Callable, Literal
from uuid import uuid4

from news_manager.models import PipelineDbRunResult
from news_manager.pipeline import run_pipeline_from_db
from news_manager.supabase_sync import create_supabase_client

logger = logging.getLogger(__name__)

JobStatus = Literal["queued", "running", "succeeded", "failed"]


@dataclass(frozen=True)
class PipelineRunParams:
    user_id: str
    category: str | None
    source: str | None
    max_articles: int
    timeout: float
    content_max_chars: int
    reprocess: bool = False
    html_discovery_llm: bool = False

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "category": self.category,
            "source": self.source,
            "max_articles": self.max_articles,
            "timeout": self.timeout,
            "content_max_chars": self.content_max_chars,
            "reprocess": self.reprocess,
            "html_discovery_llm": self.html_discovery_llm,
        }


@dataclass
class _PipelineRunJob:
    job_id: str
    owner_user_id: str
    status: JobStatus
    params: PipelineRunParams
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    result: list[dict[str, Any]] | None = None
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
_jobs: dict[str, _PipelineRunJob] = {}


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")


def _run_job(
    *,
    job_id: str,
    params: PipelineRunParams,
    supabase_client_factory: Callable[[], Any],
    pipeline_runner: Callable[..., PipelineDbRunResult],
) -> None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return
        job.status = "running"
        job.started_at = _now_iso()

    try:
        supabase_client = supabase_client_factory()
        results = pipeline_runner(
            supabase_client=supabase_client,
            max_articles=params.max_articles,
            http_timeout=params.timeout,
            content_max_chars=params.content_max_chars,
            user_id_selector=params.user_id,
            category_selector=params.category,
            source_selector=params.source,
            reprocess=params.reprocess,
            html_discovery_llm=params.html_discovery_llm,
        )
        payload = results.article_decisions
    except Exception as e:
        logger.exception("Pipeline job %s failed", job_id)
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


def start_pipeline_job(
    *,
    params: PipelineRunParams,
    supabase_client_factory: Callable[[], Any] = create_supabase_client,
    pipeline_runner: Callable[..., PipelineDbRunResult] = run_pipeline_from_db,
) -> dict[str, Any]:
    job_id = str(uuid4())
    job = _PipelineRunJob(
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
            "supabase_client_factory": supabase_client_factory,
            "pipeline_runner": pipeline_runner,
        },
        daemon=True,
        name=f"pipeline-job-{job_id[:8]}",
    )
    thread.start()
    return get_pipeline_job(job_id) or {"ok": True, "job_id": job_id, "status": "queued"}


def get_pipeline_job(job_id: str) -> dict[str, Any] | None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return None
        return job.to_json_dict()


def get_pipeline_job_owner_user_id(job_id: str) -> str | None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return None
        return job.owner_user_id
