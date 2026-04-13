"""Flask app: POST /api/sources/resolve (JWT required)."""

from __future__ import annotations

import logging
import os

import jwt
from flask import Flask, jsonify, request

from news_manager.auth_supabase import verify_supabase_jwt
from news_manager.config import (
    assert_resolve_api_supabase_auth_config,
    groq_api_key,
    load_dotenv_if_present,
)
from news_manager.source_resolve import resolve_source_json_body

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

    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    load_dotenv_if_present()
    groq_api_key()
    assert_resolve_api_supabase_auth_config()
    app = create_app()
    port = int(os.environ.get("RESOLVE_API_PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
