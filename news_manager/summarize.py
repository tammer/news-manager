"""Filter and summarize articles using Groq."""

from __future__ import annotations

import json
import logging
import re
import sys
from dataclasses import dataclass
from typing import Any, Literal

from news_manager.config import DEFAULT_CONTENT_MAX_CHARS, groq_model
from news_manager.llm import get_client
from news_manager.models import OutputArticle, RawArticle

logger = logging.getLogger(__name__)


@dataclass
class SummarizeOutcome:
    """Result of processing one article (for caching and progress)."""

    output: OutputArticle | None
    outcome: Literal["included", "excluded", "error"]
    why: str | None = None

_JSON_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)

_Decision = Literal["included", "excluded", "error"]


def _one_line_title(title: str) -> str:
    return " ".join(title.split()).strip() or "(no title)"


def _emit_decision(title: str, decision: _Decision) -> None:
    """Progress to stderr so stdout stays free for optional future piping."""
    print(f"[{decision}] {_one_line_title(title)}", file=sys.stderr)


def _maybe_emit_stderr(emit_stderr: bool, title: str, decision: _Decision) -> None:
    if emit_stderr:
        _emit_decision(title, decision)


def emit_cached_decision(decision: Literal["included", "excluded"], title: str) -> None:
    """Deprecated: disk cache removed; kept for any external callers."""
    print(
        f"[cached] [{decision}] {_one_line_title(title)}",
        file=sys.stderr,
    )


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def _normalize_why(why_raw: Any, *, fallback: str) -> str:
    if not isinstance(why_raw, str):
        why_raw = str(why_raw) if why_raw is not None else ""
    why_one = " ".join(why_raw.split()).strip()
    return why_one or fallback


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


def _summarize_only(
    article: RawArticle,
    *,
    category: str,
    instructions: str,
    content_max_chars: int,
    source: str,
    emit_stderr: bool = True,
) -> SummarizeOutcome:
    """LLM: summaries only (no include/exclude). All articles that succeed are kept."""
    client = get_client()
    model = groq_model()
    body = _truncate(article.content, content_max_chars)
    system = (
        "You are a careful news assistant. Summarize the article for the user. "
        "Respond with a single JSON object only, no other text."
    )
    user = f"""Context (category "{category}"; instructions are for tone/focus only — include every article):

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
  "short_summary": "<about 25 words>",
  "full_summary": "<about 200 words>"
}}
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
        _maybe_emit_stderr(emit_stderr, title, "error")
        return SummarizeOutcome(output=None, outcome="error")

    choice = resp.choices[0].message.content
    if not choice:
        logger.warning("Empty Groq response for %s", article.url)
        _maybe_emit_stderr(emit_stderr, title, "error")
        return SummarizeOutcome(output=None, outcome="error")

    data = _parse_json_response(choice)
    if data is None:
        logger.warning("Could not parse JSON from model for %s: %r", article.url, choice[:500])
        _maybe_emit_stderr(emit_stderr, title, "error")
        return SummarizeOutcome(output=None, outcome="error")

    short_s = data.get("short_summary", "")
    full_s = data.get("full_summary", "")
    if not isinstance(short_s, str):
        short_s = str(short_s)
    if not isinstance(full_s, str):
        full_s = str(full_s)

    _maybe_emit_stderr(emit_stderr, title, "included")
    out = OutputArticle(
        title=article.title,
        date=article.date,
        content=article.content,
        url=article.url,
        short_summary=short_s.strip(),
        full_summary=full_s.strip(),
        source=source,
    )
    return SummarizeOutcome(
        output=out,
        outcome="included",
        why="Included because filtering is disabled for this source.",
    )


def filter_and_summarize_outcome(
    article: RawArticle,
    *,
    category: str,
    instructions: str,
    content_max_chars: int = DEFAULT_CONTENT_MAX_CHARS,
    apply_filter: bool = True,
    source: str = "",
    emit_stderr: bool = True,
) -> SummarizeOutcome:
    """
    One LLM call: filter+summarize, or summarize only (apply_filter False).
    Use this when you need included vs excluded vs error (e.g. caching).
    """
    cleaned_instructions = instructions.strip()
    if not apply_filter or not cleaned_instructions:
        return _summarize_only(
            article,
            category=category,
            instructions=cleaned_instructions,
            content_max_chars=content_max_chars,
            source=source,
            emit_stderr=emit_stderr,
        )

    client = get_client()
    model = groq_model()
    body = _truncate(article.content, content_max_chars)

    system = (
        "You are a careful news assistant. You filter and summarize articles "
        "according to the user's instructions. "
        "Respond with a single JSON object only, no other text."
    )
    user = f"""USER_INSTRUCTIONS:

{cleaned_instructions}

---

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
  "full_summary": "<about 200 words if include is true, else empty string>",
  "why": "<very concise reason for the decision. If include is false: why filtered out. If include is true: why it matches the instructions>"
}}

Always provide a non-empty "why" with one clear sentence tied to the instructions and article content.
If the article does not match what the user wants, set include to false.
The "full_summary" should summarize the article and make no reference to the user's instructions.
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
        _maybe_emit_stderr(emit_stderr, title, "error")
        return SummarizeOutcome(output=None, outcome="error")

    choice = resp.choices[0].message.content
    if not choice:
        logger.warning("Empty Groq response for %s", article.url)
        _maybe_emit_stderr(emit_stderr, title, "error")
        return SummarizeOutcome(output=None, outcome="error")

    data = _parse_json_response(choice)
    if data is None:
        logger.warning("Could not parse JSON from model for %s: %r", article.url, choice[:500])
        _maybe_emit_stderr(emit_stderr, title, "error")
        return SummarizeOutcome(output=None, outcome="error")

    include = data.get("include")
    if include is not True:
        why_one = _normalize_why(data.get("why"), fallback="Excluded by filter.")
        _maybe_emit_stderr(emit_stderr, title, "excluded")
        return SummarizeOutcome(output=None, outcome="excluded", why=why_one)

    short_s = data.get("short_summary", "")
    full_s = data.get("full_summary", "")
    if not isinstance(short_s, str):
        short_s = str(short_s)
    if not isinstance(full_s, str):
        full_s = str(full_s)

    _maybe_emit_stderr(emit_stderr, title, "included")
    out = OutputArticle(
        title=article.title,
        date=article.date,
        content=article.content,
        url=article.url,
        short_summary=short_s.strip(),
        full_summary=full_s.strip(),
        source=source,
    )
    why_one = _normalize_why(data.get("why"), fallback="Included by filter.")
    return SummarizeOutcome(output=out, outcome="included", why=why_one)


def filter_and_summarize(
    article: RawArticle,
    *,
    category: str,
    instructions: str,
    content_max_chars: int = DEFAULT_CONTENT_MAX_CHARS,
    apply_filter: bool = True,
    source: str = "",
) -> OutputArticle | None:
    """
    One LLM call: either filter+summarize, or summarize only (when apply_filter is False).
    Returns None if the article is excluded (filter mode) or on LLM/parse error.
    """
    return filter_and_summarize_outcome(
        article,
        category=category,
        instructions=instructions,
        content_max_chars=content_max_chars,
        apply_filter=apply_filter,
        source=source,
    ).output
