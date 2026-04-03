"""Tests for config loading and validation."""

import json
from pathlib import Path

import pytest

from news_manager.config import read_instructions, read_sources_json
from news_manager.models import CategoryResult, OutputArticle, Source
from news_manager.output import write_output


def test_read_sources_json_valid(tmp_path: Path) -> None:
    p = tmp_path / "sources.json"
    p.write_text(
        json.dumps(
            [
                {"category": "News", "sources": ["cnn.com"]},
                {"category": "Science", "sources": ["a.com", "b.com"]},
            ]
        ),
        encoding="utf-8",
    )
    cats = read_sources_json(p)
    assert len(cats) == 2
    assert cats[0].category == "News"
    assert cats[0].sources == [Source(url="cnn.com", kind="html", filter=True)]
    assert cats[1].sources == [
        Source(url="a.com", kind="html", filter=True),
        Source(url="b.com", kind="html", filter=True),
    ]


def test_read_sources_json_invalid_not_array(tmp_path: Path) -> None:
    p = tmp_path / "sources.json"
    p.write_text('{"category": "x"}', encoding="utf-8")
    with pytest.raises(ValueError, match="array"):
        read_sources_json(p)


def test_read_sources_json_invalid_category(tmp_path: Path) -> None:
    p = tmp_path / "sources.json"
    p.write_text(json.dumps([{"category": "", "sources": ["a.com"]}]), encoding="utf-8")
    with pytest.raises(ValueError, match="category"):
        read_sources_json(p)


def test_read_instructions(tmp_path: Path) -> None:
    p = tmp_path / "instructions.md"
    p.write_text("Hello **world**", encoding="utf-8")
    assert read_instructions(p) == "Hello **world**"


def test_read_sources_json_rss_object(tmp_path: Path) -> None:
    p = tmp_path / "sources.json"
    p.write_text(
        json.dumps(
            [
                {
                    "category": "Tech",
                    "sources": [
                        {"url": "https://example.substack.com/feed", "kind": "rss"},
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )
    cats = read_sources_json(p)
    assert cats[0].sources[0] == Source(
        url="https://example.substack.com/feed",
        kind="rss",
        filter=True,
    )


def test_read_sources_json_filter_false_per_source(tmp_path: Path) -> None:
    p = tmp_path / "sources.json"
    p.write_text(
        json.dumps(
            [
                {
                    "category": "X",
                    "sources": [
                        {"url": "https://a.com", "kind": "html", "filter": False},
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )
    cats = read_sources_json(p)
    assert cats[0].sources[0].filter is False


def test_read_sources_json_filter_must_be_boolean_on_source(tmp_path: Path) -> None:
    p = tmp_path / "sources.json"
    p.write_text(
        json.dumps(
            [
                {
                    "category": "X",
                    "sources": [{"url": "https://a.com", "filter": "yes"}],
                }
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="filter"):
        read_sources_json(p)


def test_merge_category_output_roundtrip(tmp_path: Path) -> None:
    """Output shape: categories in order, empty articles allowed."""
    out = [
        CategoryResult(
            category="A",
            articles=[
                OutputArticle(
                    title="t",
                    date=None,
                    content="c",
                    url="https://e",
                    short_summary="s",
                    full_summary="f",
                    source="e.example.com",
                )
            ],
        ),
        CategoryResult(category="B", articles=[]),
    ]
    path = tmp_path / "out.json"
    write_output(path, out)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data[0]["category"] == "A"
    assert len(data[0]["articles"]) == 1
    assert data[0]["articles"][0]["source"] == "e.example.com"
    assert data[1]["category"] == "B"
    assert data[1]["articles"] == []
