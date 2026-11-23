# Which of 100 apps can become an agent toolkit today?

**Case study (start here):** https://prithvi-raj-kandan.github.io/Composio-product-ops-assignment/

A docs-grounded research agent for the Composio AI Product Ops take-home. For each of 100 apps it records category, a one-line description, auth methods, self-serve vs gated access, API surface (REST / GraphQL, breadth), existing MCP servers, Composio coverage, a buildability verdict and the main blocker. Every non-`unknown` value carries an evidence URL and a verbatim quote. `unknown` is an allowed answer; the agent does not guess.

- **Humans:** the one-page case study (`site/index.html`, published on GitHub Pages): headline patterns, charts, a filterable 100-app matrix with evidence links, the agent workflow, verification hits and misses, and an honesty section.
- **Agents:** `data/results.json` (validated against `schema/app_record.schema.json`), `data/results.csv`, `data/patterns.json`, `llms.txt`, JSON-LD in the page, and an MCP server (`python -m opsresearch.mcp_server`).

## How it works

```
app + hint ─► domain override? ─► search (Composio Tavily toolkit → direct Tavily → page cache)
          ─► official-domain filter ─► fetch to markdown cache ─► extract (Gemini, strict schema, quote per field)
          ─► pass 1 result
          ─► verify: quote on page · Composio toolkit API oracle · MCP registry · second model (Groq gpt-oss-120b → gemini-3.1-flash-lite)
          ─► failing fields re-searched and re-extracted (max 2 loops) ─► pass 2 result, or needs_human
```

LangGraph runs the per-app subgraph; a thread pool fans out over the 100 apps. Patterns are computed with pandas (`opsresearch/patterns.py`); an LLM only words the headline and a numeric fact-check rejects any number not in the tables. `buildability` and `main_blocker` are derived by a fixed rule from evidenced fields when the model gives no quote for them (cited with `source=derived_rule`).

## Run it

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
export COMPOSIO_API_KEY=... GEMINI_API_KEY=... GROQ_API_KEY=... TAVILY_API_KEY=...
python -m opsresearch connect-tavily          # once: connects Composio's Tavily toolkit with your Tavily key
python -m opsresearch smoke                   # checks every service, never prints keys
python -m opsresearch run --pass 1 --concurrency 6
python -m opsresearch run --pass 2 --concurrency 6
python -m opsresearch patterns                # pandas tables + fact-checked headline -> data/patterns.json
python -m opsresearch eval                    # gold-set accuracy, dev and test, pass 1 vs pass 2
python -m opsresearch render --out site       # the case-study page + agent files
```

Research a company that is not in the list:

```bash
python -m opsresearch research-one "Notion"
python -m opsresearch research-one "https://developers.notion.com" --append   # adds it to results.json
```

Or with no local setup: run the **research-one** workflow in GitHub Actions (Actions → research-one → Run workflow, enter a name or URL). It researches, appends, recomputes patterns, commits, and redeploys the page.

MCP server for Claude, Cursor or any MCP client:

```json
{"mcpServers": {"composio-apps": {"command": "python", "args": ["-m", "opsresearch.mcp_server"],
  "env": {"OPSRESEARCH_REPO": "/path/to/this/repo"}}}}
```

Tools: `query_apps` (filter by category, self_serve, gate_type, existing_mcp, composio_has_toolkit, buildability, main_blocker, auth_method, text, needs_human), `get_app`, `patterns`, `research_one`.

## Environment

| Variable | Needed | Used for |
|---|---|---|
| `COMPOSIO_API_KEY` | yes | Composio toolkit metadata (the oracle) and search through Composio's Tavily toolkit |
| `GEMINI_API_KEY` | yes | Extraction (`gemini-3.5-flash-lite`, fallback `gemini-3.1-flash-lite`) and verifier fallback |
| `GROQ_API_KEY` | recommended | Cross-model verifier `openai/gpt-oss-120b` |
| `TAVILY_API_KEY` | recommended | Search (via Composio, then direct) |
| `OPSRESEARCH_EXTRACT_MODELS` | no | Comma list overriding the extraction models |
| `OPSRESEARCH_GEMINI_RPM` | no | Per-model pacing, default 14 (free tier is 15/min) |

## Where a human is needed

The workflow stops for human judgement at fixed points on every run. [HUMAN_IN_THE_LOOP.md](HUMAN_IN_THE_LOOP.md) lists each point with its trigger in the code, what the human decides, what file they edit, and how the decision flows back (rerun or `--resume`). The page's section "Where the agent stops and a human decides" shows the same points with counts from this run. One-time setup is separate: API keys, `connect-tavily`, and approving merges and the Pages publish.

## Verification

Gold labels were drafted by an AI model from official docs, independently of this pipeline (each row has an evidence URL per field). Human review is pending, so treat these as agreement scores until it is done. The sample is 20 apps, stratified 2 per category, with dev 10 and test 10 (`data/gold/gold_sample.csv`). Reports are in `data/eval/report_dev.json` and `report_test.json`. Re-run with `python -m opsresearch eval`.

| Split | Pass 1 | Pass 2 | Pass 3 |
|---|---|---|---|
| dev (80 labelled cells) | 13.75% (11) | 13.75% (11) | 21.25% (17) |
| test (80 labelled cells) | 27.5% (22) | 15.0% (12) | 31.25% (25) |

Most misses are the agent answering `unknown` where the label has a value (underclaim). Pass 2 fell on test mainly because 37 apps crashed in pass 2 when the model quota ran out. API breadth agreed on only 1 of 20 apps, which is why it is left off the page; it stays in the data and in these scores.

Automated checks on all 100 apps (pass 2 loops): 535 supported, 242 insufficient, 55 flagged, 29 contradicted findings. 80 apps needed no re-research loop, 8 needed one and 12 needed two, and 110 fields were settled by re-research. These are consistency checks, not accuracy.

## Tests

```bash
ruff check . && pytest
```

Tests use fixtures and fakes, with a socket guard against live calls. CI runs both on Python 3.11.
