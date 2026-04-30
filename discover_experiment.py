#!/usr/bin/env python3
"""Simple discovery endpoint experiment helper.

Flow:
1) Prompt for Supabase password and fetch an access token.
2) Prompt for a discovery intent.
3) Start discovery job on the API.
4) Poll until the job is complete.
5) Print final payload.
"""

from __future__ import annotations

import getpass
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

SUPABASE_URL = "https://uaizrqhyomcgaowjetyd.supabase.co"
SUPABASE_PUBLISHABLE_KEY = "sb_publishable_DSZ2FtoAtzUbMitch1yaMA_L_P1CsPK"
SUPABASE_EMAIL = "tammer@tammer.com"


def _http_json(method: str, url: str, headers: dict[str, str], body: dict | None = None) -> dict:
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url=url, data=data, method=method)
    for key, value in headers.items():
        request.add_header(key, value)

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {url} failed with {exc.code}: {details}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"{method} {url} failed: {exc.reason}") from exc


def get_supabase_access_token(password: str) -> str:
    token_url = (
        f"{SUPABASE_URL}/auth/v1/token?"
        + urllib.parse.urlencode({"grant_type": "password"})
    )
    payload = {
        "email": SUPABASE_EMAIL,
        "password": password,
    }
    headers = {
        "apikey": SUPABASE_PUBLISHABLE_KEY,
        "Content-Type": "application/json",
    }
    response = _http_json("POST", token_url, headers=headers, body=payload)
    access_token = response.get("access_token")
    if not access_token:
        raise RuntimeError(f"Supabase token response missing access_token: {response}")
    return access_token


def start_discovery_job(api_base_url: str, access_token: str, intent: str) -> str:
    url = f"{api_base_url.rstrip('/')}/api/sources/discover"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    response = _http_json("POST", url, headers=headers, body={"query": intent})
    if not response.get("ok"):
        raise RuntimeError(f"Discovery start failed: {response}")
    job_id = response.get("job_id")
    if not isinstance(job_id, str) or not job_id:
        raise RuntimeError(f"Discovery response missing job_id: {response}")
    return job_id


def poll_discovery_job(api_base_url: str, access_token: str, job_id: str) -> dict:
    url = f"{api_base_url.rstrip('/')}/api/sources/discover/{job_id}"
    headers = {
        "Authorization": f"Bearer {access_token}",
    }
    while True:
        response = _http_json("GET", url, headers=headers)
        status = response.get("status")
        print(f"Job {job_id} status: {status}")
        if status == "succeeded":
            return response
        if status == "failed":
            raise RuntimeError(f"Discovery job failed: {response}")
        if status not in {"queued", "running"}:
            raise RuntimeError(f"Unexpected discovery job status: {response}")
        time.sleep(2)


def main() -> int:
    print("Supabase URL:", SUPABASE_URL)
    print("Email:", SUPABASE_EMAIL)
    api_base_url = (
        input("Discovery API base URL [http://127.0.0.1:8080]: ").strip()
        or "http://127.0.0.1:8080"
    )
    password = getpass.getpass("Supabase password: ")
    intent = input("Discovery intent: ").strip()
    if not intent:
        print("Intent is required.")
        return 1

    try:
        print("Getting Supabase access token...")
        access_token = get_supabase_access_token(password)
        print("Starting discovery job...")
        job_id = start_discovery_job(api_base_url, access_token, intent)
        print("Started job:", job_id)
        final_payload = poll_discovery_job(api_base_url, access_token, job_id)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print("\nFinal payload:")
    print(json.dumps(final_payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
