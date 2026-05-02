"""Source discovery API and job tests."""

from __future__ import annotations

import os
import time
from typing import Any
from unittest.mock import patch

import jwt
import pytest

from news_manager.resolve_app import create_app
from news_manager.source_discovery import discover_sources
from news_manager.source_discovery_jobs import (
    SourceDiscoveryParams,
    get_source_discovery_job,
    start_source_discovery_job,
)


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


def test_source_discover_start_401_without_token(jwt_secret: str) -> None:
    os.environ["SUPABASE_JWT_SECRET"] = jwt_secret
    c = _new_client()
    r = c.post("/api/sources/discover", json={"query": "privacy news"})
    assert r.status_code == 401


def test_source_discover_status_401_without_token(jwt_secret: str) -> None:
    os.environ["SUPABASE_JWT_SECRET"] = jwt_secret
    c = _new_client()
    r = c.get("/api/sources/discover/job-1")
    assert r.status_code == 401


@patch("news_manager.resolve_app.fetch_user_source_urls")
@patch("news_manager.resolve_app.create_supabase_client")
@patch("news_manager.resolve_app.start_source_discovery_job")
def test_source_discover_start_202_and_params_mapped(
    mock_start_job: Any, mock_create_sb: Any, mock_fetch_urls: Any, jwt_secret: str
) -> None:
    mock_create_sb.return_value = object()
    mock_fetch_urls.return_value = ["https://already-have.example/"]
    mock_start_job.return_value = {"job_id": "job-123", "status": "queued"}
    c = _new_client()
    headers = _authed_headers(jwt_secret, sub="user-123")
    r = c.post(
        "/api/sources/discover",
        json={"query": "indie tech blogs", "locale": "us-en", "max_results": 7},
        headers=headers,
    )
    assert r.status_code == 202
    assert r.get_json() == {"ok": True, "job_id": "job-123", "status": "queued"}
    params = mock_start_job.call_args.kwargs["params"]
    assert isinstance(params, SourceDiscoveryParams)
    assert params.user_id == "user-123"
    assert params.query == "indie tech blogs"
    assert params.locale == "us-en"
    assert params.existing_source_urls == ("https://already-have.example/",)


@patch("news_manager.resolve_app.create_supabase_client")
def test_source_discover_start_503_when_supabase_not_configured(
    mock_create_sb: Any, jwt_secret: str
) -> None:
    mock_create_sb.side_effect = ValueError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set.")
    c = _new_client()
    headers = _authed_headers(jwt_secret, sub="user-123")
    r = c.post(
        "/api/sources/discover",
        json={"query": "indie tech blogs"},
        headers=headers,
    )
    assert r.status_code == 503
    payload = r.get_json()
    assert payload["ok"] is False
    assert payload["error"] == "server_misconfigured"


@patch("news_manager.resolve_app.fetch_user_source_urls")
@patch("news_manager.resolve_app.create_supabase_client")
def test_source_discover_start_500_when_existing_sources_lookup_fails(
    mock_create_sb: Any, mock_fetch_urls: Any, jwt_secret: str
) -> None:
    mock_create_sb.return_value = object()
    mock_fetch_urls.side_effect = RuntimeError("boom")
    c = _new_client()
    headers = _authed_headers(jwt_secret, sub="user-123")
    r = c.post(
        "/api/sources/discover",
        json={"query": "indie tech blogs"},
        headers=headers,
    )
    assert r.status_code == 500
    payload = r.get_json()
    assert payload["ok"] is False
    assert payload["error"] == "discover_failed"


def test_source_discover_start_400_for_invalid_max_results(jwt_secret: str) -> None:
    c = _new_client()
    headers = _authed_headers(jwt_secret, sub="user-123")
    r = c.post(
        "/api/sources/discover",
        json={"query": "privacy", "max_results": "five"},
        headers=headers,
    )
    assert r.status_code == 400
    payload = r.get_json()
    assert payload["ok"] is False
    assert "max_results" in payload["message"]


@patch("news_manager.resolve_app.get_source_discovery_job")
@patch("news_manager.resolve_app.get_source_discovery_job_owner_user_id")
def test_source_discover_status_forbidden_cross_user(
    mock_owner: Any, mock_get_job: Any, jwt_secret: str
) -> None:
    mock_owner.return_value = "other-user"
    mock_get_job.return_value = {"ok": True, "job_id": "job-1", "status": "queued"}
    c = _new_client()
    headers = _authed_headers(jwt_secret, sub="user-123")
    r = c.get("/api/sources/discover/job-1", headers=headers)
    assert r.status_code == 403


