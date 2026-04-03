"""Pipeline orchestration with mocks."""

from unittest.mock import MagicMock, patch

from news_manager.models import OutputArticle, RawArticle, SourceCategory
from news_manager.pipeline import run_pipeline


@patch("news_manager.pipeline.filter_and_summarize")
@patch("news_manager.pipeline.fetch_articles_for_source")
def test_run_pipeline_keeps_empty_category(
    mock_fetch: MagicMock,
    mock_summarize: MagicMock,
) -> None:
    mock_fetch.return_value = [
        RawArticle(title="T", date=None, content="c", url="https://u"),
    ]
    mock_summarize.return_value = None

    cats = [
        SourceCategory(category="News", sources=["a.com"]),
        SourceCategory(category="Science", sources=["b.com"]),
    ]
    out = run_pipeline(cats, instructions="x", max_articles=5, http_timeout=1.0)
    assert len(out) == 2
    assert out[0].category == "News"
    assert out[0].articles == []
    assert out[1].category == "Science"
    assert out[1].articles == []


@patch("news_manager.pipeline.filter_and_summarize")
@patch("news_manager.pipeline.fetch_articles_for_source")
def test_run_pipeline_includes_summarized(
    mock_fetch: MagicMock,
    mock_summarize: MagicMock,
) -> None:
    mock_fetch.return_value = [
        RawArticle(title="T", date=None, content="c", url="https://u"),
    ]
    mock_summarize.return_value = OutputArticle(
        title="T",
        date=None,
        content="c",
        url="https://u",
        short_summary="s",
        full_summary="f",
    )

    cats = [SourceCategory(category="News", sources=["a.com"])]
    out = run_pipeline(cats, instructions="x", max_articles=5, http_timeout=1.0)
    assert len(out[0].articles) == 1
    assert out[0].articles[0].short_summary == "s"
