"""Pipeline run API tests (auth, ownership, async lifecycle)."""

from __future__ import annotations

import os
import time
from typing import Any
from unittest.mock import patch

import jwt
import pytest

from news_manager.models import PipelineDbRunResult
from news_manager.pipeline_jobs import PipelineRunParams, get_pipeline_job, start_pipeline_job
from news_manager.resolve_app import create_app


@pytest.fixture
def jwt_secret() -> str:
    return "test-secret-for-jwt-verify-minimum-32-bytes!!"


def _authed_headers(jwt_secret: str, *, sub: str) -> dict[str, str]:
    os.environ["SUPABASE_JWT_SECRET"] = jwt_secret
    token = jwt.encode(
        {
            "sub": sub,
            "aud": "authenticated",
            "exp": int(time.time()) + 3600,
            "role": "authenticated",
        },
        jwt_secret,
        algorithm="HS256",
    )
    token_s = token.decode("ascii") if isinstance(token, bytes) else str(token)
    return {"Authorization": f"Bearer {token_s}"}


def _new_client() -> Any:
    app = create_app()
    app.testing = True
    return app.test_client()


def test_pipeline_run_start_401_without_token(jwt_secret: str) -> None:
    os.environ["SUPABASE_JWT_SECRET"] = jwt_secret
    c = _new_client()
    r = c.post("/api/pipeline/run", json={})
    assert r.status_code == 401


def test_pipeline_run_status_401_without_token(jwt_secret: str) -> None:
    os.environ["SUPABASE_JWT_SECRET"] = jwt_secret
    c = _new_client()
    r = c.get("/api/pipeline/run/job-1")
    assert r.status_code == 401


@patch("news_manager.resolve_app.start_pipeline_job")
def test_pipeline_run_start_202_and_params_mapped(
    mock_start_job: Any, jwt_secret: str
) -> None:
    mock_start_job.return_value = {"job_id": "job-123", "status": "queued"}
    c = _new_client()
    headers = _authed_headers(jwt_secret, sub="user-123")

    r = c.post(
        "/api/pipeline/run",
        json={
            "user_id": "user-123",
            "category": "Politics",
            "source": "BBC",
            "max_articles": 3,
            "timeout": 12.5,
            "content_max_chars": 9000,
        },
        headers=headers,
    )

    assert r.status_code == 202
    payload = r.get_json()
    assert payload == {"ok": True, "job_id": "job-123", "status": "queued"}
    params = mock_start_job.call_args.kwargs["params"]
    assert isinstance(params, PipelineRunParams)
    assert params.user_id == "user-123"
    assert params.category == "Politics"
    assert params.source == "BBC"
    assert params.max_articles == 3
    assert params.timeout == 12.5
    assert params.content_max_chars == 9000
    assert params.reprocess is False
    assert params.html_discovery_llm is False


@patch("news_manager.resolve_app.start_pipeline_job")
def test_pipeline_run_start_accepts_html_discovery_llm(
    mock_start_job: Any, jwt_secret: str
) -> None:
    mock_start_job.return_value = {"job_id": "job-456", "status": "queued"}
    c = _new_client()
    headers = _authed_headers(jwt_secret, sub="user-123")

    r = c.post(
        "/api/pipeline/run",
        json={"html_discovery_llm": True},
        headers=headers,
    )

    assert r.status_code == 202
    params = mock_start_job.call_args.kwargs["params"]
    assert params.html_discovery_llm is True


def test_pipeline_run_start_400_html_discovery_llm_not_boolean(jwt_secret: str) -> None:
    c = _new_client()
    headers = _authed_headers(jwt_secret, sub="user-123")
    r = c.post(
        "/api/pipeline/run",
        json={"html_discovery_llm": "yes"},
        headers=headers,
    )
    assert r.status_code == 400
    payload = r.get_json()
    assert payload["ok"] is False
    assert "html_discovery_llm" in payload["message"]


def test_pipeline_run_start_forbidden_user_mismatch(jwt_secret: str) -> None:
    c = _new_client()
    headers = _authed_headers(jwt_secret, sub="user-123")
    r = c.post("/api/pipeline/run", json={"user_id": "user-999"}, headers=headers)
    assert r.status_code == 403
    payload = r.get_json()
    assert payload["ok"] is False
    assert payload["error"] == "forbidden"


@patch("news_manager.resolve_app.get_pipeline_job")
@patch("news_manager.resolve_app.get_pipeline_job_owner_user_id")
def test_pipeline_run_status_forbidden_cross_user(
    mock_owner: Any, mock_get_job: Any, jwt_secret: str
) -> None:
    mock_owner.return_value = "other-user"
    mock_get_job.return_value = {"ok": True, "job_id": "job-1", "status": "queued"}

    c = _new_client()
    headers = _authed_headers(jwt_secret, sub="user-123")
    r = c.get("/api/pipeline/run/job-1", headers=headers)
    assert r.status_code == 403


