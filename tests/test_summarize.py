"""Summarization with mocked Groq client."""

from unittest.mock import MagicMock, patch

from news_manager.models import RawArticle
from news_manager.summarize import filter_and_summarize, _parse_json_response


def test_parse_json_raw() -> None:
    d = _parse_json_response('{"include": true, "short_summary": "a", "full_summary": "b"}')
    assert d is not None
    assert d["include"] is True


def test_parse_json_fenced() -> None:
    text = '```json\n{"include": false, "short_summary": "", "full_summary": ""}\n```'
    d = _parse_json_response(text)
    assert d is not None
    assert d["include"] is False


@patch("news_manager.summarize.get_client")
def test_filter_exclude(mock_get_client: MagicMock) -> None:
    client = MagicMock()
    mock_get_client.return_value = client
    client.chat.completions.create.return_value = MagicMock(
        choices=[
            MagicMock(
                message=MagicMock(
                    content='{"include": false, "short_summary": "", "full_summary": ""}'
                )
            )
        ]
    )
    raw = RawArticle(
        title="T",
        date=None,
        content="body",
        url="https://x",
    )
    assert filter_and_summarize(raw, category="News", instructions="none") is None


@patch("news_manager.summarize.get_client")
def test_filter_include(mock_get_client: MagicMock) -> None:
    client = MagicMock()
    mock_get_client.return_value = client
    client.chat.completions.create.return_value = MagicMock(
        choices=[
            MagicMock(
                message=MagicMock(
                    content=(
                        '{"include": true, "short_summary": "Short here.", '
                        '"full_summary": "Longer summary text."}'
                    )
                )
            )
        ]
    )
    raw = RawArticle(
        title="T",
        date="2024-01-01",
        content="body",
        url="https://x",
    )
    out = filter_and_summarize(raw, category="News", instructions="like news")
    assert out is not None
    assert out.short_summary == "Short here."
    assert out.full_summary == "Longer summary text."
    assert out.url == "https://x"


@patch("news_manager.summarize.get_client")
def test_summarize_only_when_apply_filter_false(mock_get_client: MagicMock) -> None:
    client = MagicMock()
    mock_get_client.return_value = client
    client.chat.completions.create.return_value = MagicMock(
        choices=[
            MagicMock(
                message=MagicMock(
                    content='{"short_summary": "Short.", "full_summary": "Longer text here."}'
                )
            )
        ]
    )
    raw = RawArticle(
        title="T",
        date=None,
        content="body",
        url="https://x",
    )
    out = filter_and_summarize(
        raw,
        category="News",
        instructions="context",
        apply_filter=False,
    )
    assert out is not None
    assert out.short_summary == "Short."
    assert out.full_summary == "Longer text here."
