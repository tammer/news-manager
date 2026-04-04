"""Cookie JSON loader for subscriber fetches."""

import json
import time
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from news_manager.cookies_loader import (
    load_cookie_jar,
    resolve_cookie_file,
    resolve_cookie_file_for_home_url,
)
from news_manager.models import Source


def test_load_cookie_jar_parses_browser_export(tmp_path: Path) -> None:
    p = tmp_path / "x.json"
    p.write_text(
        json.dumps(
            [
                {
                    "domain": ".example.com",
                    "name": "sid",
                    "value": "abc123",
                    "path": "/",
                    "secure": True,
                    "httpOnly": True,
                }
            ]
        ),
        encoding="utf-8",
    )
    jar = load_cookie_jar(p)
    assert jar is not None
    req = httpx.Request("GET", "https://www.example.com/")
    jar.set_cookie_header(req)
    assert req.headers.get("cookie") == "sid=abc123"


def test_load_cookie_jar_skips_expired(tmp_path: Path) -> None:
    p = tmp_path / "exp.json"
    past = time.time() - 3600
    future = time.time() + 86400
    p.write_text(
        json.dumps(
            [
                {
                    "domain": "www.example.com",
                    "name": "old",
                    "value": "x",
                    "path": "/",
                    "expirationDate": past,
                },
                {
                    "domain": "www.example.com",
                    "name": "new",
                    "value": "y",
                    "path": "/",
                    "expirationDate": future,
                },
            ]
        ),
        encoding="utf-8",
    )
    jar = load_cookie_jar(p)
    assert jar is not None
    req = httpx.Request("GET", "https://www.example.com/")
    jar.set_cookie_header(req)
    h = req.headers.get("cookie") or ""
    assert "old=" not in h
    assert "new=y" in h


def test_load_cookie_jar_invalid_type_raises(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text('{"not": "array"}', encoding="utf-8")
    with pytest.raises(ValueError, match="array"):
        load_cookie_jar(p)


def test_resolve_cookie_file_explicit_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    f = tmp_path / "my.json"
    f.write_text("[]", encoding="utf-8")
    src = Source(url="https://example.com/", cookies="my.json")
    got = resolve_cookie_file(src, tmp_path / "cookies")
    assert got == f


def test_resolve_cookie_file_by_host(tmp_path: Path) -> None:
    d = tmp_path / "cookies"
    d.mkdir()
    f = d / "thestar.com.json"
    f.write_text("[]", encoding="utf-8")
    src = Source(url="https://www.thestar.com/feed/")
    got = resolve_cookie_file(src, d)
    assert got == f


def test_resolve_cookie_file_www_fallback(tmp_path: Path) -> None:
    d = tmp_path / "cookies"
    d.mkdir()
    f = d / "www.example.com.json"
    f.write_text("[]", encoding="utf-8")
    src = Source(url="https://example.com/")
    got = resolve_cookie_file(src, d)
    assert got == f


def test_resolve_cookie_file_for_home_url_matches(tmp_path: Path) -> None:
    d = tmp_path / "cookies"
    d.mkdir()
    f = d / "news.example.com.json"
    f.write_text("[]", encoding="utf-8")
    assert resolve_cookie_file_for_home_url("https://news.example.com/rss", d) == f


def test_cookie_jar_for_home_url_logs_name(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    import logging

    from news_manager.cookies_loader import cookie_jar_for_home_url

    d = tmp_path / "cookies"
    d.mkdir()
    p = d / "foo.com.json"
    p.write_text(
        json.dumps(
            [
                {
                    "domain": ".foo.com",
                    "name": "a",
                    "value": "b",
                    "path": "/",
                }
            ]
        ),
        encoding="utf-8",
    )
    with caplog.at_level(logging.INFO):
        with patch.dict("os.environ", {"NEWS_MANAGER_COOKIES_DIR": str(d)}):
            jar = cookie_jar_for_home_url("https://www.foo.com/")
    assert jar is not None
    assert "foo.com.json" in caplog.text
