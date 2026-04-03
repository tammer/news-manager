"""Shared Groq (OpenAI-compatible) client."""

from __future__ import annotations

from functools import lru_cache

from openai import OpenAI

from news_manager.config import GROQ_BASE_URL, groq_api_key


@lru_cache(maxsize=1)
def get_client() -> OpenAI:
    return OpenAI(
        base_url=GROQ_BASE_URL,
        api_key=groq_api_key(),
    )
