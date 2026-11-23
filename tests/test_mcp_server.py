"""PO-14: MCP tool contract tests against fixture data (no network)."""

import asyncio
import json

import pytest
from opsresearch.mcp_server import build_server, get_app, query_apps

RECS = [
    {
        "app": "Acme",
        "category": "CRM",
        "one_liner": "CRM thing",
        "auth_methods": ["oauth2"],
        "self_serve": "self_serve_free",
        "gate_type": "none",
        "existing_mcp": "official",
        "composio_has_toolkit": "yes",
        "buildability": "yes",
        "main_blocker": "none",
        "needs_human": False,
        "evidence": [
            {"field": "auth_methods", "url": "https://acme.dev", "quote": "OAuth 2.0 everywhere", "source": "doc"}
        ],
    },
    {
        "app": "Beta",
        "category": "Finance",
        "one_liner": "ledger",
        "auth_methods": ["api_key"],
        "self_serve": "partner_gated",
        "gate_type": "partner",
        "existing_mcp": "none",
        "composio_has_toolkit": "no",
        "buildability": "no",
        "main_blocker": "partner_program",
        "needs_human": True,
        "evidence": [],
    },
]


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "results.json").write_text(json.dumps({"records": RECS}))
    return tmp_path


def test_query_filters_and_rejects_unknown_filters() -> None:
    assert query_apps(RECS, self_serve="partner_gated")["count"] == 1
    assert query_apps(RECS, auth_method="oauth2")["apps"][0]["app"] == "Acme"
    assert query_apps(RECS, needs_human=True)["apps"][0]["app"] == "Beta"
    with pytest.raises(ValueError):
        query_apps(RECS, colour="blue")


def test_get_app_returns_evidence_and_suggests() -> None:
    assert get_app(RECS, "acme")["evidence"][0]["url"] == "https://acme.dev"
    with pytest.raises(ValueError, match="did you mean Acme"):
        get_app(RECS, "Acm")


def test_server_lists_the_four_tools(repo) -> None:
    server = build_server(repo)
    names = sorted(t.name for t in asyncio.run(server.list_tools()))
    assert names == ["get_app", "patterns", "query_apps", "research_one"]


def test_server_call_query_apps(repo) -> None:
    server = build_server(repo)
    result = asyncio.run(server.call_tool("query_apps", {"buildability": "no"}))
    text = json.dumps(result.model_dump(mode="json") if hasattr(result, "model_dump") else result, default=str)
    assert "Beta" in text and "Acme" not in text


def test_patterns_tool_computes_from_records(repo) -> None:
    server = build_server(repo)
    result = asyncio.run(server.call_tool("patterns", {}))
    text = json.dumps(result.model_dump(mode="json") if hasattr(result, "model_dump") else result, default=str)
    assert "auth_counts" in text


def test_stdio_round_trip(repo) -> None:
    """Spawn the real server over stdio, as Claude or Cursor would, and call a tool."""
    import os
    import sys

    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "opsresearch.mcp_server"],
        env={**os.environ, "OPSRESEARCH_REPO": str(repo), "PYTHONPATH": os.getcwd()},
    )

    async def go():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                result = await session.call_tool("get_app", {"name": "Beta"})
                return sorted(t.name for t in tools.tools), result

    names, result = asyncio.run(asyncio.wait_for(go(), 30))
    assert "query_apps" in names
    assert "partner_gated" in json.dumps(result.model_dump(mode="json"), default=str)
