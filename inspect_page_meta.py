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
    "is an a) article (blog post or news story) b) home page (blog home page or news home page) c) other. "
    "You must pick one of these four categories: blog home, news home, article, other. "
    "You will also determine if the primary theme of the page is related to the theme of 'book reviews'."
    'Respond with JSON only in this exact shape: {"class":"blog home|news home|article|other","reasoning":"...","on_theme":"yes|no|unknown"}'
)

ALLOWED_CLASSES = {"blog home", "news home", "article", "other"}
ALLOWED_ON_THEME = {"yes", "no", "unknown"}


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
    reply = reply.strip()

    try:
        parsed = json.loads(reply)
    except json.JSONDecodeError:
        print(reply)
        print("LLM response was not valid JSON", file=sys.stderr)
        sys.exit(1)

    if not isinstance(parsed, dict):
        print(parsed)
        print("LLM JSON must be an object", file=sys.stderr)
        sys.exit(1)

    klass = parsed.get("class")
    reasoning = parsed.get("reasoning")
    on_theme = parsed.get("on_theme")
    if (
        not isinstance(klass, str)
        or not isinstance(reasoning, str)
        or not isinstance(on_theme, str)
    ):
        print(
            'LLM JSON must contain string fields "class", "reasoning", and "on_theme"',
            file=sys.stderr,
        )
        sys.exit(1)

    klass = klass.strip().lower()
    reasoning = reasoning.strip()
    on_theme = on_theme.strip().lower()
    if klass not in ALLOWED_CLASSES:
        print(reply)
        print(
            'LLM "class" must be one of: "blog home", "news home", "article", "other"',
            file=sys.stderr,
        )
        sys.exit(1)
    if on_theme not in ALLOWED_ON_THEME:
        print(reply)
        print('LLM "on_theme" must be one of: "yes", "no", "unknown"', file=sys.stderr)
        sys.exit(1)

    output = {"class": klass, "reasoning": reasoning, "on_theme": on_theme}
    print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()