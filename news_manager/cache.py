"""Disk-backed cache for processed articles (skip re-fetch and re-LLM on repeat runs)."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Literal

from news_manager.models import OutputArticle

logger = logging.getLogger(__name__)

CACHE_FILE_VERSION = 1

CacheStatus = Literal["included", "excluded"]

DEFAULT_CACHE_PATH = Path(".news-manager-cache.json")


def cache_key(
    url: str,
    category: str,
    instructions: str,
    apply_filter: bool,
) -> str:
    """Stable key for URL + processing context (instructions change invalidates)."""
    payload = f"{url}\n{category}\n{instructions}\n{apply_filter!s}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _output_article_to_dict(a: OutputArticle) -> dict[str, Any]:
    return {
        "title": a.title,
        "date": a.date,
        "content": a.content,
        "url": a.url,
        "short_summary": a.short_summary,
        "full_summary": a.full_summary,
    }


def _dict_to_output_article(d: dict[str, Any]) -> OutputArticle:
    return OutputArticle(
        title=str(d["title"]),
        date=d.get("date"),
        content=str(d["content"]),
        url=str(d["url"]),
        short_summary=str(d["short_summary"]),
        full_summary=str(d["full_summary"]),
    )


class ArticleCache:
    """
    JSON file: { "version": 1, "entries": { hex_key: { "status", "article"? } } }
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._entries: dict[str, dict[str, Any]] = {}
        self._dirty = False
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Could not load cache %s: %s — starting empty", self.path, e)
            return
        if not isinstance(raw, dict):
            return
        if raw.get("version") != CACHE_FILE_VERSION:
            logger.warning("Cache file version mismatch; ignoring %s", self.path)
            return
        ent = raw.get("entries")
        if isinstance(ent, dict):
            self._entries = {k: v for k, v in ent.items() if isinstance(v, dict)}

    def lookup(
        self,
        url: str,
        category: str,
        instructions: str,
        apply_filter: bool,
    ) -> tuple[CacheStatus, OutputArticle | None] | None:
        """Return (status, article if included) or None if miss."""
        k = cache_key(url, category, instructions, apply_filter)
        rec = self._entries.get(k)
        if not rec:
            return None
        st = rec.get("status")
        if st == "excluded":
            return ("excluded", None)
        if st == "included":
            ad = rec.get("article")
            if not isinstance(ad, dict):
                return None
            try:
                return ("included", _dict_to_output_article(ad))
            except (KeyError, TypeError, ValueError):
                return None
        return None

    def put(
        self,
        url: str,
        category: str,
        instructions: str,
        apply_filter: bool,
        status: CacheStatus,
        article: OutputArticle | None,
    ) -> None:
        k = cache_key(url, category, instructions, apply_filter)
        if status == "included" and article is not None:
            self._entries[k] = {
                "status": "included",
                "article": _output_article_to_dict(article),
            }
        elif status == "excluded":
            self._entries[k] = {"status": "excluded"}
        else:
            return
        self._dirty = True

    def save(self) -> None:
        if not self._dirty:
            return
        payload = {
            "version": CACHE_FILE_VERSION,
            "entries": self._entries,
        }
        text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                delete=False,
                dir=self.path.parent,
                prefix=".news-manager-cache-",
                suffix=".tmp",
            ) as f:
                f.write(text)
                tmp_path = f.name
            os.replace(tmp_path, self.path)
        except OSError as e:
            logger.warning("Failed to write cache: %s", e)
            if tmp_path is not None:
                try:
                    Path(tmp_path).unlink(missing_ok=True)
                except OSError:
                    pass
            return
        self._dirty = False
