"""PO-11 render: one static, Composio-styled case-study page plus the agent-readable files beside it."""

from __future__ import annotations

import csv
import json
import shutil
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from opsresearch.patterns import compute_patterns, headline, records_frame

REPO_URL = "https://github.com/Prithvi-Raj-Kandan/Composio-product-ops-assignment"
PAGES_URL = "https://prithvi-raj-kandan.github.io/Composio-product-ops-assignment/"

HUMAN_POINTS = [
    (
        "API keys",
        "Replicator",
        "Before any run",
        "Set COMPOSIO_API_KEY, GEMINI_API_KEY, GROQ_API_KEY, TAVILY_API_KEY (env or repo secrets).",
    ),
    (
        "Composio Tavily connection",
        "Replicator, once",
        "Before the first run",
        "Run `python -m opsresearch connect-tavily` so search goes through Composio's Tavily toolkit.",
    ),
    (
        "Ambiguous domains",
        "Owner (done for 6 apps)",
        "Before pass 1",
        "Confirm the official site for entities the agent cannot pin down; stored in data/domain_overrides.csv "
        "and marked 'human override' on every affected row.",
    ),
    (
        "Gold labels (audit)",
        "Owner / reviewer (not done yet)",
        "Before accuracy is trusted",
        "The 20 gold labels were made by an AI assistant from official docs, not by a human. A human should audit "
        "them (evidence URL per field in data/gold/gold_labels.csv), fix any wrong cell, and re-run eval.",
    ),
    (
        "needs_human rows",
        "Owner / reviewer",
        "After pass 2",
        "Read rows where the verification loops could not settle a field, plus their run logs in data/runs/.",
    ),
    (
        "Gated edge cases",
        "Owner",
        "While reading results",
        "Judge cases the rubric cannot see: a trial that needs a card, a sandbox that still needs sales.",
    ),
    (
        "Publish",
        "Owner",
        "Before submit",
        "Approve merges and the GitHub Pages publish. The agent does not publish itself.",
    ),
]


def _gold_labelled_by_ai(path: Path) -> bool:
    """True when the gold labels say an AI assistant made them (labeller note on any row)."""
    if not path.exists():
        return False
    with path.open(newline="", encoding="utf-8") as handle:
        return any("assistant-labelled" in (row.get("notes") or "").lower() for row in csv.DictReader(handle))


