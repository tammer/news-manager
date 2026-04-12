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
    #: If False, every fetched article from this source is summarized and included (no LLM exclude step).
    filter: bool = True
    #: Optional path to browser cookie export JSON (subscriber sessions); relative to cwd unless absolute.
    cookies: str | None = None


@dataclass(frozen=True)
class IngestSource:
    """One row from Supabase ``sources`` (v2 ingest)."""

    url: str
    category_id: str
    category_name: str
    use_rss: bool
    #: Per-source override for the LLM; None or empty means use global instructions in the pipeline.
    instruction: str | None = None
    filter: bool = True
    cookies: str | None = None

    def to_fetch_source(self) -> Source:
        return Source(
            url=self.url,
            kind="rss" if self.use_rss else "html",
            filter=self.filter,
            cookies=self.cookies,
        )


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
    #: Base hostname of the configured source (e.g. nextbigthing.substack.com).
    source: str

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "date": self.date,
            "content": self.content,
            "url": self.url,
            "short_summary": self.short_summary,
            "full_summary": self.full_summary,
            "source": self.source,
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


@dataclass
class UserPipelineResult:
    """Results for one user after a v2 DB-backed pipeline run."""

    user_id: str
    categories: list[CategoryResult] = field(default_factory=list)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "categories": [c.to_json_dict() for c in self.categories],
        }
