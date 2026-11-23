"""PO-14 MCP server (stdio). Read tools over data/results.json, plus research_one that runs the graph.

Run: python -m opsresearch.mcp_server   (set OPSRESEARCH_REPO to point at another checkout)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

FILTERABLE = (
    "category",
    "self_serve",
    "gate_type",
    "existing_mcp",
    "composio_has_toolkit",
    "buildability",
    "main_blocker",
)


def repo_root() -> Path:
    return Path(os.environ.get("OPSRESEARCH_REPO") or Path(__file__).resolve().parents[1])


def load_records(repo: Path | None = None) -> list[dict[str, Any]]:
    path = (repo or repo_root()) / "data" / "results.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return list(payload["records"] if isinstance(payload, dict) else payload)


def _summary(r: dict[str, Any]) -> dict[str, Any]:
    keys = ("app", "category", "one_liner", "auth_methods", *FILTERABLE[1:], "needs_human")
    return {k: r.get(k) for k in keys if k != "category"} | {"category": r.get("category")}


def query_apps(
    records: list[dict[str, Any]],
    *,
    text: str = "",
    auth_method: str = "",
    needs_human: bool | None = None,
    limit: int = 100,
    **filters: str,
) -> dict[str, Any]:
    bad = sorted(set(filters) - set(FILTERABLE))
    if bad:
        raise ValueError(f"unknown filter(s): {', '.join(bad)}; allowed: {', '.join(FILTERABLE)}")
    out = []
    for r in records:
        if text and text.lower() not in (str(r.get("app", "")) + " " + str(r.get("one_liner", ""))).lower():
            continue
        if auth_method and auth_method not in (r.get("auth_methods") or []):
            continue
        if needs_human is not None and bool(r.get("needs_human")) != needs_human:
            continue
        if any(v and str(r.get(k, "")).lower() != str(v).lower() for k, v in filters.items()):
            continue
        out.append(_summary(r))
    out.sort(key=lambda r: str(r["app"]).lower())
    return {"count": len(out), "apps": out[: max(1, limit)]}


def get_app(records: list[dict[str, Any]], name: str) -> dict[str, Any]:
    for r in records:
        if str(r.get("app", "")).lower() == name.strip().lower():
            return r
    close = [r["app"] for r in records if name.strip().lower() in str(r.get("app", "")).lower()]
    raise ValueError(f"no app named {name!r}" + (f"; did you mean {', '.join(close[:5])}" if close else ""))


def get_patterns(repo: Path | None = None) -> dict[str, Any]:
    root = repo or repo_root()
    cached = root / "data" / "patterns.json"
    if cached.exists():
        return json.loads(cached.read_text(encoding="utf-8"))
    from opsresearch.patterns import compute_patterns, headline, records_frame

    p = compute_patterns(records_frame(load_records(root)))
    p["headline"] = headline(p)
    return p


def research_one(target: str, append: bool = False, repo: Path | None = None) -> dict[str, Any]:
    """Run the full pass-2 graph for one app. Needs the API keys in the server's environment."""
    from opsresearch.export import export_bundle
    from opsresearch.graph import append_record, build_deps, resolve_target, run_app, write_run_log
    from opsresearch.seed import load_apps, load_overrides

    root = repo or repo_root()
    apps_path = root / "data" / "apps.csv"
    apps = load_apps(apps_path) if apps_path.exists() else []
    overrides_path = root / "data" / "domain_overrides.csv"
    app = resolve_target(apps, target, load_overrides(overrides_path))
    record, log = run_app(app, 2, build_deps(root / "cache", overrides_path))
    write_run_log(root / "data" / "runs", record.app, 2, log)
    if append:
        append_record(root / "data" / "results.json", record)
        export_bundle(repo=root, source=root / "data" / "results.json")
    return record.model_dump(mode="json")


def build_server(repo: Path | None = None):
    from mcp.server.mcpserver import MCPServer

    server = MCPServer(
        name="composio-app-research",
        instructions=(
            "Research records on 100 apps: auth, self-serve vs gated, API surface, MCP, Composio coverage, "
            "buildability, each with evidence URLs and quotes. 'unknown' means no quotable evidence was found."
        ),
    )

    @server.tool(
        name="query_apps", description="Filter the app records by " + ", ".join(FILTERABLE) + ", auth_method, text."
    )
    def query_apps_tool(
        text: str = "",
        category: str = "",
        self_serve: str = "",
        gate_type: str = "",
        existing_mcp: str = "",
        composio_has_toolkit: str = "",
        buildability: str = "",
        main_blocker: str = "",
        auth_method: str = "",
        needs_human: bool | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        filters = {
            "category": category,
            "self_serve": self_serve,
            "gate_type": gate_type,
            "existing_mcp": existing_mcp,
            "composio_has_toolkit": composio_has_toolkit,
            "buildability": buildability,
            "main_blocker": main_blocker,
        }
        return query_apps(
            load_records(repo), text=text, auth_method=auth_method, needs_human=needs_human, limit=limit, **filters
        )

    @server.tool(name="get_app", description="Full record for one app, with evidence URLs and quotes.")
    def get_app_tool(name: str) -> dict[str, Any]:
        return get_app(load_records(repo), name)

    @server.tool(name="patterns", description="Computed pattern tables (pandas) and the fact-checked headline.")
    def patterns_tool() -> dict[str, Any]:
        return get_patterns(repo)

    @server.tool(
        name="research_one",
        description="Research a new app by name or URL with the full verified pipeline; append=true saves it.",
    )
    def research_one_tool(target: str, append: bool = False) -> dict[str, Any]:
        return research_one(target, append=append, repo=repo)

    return server


def main() -> None:
    build_server().run("stdio")


if __name__ == "__main__":
    main()
