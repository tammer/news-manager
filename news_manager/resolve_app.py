"""Flask app: POST /api/sources/resolve (JWT required)."""

from __future__ import annotations

import logging
import os

import jwt
from flask import Flask, jsonify, request

from news_manager.auth_supabase import verify_supabase_jwt
from news_manager.config import groq_api_key, load_dotenv_if_present, supabase_jwt_secret
from news_manager.source_resolve import resolve_source_json_body

logger = logging.getLogger(__name__)


def create_app() -> Flask:
    app = Flask(__name__)

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
    supabase_jwt_secret()
    app = create_app()
    port = int(os.environ.get("RESOLVE_API_PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
