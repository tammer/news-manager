"""Structured stdout lines for pipeline progress (cache_change_plan.md)."""

from __future__ import annotations

from typing import Literal

_Result = Literal["included", "excluded"]


def report_already_in_articles(url: str) -> None:
    print(url)
    print("Already in database")


def report_already_excluded(url: str) -> None:
    print(url)
    print("Already excluded")


def report_processed(
    url: str,
    category: str,
    ok: bool,
    detail: str = "",
    *,
    result: _Result | None = None,
) -> None:
    print(url)
    print(category)
    if ok:
        if result is not None:
            print(f"success {result}")
        else:
            print("success")
    else:
        print(f"failure: {detail}" if detail else "failure")
