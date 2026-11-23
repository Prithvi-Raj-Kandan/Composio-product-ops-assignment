# Composio product-ops research

A docs-grounded agent that, for each app in a fixed list, records category, a one-line description, auth methods, self-serve versus gated access, API surface, whether an MCP server already exists, and a buildability verdict. Every non-`unknown` claim carries a URL and a verbatim quote. `unknown` is an allowed answer. The agent does not guess.

This repository currently has the project scaffold (package, pytest, ruff, CI). The research pipeline lands in the following tickets. The commands below are the contract; until a ticket implements one, it exits with a clear "not implemented yet" message.

## Requirements

- Python 3.11 (3.12 also runs). CI uses 3.11.
- API keys are environment variables. Nothing in this repo contains a key.

| Variable | Required | Used for |
|---|---|---|
| `COMPOSIO_API_KEY` | yes, for a live run | Toolkit metadata oracle and search via the Composio SDK |
| `GEMINI_API_KEY` | yes, for a live run | Extraction (`gemini-2.5-flash`) |
| `GROQ_API_KEY` | no | Independent cross-model verifier. If it is unset, that verifier calls Gemini again with a different model and prompt |
| `TAVILY_API_KEY` | no | Direct Tavily search when Composio search is unavailable |
| `EXA_API_KEY` | no | Direct Exa search when Composio and Tavily are unavailable |
| `GROQ_MODEL` | no | Defaults to `llama-3.3-70b-versatile` |
| `OPSRESEARCH_MIN_INTERVAL_S` | no | Shared rate-limit gap. Default `0.4` seconds |

## Install

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Commands

```bash
python -m opsresearch --help
python -m opsresearch smoke
python -m opsresearch run --apps data/apps.csv --pass 1
python -m opsresearch run --apps data/apps.csv --pass 2 --only "Slack,GitHub" --concurrency 4
python -m opsresearch research-one "Linear" --append
python -m opsresearch research-one "https://docs.github.com"
python -m opsresearch export
python -m opsresearch eval --split dev
python -m opsresearch eval --split test
```

`smoke` prints a status table and never prints key values. `export` writes `schema/app_record.schema.json`, `data/results.json`, and `data/results.csv`.

## How to replicate

1. Clone the repo and install as above.
2. Export the environment variables you have. Groq, Tavily, and Exa are optional.
3. Confirm `data/domain_overrides.csv` for the ambiguous names. Those rows are marked `needs human confirm`.
4. Run pass 1, then pass 2. Fetched pages are cached under `cache/<app>/` (gitignored), so a rerun does not refetch.
5. Run `export`, then `eval`. The 20-app gold set ships in the repo. Replicators do not label it again. See `HUMAN_IN_THE_LOOP.md`.

## Cost

A full run is about 100 apps, a handful of searches and page fetches each, one Gemini extraction, and a second model on pass 2. On Gemini 2.5 Flash that is a few dollars at most. Composio's free tier (10,000 tool calls a month) covers the toolkit lookups. Optional Tavily or Exa keys are only used when Composio search is not available. The disk cache is what keeps a second pass cheap.

## Tests

```bash
ruff check .
pytest
```

Tests use recorded fixtures. They do not call the network.
