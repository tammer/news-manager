# news-manager

Fetches configured news home pages, discovers article links, filters and summarizes articles using your preferences in `instructions.md` and the **Groq** API (OpenAI-compatible).

## Setup

Requires **Python 3.11+**.

```bash
cd news-manager
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Copy `.env.example` to `.env` and set your Groq API key from [Groq Console](https://console.groq.com/):

```text
GROQ_API_KEY=your_key_here
GROQ_MODEL=llama-3.3-70b-versatile
```

## Usage

Create `sources.json` and `instructions.md` (see [plan.md](plan.md)), then:

```bash
news-manager --sources sources.json --instructions instructions.md --output output.json
```

Or:

```bash
python -m news_manager --sources sources.json --instructions instructions.md
```

Default output path is `output.json` in the current working directory.

### Options

| Flag | Description |
|------|-------------|
| `--sources` | Path to `sources.json` (required) |
| `--instructions` | Path to `instructions.md` (required) |
| `--output` | Output JSON path (default: `output.json`) |
| `--max-articles` | Max articles to fetch per source (default: 15) |
| `--timeout` | HTTP timeout in seconds (default: 30) |
| `--content-max-chars` | Max characters of article body sent to the LLM (default: 12000) |
| `-v`, `--verbose` | INFO logging to stderr |

## Testing

```bash
pytest
```

Tests mock HTTP and Groq; no API key is required for the default test run.

### Optional integration test

With `GROQ_API_KEY` set in the environment, you can run a live call (not included by default in CI). See `tests/test_summarize.py` for patterns using `pytest.mark.integration` if you add one later.

## Product spec

See [plan.md](plan.md) for input/output formats and behavior.
