#!/usr/bin/env python3
"""Minimal REPL that talks to OpenAI's Chat Completions API.

Set OPENAI_API_KEY to your API key, then run:

    python experiment_openai/chat.py
"""

from __future__ import annotations

import os
import sys

from openai import OpenAI

API_KEY_ENV = "OPENAI_API_KEY"
DEFAULT_MODEL = "gpt-5.4-mini"


def main() -> None:
    api_key = os.environ.get(API_KEY_ENV)
    if not api_key:
        print(f"Missing {API_KEY_ENV}. Export your OpenAI API key first.", file=sys.stderr)
        sys.exit(1)

    model = os.environ.get("OPENAI_CHAT_MODEL", DEFAULT_MODEL)
    client = OpenAI(api_key=api_key)
    messages: list[dict[str, str]] = [
        {"role": "system", "content": "You are a helpful assistant."},
    ]

    print(f"Model: {model}. Type a message; empty line, quit, or Ctrl-D to exit.\n")

    while True:
        try:
            user = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user or user.lower() in ("quit", "exit", "q"):
            break

        messages.append({"role": "user", "content": user})

        stream = client.chat.completions.create(
            model=model,
            messages=messages,
            stream=True,
        )

        print("Assistant: ", end="", flush=True)
        parts: list[str] = []
        for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                print(delta, end="", flush=True)
                parts.append(delta)
        print()

        assistant_text = "".join(parts)
        messages.append({"role": "assistant", "content": assistant_text})


if __name__ == "__main__":
    main()
