"""Flask app: POST /api/sources/resolve (JWT required)."""

from __future__ import annotations

import json
import logging
import os

import jwt
from flask import Flask, jsonify, request

from news_manager.auth_supabase import verify_supabase_jwt
from news_manager.config import (
    assert_resolve_api_supabase_auth_config,
    groq_api_key,
    load_dotenv_if_present,
    supabase_settings,
)
from news_manager.source_resolve import resolve_source_json_body
from news_manager.supabase_sync import create_supabase_client
from news_manager.user_sources_catalog import import_user_sources_catalog

logger = logging.getLogger(__name__)


def _normalize_origin(value: str | None) -> str | None:
    if not value:
        return None
    return value.rstrip("/")


def _allowed_cors_origins() -> frozenset[str]:
    raw = os.environ.get("RESOLVE_CORS_ORIGIN", "http://localhost:5173").strip()
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return frozenset(_normalize_origin(p) for p in parts)


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
            response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
            response.headers["Access-Control-Max-Age"] = "86400"
        return response

    @app.route("/api/sources/resolve", methods=["OPTIONS"])
    def resolve_preflight() -> tuple[str, int]:
        return "", 204

    @app.post("/api/sources/resolve")
    def resolve() -> tuple[object, int]:
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
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
        token = auth[7:].strip()
        if not token:
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
        try:
            verify_supabase_jwt(token)
        except jwt.PyJWTError as e:
            logger.debug("JWT verification failed: %s", e)
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

        payload, status = resolve_source_json_body(request.get_data())
        return jsonify(payload), status

    @app.route("/api/user/sources/import", methods=["OPTIONS"])
    def user_sources_import_preflight() -> tuple[str, int]:
        return "", 204

    @app.post("/api/user/sources/import")
    def user_sources_import() -> tuple[object, int]:
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "unauthorized",
                        "message": "Authorization Bearer token required.",
                    }
                ),
                401,
            )
        token = auth[7:].strip()
        if not token:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "unauthorized",
                        "message": "Authorization Bearer token required.",
                    }
                ),
                401,
            )
        try:
            claims = verify_supabase_jwt(token)
        except jwt.PyJWTError as e:
            logger.debug("JWT verification failed: %s", e)
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "unauthorized",
                        "message": "Invalid or expired token.",
                    }
                ),
                401,
            )

        sub = claims.get("sub")
        if not isinstance(sub, str) or not sub.strip():
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "invalid_token",
                        "message": "Token missing subject (sub).",
                    }
                ),
                401,
            )
        user_id = sub.strip()

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

    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    load_dotenv_if_present()
    groq_api_key()
    assert_resolve_api_supabase_auth_config()
    app = create_app()
    port = int(os.environ.get("RESOLVE_API_PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
