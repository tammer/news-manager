"""Human-readable ingest progress output with verbosity levels."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class SourceSummary:
    processed: int = 0
    included: int = 0
    rejected: int = 0


def _emit(verbosity: int, level: int, message: str) -> None:
    if verbosity >= level:
        print(message)


def report_start(*, verbosity: int) -> None:
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _emit(verbosity, 1, f"Starting at: {started_at}")


def report_user(*, verbosity: int, user_id: str) -> None:
    _emit(verbosity, 2, f"Processing user: {user_id}")


def report_category(*, verbosity: int, category: str) -> None:
    _emit(verbosity, 1, f"\nProcessing category: {category}")


def report_source(*, verbosity: int, source: str) -> None:
    _emit(verbosity, 1, f"Processing source: {source}")


def report_article(*, verbosity: int, url: str) -> None:
    _emit(verbosity, 1, f"Processing article: {url}")


def report_decision(
    *,
    verbosity: int,
    included: bool,
    reason: str,
) -> None:
    decision = "Include" if included else "Exclude"
    _emit(verbosity, 1, f"Decision: {decision} because: {reason}")


def report_source_summary(
    *,
    verbosity: int,
    category: str,
    source: str,
    index_url: str,
    summary: SourceSummary,
) -> None:
    if verbosity < 1:
        return
    print(f"\nSummary for category/source: {category} / {source}")
    print(f"Index URL: {index_url}")
    print(f"Processed {summary.processed} articles")
    print(f"Included {summary.included} articles")
    print(f"Rejected {summary.rejected} articles")
