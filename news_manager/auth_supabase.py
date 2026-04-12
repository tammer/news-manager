"""Verify Supabase-issued JWTs (HS256, user session)."""

from __future__ import annotations

import jwt

from news_manager.config import supabase_jwt_secret


def verify_supabase_jwt(token: str) -> dict:
    """
    Decode and validate a Supabase access token from the browser/client.
    Raises jwt.PyJWTError on failure.
    """
    secret = supabase_jwt_secret()
    return jwt.decode(
        token,
        secret,
        algorithms=["HS256"],
        audience="authenticated",
        options={"require": ["exp", "sub"]},
    )
