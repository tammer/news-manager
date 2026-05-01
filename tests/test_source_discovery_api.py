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
    assert params.max_results == 7
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
            "max_results": 5,
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
@patch("news_manager.source_discovery.fetch_html_limited")
@patch("news_manager.source_discovery.ddg_text_search")
def test_discover_sources_uses_required_seed_query(
    mock_ddg: Any,
    mock_fetch: Any,
    mock_chat: Any,
) -> None:
    mock_ddg.return_value = [{"title": "Seed", "href": "https://seed.example/", "body": ""}]
    mock_fetch.return_value = ("<html><title>Seed</title><body>hello</body></html>", "https://seed.example/", None)
    mock_chat.return_value = {"classification": "other", "reason": "Not relevant."}

    discover_sources("privacy security", locale="us-en", max_results=3)

    mock_ddg.assert_called_once_with(
        "blogs or news sites about privacy security",
        max_results=30,
        region="us-en",
    )


@patch("news_manager.source_discovery._chat_json")
@patch("news_manager.source_discovery.fetch_html_limited")
@patch("news_manager.source_discovery.ddg_text_search")
def test_discover_sources_article_recommendations_are_reclassified(
    mock_ddg: Any,
    mock_fetch: Any,
    mock_chat: Any,
) -> None:
    mock_ddg.return_value = [
        {"title": "seed", "href": "https://seed.example/", "body": ""},
    ]

    def _fetch_side_effect(url: str) -> tuple[str | None, str | None, dict[str, Any] | None]:
        if url == "https://seed.example/":
            return ('<html><title>Seed story</title><body><a href="https://rec.example/page">rec</a></body></html>', url, None)
        if url == "https://rec.example/page":
            return ("<html><title>Rec Home</title><body>home</body></html>", url, None)
        return ("<html><title>Unknown</title><body>none</body></html>", url, None)

    mock_fetch.side_effect = _fetch_side_effect
    mock_chat.side_effect = [
        {"classification": "article", "reason": "Single story."},
        {
            "recommended": [{"name": "Rec", "url": "https://rec.example/page"}],
            "reasoning": "mentioned in article",
        },
        {"classification": "blog home", "reason": "Looks like a blog index."},
    ]

    out = discover_sources("book reviews", max_results=1)

    assert out["ok"] is True
    assert [item["url"] for item in out["suggestions"]] == ["https://rec.example/page"]
    assert out["suggestions"][0]["classification"] == "blog home"


@patch("news_manager.source_discovery._chat_json")
@patch("news_manager.source_discovery.fetch_html_limited")
@patch("news_manager.source_discovery.ddg_text_search")
def test_discover_sources_uses_recommender_link_without_root_normalization(
    mock_ddg: Any,
    mock_fetch: Any,
    mock_chat: Any,
) -> None:
    mock_ddg.return_value = [{"title": "Seed", "href": "https://seed.example/", "body": ""}]

    def _fetch_side_effect(url: str) -> tuple[str | None, str | None, dict[str, Any] | None]:
        if url == "https://seed.example/":
            return ("<html><title>seed</title><body>story</body></html>", url, None)
        if url == "https://example.com/path/to/page":
            return ("<html><title>section home</title><body>index</body></html>", url, None)
        return ("<html><title>other</title><body>none</body></html>", url, None)

    mock_fetch.side_effect = _fetch_side_effect
    mock_chat.side_effect = [
        {"classification": "article", "reason": "article"},
        {
            "recommended": [{"name": "Exact", "url": "https://example.com/path/to/page"}],
            "reasoning": "linked directly",
        },
        {"classification": "news home", "reason": "home"},
    ]

    out = discover_sources("topic", max_results=1)
    assert out["ok"] is True
    assert out["suggestions"][0]["url"] == "https://example.com/path/to/page"
    fetched_urls = [call.args[0] for call in mock_fetch.call_args_list]
    assert "https://example.com/path/to/page" in fetched_urls
    assert "https://example.com/" not in fetched_urls


