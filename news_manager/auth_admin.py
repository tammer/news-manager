"""Supabase GoTrue admin API helpers (service role)."""

from __future__ import annotations

import json
from typing import Any

import httpx


class AuthAdminError(RuntimeError):
    """GoTrue admin API failure."""


class AuthAdminUnauthorized(AuthAdminError):
    """HTTP 401 from GoTrue admin (check service role key)."""


class AuthAdminDuplicateEmail(AuthAdminError):
    """User with this email already exists."""


def create_auth_user_with_password(
    *,
    supabase_url: str,
    service_role_key: str,
    email: str,
    password: str,
) -> str:
    """
    Create a confirmed email/password user via ``POST /auth/v1/admin/users``.

    Returns the new ``auth.users`` UUID string.
    """
    base = supabase_url.strip().rstrip("/")
    em = email.strip()
    pw = password
    if not em:
        raise ValueError("email must be non-empty")
    if not pw:
        raise ValueError("password must be non-empty")
    key = service_role_key.strip()
    if not key:
        raise ValueError("service role key must be non-empty")

    url = f"{base}/auth/v1/admin/users"
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    body: dict[str, Any] = {
        "email": em,
        "password": pw,
        "email_confirm": True,
    }

    try:
        with httpx.Client(timeout=30.0) as http:
            resp = http.post(url, headers=headers, json=body)
    except httpx.RequestError as e:
        raise AuthAdminError(f"Auth admin request failed: {e}") from e

    if resp.status_code == 401:
        raise AuthAdminUnauthorized(
            "Auth admin returned 401 (check SUPABASE_SERVICE_ROLE_KEY)."
        )

    if resp.status_code == 409:
        raise AuthAdminDuplicateEmail(
            f"Auth user already exists for email {em!r} (HTTP 409)."
        )

    if resp.status_code == 422 and _response_suggests_duplicate_email(resp):
        raise AuthAdminDuplicateEmail(
            f"Auth user already exists for email {em!r} (HTTP 422)."
        )

    if resp.status_code not in (200, 201):
        snippet = (resp.text or "")[:500]
        raise AuthAdminError(
            f"Auth admin create user failed: HTTP {resp.status_code}: {snippet}"
        )

    try:
        data = resp.json()
    except json.JSONDecodeError as e:
        raise AuthAdminError("Auth admin returned non-JSON body.") from e

    if not isinstance(data, dict):
        raise AuthAdminError("Auth admin response must be a JSON object.")

    uid = data.get("id")
    if uid is None:
        raise AuthAdminError("Auth admin response missing 'id'.")
    return str(uid)


def _response_suggests_duplicate_email(resp: httpx.Response) -> bool:
    text = (resp.text or "").lower()
    if "already" in text or "registered" in text or "exists" in text:
        return True
    try:
        data = resp.json()
    except json.JSONDecodeError:
        return False
    if not isinstance(data, dict):
        return False
    code = data.get("error_code") or data.get("code")
    if isinstance(code, str) and "already" in code.lower():
        return True
    msg = data.get("msg") or data.get("message") or data.get("error_description")
    if isinstance(msg, str) and ("already" in msg.lower() or "registered" in msg.lower()):
        return True
    return False
