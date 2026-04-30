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
from typing import Any

SUPABASE_URL = "https://uaizrqhyomcgaowjetyd.supabase.co"
SUPABASE_PUBLISHABLE_KEY = "sb_publishable_DSZ2FtoAtzUbMitch1yaMA_L_P1CsPK"
SUPABASE_EMAIL = "tammer@tammer.com"
DISCOVERY_API_BASE_URL = "http://127.0.0.1:8080"


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


def extract_base_domain(raw_url: str) -> str:
    candidate = (raw_url or "").strip()
    if not candidate:
        return ""
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    parsed = urllib.parse.urlparse(candidate)
    host = (parsed.netloc or "").lower()
    if "@" in host:
        host = host.split("@", 1)[1]
    if ":" in host:
        host = host.split(":", 1)[0]
    if host.startswith("www."):
        host = host[4:]
    return host


def get_user_sources(access_token: str) -> list[dict[str, Any]]:
    url = f"{SUPABASE_URL}/rest/v1/sources?select=url"
    headers = {
        "apikey": SUPABASE_PUBLISHABLE_KEY,
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
    response = _http_json("GET", url, headers=headers)
    if not isinstance(response, list):
        raise RuntimeError(f"Unexpected sources response shape: {response}")
    return response


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
    password = sys.argv[1] if len(sys.argv) > 1 else getpass.getpass("Supabase password: ")
    intent = input("Discovery intent: ").strip()
    if not intent:
        print("Intent is required.")
        return 1

    try:
        print("Getting Supabase access token...")
        access_token = get_supabase_access_token(password)
        print("Loading current user sources...")
        user_sources = get_user_sources(access_token)
        existing_domains: dict[str, list[str]] = {}
        for source in user_sources:
            source_url = str(source.get("url", "")).strip()
            domain = extract_base_domain(source_url)
            if not domain:
                continue
            existing_domains.setdefault(domain, []).append(source_url)

        print("Starting discovery job...")
        job_id = start_discovery_job(DISCOVERY_API_BASE_URL, access_token, intent)
        print("Started job:", job_id)
        final_payload = poll_discovery_job(DISCOVERY_API_BASE_URL, access_token, job_id)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    result = final_payload.get("result")
    suggestions = result.get("suggestions") if isinstance(result, dict) else None
    if isinstance(suggestions, list):
        for suggestion in suggestions:
            if not isinstance(suggestion, dict):
                continue
            suggestion_url = str(suggestion.get("url", "")).strip()
            suggestion_domain = extract_base_domain(suggestion_url)
            matches = existing_domains.get(suggestion_domain, [])
            suggestion["base_domain"] = suggestion_domain
            suggestion["already_in_user_sources"] = bool(matches)
            suggestion["matching_user_source_urls"] = matches

    print("\nFinal payload:")
    print(json.dumps(final_payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
