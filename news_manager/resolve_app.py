"""Flask app: POST /api/sources/resolve (JWT required)."""

from __future__ import annotations

import hmac
import json
import logging
import os

import jwt
from flask import Flask, jsonify, request

from news_manager.auth_admin import (
    AuthAdminDuplicateEmail,
    AuthAdminError,
    AuthAdminUnauthorized,
    create_auth_user_with_password,
)
from news_manager.auth_supabase import verify_supabase_jwt
from news_manager.config import (
    assert_resolve_api_supabase_auth_config,
    groq_api_key,
    load_default_user_catalog_dict,
    load_dotenv_if_present,
    news_manager_admin_api_key_optional,
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


def _admin_bearer_matches(header: str, *, expected: str) -> bool:
    if not header.startswith("Bearer "):
        return False
    token = header[7:].strip()
    if not token or not expected:
        return False
    if len(token) != len(expected):
        return False
    return hmac.compare_digest(token, expected)


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

    @app.route("/api/admin/users", methods=["OPTIONS"])
    def admin_create_user_preflight() -> tuple[str, int]:
        return "", 204

    @app.post("/api/admin/users")
    def admin_create_user() -> tuple[object, int]:
        expected_key = news_manager_admin_api_key_optional()
        if not expected_key:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "server_misconfigured",
                        "message": "NEWS_MANAGER_ADMIN_API_KEY is not set.",
                    }
                ),
                503,
            )

        auth = request.headers.get("Authorization", "")
        if not _admin_bearer_matches(auth, expected=expected_key):
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "unauthorized",
                        "message": "Invalid or missing admin Authorization Bearer token.",
                    }
                ),
                401,
            )

        try:
            raw_b = request.get_data(cache=False)
            raw = raw_b.decode("utf-8") if raw_b else ""
            body = json.loads(raw) if raw.strip() else None
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

        if not isinstance(body, dict):
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

        email_raw = body.get("email")
        password_raw = body.get("password")
        if not isinstance(email_raw, str) or not email_raw.strip():
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "validation_error",
                        "message": "Field 'email' must be a non-empty string.",
                    }
                ),
                400,
            )
        if not isinstance(password_raw, str) or not password_raw:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "validation_error",
                        "message": "Field 'password' must be a non-empty string.",
                    }
                ),
                400,
            )
        email = email_raw.strip()
        password = password_raw
        if len(password) < 8:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "validation_error",
                        "message": "Field 'password' must be at least 8 characters.",
                    }
                ),
                400,
            )

        try:
            url, service_key = supabase_settings()
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
            catalog = load_default_user_catalog_dict()
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
            user_id = create_auth_user_with_password(
                supabase_url=url,
                service_role_key=service_key,
                email=email,
                password=password,
            )
        except AuthAdminDuplicateEmail as e:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "user_exists",
                        "message": str(e),
                    }
                ),
                409,
            )
        except AuthAdminUnauthorized as e:
            logger.warning("admin create user: auth admin unauthorized: %s", e)
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "auth_admin_failed",
                        "message": str(e),
                    }
                ),
                502,
            )
        except AuthAdminError as e:
            logger.warning("admin create user: auth admin failed: %s", e)
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "auth_admin_failed",
                        "message": str(e),
                    }
                ),
                502,
            )

        try:
            sb = create_supabase_client()
            summary = import_user_sources_catalog(sb, user_id, catalog)
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
            logger.warning("admin create user: catalog import failed: %s", e)
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
                    "user_id": user_id,
                    "email": email,
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
