#!/usr/bin/env python3
"""Fetch a URL, build title/meta JSON, classify with Groq (blog post vs home vs neither)."""

from __future__ import annotations

import argparse
import json
import sys

import httpx
from bs4 import BeautifulSoup

from news_manager.config import groq_model, load_dotenv_if_present
from news_manager.llm import get_client

SYSTEM_PROMPT = (
    "You are a classifer.  given a URL, meta tags and title, determine if this web page "
    "is a blog post or a blog home page or news home page or neither"
)


def main() -> None:
    p = argparse.ArgumentParser(
        description="Fetch page head metadata and classify with Groq."
    )
    p.add_argument("url", help="HTTPS or HTTP URL to fetch")
    args = p.parse_args()

    load_dotenv_if_present()

    try:
        r = httpx.get(
            args.url,
            follow_redirects=True,
            headers={"User-Agent": "inspect_page_meta/1.0"},
            timeout=30.0,
        )
        r.raise_for_status()
    except httpx.HTTPError as e:
        print(f"Request failed: {e}", file=sys.stderr)
        sys.exit(1)

    soup = BeautifulSoup(r.text, "lxml")

    el = soup.title
    title_text = el.get_text(strip=True) if el else ""

    meta_list = [dict(m.attrs) for m in soup.find_all("meta")]

    payload = {
        "title": title_text,
        "meta": meta_list,
        "url": str(r.url),
    }
    user_message = json.dumps(payload, ensure_ascii=False)

    try:
        client = get_client()
        resp = client.chat.completions.create(
            model=groq_model(),
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            temperature=0.2,
        )
    except ValueError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"LLM request failed: {e}", file=sys.stderr)
        sys.exit(1)

    reply = resp.choices[0].message.content
    if not reply:
        print("Empty LLM response", file=sys.stderr)
        sys.exit(1)

    sys.stdout.write(reply.strip())
    if not reply.endswith("\n"):
        sys.stdout.write("\n")


if __name__ == "__main__":
    main()
