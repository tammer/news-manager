"""HTML discovery LLM selection (mocked Groq)."""

from unittest.mock import MagicMock, patch

from news_manager.html_discovery_llm import select_article_urls_with_llm


@patch("news_manager.html_discovery_llm.get_client")
def test_select_article_urls_with_llm_drops_urls_not_in_candidates(mock_get_client: MagicMock) -> None:
    mock_resp = MagicMock()
    mock_resp.choices = [
        MagicMock(
            message=MagicMock(
                content='{"article_urls":["https://h.com/a","https://evil.com/x","https://h.com/b"]}'
            )
        )
    ]
    mock_resp.usage = None
    client = MagicMock()
    client.chat.completions.create.return_value = mock_resp
    mock_get_client.return_value = client

    candidates = [("https://h.com/a", "A"), ("https://h.com/b", "B")]
    out = select_article_urls_with_llm("https://h.com/", candidates)
    assert out == ["https://h.com/a", "https://h.com/b"]


@patch("news_manager.html_discovery_llm.get_client")
def test_select_article_urls_with_llm_returns_none_on_empty_message(mock_get_client: MagicMock) -> None:
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock(message=MagicMock(content=None))]
    mock_resp.usage = None
    client = MagicMock()
    client.chat.completions.create.return_value = mock_resp
    mock_get_client.return_value = client

    out = select_article_urls_with_llm(
        "https://h.com/", [("https://h.com/a", "A")], home_host="h.com"
    )
    assert out is None
