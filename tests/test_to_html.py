"""HTML export from output.json."""

import json
from pathlib import Path

import pytest

from news_manager.to_html import generate_html_files, main


def test_generate_html_files_writes_index_and_category(tmp_path: Path) -> None:
    data = [
        {
            "category": "News",
            "articles": [
                {
                    "title": "Test & Co",
                    "date": "2024-01-15",
                    "content": "body",
                    "url": "https://example.com/a",
                    "short_summary": "Short here.",
                    "full_summary": "Full text here.",
                }
            ],
        }
    ]
    inp = tmp_path / "output.json"
    inp.write_text(json.dumps(data), encoding="utf-8")
    outd = tmp_path / "site"
    paths = generate_html_files(inp, outd)

    assert (outd / "index.html").exists()
    assert (outd / "news.html").exists()
    assert len(paths) >= 2

    index_html = (outd / "index.html").read_text(encoding="utf-8")
    assert "News" in index_html
    assert 'href="news.html"' in index_html

    cat_html = (outd / "news.html").read_text(encoding="utf-8")
    assert "Test &amp; Co" in cat_html
    assert 'href="https://example.com/a"' in cat_html
    assert "2024-01-15" in cat_html
    assert "Short here." in cat_html
    assert "Full text here." in cat_html


def test_duplicate_category_slugs(tmp_path: Path) -> None:
    data = [
        {"category": "Same", "articles": []},
        {"category": "Same", "articles": []},
    ]
    inp = tmp_path / "out.json"
    inp.write_text(json.dumps(data), encoding="utf-8")
    outd = tmp_path / "html"
    generate_html_files(inp, outd)
    assert (outd / "same.html").exists()
    assert (outd / "same-1.html").exists()


def test_invalid_json_exits(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text("not json", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid JSON"):
        generate_html_files(p, tmp_path / "o")


def test_main_ok(tmp_path: Path) -> None:
    inp = tmp_path / "output.json"
    inp.write_text(json.dumps([{"category": "X", "articles": []}]), encoding="utf-8")
    outd = tmp_path / "html"
    code = main(["-i", str(inp), "-o", str(outd)])
    assert code == 0
    assert (outd / "index.html").exists()
