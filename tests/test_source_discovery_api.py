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
from news_manager.source_resolve import _collect_candidates_from_query
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


@patch("news_manager.resolve_app.start_source_discovery_job")
def test_source_discover_start_202_and_params_mapped(mock_start_job: Any, jwt_secret: str) -> None:
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
    assert params.max_results == 7


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
            "max_results": 5,
        },
        "result": {
            "ok": True,
            "suggestions": [
                {
                    "name": "EFF",
                    "url": "https://www.eff.org/",
                    "index": "https://www.eff.org/feed",
                    "index_is_rss": True,
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
        max_results=3,
    )

    def runner(_query: str, *, locale: str | None, max_results: int) -> dict[str, Any]:
        _ = locale
        time.sleep(0.05)
        return {
            "ok": True,
            "suggestions": [{"name": "A", "url": "https://example.com/", "why": "Relevant."}] * max_results,
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
        max_results=3,
    )

    def runner(_query: str, *, locale: str | None, max_results: int) -> dict[str, Any]:
        _ = (locale, max_results)
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


@patch("news_manager.source_discovery._chat_json")
@patch("news_manager.source_discovery.resolve_source")
@patch("news_manager.source_discovery.fetch_html_limited")
@patch("news_manager.source_discovery._collect_candidates_from_query")
def test_discover_sources_fallback_when_llm_invalid(
    mock_collect: Any,
    mock_fetch: Any,
    mock_resolve_source: Any,
    mock_chat: Any,
) -> None:
    mock_collect.return_value = [
        {"title": "EFF", "href": "https://www.eff.org/", "body": "Digital rights."},
        {"title": "Krebs", "href": "https://krebsonsecurity.com/", "body": "Security investigations."},
    ]
    mock_fetch.return_value = ("<html><title>Site</title></html>", "https://www.eff.org/", None)
    mock_resolve_source.return_value = {
        "ok": True,
        "resolved_url": "https://www.eff.org/feed",
        "use_rss": True,
    }
    mock_chat.return_value = {"oops": "bad shape"}
    out = discover_sources("privacy news", max_results=2)
    assert out["ok"] is True
    assert len(out["suggestions"]) == 2
    for item in out["suggestions"]:
        assert isinstance(item["name"], str)
        assert isinstance(item["url"], str)
        assert isinstance(item["index"], str)
        assert isinstance(item["index_is_rss"], bool)
        assert isinstance(item["why"], str)


@patch("news_manager.source_discovery._chat_json")
@patch("news_manager.source_discovery.resolve_source")
@patch("news_manager.source_discovery.fetch_html_limited")
@patch("news_manager.source_discovery._collect_candidates_from_query")
def test_discover_sources_filters_unsafe_llm_urls(
    mock_collect: Any,
    mock_fetch: Any,
    mock_resolve_source: Any,
    mock_chat: Any,
) -> None:
    mock_collect.return_value = [
        {"title": "EFF", "href": "https://www.eff.org/", "body": "Digital rights."},
    ]
    mock_fetch.return_value = ("<html><title>EFF</title></html>", "https://www.eff.org/", None)
    mock_resolve_source.return_value = {
        "ok": True,
        "resolved_url": "https://www.eff.org/feed",
        "use_rss": True,
    }
    mock_chat.return_value = {
        "suggestions": [
            {"name": "Bad", "url": "https://127.0.0.1/private", "why": "Nope"},
            {"name": "EFF", "url": "https://www.eff.org/", "why": "Good"},
        ]
    }
    out = discover_sources("privacy news", max_results=3)
    assert out["ok"] is True
    assert len(out["suggestions"]) == 1
    assert out["suggestions"][0]["url"] == "https://www.eff.org/"
    assert out["suggestions"][0]["index"] == "https://www.eff.org/feed"
    assert out["suggestions"][0]["index_is_rss"] is True


@patch("news_manager.source_resolve.ddg_text_search")
def test_collect_candidates_plain_english_uses_search_not_direct(mock_ddg: Any) -> None:
    mock_ddg.return_value = [{"title": "EFF", "href": "https://www.eff.org/", "body": "Digital rights."}]
    rows = _collect_candidates_from_query(
        "independent cybersecurity and privacy news sources",
        max_results=10,
        region="us-en",
    )
    assert rows == mock_ddg.return_value
    mock_ddg.assert_called_once()