@patch("news_manager.resolve_app.get_source_discovery_job")
@patch("news_manager.resolve_app.get_source_discovery_job_owner_user_id")
def test_source_discover_status_not_found(
    mock_owner: Any, mock_get_job: Any, jwt_secret: str
) -> None:
    mock_owner.return_value = None
    mock_get_job.return_value = None
    c = _new_client()
    headers = _authed_headers(jwt_secret, sub="user-123")
    r = c.get("/api/sources/discover/missing", headers=headers)
    assert r.status_code == 404


@patch("news_manager.resolve_app.get_source_discovery_job")
@patch("news_manager.resolve_app.get_source_discovery_job_owner_user_id")
def test_source_discover_status_success(
    mock_owner: Any, mock_get_job: Any, jwt_secret: str
) -> None:
    mock_owner.return_value = "user-123"
    mock_get_job.return_value = {
        "ok": True,
        "job_id": "job-1",
        "status": "succeeded",
        "started_at": "2026-04-28T00:00:00Z",
        "finished_at": "2026-04-28T00:00:01Z",
        "params": {
            "user_id": "user-123",
            "query": "privacy news",
            "locale": None,
            "existing_source_urls_count": 0,
        },
        "result": {
            "ok": True,
            "suggestions": [
                {
                    "name": "EFF",
                    "url": "https://www.eff.org/",
                    "why": "Civil liberties and digital rights coverage.",
                }
            ],
            "meta": {"query": "privacy news"},
        },
        "error": None,
    }
    c = _new_client()
    headers = _authed_headers(jwt_secret, sub="user-123")
    r = c.get("/api/sources/discover/job-1", headers=headers)
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True
    assert data["status"] == "succeeded"
    assert isinstance(data["result"]["suggestions"], list)


def test_source_discovery_jobs_async_lifecycle_success() -> None:
    params = SourceDiscoveryParams(
        user_id="user-123",
        query="open source ai news",
        locale=None,
    )

    def runner(
        _query: str,
        *,
        locale: str | None,
        excluded_source_urls: set[str] | None = None,
    ) -> dict[str, Any]:
        _ = (locale, excluded_source_urls)
        time.sleep(0.05)
        return {
            "ok": True,
            "suggestions": [
                {
                    "domain": "example.com",
                    "url": "https://example.com/",
                    "title": "Example",
                    "kind": "news",
                    "score": 4,
                    "reason": "Relevant.",
                }
            ],
            "meta": {},
        }

    job = start_source_discovery_job(params=params, discovery_runner=runner)
    assert job["status"] in {"queued", "running"}

    deadline = time.time() + 2
    last: dict[str, Any] | None = None
    while time.time() < deadline:
        last = get_source_discovery_job(job["job_id"])
        if last is not None and last["status"] == "succeeded":
            break
        time.sleep(0.02)
    assert last is not None
    assert last["status"] == "succeeded"
    assert isinstance(last["result"], dict)
    assert last["error"] is None


def test_source_discovery_jobs_async_lifecycle_failure() -> None:
    params = SourceDiscoveryParams(
        user_id="user-123",
        query="open source ai news",
        locale=None,
    )

    def runner(
        _query: str,
        *,
        locale: str | None,
        excluded_source_urls: set[str] | None = None,
    ) -> dict[str, Any]:
        _ = (locale, excluded_source_urls)
        time.sleep(0.01)
        raise RuntimeError("boom")

    job = start_source_discovery_job(params=params, discovery_runner=runner)
    deadline = time.time() + 2
    last: dict[str, Any] | None = None
    while time.time() < deadline:
        last = get_source_discovery_job(job["job_id"])
        if last is not None and last["status"] == "failed":
            break
        time.sleep(0.02)
    assert last is not None
    assert last["status"] == "failed"
    assert "boom" in (last["error"] or "")


@patch("news_manager.source_discovery._judge_batches")
@patch("news_manager.source_discovery._ddg_search_worldwide")
@patch("news_manager.source_discovery._generate_queries")
def test_discover_sources_falls_back_to_home_when_judge_omits_suggested_url(
    mock_gen: Any,
    mock_ddg: Any,
    mock_judge: Any,
) -> None:
    mock_gen.return_value = ["q"]
    mock_ddg.return_value = [
        {"title": "T", "href": "https://root.example/section", "body": ""},
    ]
    mock_judge.return_value = [
        {
            "domain": "root.example",
            "verdict": "keep",
            "score": 4,
            "kind": "news",
            "reason": "ok",
            "title": "T",
            "snippet": "",
            "hit_count": 1,
        }
    ]
    out = discover_sources("topic")
    assert out["suggestions"][0]["url"] == "https://root.example/"


