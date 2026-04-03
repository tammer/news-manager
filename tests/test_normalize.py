"""URL normalization tests."""

import pytest

from news_manager.fetch import normalize_url, same_site


def test_normalize_adds_https() -> None:
    assert normalize_url("example.com").startswith("https://")
    assert normalize_url("example.com") == "https://example.com"


def test_normalize_preserves_path() -> None:
    u = normalize_url("https://news.example.com/world/")
    assert u == "https://news.example.com/world/"


def test_normalize_strips_fragment() -> None:
    assert "#" not in normalize_url("https://example.com/a#b")


def test_normalize_bare_hostname() -> None:
    assert normalize_url("CNN.com") == "https://CNN.com"


def test_normalize_empty_raises() -> None:
    with pytest.raises(ValueError):
        normalize_url("   ")


def test_same_site_www() -> None:
    assert same_site("https://www.example.com/", "https://example.com/a")
    assert same_site("https://example.com/", "https://www.example.com/b")
