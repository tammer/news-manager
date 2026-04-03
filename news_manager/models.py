"""Data models for articles and configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

SourceKind = Literal["html", "rss"]


@dataclass(frozen=True)
class Source:
    """One source entry: homepage (HTML) or RSS/Atom feed URL."""

    url: str
    kind: SourceKind = "html"


@dataclass
class SourceCategory:
    """One row from sources.json."""

    category: str
    sources: list[Source]


@dataclass
class RawArticle:
    """Article after fetch, before summarization."""

    title: str
    date: str | None
    content: str
    url: str


@dataclass
class OutputArticle:
    """Article in final JSON output."""

    title: str
    date: str | None
    content: str
    url: str
    short_summary: str
    full_summary: str

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "date": self.date,
            "content": self.content,
            "url": self.url,
            "short_summary": self.short_summary,
            "full_summary": self.full_summary,
        }


@dataclass
class CategoryResult:
    """One category block in output.json."""

    category: str
    articles: list[OutputArticle] = field(default_factory=list)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "articles": [a.to_json_dict() for a in self.articles],
        }
