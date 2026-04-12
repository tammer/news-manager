"""Supabase helpers (mocked client)."""

from unittest.mock import MagicMock

from news_manager.models import OutputArticle
from news_manager.supabase_sync import (
    fetch_sources_with_categories,
    fetch_user_instructions,
    list_user_ids_with_sources,
    output_article_to_upsert_row,
    output_article_to_upsert_row_v2,
    parse_article_date_iso,
    prefetch_processed_urls_for_category,
    prefetch_processed_urls_v2,
    upsert_excluded_url,
    upsert_excluded_url_v2,
    upsert_included_article,
    upsert_included_article_v2,
)


def test_parse_article_date_iso_z_suffix() -> None:
    assert parse_article_date_iso("2024-01-15T12:00:00Z") == "2024-01-15T12:00:00+00:00"


def test_parse_article_date_iso_naive_becomes_utc() -> None:
    assert parse_article_date_iso("2024-06-01T08:30:00") == "2024-06-01T08:30:00+00:00"


def test_parse_article_date_iso_none_and_empty() -> None:
    assert parse_article_date_iso(None) is None
    assert parse_article_date_iso("") is None
    assert parse_article_date_iso("   ") is None


def test_parse_article_date_iso_garbage() -> None:
    assert parse_article_date_iso("not a date") is None


def test_output_article_to_upsert_row_omits_date_when_unparseable() -> None:
    art = OutputArticle(
        title="  Hi  ",
        date="nope",
        content="c",
        url="https://ex.com/a",
        short_summary="s",
        full_summary="f",
        source="ex.com",
    )
    row = output_article_to_upsert_row("News", art)
    assert row["category"] == "News"
    assert row["url"] == "https://ex.com/a"
    assert row["headline"] == "Hi"
    assert row["source"] == "ex.com"
    assert row["short_summary"] == "s"
    assert row["full_summary"] == "f"
    assert "article_date" not in row
    assert "read" not in row
    assert "liked" not in row


def test_output_article_to_upsert_row_empty_title() -> None:
    art = OutputArticle(
        title="  \n  ",
        date=None,
        content="c",
        url="https://ex.com/b",
        short_summary="s",
        full_summary="f",
        source="src",
    )
    row = output_article_to_upsert_row("Tech", art)
    assert row["headline"] == "(no title)"


def test_output_article_to_upsert_row_includes_article_date() -> None:
    art = OutputArticle(
        title="T",
        date="2024-03-20T00:00:00+00:00",
        content="c",
        url="https://ex.com/c",
        short_summary="s",
        full_summary="f",
        source="src",
    )
    row = output_article_to_upsert_row("Tech", art)
    assert row["article_date"] == "2024-03-20T00:00:00+00:00"


def _client_with_prefetch(news_urls: list[str], excl_urls: list[str]) -> MagicMock:
    client = MagicMock()

    def table(name: str) -> MagicMock:
        t = MagicMock()
        sel = MagicMock()
        urls = news_urls if name == "news_articles" else excl_urls
        sel.execute.return_value = MagicMock(data=[{"url": u} for u in urls])
        t.select.return_value.eq.return_value = sel
        up = MagicMock()
        up.execute.return_value = MagicMock()
        t.upsert.return_value = up
        return t

    client.table.side_effect = table
    return client


def test_prefetch_processed_urls_for_category() -> None:
    client = _client_with_prefetch(
        news_urls=["https://one.example/p"],
        excl_urls=["https://two.example/q"],
    )
    inc, exc = prefetch_processed_urls_for_category(client, "News")
    assert "https://one.example/p" in inc
    assert "https://two.example/q" in exc
    client.table.assert_called()


def test_upsert_included_article_calls_news_articles() -> None:
    news_table = MagicMock()
    excl_table = MagicMock()
    for t in (news_table, excl_table):
        sel = MagicMock()
        sel.execute.return_value = MagicMock(data=[])
        t.select.return_value.eq.return_value = sel
        up = MagicMock()
        up.execute.return_value = MagicMock()
        t.upsert.return_value = up

    client = MagicMock()

    def table(name: str) -> MagicMock:
        return news_table if name == "news_articles" else excl_table

    client.table.side_effect = table

    art = OutputArticle(
        title="T",
        date=None,
        content="c",
        url="https://ex.com/z",
        short_summary="s",
        full_summary="f",
        source="src",
    )
    assert upsert_included_article(client, "News", art) is None
    assert news_table.upsert.called
    kwargs = news_table.upsert.call_args[1]
    assert kwargs["on_conflict"] == "url,category"
    assert kwargs["default_to_null"] is False


