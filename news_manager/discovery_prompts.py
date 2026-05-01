"""Prompt templates used by source discovery."""

from __future__ import annotations

DISCOVERY_CLASSIFICATION_ALLOWED = frozenset({"irrelevant", "follow", "is_index"})

DISCOVERY_CLASSIFICATION_PROMPT = """# HTML Page Classification Prompt

You are a precise classifier. The user message supplies:

- **Intent** — use it as a required relevance filter for `is_index`.
- **URL**
- **Document title** — text taken from the HTML `<title>` element (may be empty).
- **Body text** — plain text extracted from the HTML `<body>`.

Use **both** the document `<title>` and the body. The title often reveals page purpose (homepage vs single article vs listicle/roundup), the publication or product, and topical focus; combine it with body structure and links.

**Intent alignment is mandatory for `is_index`:** only choose `is_index` when the page is a plausible single-site news/blog index *and* its titles/topics align with the user intent. If the page is index-like but off-topic vs intent, classify as `irrelevant`.

---

## Categories

### 1) Home Page (News/Blog Index)
The page is the main landing page of a single news site or blog.
The response for this category should be "is_index".

**Typical signals:**
- Multiple article headlines or summaries
- Repeated structure (cards, list items, grids)
- Links mostly pointing to the same domain
- Navigation menus, categories, or sections (e.g., Politics, Tech, Opinion)
- Little to no full-length content; mostly previews or excerpts
- Document `<title>` often matches site branding or a section home (not a long article headline)
- Document `<title>` is only a few words.
- the URL does not contain a huyphen delimited article title.

---

### 2) Meta Article (Discussing Other Blogs/Sites)
The page is a single article/post that primarily discusses, summarizes, or links to content from *other* blogs, websites, or sources.

**Typical signals:**
- Long-form content focused on commentary or aggregation
- Many outbound links to different domains
- Mentions of multiple publications, authors, or sources
- Phrases like:
  - "according to"
  - "as reported by"
  - "here are some of the best articles"
- Structured as a narrative or analysis rather than a list of internal posts
- Document `<title>` often reads like one article or listicle ("The best…", "100 blogs…", "Ultimate list…")

The response for this category should be "follow".
---

### 3) Other
Anything that does not clearly fit into the above categories.

**Examples include:**
- A single standard article that is not about other blogs
- Product pages, landing pages, or marketing sites
- Login/signup pages
- Documentation or API references
- Forums or social media pages
- Index or article pages whose topics do not match the user intent

## Output format (STRICT JSON ONLY)

Return exactly:

{
  "classification": "irrelevant | follow | is_index",
  "reason": "Brief explanation referencing title, body, domain/link structure, and intent where relevant."
}

---

Return only JSON. No markdown. No extra keys.
"""


def build_discovery_classification_user_prompt(*, intent: str, url: str, page_title: str, body_text: str) -> str:
    title_line = page_title.strip() if page_title.strip() else "(empty — no <title> text found)"
    return (
        f"Intent: {intent}\n"
        f"URL: {url}\n\n"
        "Document <title> text (from HTML <title>):\n"
        f"{title_line}\n\n"
        "You must use the intent above as a required relevance filter.\n"
        "Only return is_index when the indexed content is consistent with that intent.\n"
        "Use the document title together with the body for classification and relevance.\n\n"
        "HTML <body> plain text:\n"
        f"{body_text}"
    )