@patch("news_manager.source_discovery._judge_batches")
@patch("news_manager.source_discovery._ddg_search_worldwide")
@patch("news_manager.source_discovery._generate_queries")
def test_discover_sources_uses_judge_suggested_url_from_candidates(
    mock_gen: Any,
    mock_ddg: Any,
    mock_judge: Any,
) -> None:
    mock_gen.return_value = ["indie blogs"]
    mock_ddg.return_value = [
        {"title": "Cool Blog", "href": "https://cool.example/post/1", "body": "snippet about indie"},
    ]
    mock_judge.return_value = [
        {
            "domain": "cool.example",
            "verdict": "keep",
            "score": 5,
            "kind": "blog",
            "reason": "Looks like a publication.",
            "title": "Cool Blog",
            "snippet": "snippet",
            "hit_count": 1,
            "suggested_url": "https://cool.example/post/1",
        }
    ]

    out = discover_sources("indie games", locale="us-en")

    assert out["ok"] is True
    assert out["suggestions"][0]["url"] == "https://cool.example/post/1"
    assert out["suggestions"][0]["domain"] == "cool.example"
    assert out["suggestions"][0]["kind"] == "blog"
    assert out["suggestions"][0]["score"] == 5
    assert set(out["suggestions"][0].keys()) == {"domain", "url", "title", "kind", "score", "reason"}
    assert "generated_queries" in out["meta"]
    assert out["meta"]["llm_call_count"] == 0
    assert out["meta"]["min_score"] == 4


@patch("news_manager.source_discovery._judge_batches")
@patch("news_manager.source_discovery._ddg_search_worldwide")
@patch("news_manager.source_discovery._generate_queries")
def test_discover_sources_excludes_existing_domains(
    mock_gen: Any,
    mock_ddg: Any,
    mock_judge: Any,
) -> None:
    mock_gen.return_value = ["q"]
    mock_ddg.return_value = [{"title": "t", "href": "https://owned.example/a", "body": "b"}]
    mock_judge.return_value = [
        {
            "domain": "owned.example",
            "verdict": "keep",
            "score": 4,
            "kind": "news",
            "reason": "x",
            "title": "Owned",
            "snippet": "s",
            "hit_count": 1,
        },
        {
            "domain": "new.example",
            "verdict": "keep",
            "score": 4,
            "kind": "blog",
            "reason": "y",
            "title": "New",
            "snippet": "s2",
            "hit_count": 1,
        },
    ]

    out = discover_sources(
        "books",
        excluded_source_urls={"https://owned.example/"},
    )
    domains = {s["domain"] for s in out["suggestions"]}
    assert "owned.example" not in domains
    assert "new.example" in domains


@patch("news_manager.source_discovery._judge_batches")
@patch("news_manager.source_discovery._ddg_search_worldwide")
@patch("news_manager.source_discovery._generate_queries")
def test_discover_sources_sorts_verdict_then_score(
    mock_gen: Any,
    mock_ddg: Any,
    mock_judge: Any,
) -> None:
    mock_gen.return_value = ["q"]
    mock_ddg.return_value = [{"title": "t", "href": "https://b.example/", "body": ""}]
    mock_judge.return_value = [
        {
            "domain": "b.example",
            "verdict": "drop",
            "score": 5,
            "kind": "other",
            "reason": "dropped",
            "title": "B",
            "snippet": "",
            "hit_count": 1,
        },
        {
            "domain": "a.example",
            "verdict": "keep",
            "score": 4,
            "kind": "news",
            "reason": "kept",
            "title": "A",
            "snippet": "",
            "hit_count": 1,
        },
    ]

    out = discover_sources("topic")
    # Score floor 4: both included; keep (a) sorts before drop (b).
    assert [s["domain"] for s in out["suggestions"]] == ["a.example", "b.example"]


@patch("news_manager.source_discovery._judge_batches")
@patch("news_manager.source_discovery._ddg_search_worldwide")
@patch("news_manager.source_discovery._generate_queries")
def test_discover_sources_omits_scores_below_four(
    mock_gen: Any,
    mock_ddg: Any,
    mock_judge: Any,
) -> None:
    mock_gen.return_value = ["q"]
    mock_ddg.return_value = [{"title": "t", "href": "https://low.example/", "body": ""}]
    mock_judge.return_value = [
        {
            "domain": "low.example",
            "verdict": "keep",
            "score": 3,
            "kind": "blog",
            "reason": "weak",
            "title": "Low",
            "snippet": "",
            "hit_count": 1,
        },
    ]
    out = discover_sources("topic")
    assert out["suggestions"] == []


@patch("news_manager.source_discovery._judge_batches")
@patch("news_manager.source_discovery._ddg_search_worldwide")
@patch("news_manager.source_discovery._generate_queries")
def test_discover_sources_empty_when_no_hits(
    mock_gen: Any,
    mock_ddg: Any,
    mock_judge: Any,
) -> None:
    mock_gen.return_value = ["q"]
    mock_ddg.return_value = []
    mock_judge.return_value = []

    out = discover_sources("niche topic xyz123")
    assert out["ok"] is True
    assert out["suggestions"] == []
    assert out["meta"]["distinct_domains"] == 0
    mock_judge.assert_not_called()