def test_upsert_included_article_returns_error_on_failure() -> None:
    client = MagicMock()

    def table(_name: str) -> MagicMock:
        t = MagicMock()
        sel = MagicMock()
        sel.execute.return_value = MagicMock(data=[])
        t.select.return_value.eq.return_value = sel
        up = MagicMock()
        up.execute.side_effect = RuntimeError("boom")
        t.upsert.return_value = up
        return t

    client.table.side_effect = table
    art = OutputArticle(
        title="T",
        date=None,
        content="c",
        url="https://ex.com/z",
        short_summary="s",
        full_summary="f",
        source="src",
    )
    err = upsert_included_article(client, "News", art)
    assert err is not None
    assert "boom" in err


def test_upsert_excluded_url_calls_exclusions_table() -> None:
    news_table = MagicMock()
    excl_table = MagicMock()
    for t in (news_table, excl_table):
        sel = MagicMock()
        sel.execute.return_value = MagicMock(data=[])
        t.select.return_value.eq.return_value = sel
        up = MagicMock()
        up.execute.return_value = MagicMock()
        t.upsert.return_value = up

    client = MagicMock()

    def table(name: str) -> MagicMock:
        return news_table if name == "news_articles" else excl_table

    client.table.side_effect = table

    assert upsert_excluded_url(client, "https://x.com/a", "News") is None
    assert excl_table.upsert.called
    row = excl_table.upsert.call_args[0][0][0]
    assert row["url"] == "https://x.com/a"
    assert row["category"] == "News"
    assert row["why"] is None


def test_upsert_excluded_url_passes_why() -> None:
    excl_table = MagicMock()
    excl_table.upsert.return_value.execute.return_value = MagicMock()
    client = MagicMock()
    client.table.return_value = excl_table
    assert upsert_excluded_url(client, "https://x.com/b", "News", why="Off-topic.") is None
    row = excl_table.upsert.call_args[0][0][0]
    assert row["why"] == "Off-topic."


def _client_v2_prefetch(news_urls: list[str], excl_urls: list[str]) -> MagicMock:
    """v2 prefetch: news_articles uses select().eq().eq().execute(); exclusions one .eq()."""

    def table(name: str) -> MagicMock:
        t = MagicMock()
        urls = news_urls if name == "news_articles" else excl_urls
        exec_mock = MagicMock()
        exec_mock.execute.return_value = MagicMock(data=[{"url": u} for u in urls])
        if name == "news_articles":
            eq_inner = MagicMock()
            eq_inner.eq.return_value = exec_mock
            t.select.return_value.eq.return_value = eq_inner
        else:
            t.select.return_value.eq.return_value = exec_mock
        return t

    client = MagicMock()
    client.table.side_effect = table
    return client


def test_prefetch_processed_urls_v2() -> None:
    client = _client_v2_prefetch(
        news_urls=["https://one.example/p"],
        excl_urls=["https://two.example/q"],
    )
    inc, exc = prefetch_processed_urls_v2(client, "user-1", "cat-uuid")
    assert "https://one.example/p" in inc
    assert "https://two.example/q" in exc


def test_output_article_to_upsert_row_v2() -> None:
    art = OutputArticle(
        title="T",
        date="2024-03-20T00:00:00+00:00",
        content="c",
        url="https://ex.com/c",
        short_summary="s",
        full_summary="f",
        source="src",
    )
    row = output_article_to_upsert_row_v2("u1", "c1", art)
    assert row["user_id"] == "u1"
    assert row["category_id"] == "c1"
    assert row["headline"] == "T"
    assert row["article_date"] == "2024-03-20T00:00:00+00:00"


def test_list_user_ids_with_sources() -> None:
    client = MagicMock()
    src = MagicMock()
    src.select.return_value.execute.return_value = MagicMock(
        data=[{"user_id": "b"}, {"user_id": "a"}, {"user_id": "b"}]
    )

    def table(name: str) -> MagicMock:
        assert name == "sources"
        return src

    client.table.side_effect = table
    assert list_user_ids_with_sources(client) == ["a", "b"]


