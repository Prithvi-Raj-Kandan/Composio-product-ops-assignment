# Composio product-ops research

A docs-grounded agent that, for each app, records category, a one-line description, auth methods, self-serve versus gated access, API surface, whether an MCP server already exists, and a buildability verdict. Every non-`unknown` claim carries a URL and a verbatim quote. `unknown` is an allowed answer. The agent does not guess.

Pass 1 extracts from the pages it fetched. Pass 2 checks the quote against the page, asks Composio's toolkit metadata API what auth it actually supports, checks the official MCP registry, and asks a second model (Groq, or Gemini again when Groq is unset) whether the claim holds. Fields that come back contradicted or insufficient are researched again, at most twice. Anything still open is flagged `needs_human`.

Both passes are saved. Accuracy is scored only on the 20-app gold set that ships with this repo.

## Requirements

Python 3.11. CI runs 3.11. Python 3.12 works locally. No API key is stored in the repo.

| Variable | Required for a live run | Used for |
|---|---|---|
| `COMPOSIO_API_KEY` | yes | Toolkit metadata (`composio.toolkits.get`) and search through `composio` + `composio_langchain` |
| `GEMINI_API_KEY` | yes | Extraction with `gemini-2.5-flash`. Also the verifier when Groq is unset |
| `GROQ_API_KEY` | no | Cross-model verifier. If missing, the verifier uses `gemini-2.5-pro` and logs that |
| `GROQ_MODEL` | no | Defaults to `llama-3.3-70b-versatile` |
| `TAVILY_API_KEY` | no | Direct Tavily search when Composio search is unavailable |
| `EXA_API_KEY` | no | Direct Exa search when Composio and Tavily are unavailable |
| `OPSRESEARCH_MIN_INTERVAL_S` | no | Shared gap between model calls. Default `0.4` |

Search order is Composio session (`TAVILY_SEARCH`, then `EXA_SEARCH`), then direct Tavily, then direct Exa, then the hint URL only. Fetched pages are cached as markdown under `cache/<app>/`, which is gitignored.

## Install

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Commands

```bash
python -m opsresearch smoke
python -m opsresearch run --apps data/apps.csv --pass 1
python -m opsresearch run --apps data/apps.csv --pass 2 --only "Slack,GitHub" --concurrency 4
python -m opsresearch research-one "Linear"
python -m opsresearch research-one "https://docs.github.com" --append
python -m opsresearch export
python -m opsresearch export --from data/results_pass1.json
python -m opsresearch eval --split dev
python -m opsresearch eval --split test
```

`smoke` prints a status table for Composio, Gemini, Groq, Tavily, and Exa. It never prints key values. Groq, Tavily, and Exa show as skipped when their keys are unset. Composio and Gemini show as missing, and the command exits non-zero, when those keys are unset or the probe fails.

`run --pass 1` writes `data/results_pass1.json` and a per-app log in `data/runs/<slug>_pass1.json`. `run --pass 2` writes `data/results_pass2.json` and the pass-2 logs. Each run also refreshes the published export from that pass. `export` with no `--from` prefers pass 2, then `data/results.json`, then pass 1. It always rewrites:

- `schema/app_record.schema.json` (generated from the Pydantic model)
- `data/results.json` (lossless, for an agent)
- `data/results.csv` (one row per app, for a person)

`research-one` prints one record. `--append` adds it to `data/results.json`, replacing an earlier row with the same app name, then refreshes the CSV.

`eval` scores the gold labels against both pass files and writes `data/eval/report_<split>.json`. Dev and test are reported separately. An empty label is unlabelled, not a miss, and the accuracy for a field with no labels is null.

## Data contract

Schema version `1.0.0`. The JSON file is the contract an agent should read. The CSV is the same records flattened. List fields are joined with `|`. `evidence_json` and `unknown_reasons` are JSON strings in the CSV. `yes` / `no` / `unknown` are strings, not booleans, so "we did not find out" is not stored as false.

| Field | Values |
|---|---|
| `auth_methods` | `oauth2`, `oauth1`, `api_key`, `basic`, `bearer_token`, `jwt`, `session`, `none`, `unknown` |
| `self_serve` | `self_serve_free`, `self_serve_trial`, `paid_plan_required`, `admin_approval`, `partner_gated`, `contact_sales`, `unknown` |
| `gate_type` | `none`, `paid_plan`, `admin_approval`, `partner`, `contact_sales`, `platform_review`, `login_wall`, `unknown` |
| `api_rest`, `api_graphql` | `yes`, `no`, `unknown` |
| `api_breadth` | `narrow` (under 20 endpoints), `medium` (20–99), `broad` (100+), `unknown` |
| `api_styles` | `rest`, `graphql`, `soap`, `sdk_only`, `cli_only`, `none`, `unknown` |
| `existing_mcp` | `official`, `community`, `none`, `unknown`. Official means the registry publisher's domain matches the vendor |
| `composio_has_toolkit` | `yes`, `no`, `unknown` |
| `buildability` | `yes`, `yes_with_caveats`, `no`, `unknown` |
| `main_blocker` | `none`, `partner_program`, `contact_sales`, `no_public_api`, `login_walled_docs`, `unclear_entity`, `platform_review`, `enterprise_only`, `unknown` |

A concrete value with no usable evidence (http(s) URL and a verbatim quote of at least 8 characters) is rewritten to `unknown`, and `unknown_reasons` says why. Quotes from the Composio toolkit API and the MCP registry use `evidence.source` of `composio_oracle` or `mcp_registry`. Doc quotes are checked against the fetched page.

`data/results.json` is an envelope: `schema_version`, `json_schema`, `pass`, `record_count`, `records`.

## How to replicate

1. Install, and export the keys you have.
2. Read `data/domain_overrides.csv`. Those eight names are best guesses marked `needs human confirm`. Change a domain before you trust that app's pages.
3. Run pass 1, then pass 2. The cache makes the second run cheaper.
4. Run `export`, then `eval --split dev` and `eval --split test`.
5. The gold sample and, once filled, the gold labels ship in `data/gold/`. Replicators do not label again. See `HUMAN_IN_THE_LOOP.md`.

`data/gold/gold_labels.csv` is the labelling sheet. Until those cells are filled, eval reports null accuracy on purpose.

## Cost

A full run is about 100 apps, a few searches and page fetches each, one Gemini extraction, and on pass 2 a second model call plus Composio toolkit lookups. On Gemini 2.5 Flash that is a few dollars at most. Composio's free tier (10,000 tool calls a month) covers the metadata and search calls. Groq, Tavily, and Exa are optional. The disk cache is what keeps a rerun cheap. Nothing is trained.

## Tests

```bash
ruff check .
pytest
```

Tests use fixtures and fakes. A socket guard refuses live network calls. GitHub Actions runs the same two commands on Python 3.11.