@patch("news_manager.source_discovery._chat_json")
@patch("news_manager.source_discovery.fetch_html_limited")
@patch("news_manager.source_discovery.ddg_text_search")
def test_discover_sources_stops_at_five_results_even_if_higher_requested(
    mock_ddg: Any,
    mock_fetch: Any,
    mock_chat: Any,
) -> None:
    mock_ddg.return_value = [
        {"title": f"Seed{i}", "href": f"https://site{i}.example/", "body": ""}
        for i in range(1, 8)
    ]
    mock_fetch.side_effect = lambda url: (f"<html><title>{url}</title><body>body</body></html>", url, None)
    mock_chat.side_effect = [{"classification": "news home", "reason": "homepage"} for _ in range(7)]

    out = discover_sources("ai news", max_results=10)
    assert len(out["suggestions"]) == 5
    assert out["meta"]["max_results"] == 5


@patch("news_manager.source_discovery._chat_json")
@patch("news_manager.source_discovery.fetch_html_limited")
@patch("news_manager.source_discovery.ddg_text_search")
def test_discover_sources_excludes_existing_user_sources(
    mock_ddg: Any,
    mock_fetch: Any,
    mock_chat: Any,
) -> None:
    mock_ddg.return_value = [
        {"title": "Owned", "href": "https://owned.example/", "body": ""},
        {"title": "New", "href": "https://new.example/", "body": ""},
    ]
    mock_fetch.side_effect = lambda url: (f"<html><title>{url}</title><body>body</body></html>", url, None)
    mock_chat.side_effect = [
        {"classification": "news home", "reason": "owned"},
        {"classification": "blog home", "reason": "new"},
    ]

    out = discover_sources("book reviews", max_results=2, excluded_source_urls={"https://owned.example/"})
    assert len(out["suggestions"]) == 1
    assert out["suggestions"][0]["url"] == "https://new.example/"


@patch("news_manager.source_discovery._chat_json")
@patch("news_manager.source_discovery.fetch_html_limited")
@patch("news_manager.source_discovery.ddg_text_search")
def test_discover_sources_ignores_invalid_llm_classification(
    mock_ddg: Any,
    mock_fetch: Any,
    mock_chat: Any,
) -> None:
    mock_ddg.return_value = [{"title": "Bad", "href": "https://bad.example/", "body": ""}]
    mock_fetch.return_value = ("<html><title>Bad</title><body>x</body></html>", "https://bad.example/", None)
    mock_chat.return_value = {"classification": "unknown", "reason": "bad label"}

    out = discover_sources("topic", max_results=3)
    assert out["ok"] is True
    assert out["suggestions"] == []


@patch("news_manager.source_discovery._chat_json")
@patch("news_manager.source_discovery.fetch_html_limited")
@patch("news_manager.source_discovery.ddg_text_search")
def test_discover_sources_result_payload_is_minimal(
    mock_ddg: Any,
    mock_fetch: Any,
    mock_chat: Any,
) -> None:
    mock_ddg.return_value = [{"title": "Site", "href": "https://site.example/", "body": ""}]
    mock_fetch.return_value = ("<html><title>Site</title><body>news body</body></html>", "https://site.example/", None)
    mock_chat.return_value = {"classification": "news home", "reason": "homepage layout"}

    out = discover_sources("topic", max_results=1)
    suggestion = out["suggestions"][0]
    assert set(suggestion.keys()) == {"title", "url", "base_domain", "classification", "reason"}


@patch("news_manager.source_discovery._chat_json")
@patch("news_manager.source_discovery.fetch_html_limited")
@patch("news_manager.source_discovery.ddg_text_search")
def test_discover_sources_passes_intent_to_classifier_prompt(
    mock_ddg: Any,
    mock_fetch: Any,
    mock_chat: Any,
) -> None:
    mock_ddg.return_value = [{"title": "Site", "href": "https://site.example/", "body": ""}]
    mock_fetch.return_value = ("<html><title>Site</title><body>security content</body></html>", "https://site.example/", None)
    mock_chat.return_value = {"classification": "other", "reason": "not aligned"}

    discover_sources("cybersecurity news", max_results=1)

    system_prompt = mock_chat.call_args.args[0]
    user_prompt = mock_chat.call_args.args[1]
    assert "Classify the page into exactly one class" in system_prompt
    assert "Title:" in user_prompt
    assert "Meta tags JSON:" in user_prompt
    assert "Intent: cybersecurity news" in user_prompt
    assert "Site" in user_prompt