def test_fetch_user_instructions_empty() -> None:
    client = MagicMock()
    ui = MagicMock()
    ui.select.return_value.eq.return_value.limit.return_value.execute.return_value = MagicMock(
        data=[]
    )

    def table(name: str) -> MagicMock:
        assert name == "user_instructions"
        return ui

    client.table.side_effect = table
    assert fetch_user_instructions(client, "u1") == ""


def test_fetch_user_instructions_found() -> None:
    client = MagicMock()
    ui = MagicMock()
    ui.select.return_value.eq.return_value.limit.return_value.execute.return_value = MagicMock(
        data=[{"instruction": "  global  "}]
    )

    def table(name: str) -> MagicMock:
        return ui

    client.table.side_effect = table
    assert fetch_user_instructions(client, "u1") == "global"


def test_fetch_sources_with_categories() -> None:
    client = MagicMock()
    sources_t = MagicMock()
    sources_t.select.return_value.eq.return_value.execute.return_value = MagicMock(
        data=[
            {
                "url": "https://a.com",
                "use_rss": True,
                "category_id": "cid1",
                "instruction": "per",
            }
        ]
    )
    cat_t = MagicMock()
    cat_t.select.return_value.in_.return_value.execute.return_value = MagicMock(
        data=[{"id": "cid1", "name": "News"}]
    )

    def table(name: str) -> MagicMock:
        if name == "sources":
            return sources_t
        if name == "categories":
            return cat_t
        raise AssertionError(name)

    client.table.side_effect = table
    rows = fetch_sources_with_categories(client, "u1")
    assert len(rows) == 1
    assert rows[0]["url"] == "https://a.com"
    assert rows[0]["use_rss"] is True
    assert rows[0]["category_id"] == "cid1"
    assert rows[0]["category_name"] == "News"
    assert rows[0]["instruction"] == "per"


def test_fetch_sources_with_categories_null_or_blank_instruction() -> None:
    client = MagicMock()
    sources_t = MagicMock()
    sources_t.select.return_value.eq.return_value.execute.return_value = MagicMock(
        data=[
            {
                "url": "https://a.com",
                "use_rss": True,
                "category_id": "cid1",
                "instruction": None,
            },
            {
                "url": "https://b.com",
                "use_rss": False,
                "category_id": "cid1",
                "instruction": "   ",
            },
        ]
    )
    cat_t = MagicMock()
    cat_t.select.return_value.in_.return_value.execute.return_value = MagicMock(
        data=[{"id": "cid1", "name": "News"}]
    )

    def table(name: str) -> MagicMock:
        if name == "sources":
            return sources_t
        if name == "categories":
            return cat_t
        raise AssertionError(name)

    client.table.side_effect = table
    rows = fetch_sources_with_categories(client, "u1")
    assert len(rows) == 2
    assert rows[0]["instruction"] is None
    assert rows[1]["instruction"] is None


def test_upsert_included_article_v2_on_conflict() -> None:
    news_table = MagicMock()
    up = MagicMock()
    up.execute.return_value = MagicMock()
    news_table.upsert.return_value = up
    client = MagicMock()
    client.table.return_value = news_table
    art = OutputArticle(
        title="T",
        date=None,
        content="c",
        url="https://ex.com/z",
        short_summary="s",
        full_summary="f",
        source="src",
    )
    assert upsert_included_article_v2(client, "u1", "c1", art) is None
    kwargs = news_table.upsert.call_args[1]
    assert kwargs["on_conflict"] == "user_id,category_id,url"


def test_upsert_excluded_url_v2() -> None:
    excl_table = MagicMock()
    excl_table.upsert.return_value.execute.return_value = MagicMock()
    client = MagicMock()
    client.table.return_value = excl_table
    assert upsert_excluded_url_v2(client, "https://x.com/a", "c1") is None
    row = excl_table.upsert.call_args[0][0][0]
    assert row["url"] == "https://x.com/a"
    assert row["category_id"] == "c1"
    assert row["why"] is None
    assert excl_table.upsert.call_args[1]["on_conflict"] == "category_id,url"


def test_upsert_excluded_url_v2_passes_why() -> None:
    excl_table = MagicMock()
    excl_table.upsert.return_value.execute.return_value = MagicMock()
    client = MagicMock()
    client.table.return_value = excl_table
    assert (
        upsert_excluded_url_v2(client, "https://x.com/b", "c1", why="Not a match.") is None
    )
    assert excl_table.upsert.call_args[0][0][0]["why"] == "Not a match."
