#!/usr/bin/env python3
"""Fetch a URL, send <body> text and links to Groq, list recommended blogs or news sites."""

from __future__ import annotations

import argparse
import json
import re
import sys
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from news_manager.config import DEFAULT_CONTENT_MAX_CHARS, groq_model, load_dotenv_if_present
from news_manager.llm import get_client

_MAX_LINK_LINES = 200
_MAX_ANCHOR_TEXT = 240

_JSON_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)

SYSTEM_PROMPT = (
    "You analyze the visible body text and outbound links of a single web page. "
    "List blogs or news sites that this page recommends to the reader: blogrolls, "
    '"further reading", "see also", similar sites, cited publications, or other '
    "explicit suggestions to follow another editorial site. "
    "Exclude: this page's own site (same registrable domain as page_url), generic "
    "social networks and hosts (e.g. twitter.com, facebook.com, linkedin.com, "
    "youtube.com unless the page clearly recommends a specific news channel), "
    "app stores, CDN/asset URLs, and pure shopping unless clearly a news/blog recommendation. "
    'Respond with JSON only in this exact shape: '
    '{"recommended":[{"name":"short label","url":"https://... or null"}],'
    '"reasoning":"brief explanation of what on the page supported each entry"}. '
    "Use absolute https URLs copied from the provided links when available; use null "
    "for url when only a site name appears in the body. recommended may be empty."
)


def _strip_json_fence(content: str) -> str:
    content = content.strip()
    m = _JSON_FENCE.search(content)
    if m:
        return m.group(1).strip()
    return content


def _build_body_payload(html: str, page_url: str) -> dict[str, object]:
    soup = BeautifulSoup(html, "lxml")
    body = soup.body if soup.body is not None else soup
    for tag in body.find_all(["script", "style", "noscript"]):
        tag.decompose()

    text = " ".join(body.get_text(separator=" ", strip=True).split())
    text = text[:DEFAULT_CONTENT_MAX_CHARS]

    seen_href: set[str] = set()
    link_lines: list[str] = []
    for anchor in body.find_all("a", href=True):
        if len(link_lines) >= _MAX_LINK_LINES:
            break
        href = anchor.get("href")
        if not isinstance(href, str):
            continue
        raw = href.strip()
        if not raw or raw.startswith("#") or raw.lower().startswith(
            ("javascript:", "mailto:", "tel:")
        ):
            continue
        absolute = urljoin(page_url, raw)
        key = absolute.lower()
        if key in seen_href:
            continue
        seen_href.add(key)
        label = anchor.get_text(separator=" ", strip=True)
        if len(label) > _MAX_ANCHOR_TEXT:
            label = label[: _MAX_ANCHOR_TEXT - 3] + "..."
        line = f"{absolute}\t{label.replace(chr(9), ' ')}"
        link_lines.append(line)

    return {
        "page_url": page_url,
        "body_text": text,
        "outbound_links_tab_separated": link_lines,
    }


def main() -> None:
    p = argparse.ArgumentParser(
        description="Fetch a page, analyze <body> with Groq for recommended blogs/news sites."
    )
    p.add_argument("url", help="HTTPS or HTTP URL to fetch")
    args = p.parse_args()

    load_dotenv_if_present()

    try:
        r = httpx.get(
            args.url,
            follow_redirects=True,
            headers={"User-Agent": "inspect_article/1.0"},
            timeout=30.0,
        )
        r.raise_for_status()
    except httpx.HTTPError as e:
        print(f"Request failed: {e}", file=sys.stderr)
        sys.exit(1)

    final_url = str(r.url)
    payload = _build_body_payload(r.text, final_url)
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
        try:
            parsed = json.loads(_strip_json_fence(reply))
        except json.JSONDecodeError:
            print(reply)
            print("LLM response was not valid JSON", file=sys.stderr)
            sys.exit(1)

    if not isinstance(parsed, dict):
        print(reply)
        print("LLM JSON must be an object", file=sys.stderr)
        sys.exit(1)

    recommended = parsed.get("recommended")
    reasoning = parsed.get("reasoning")
    if not isinstance(recommended, list) or not isinstance(reasoning, str):
        print(reply)
        print(
            'LLM JSON must contain list "recommended" and string "reasoning"',
            file=sys.stderr,
        )
        sys.exit(1)

    cleaned: list[dict[str, str | None]] = []
    for i, item in enumerate(recommended):
        if not isinstance(item, dict):
            print(reply)
            print(f'LLM recommended[{i}] must be an object', file=sys.stderr)
            sys.exit(1)
        name = item.get("name")
        url_val = item.get("url")
        if not isinstance(name, str) or not name.strip():
            print(reply)
            print(f'LLM recommended[{i}] needs non-empty string "name"', file=sys.stderr)
            sys.exit(1)
        if url_val is not None and not isinstance(url_val, str):
            print(reply)
            print(f'LLM recommended[{i}] "url" must be a string or null', file=sys.stderr)
            sys.exit(1)
        u = url_val.strip() if isinstance(url_val, str) else None
        if u == "":
            u = None
        cleaned.append({"name": name.strip(), "url": u})

    output = {
        "page_url": final_url,
        "recommended": cleaned,
        "reasoning": reasoning.strip(),
    }
    print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
