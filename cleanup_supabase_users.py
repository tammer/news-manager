#!/usr/bin/env python3
"""Delete all Supabase auth users except an allowlist of emails.

Usage:
  python cleanup_supabase_users.py                 # dry-run (default)
  python cleanup_supabase_users.py --apply         # actually delete
  python cleanup_supabase_users.py --apply --yes   # skip confirmation prompt

Required environment variables:
  SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

import httpx
from dotenv import load_dotenv


KEEP_EMAILS = {"tammer@tammer.com", "thomas@thomas.com"}


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value


def _list_auth_users(*, base_url: str, service_role_key: str, per_page: int = 200) -> list[dict[str, Any]]:
    headers = {
        "apikey": service_role_key,
        "Authorization": f"Bearer {service_role_key}",
    }
    users: list[dict[str, Any]] = []
    page = 1

    with httpx.Client(timeout=30.0) as http:
        while True:
            url = f"{base_url}/auth/v1/admin/users?page={page}&per_page={per_page}"
            resp = http.get(url, headers=headers)
            resp.raise_for_status()
            body = resp.json()
            chunk = body.get("users") or []
            if not isinstance(chunk, list):
                raise RuntimeError("Unexpected response shape from auth admin API.")
            users.extend([u for u in chunk if isinstance(u, dict)])
            if len(chunk) < per_page:
                break
            page += 1

    return users


def _delete_auth_user(*, base_url: str, service_role_key: str, user_id: str) -> None:
    headers = {
        "apikey": service_role_key,
        "Authorization": f"Bearer {service_role_key}",
    }
    with httpx.Client(timeout=30.0) as http:
        resp = http.delete(f"{base_url}/auth/v1/admin/users/{user_id}", headers=headers)
        resp.raise_for_status()


def _normalized_email(user: dict[str, Any]) -> str:
    email = user.get("email")
    if not isinstance(email, str):
        return ""
    return email.strip().lower()


def _select_users_to_delete(users: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for user in users:
        if _normalized_email(user) not in KEEP_EMAILS:
            out.append(user)
    return out


def _fmt_user(user: dict[str, Any]) -> str:
    user_id = str(user.get("id", "<no-id>"))
    email = _normalized_email(user) or "<no-email>"
    return f"{email} ({user_id})"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Delete Supabase users except a fixed email allowlist."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete users. Default behavior is dry-run.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation prompt when --apply is used.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])

    load_dotenv()
    base_url = _require_env("SUPABASE_URL").rstrip("/")
    service_role_key = _require_env("SUPABASE_SERVICE_ROLE_KEY")

    users = _list_auth_users(base_url=base_url, service_role_key=service_role_key)
    targets = _select_users_to_delete(users)

    print(f"Total auth users: {len(users)}")
    print(f"Keep allowlist: {', '.join(sorted(KEEP_EMAILS))}")
    print(f"Users to delete: {len(targets)}")
    for user in targets:
        print(f" - {_fmt_user(user)}")

    if not args.apply:
        print("\nDry run complete. Re-run with --apply to delete.")
        return 0

    if not args.yes:
        answer = input("\nProceed with deletion? Type 'delete' to continue: ").strip().lower()
        if answer != "delete":
            print("Aborted.")
            return 1

    deleted = 0
    for user in targets:
        user_id = user.get("id")
        if not isinstance(user_id, str) or not user_id.strip():
            print(f" ! Skipping malformed user record: {_fmt_user(user)}")
            continue
        _delete_auth_user(
            base_url=base_url,
            service_role_key=service_role_key,
            user_id=user_id,
        )
        deleted += 1
        print(f"Deleted: {_fmt_user(user)}")

    print(f"\nDone. Deleted {deleted} user(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
