"""Prompt templates used by source discovery."""

from __future__ import annotations

DISCOVERY_CLASSIFICATION_ALLOWED = frozenset({"blog home", "news home", "article", "other"})

DISCOVERY_CLASSIFICATION_PROMPT = """You are a web-page classifier for source discovery.

The user message supplies:
- intent
- page URL
- page title text
- page meta tags (name/property/http-equiv + content values)

Classify the page into exactly one class:
- "blog home": homepage or section landing page for a blog-style publication
- "news home": homepage or section landing page for a news publication
- "article": a single article/post/story page
- "other": anything else

Use intent relevance as a strong signal:
- Prefer "other" when the page is clearly off-intent.
- For homepages, prefer "blog home" or "news home" only when the publication likely matches intent.

Output JSON only in this exact shape:
{"classification":"blog home|news home|article|other","reason":"brief explanation"}
"""


def build_discovery_classification_user_prompt(
    *,
    intent: str,
    url: str,
    page_title: str,
    meta_tags: list[dict[str, str]],
) -> str:
    return (
        f"Intent: {intent}\n"
        f"URL: {url}\n"
        f"Title: {page_title or '(empty)'}\n"
        f"Meta tags JSON: {meta_tags}"
    )
