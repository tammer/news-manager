"""Article cache."""

from pathlib import Path

from news_manager.cache import ArticleCache, cache_key
from news_manager.models import OutputArticle


def test_cache_key_url_only() -> None:
    a = cache_key("https://x.com/a")
    b = cache_key("https://x.com/a")
    assert a == b


def test_cache_key_differs_for_different_urls() -> None:
    a = cache_key("https://x.com/a")
    b = cache_key("https://x.com/b")
    assert a != b


def test_cache_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    c = ArticleCache(path)
    art = OutputArticle(
        title="T",
        date=None,
        content="body",
        url="https://e",
        short_summary="s",
        full_summary="f",
        source="e.example.com",
    )
    c.put("https://e", "included", art)
    c.save()

    c2 = ArticleCache(path)
    hit = c2.lookup("https://e")
    assert hit is not None
    st, got = hit
    assert st == "included"
    assert got is not None
    assert got.short_summary == "s"

    c2.put("https://e2", "excluded", None)
    c2.save()
    c3 = ArticleCache(path)
    hit_e = c3.lookup("https://e2")
    assert hit_e == ("excluded", None)
