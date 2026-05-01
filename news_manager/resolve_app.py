"""Flask app: POST /api/sources/resolve (JWT required)."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import jwt
from flask import Flask, jsonify, request

from news_manager.auth_supabase import verify_supabase_jwt
from news_manager.config import (
    DEFAULT_CONTENT_MAX_CHARS,
    DEFAULT_HTTP_TIMEOUT,
    DEFAULT_MAX_ARTICLES,
    assert_resolve_api_supabase_auth_config,
    groq_api_key,
    load_dotenv_if_present,
    supabase_settings,
)
from news_manager.pipeline import evaluate_single_article_from_db
from news_manager.pipeline_jobs import (
    PipelineRunParams,
    get_pipeline_job,
    get_pipeline_job_owner_user_id,
    start_pipeline_job,
)
from news_manager.source_discovery_jobs import (
    SourceDiscoveryParams,
    get_source_discovery_job,
    get_source_discovery_job_owner_user_id,
    start_source_discovery_job,
)
from news_manager.source_resolve import resolve_source_json_body
from news_manager.supabase_sync import create_supabase_client
from news_manager.supabase_sync import fetch_user_source_urls
from news_manager.user_sources_catalog import import_user_sources_catalog

logger = logging.getLogger(__name__)

DEFAULT_CORS_ORIGINS = (
    "http://localhost:5173",
    "https://gistprism.tammer.com",
)


def _normalize_origin(value: str | None) -> str | None:
    if not value:
        return None
    return value.rstrip("/")


def _allowed_cors_origins() -> frozenset[str]:
    raw = os.environ.get("RESOLVE_CORS_ORIGIN", "").strip()
    parts = list(DEFAULT_CORS_ORIGINS)
    parts.extend(p.strip() for p in raw.split(",") if p.strip())
    normalized = (_normalize_origin(p) for p in parts)
    return frozenset(p for p in normalized if p)


def _auth_required_response() -> tuple[object, int]:
    return (
        jsonify(
            {
                "ok": False,
                "error": "no_results",
                "message": "Authorization Bearer token required.",
            }
        ),
        401,
    )


def _invalid_token_response() -> tuple[object, int]:
    return (
        jsonify(
            {
                "ok": False,
                "error": "no_results",
                "message": "Invalid or expired token.",
            }
        ),
        401,
    )


def _require_auth_claims() -> tuple[dict[str, Any] | None, tuple[object, int] | None]:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None, _auth_required_response()
    token = auth[7:].strip()
    if not token:
        return None, _auth_required_response()
    try:
        claims = verify_supabase_jwt(token)
    except jwt.PyJWTError as e:
        logger.debug("JWT verification failed: %s", e)
        return None, _invalid_token_response()
    return claims, None


def _json_error(message: str, *, status: int, error: str = "no_results") -> tuple[object, int]:
    return jsonify({"ok": False, "error": error, "message": message}), status


def _optional_str_field(body: dict[str, Any], key: str) -> tuple[str | None, tuple[object, int] | None]:
    value = body.get(key)
    if value is None:
        return None, None
    if not isinstance(value, str):
        return None, _json_error(f"'{key}' must be a string.", status=400)
    trimmed = value.strip()
    return (trimmed or None), None


def _required_sub(claims: dict[str, Any]) -> tuple[str | None, tuple[object, int] | None]:
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub.strip():
        return None, _json_error("Token missing required 'sub' claim.", status=401)
    return sub.strip(), None


def _parse_pipeline_run_request(
    *,
    body: dict[str, Any],
    auth_user_id: str,
) -> tuple[PipelineRunParams | None, tuple[object, int] | None]:
    category, category_err = _optional_str_field(body, "category")
    if category_err is not None:
        return None, category_err
    source, source_err = _optional_str_field(body, "source")
    if source_err is not None:
        return None, source_err
    user_id, user_id_err = _optional_str_field(body, "user_id")
    if user_id_err is not None:
        return None, user_id_err
    if user_id is not None and user_id != auth_user_id:
        return None, _json_error(
            "Provided user_id does not match authenticated user.",
            status=403,
            error="forbidden",
        )

    max_articles = body.get("max_articles", DEFAULT_MAX_ARTICLES)
    if not isinstance(max_articles, int):
        return None, _json_error("'max_articles' must be an integer.", status=400)

    timeout = body.get("timeout", DEFAULT_HTTP_TIMEOUT)
    if not isinstance(timeout, (int, float)):
        return None, _json_error("'timeout' must be a number.", status=400)

    content_max_chars = body.get("content_max_chars", DEFAULT_CONTENT_MAX_CHARS)
    if not isinstance(content_max_chars, int):
        return None, _json_error("'content_max_chars' must be an integer.", status=400)

    reprocess = body.get("reprocess", False)
    if not isinstance(reprocess, bool):
        return None, _json_error("'reprocess' must be a boolean.", status=400)

    html_discovery_llm = body.get("html_discovery_llm", False)
    if not isinstance(html_discovery_llm, bool):
        return None, _json_error("'html_discovery_llm' must be a boolean.", status=400)

    return (
        PipelineRunParams(
            user_id=auth_user_id,
            category=category,
            source=source,
            max_articles=max_articles,
            timeout=float(timeout),
            content_max_chars=content_max_chars,
            reprocess=reprocess,
            html_discovery_llm=html_discovery_llm,
        ),
        None,
    )


def _parse_evaluate_article_request(
    *,
    body: dict[str, Any],
) -> tuple[dict[str, Any] | None, tuple[object, int] | None]:
    category_id, category_err = _optional_str_field(body, "category_id")
    if category_err is not None:
        return None, category_err
    if category_id is None:
        return None, _json_error("'category_id' is required.", status=400)

    url, url_err = _optional_str_field(body, "url")
    if url_err is not None:
        return None, url_err
    article_id, article_err = _optional_str_field(body, "article_id")
    if article_err is not None:
        return None, article_err

    if bool(url) == bool(article_id):
        return None, _json_error("Provide exactly one of 'url' or 'article_id'.", status=400)

    instructions_override, instructions_err = _optional_str_field(
        body, "instructions_override"
    )
    if instructions_err is not None:
        return None, instructions_err

    persist = body.get("persist", False)
    if not isinstance(persist, bool):
        return None, _json_error("'persist' must be a boolean.", status=400)

    content_max_chars = body.get("content_max_chars", DEFAULT_CONTENT_MAX_CHARS)
    if not isinstance(content_max_chars, int):
        return None, _json_error("'content_max_chars' must be an integer.", status=400)

    timeout = body.get("timeout", DEFAULT_HTTP_TIMEOUT)
    if not isinstance(timeout, (int, float)):
        return None, _json_error("'timeout' must be a number.", status=400)

    return (
        {
            "category_id": category_id,
            "url": url,
            "article_id": article_id,
            "instructions_override": instructions_override,
            "persist": persist,
            "content_max_chars": content_max_chars,
            "timeout": float(timeout),
        },
        None,
    )


def _parse_source_discovery_request(
    *,
    body: dict[str, Any],
    auth_user_id: str,
) -> tuple[SourceDiscoveryParams | None, tuple[object, int] | None]:
    query, query_err = _optional_str_field(body, "query")
    if query_err is not None:
        return None, query_err
    if query is None:
        return None, _json_error("'query' is required.", status=400)

    locale, locale_err = _optional_str_field(body, "locale")
    if locale_err is not None:
        return None, locale_err

    max_results = body.get("max_results", 5)
    if not isinstance(max_results, int):
        return None, _json_error("'max_results' must be an integer.", status=400)
    max_results_clamped = max(1, min(max_results, 10))

    return (
        SourceDiscoveryParams(
            user_id=auth_user_id,
            query=query,
            locale=locale,
            max_results=max_results_clamped,
        ),
        None,
    )


def create_app() -> Flask:
    app = Flask(__name__)
    allowed_origins = _allowed_cors_origins()

    @app.after_request
    def add_cors_headers(response):
        origin = request.headers.get("Origin")
        if _normalize_origin(origin) in allowed_origins and origin:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"
            response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
            response.headers["Access-Control-Allow-Methods"] = "POST, GET, OPTIONS"
            response.headers["Access-Control-Max-Age"] = "86400"
        return response

    @app.route("/api/sources/resolve", methods=["OPTIONS"])
    def resolve_preflight() -> tuple[str, int]:
        return "", 204

    @app.post("/api/sources/resolve")
    def resolve() -> tuple[object, int]:
        _claims, auth_err = _require_auth_claims()
        if auth_err is not None:
            return auth_err

        payload, status = resolve_source_json_body(request.get_data())
        return jsonify(payload), status

    @app.route("/api/sources/discover", methods=["OPTIONS"])
    def source_discover_preflight() -> tuple[str, int]:
        return "", 204

    @app.post("/api/sources/discover")
    def source_discover_start() -> tuple[object, int]:
        claims, auth_err = _require_auth_claims()
        if auth_err is not None:
            return auth_err
        assert claims is not None
        auth_user_id, sub_err = _required_sub(claims)
        if sub_err is not None:
            return sub_err
        assert auth_user_id is not None

        body = request.get_json(silent=True)
        if body is None or not isinstance(body, dict):
            return _json_error("Body must be a JSON object.", status=400)

        params, parse_err = _parse_source_discovery_request(body=body, auth_user_id=auth_user_id)
        if parse_err is not None:
            return parse_err
        assert params is not None
        try:
            sb = create_supabase_client()
            existing_urls = fetch_user_source_urls(sb, auth_user_id)
        except ValueError as e:
            return _json_error(str(e), status=503, error="server_misconfigured")
        except RuntimeError as e:
            logger.warning("source discovery preload failed for user_id=%s: %s", auth_user_id, e)
            return _json_error("Failed to load existing sources.", status=500, error="discover_failed")

        params = SourceDiscoveryParams(
            user_id=params.user_id,
            query=params.query,
            locale=params.locale,
            max_results=params.max_results,
            existing_source_urls=tuple(existing_urls),
        )

        job = start_source_discovery_job(params=params)
        return jsonify({"ok": True, "job_id": job["job_id"], "status": job["status"]}), 202

    @app.route("/api/sources/discover/<job_id>", methods=["OPTIONS"])
    def source_discover_status_preflight(job_id: str) -> tuple[str, int]:
        _ = job_id
        return "", 204

    @app.get("/api/sources/discover/<job_id>")
    def source_discover_status(job_id: str) -> tuple[object, int]:
        claims, auth_err = _require_auth_claims()
        if auth_err is not None:
            return auth_err
        assert claims is not None
        auth_user_id, sub_err = _required_sub(claims)
        if sub_err is not None:
            return sub_err
        assert auth_user_id is not None

        owner = get_source_discovery_job_owner_user_id(job_id)
        if owner is None:
            return _json_error("Source discovery job not found.", status=404, error="not_found")
        if owner != auth_user_id:
            return _json_error(
                "You are not allowed to access this source discovery job.",
                status=403,
                error="forbidden",
            )

        payload = get_source_discovery_job(job_id)
        if payload is None:
            return _json_error("Source discovery job not found.", status=404, error="not_found")
        return jsonify(payload), 200

    @app.route("/api/user/sources/import", methods=["OPTIONS"])
    def user_sources_import_preflight() -> tuple[str, int]:
        return "", 204

    @app.post("/api/user/sources/import")
    def user_sources_import() -> tuple[object, int]:
        claims, auth_err = _require_auth_claims()
        if auth_err is not None:
            return auth_err
        assert claims is not None
        user_id, sub_err = _required_sub(claims)
        if sub_err is not None:
            return sub_err
        assert user_id is not None

        try:
            raw_b = request.get_data(cache=False)
            raw = raw_b.decode("utf-8") if raw_b else ""
            payload = json.loads(raw) if raw.strip() else None
        except json.JSONDecodeError as e:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "invalid_json",
                        "message": f"Invalid JSON: {e}",
                    }
                ),
                400,
            )

        if not isinstance(payload, dict):
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "invalid_body",
                        "message": "Request body must be a JSON object.",
                    }
                ),
                400,
            )

        try:
            supabase_settings()
        except ValueError as e:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "server_misconfigured",
                        "message": str(e),
                    }
                ),
                503,
            )

        try:
            sb = create_supabase_client()
            summary = import_user_sources_catalog(sb, user_id, payload)
        except ValueError as e:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "validation_error",
                        "message": str(e),
                    }
                ),
                400,
            )
        except RuntimeError as e:
            logger.warning("user sources import failed: %s", e)
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "import_failed",
                        "message": str(e),
                    }
                ),
                500,
            )

        return (
            jsonify(
                {
                    "ok": True,
                    "summary": summary.to_json_dict(),
                }
            ),
            200,
        )

    @app.route("/api/pipeline/run", methods=["OPTIONS"])
    def pipeline_run_preflight() -> tuple[str, int]:
        return "", 204

    @app.post("/api/pipeline/run")
    def pipeline_run_start() -> tuple[object, int]:
        claims, auth_err = _require_auth_claims()
        if auth_err is not None:
            return auth_err
        assert claims is not None
        auth_user_id, sub_err = _required_sub(claims)
        if sub_err is not None:
            return sub_err
        assert auth_user_id is not None

        body = request.get_json(silent=True)
        if body is None:
            return _json_error("Body must be a JSON object.", status=400)
        if not isinstance(body, dict):
            return _json_error("Body must be a JSON object.", status=400)

        params, parse_err = _parse_pipeline_run_request(body=body, auth_user_id=auth_user_id)
        if parse_err is not None:
            return parse_err
        assert params is not None

        job = start_pipeline_job(params=params)
        return (
            jsonify(
                {
                    "ok": True,
                    "job_id": job["job_id"],
                    "status": job["status"],
                }
            ),
            202,
        )

    @app.route("/api/pipeline/run/<job_id>", methods=["OPTIONS"])
    def pipeline_run_status_preflight(job_id: str) -> tuple[str, int]:
        _ = job_id
        return "", 204

    @app.get("/api/pipeline/run/<job_id>")
    def pipeline_run_status(job_id: str) -> tuple[object, int]:
        claims, auth_err = _require_auth_claims()
        if auth_err is not None:
            return auth_err
        assert claims is not None
        auth_user_id, sub_err = _required_sub(claims)
        if sub_err is not None:
            return sub_err
        assert auth_user_id is not None

        owner = get_pipeline_job_owner_user_id(job_id)
        if owner is None:
            return _json_error("Pipeline job not found.", status=404, error="not_found")
        if owner != auth_user_id:
            return _json_error(
                "You are not allowed to access this pipeline job.",
                status=403,
                error="forbidden",
            )

        payload = get_pipeline_job(job_id)
        if payload is None:
            return _json_error("Pipeline job not found.", status=404, error="not_found")
        return jsonify(payload), 200

    @app.route("/api/pipeline/evaluate-article", methods=["OPTIONS"])
    def pipeline_evaluate_article_preflight() -> tuple[str, int]:
        return "", 204

    @app.post("/api/pipeline/evaluate-article")
    def pipeline_evaluate_article() -> tuple[object, int]:
        claims, auth_err = _require_auth_claims()
        if auth_err is not None:
            return auth_err
        assert claims is not None
        auth_user_id, sub_err = _required_sub(claims)
        if sub_err is not None:
            return sub_err
        assert auth_user_id is not None

        body = request.get_json(silent=True)
        if body is None or not isinstance(body, dict):
            return _json_error("Body must be a JSON object.", status=400)

        parsed, parse_err = _parse_evaluate_article_request(body=body)
        if parse_err is not None:
            return parse_err
        assert parsed is not None

        supabase_client = create_supabase_client()
        try:
            decision = evaluate_single_article_from_db(
                supabase_client=supabase_client,
                user_id=auth_user_id,
                category_id=parsed["category_id"],
                url=parsed["url"],
                article_id=parsed["article_id"],
                instructions_override=parsed["instructions_override"],
                persist=parsed["persist"],
                http_timeout=parsed["timeout"],
                content_max_chars=parsed["content_max_chars"],
            )
        except ValueError as e:
            return _json_error(str(e), status=400)
        except LookupError as e:
            return _json_error(str(e), status=404, error="not_found")
        except RuntimeError as e:
            return _json_error(str(e), status=500)

        payload = {
            "ok": True,
            "included": decision["included"],
            "why": decision["reason"],
            "url": decision["url"],
            "title": decision["title"],
            "date": decision["date"],
            "source": decision["source"],
            "short_summary": decision["short_summary"],
            "full_summary": decision["full_summary"],
            "persisted": decision["persisted"],
            "instruction_source": decision["instruction_source"],
        }
        persist_error = decision.get("persist_error")
        if isinstance(persist_error, str) and persist_error.strip():
            payload["persist_error"] = persist_error
        return jsonify(payload), 200

    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    load_dotenv_if_present()
    groq_api_key()
    assert_resolve_api_supabase_auth_config()
    app = create_app()
    port = int(os.environ.get("RESOLVE_API_PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