@patch("news_manager.resolve_app.get_pipeline_job")
@patch("news_manager.resolve_app.get_pipeline_job_owner_user_id")
def test_pipeline_run_status_success(
    mock_owner: Any, mock_get_job: Any, jwt_secret: str
) -> None:
    mock_owner.return_value = "user-123"
    mock_get_job.return_value = {
        "ok": True,
        "job_id": "job-1",
        "status": "succeeded",
        "started_at": "2026-04-14T00:00:00Z",
        "finished_at": "2026-04-14T00:00:01Z",
        "params": {
            "user_id": "user-123",
            "category": None,
            "source": None,
            "max_articles": 2,
            "timeout": 10.0,
            "content_max_chars": 5000,
        },
        "result": [],
        "error": None,
    }

    c = _new_client()
    headers = _authed_headers(jwt_secret, sub="user-123")
    r = c.get("/api/pipeline/run/job-1", headers=headers)
    assert r.status_code == 200
    payload = r.get_json()
    assert payload["ok"] is True
    assert payload["status"] == "succeeded"


def test_pipeline_evaluate_article_401_without_token(jwt_secret: str) -> None:
    os.environ["SUPABASE_JWT_SECRET"] = jwt_secret
    c = _new_client()
    r = c.post("/api/pipeline/evaluate-article", json={})
    assert r.status_code == 401


def test_pipeline_evaluate_article_400_requires_selector(jwt_secret: str) -> None:
    c = _new_client()
    headers = _authed_headers(jwt_secret, sub="user-123")
    r = c.post(
        "/api/pipeline/evaluate-article",
        json={"category_id": "cid-1"},
        headers=headers,
    )
    assert r.status_code == 400
    payload = r.get_json()
    assert payload["ok"] is False


def test_pipeline_evaluate_article_400_rejects_both_selectors(jwt_secret: str) -> None:
    c = _new_client()
    headers = _authed_headers(jwt_secret, sub="user-123")
    r = c.post(
        "/api/pipeline/evaluate-article",
        json={
            "category_id": "cid-1",
            "url": "https://example.com/a",
            "article_id": "article-1",
        },
        headers=headers,
    )
    assert r.status_code == 400
    payload = r.get_json()
    assert "exactly one" in payload["message"]


def test_pipeline_evaluate_article_400_invalid_persist_type(jwt_secret: str) -> None:
    c = _new_client()
    headers = _authed_headers(jwt_secret, sub="user-123")
    r = c.post(
        "/api/pipeline/evaluate-article",
        json={"category_id": "cid-1", "url": "https://example.com/a", "persist": "yes"},
        headers=headers,
    )
    assert r.status_code == 400
    payload = r.get_json()
    assert "'persist' must be a boolean." in payload["message"]


@patch("news_manager.resolve_app.create_supabase_client")
@patch("news_manager.resolve_app.evaluate_single_article_from_db")
def test_pipeline_evaluate_article_success_dry_run(
    mock_eval: Any, mock_client_factory: Any, jwt_secret: str
) -> None:
    mock_client_factory.return_value = object()
    mock_eval.return_value = {
        "included": False,
        "reason": "Out of scope.",
        "url": "https://example.com/a",
        "title": "Title",
        "date": "2026-04-15",
        "source": "example.com",
        "short_summary": None,
        "full_summary": None,
        "persisted": False,
        "instruction_source": "override",
        "persist_error": None,
    }
    c = _new_client()
    headers = _authed_headers(jwt_secret, sub="user-123")
    r = c.post(
        "/api/pipeline/evaluate-article",
        json={
            "category_id": "cid-1",
            "url": "https://example.com/a",
            "instructions_override": "new instruction",
        },
        headers=headers,
    )
    assert r.status_code == 200
    payload = r.get_json()
    assert payload["ok"] is True
    assert payload["included"] is False
    assert payload["why"] == "Out of scope."
    assert payload["persisted"] is False
    kwargs = mock_eval.call_args.kwargs
    assert kwargs["user_id"] == "user-123"
    assert kwargs["persist"] is False
    assert kwargs["category_id"] == "cid-1"
    assert kwargs["url"] == "https://example.com/a"