def _load(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _records(payload: Any) -> list[dict[str, Any]]:
    if payload is None:
        return []
    if isinstance(payload, dict):
        return list(payload.get("records") or [])
    return list(payload)


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def verification_stats(runs_dir: Path) -> dict[str, Any]:
    status = Counter()
    by_verifier: dict[str, Counter] = {}
    loops = Counter()
    fixed = Counter()
    apps = 0
    for path in sorted(runs_dir.glob("*_pass2.json")):
        log = _load(path, {})
        apps += 1
        loops[int(log.get("loops") or 0)] += 1
        verifies = [n for n in log.get("nodes", []) if n.get("node") == "verify"]
        for node in verifies:
            for f in node.get("findings", []):
                status[f.get("status")] += 1
                by_verifier.setdefault(f.get("verifier", "?"), Counter())[f.get("status")] += 1
        if len(verifies) >= 2:
            first = set(verifies[0].get("failing") or [])
            last = set(verifies[-1].get("failing") or [])
            for field in first - last:
                fixed[field] += 1
    return {
        "apps": apps,
        "status": dict(status),
        "by_verifier": {k: dict(v) for k, v in by_verifier.items()},
        "loops": {str(k): v for k, v in sorted(loops.items())},
        "fields_settled_by_loops": dict(fixed.most_common()),
        "fields_settled_total": int(sum(fixed.values())),
    }


def pass_delta(pass1: list[dict[str, Any]], pass2: list[dict[str, Any]]) -> dict[str, Any]:
    """Coverage change between passes (not accuracy): how many cells left 'unknown'."""
    fields = ["self_serve", "gate_type", "existing_mcp", "buildability", "main_blocker", "composio_has_toolkit"]

    def unknowns(records: list[dict[str, Any]]) -> dict[str, int]:
        out = {f: sum(1 for r in records if r.get(f) == "unknown") for f in fields}
        out["auth_methods"] = sum(1 for r in records if (r.get("auth_methods") or ["unknown"]) == ["unknown"])
        return out

    a, b = unknowns(pass1), unknowns(pass2)
    changed = 0
    index = {r["app"]: r for r in pass1}
    for r in pass2:
        old = index.get(r["app"])
        if old is None:
            continue
        for f in fields:
            if old.get(f) != r.get(f):
                changed += 1
    return {"unknown_pass1": a, "unknown_pass2": b, "cells_changed": changed}


def pass3_summary(
    pass1: list[dict[str, Any]],
    pass2: list[dict[str, Any]],
    pass3: list[dict[str, Any]],
    stats: dict[str, Any] | None,
    runs_dir: Path | None = None,
) -> dict[str, Any] | None:
    """Unknown counts pass 1 -> 2 -> 3 plus confidence tags, only when pass 3 is the published data."""
    if not stats or not pass3 or not any(r.get("field_confidence") for r in pass3):
        return None
    from opsresearch.pass3 import COUNTED, count_unknowns

    a, b, c = count_unknowns(pass1), count_unknowns(pass2), count_unknowns(pass3)
    tags: Counter = Counter()
    for r in pass3:
        tags.update((r.get("field_confidence") or {}).values())
    by_app = {r.get("app"): r for r in pass3}
    claims = checked = 0
    for path in sorted(runs_dir.glob("*_pass3.json")) if runs_dir else []:
        log = _load(path, {})
        verdicts = (by_app.get(log.get("app")) or {}).get("verifier_verdicts") or {}
        for node in log.get("nodes") or []:
            if node.get("node") != "reresearch3":
                continue
            for field in node.get("kept") or []:
                claims += 1
                checked += verdicts.get(field) == "supported"
    crashed = sum(1 for r in pass2 if any("pipeline error" in x for x in r.get("needs_human_reasons") or []))
    return {
        "research_claims": claims,
        "research_supported": checked,
        "crashed": crashed,
        "quote_only": tags.get("quote-verified", 0),
        "supported_total": tags.get("verifier-supported", 0),
        "rows": [{"field": f, "p1": a[f], "p2": b[f], "p3": c[f]} for f in COUNTED],
        "tags": dict(tags.most_common()),
        "steps": stats.get("fields_filled_by_step") or {},
    }


def _evidence_by_field(record: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    out: dict[str, list[dict[str, str]]] = {}
    for ev in record.get("evidence") or []:
        key = str(ev.get("field", "")).split(".")[0]
        out.setdefault(key, []).append(ev)
    return out


def build_context(repo: Path, *, llm: bool = True) -> dict[str, Any]:
    data = repo / "data"
    records = _records(_load(data / "results.json"))
    pass1 = _records(_load(data / "results_pass1.json"))
    patterns = _load(data / "patterns.json")
    if patterns is None or patterns.get("totals", {}).get("apps") != len(records):
        patterns = compute_patterns(records_frame(records))
        client = None
        if llm:
            from opsresearch.llm import extraction_clients

            client = extraction_clients()[0]
        patterns["headline"] = headline(patterns, client=client)
    evals = {}
    for split in ("dev", "test"):
        rep = _load(data / "eval" / f"report_{split}.json")
        if rep and rep.get("pass2", {}).get("accuracy") is not None:
            evals[split] = rep
    gold_by_ai = _gold_labelled_by_ai(data / "gold" / "gold_labels.csv")
    rows = []
    for r in sorted(records, key=lambda x: str(x.get("app")).lower()):
        derived = any(
            e.get("field") == "buildability" and e.get("source") == "derived_rule" for e in r.get("evidence") or []
        )
        rows.append(
            {
                **r,
                "ev": _evidence_by_field(r),
                "derived": derived,
                "human_override": (r.get("provenance") or {}).get("source") == "human_override",
            }
        )
    stats = {p: _load(data / f"run_stats_pass{p}.json") for p in (1, 2, 3)}
    pass2 = _records(_load(data / "results_pass2.json"))
    return {
        "generated": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "records": rows,
        "n": len(records),
        "patterns": patterns,
        "patterns_json": json.dumps(patterns, default=str),
        "evals": evals,
        "gold_by_ai": gold_by_ai,
        "verification": verification_stats(data / "runs"),
        "delta": pass_delta(pass1, records) if pass1 else None,
        "pass3": pass3_summary(pass1, pass2, records, stats.get(3), data / "runs"),
        "needs_human": [r for r in rows if r.get("needs_human")],
        "derived_count": sum(1 for r in rows if r["derived"]),
        "overrides": [r for r in rows if r["human_override"]],
        "stats": stats,
        "human_points": HUMAN_POINTS,
        "repo_url": REPO_URL,
        "pages_url": PAGES_URL,
        "pct": _pct,
    }


def json_ld(ctx: dict[str, Any]) -> str:
    base = ctx["pages_url"]
    doc = {
        "@context": "https://schema.org",
        "@type": "Dataset",
        "name": "Agent-toolkit readiness of 100 apps (auth, access gates, API surface, MCP)",
        "description": "Docs-grounded research on 100 SaaS apps: auth methods, self-serve vs gated access, "
        "REST/GraphQL surface, existing MCP servers, Composio coverage, buildability, with an evidence URL and quote "
        "per field. Produced by an agent pipeline with verification loops; unknown is allowed.",
        "url": base,
        "isAccessibleForFree": True,
        "creator": {"@type": "Person", "name": "Prithvi Raj Kandan"},
        "dateModified": ctx["generated"],
        "variableMeasured": [
            "auth_methods",
            "self_serve",
            "gate_type",
            "api_surface",
            "existing_mcp",
            "composio_has_toolkit",
            "buildability",
            "main_blocker",
            "evidence",
        ],
        "distribution": [
            {"@type": "DataDownload", "encodingFormat": "application/json", "contentUrl": base + "results.json"},
            {"@type": "DataDownload", "encodingFormat": "text/csv", "contentUrl": base + "results.csv"},
            {
                "@type": "DataDownload",
                "encodingFormat": "application/schema+json",
                "contentUrl": base + "app_record.schema.json",
            },
        ],
    }
    return json.dumps(doc, indent=1).replace("</", "<\\/")


def llms_txt(ctx: dict[str, Any]) -> str:
    p = ctx["patterns"]
    lines = [
        "# Agent-toolkit readiness of 100 apps",
        "",
        "> Docs-grounded research on 100 apps for an agent-tools company: auth, self-serve vs gated, API surface,"
        " MCP, Composio coverage, buildability. Every non-unknown field carries an evidence URL and verbatim quote.",
        "",
        "## Data",
        f"- [results.json]({ctx['pages_url']}results.json): {ctx['n']} records, schema below",
        f"- [results.csv]({ctx['pages_url']}results.csv): same, flat",
        f"- [app_record.schema.json]({ctx['pages_url']}app_record.schema.json): JSON Schema",
        f"- [patterns.json]({ctx['pages_url']}patterns.json): computed pattern tables and headline",
        "",
        "## Headline",
        *[f"- {b}" for b in p["headline"]["bullets"]],
        "",
        "## Conventions",
        "- 'unknown' means the agent found no quotable evidence. It is not a guess.",
        "- needs_human=true marks rows the verification loops could not settle.",
        "- provenance.source=human_override marks rows whose official domain a human confirmed.",
        "",
        "## Tools",
        f"- MCP server (stdio): `python -m opsresearch.mcp_server` from {ctx['repo_url']}",
        '- Research a new app: `python -m opsresearch research-one "<name or url>" --append`',
    ]
    return "\n".join(lines) + "\n"


def render_site(repo: Path, out_dir: Path, *, llm: bool = True) -> Path:
    repo = Path(repo)
    out_dir = Path(out_dir)
    ctx = build_context(repo, llm=llm)
    env = Environment(
        loader=FileSystemLoader(Path(__file__).parent / "templates"),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    ctx["json_ld"] = json_ld(ctx)
    html = env.get_template("case_study.html.j2").render(**ctx)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(html, encoding="utf-8")
    (out_dir / "llms.txt").write_text(llms_txt(ctx), encoding="utf-8")
    (out_dir / "patterns.json").write_text(json.dumps(ctx["patterns"], indent=2, default=str) + "\n", encoding="utf-8")
    for src, name in [
        (repo / "data" / "results.json", "results.json"),
        (repo / "data" / "results.csv", "results.csv"),
        (repo / "schema" / "app_record.schema.json", "app_record.schema.json"),
    ]:
        if src.exists():
            shutil.copyfile(src, out_dir / name)
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")
    return out_dir
