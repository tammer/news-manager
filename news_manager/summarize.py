"""Filter and summarize articles using Groq."""

from __future__ import annotations

import json
import logging
import re
import sys
from typing import Any, Literal

from news_manager.config import DEFAULT_CONTENT_MAX_CHARS, groq_model
from news_manager.llm import get_client
from news_manager.models import OutputArticle, RawArticle

logger = logging.getLogger(__name__)

_JSON_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)

_Decision = Literal["included", "excluded", "error"]


def _one_line_title(title: str) -> str:
    return " ".join(title.split()).strip() or "(no title)"


def _emit_decision(title: str, decision: _Decision) -> None:
    """Progress to stderr so stdout stays free for optional future piping."""
    print(f"[{decision}] {_one_line_title(title)}", file=sys.stderr)


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def _parse_json_response(content: str) -> dict[str, Any] | None:
    """Parse model output: raw JSON or fenced markdown."""
    content = content.strip()
    m = _JSON_FENCE.search(content)
    if m:
        content = m.group(1).strip()
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def filter_and_summarize(
    article: RawArticle,
    *,
    category: str,
    instructions: str,
    content_max_chars: int = DEFAULT_CONTENT_MAX_CHARS,
) -> OutputArticle | None:
    """
    One LLM call: decide include/exclude and produce summaries if included.
    Returns None if the article should be omitted from output.
    """
    client = get_client()
    model = groq_model()
    body = _truncate(article.content, content_max_chars)

    system = (
        "You are a careful news assistant. You filter and summarize articles "
        "according to the user's instructions. "
        "Respond with a single JSON object only, no other text."
    )
    user = f"""USER_INSTRUCTIONS (apply to category "{category}"):

{instructions}

---

CATEGORY: {category}

ARTICLE:
Title: {article.title}
URL: {article.url}
Date: {article.date or "unknown"}

Body:
{body}

---

Respond with JSON only, using this exact shape:
{{
  "include": <true or false>,
  "short_summary": "<about 25 words if include is true, else empty string>",
  "full_summary": "<about 200 words if include is true, else empty string>"
}}

If the article does not match what the user wants for this category, set include to false.
"""

    title = article.title

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.2,
        )
    except Exception as e:
        logger.warning("Groq API error for %s: %s", article.url, e)
        _emit_decision(title, "error")
        return None

    choice = resp.choices[0].message.content
    if not choice:
        logger.warning("Empty Groq response for %s", article.url)
        _emit_decision(title, "error")
        return None

    data = _parse_json_response(choice)
    if data is None:
        logger.warning("Could not parse JSON from model for %s: %r", article.url, choice[:500])
        _emit_decision(title, "error")
        return None

    include = data.get("include")
    if include is not True:
        _emit_decision(title, "excluded")
        return None

    short_s = data.get("short_summary", "")
    full_s = data.get("full_summary", "")
    if not isinstance(short_s, str):
        short_s = str(short_s)
    if not isinstance(full_s, str):
        full_s = str(full_s)

    _emit_decision(title, "included")
    return OutputArticle(
        title=article.title,
        date=article.date,
        content=article.content,
        url=article.url,
        short_summary=short_s.strip(),
        full_summary=full_s.strip(),
    )