@patch("news_manager.resolve_app.create_supabase_client")
@patch("news_manager.resolve_app.evaluate_single_article_from_db")
def test_pipeline_evaluate_article_success_with_article_id_selector(
    mock_eval: Any, mock_client_factory: Any, jwt_secret: str
) -> None:
    mock_client_factory.return_value = object()
    mock_eval.return_value = {
        "included": True,
        "reason": "Matches category.",
        "url": "https://example.com/a",
        "title": "Title",
        "date": None,
        "source": "example.com",
        "short_summary": "short",
        "full_summary": "full",
        "persisted": False,
        "instruction_source": "category",
        "persist_error": None,
    }
    c = _new_client()
    headers = _authed_headers(jwt_secret, sub="user-123")
    r = c.post(
        "/api/pipeline/evaluate-article",
        json={"category_id": "cid-1", "article_id": "article-1"},
        headers=headers,
    )
    assert r.status_code == 200
    kwargs = mock_eval.call_args.kwargs
    assert kwargs["article_id"] == "article-1"
    assert kwargs["url"] is None


@patch("news_manager.resolve_app.create_supabase_client")
@patch("news_manager.resolve_app.evaluate_single_article_from_db")
def test_pipeline_evaluate_article_success_persist_true(
    mock_eval: Any, mock_client_factory: Any, jwt_secret: str
) -> None:
    mock_client_factory.return_value = object()
    mock_eval.return_value = {
        "included": True,
        "reason": "Matches the category.",
        "url": "https://example.com/a",
        "title": "Title",
        "date": "2026-04-15",
        "source": "example.com",
        "short_summary": "short",
        "full_summary": "full",
        "persisted": True,
        "instruction_source": "category",
        "persist_error": None,
    }
    c = _new_client()
    headers = _authed_headers(jwt_secret, sub="user-123")
    r = c.post(
        "/api/pipeline/evaluate-article",
        json={"category_id": "cid-1", "url": "https://example.com/a", "persist": True},
        headers=headers,
    )
    assert r.status_code == 200
    payload = r.get_json()
    assert payload["persisted"] is True
    assert payload["included"] is True
    assert payload["short_summary"] == "short"
    kwargs = mock_eval.call_args.kwargs
    assert kwargs["persist"] is True


@patch("news_manager.resolve_app.create_supabase_client")
@patch("news_manager.resolve_app.evaluate_single_article_from_db")
def test_pipeline_evaluate_article_404_for_lookup_errors(
    mock_eval: Any, mock_client_factory: Any, jwt_secret: str
) -> None:
    mock_client_factory.return_value = object()
    mock_eval.side_effect = LookupError("Category not found for this user.")
    c = _new_client()
    headers = _authed_headers(jwt_secret, sub="user-123")
    r = c.post(
        "/api/pipeline/evaluate-article",
        json={"category_id": "cid-1", "article_id": "article-1"},
        headers=headers,
    )
    assert r.status_code == 404
    payload = r.get_json()
    assert payload["error"] == "not_found"


def test_pipeline_jobs_async_lifecycle_success() -> None:
    params = PipelineRunParams(
        user_id="user-123",
        category=None,
        source=None,
        max_articles=1,
        timeout=10.0,
        content_max_chars=1000,
    )

    def runner(**kwargs: Any) -> PipelineDbRunResult:
        _ = kwargs
        time.sleep(0.05)
        return PipelineDbRunResult(users=[], article_decisions=[])

    job = start_pipeline_job(
        params=params,
        supabase_client_factory=lambda: object(),
        pipeline_runner=runner,
    )
    assert job["status"] in {"queued", "running"}

    deadline = time.time() + 2
    last: dict[str, Any] | None = None
    while time.time() < deadline:
        last = get_pipeline_job(job["job_id"])
        if last is not None and last["status"] == "succeeded":
            break
        time.sleep(0.02)
    assert last is not None
    assert last["status"] == "succeeded"
    assert last["result"] == []
    assert last["error"] is None


def test_pipeline_jobs_async_lifecycle_failure() -> None:
    params = PipelineRunParams(
        user_id="user-123",
        category=None,
        source=None,
        max_articles=1,
        timeout=10.0,
        content_max_chars=1000,
    )

    def runner(**kwargs: Any) -> PipelineDbRunResult:
        _ = kwargs
        time.sleep(0.01)
        raise RuntimeError("boom")

    job = start_pipeline_job(
        params=params,
        supabase_client_factory=lambda: object(),
        pipeline_runner=runner,
    )

    deadline = time.time() + 2
    last: dict[str, Any] | None = None
    while time.time() < deadline:
        last = get_pipeline_job(job["job_id"])
        if last is not None and last["status"] == "failed":
            break
        time.sleep(0.02)
    assert last is not None
    assert last["status"] == "failed"
    assert "boom" in (last["error"] or "")
