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

See [HUMAN_IN_THE_LOOP.md](HUMAN_IN_THE_LOOP.md). In short: provide keys (and repo secrets for the Action), connect Composio's Tavily toolkit once, confirm ambiguous domains (`data/domain_overrides.csv`, already done for 6 apps and marked `human_override` on those rows), read the `needs_human` rows, and approve publishing. The 20-app gold set is labelled once by the owner and ships in `data/gold/`; replicators do not label.

## Tests

```bash
ruff check . && pytest
```

Tests use fixtures and fakes, with a socket guard against live calls. CI runs both on Python 3.11.
