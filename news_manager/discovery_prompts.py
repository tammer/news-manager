"""Prompt templates used by source discovery."""

from __future__ import annotations

DISCOVERY_CLASSIFICATION_ALLOWED = frozenset({"irrelevant", "follow", "is_index"})

DISCOVERY_CLASSIFICATION_PROMPT = """# Website Classification Prompt (Blog/News Index Detection)

You are a classifier that analyzes the raw HTML <body> content of a webpage and determines whether it is:

1. The index/homepage of a SINGLE blog or news site
2. A multi-site blog/news AGGREGATOR or directory
3. Not relevant

---

## CRITICAL RULE (HIGHEST PRIORITY)

If the page contains links to articles from MULTIPLE different root domains (e.g. nytimes.com, medium.com, cnn.com all appearing together), you MUST classify it as:

→ "follow"

Do NOT classify as "is_index" in this case under any circumstances.

---

## Decision Rules

### "follow" (highest priority if triggered)
Use "follow" if ANY of the following are true:
- Content links to multiple different domains (very important)
- It aggregates articles/posts from different publishers
- It resembles RSS feeds, “top stories from around the web”, curated lists
- It is clearly a directory of external content sources
- It is an article or blog indexing other articles or blogs

---

### "is_index"
Use "is_index" only if ALL of the following are true:
- the URL suggests it is root of a blog/news publication and not a single article
- Content represents a SINGLE blog/news publication
- Most links point to the SAME root domain
- It shows a homepage-style feed (recent posts, headlines, excerpts)
- It is clearly the main page of one publication
- The indexed articles are topically consistent with the user-provided intent

If the page may be a valid index page but its article topics are mostly unrelated to the user intent, classify as "irrelevant".

---

### "irrelevant"
Use "irrelevant" if:
- It is a single article page (not a listing/index)
- It is a marketing/landing page
- It is a forum, documentation page, login page, or product page
- It does not clearly represent a blog/news index or aggregator
- It is an index/aggregator but the indexed topics are not aligned with user intent

---

## Important Heuristics

- Domain diversity is the strongest signal in the entire task
- Multiple external domains ⇒ ALWAYS "follow"
- Single-domain feed ⇒ "is_index"
- Do not confuse “many articles” with “many sources”
- Intent alignment is mandatory for "is_index"
- If intent alignment is weak or absent, prefer "irrelevant"

---

## Output format (STRICT JSON ONLY)

Return exactly:

{
  "classification": "irrelevant | follow | is_index",
  "reason": "Brief explanation referencing domain structure and page type signals."
}

---

Return only JSON. No markdown. No extra keys.
"""


def build_discovery_classification_user_prompt(*, intent: str, url: str, body_text: str) -> str:
    return (
        f"Intent: {intent}\n"
        f"URL: {url}\n\n"
        "You must use the intent above as a required relevance filter.\n"
        "Only return is_index when the indexed content is consistent with that intent.\n\n"
        "HTML <body> text:\n"
        f"{body_text}"
    )
