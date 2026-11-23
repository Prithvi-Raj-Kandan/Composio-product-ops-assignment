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

GATED_ACCESS = {"partner_gated", "contact_sales", "admin_approval", "paid_plan_required"}
GATED_TYPES = {"partner", "contact_sales", "admin_approval", "platform_review", "paid_plan", "login_wall"}
HIGH_STAKES = ("self_serve", "gate_type", "auth_methods")

ONE_TIME_SETUP = (
    "Set COMPOSIO_API_KEY, GEMINI_API_KEY, GROQ_API_KEY and TAVILY_API_KEY (shell or repo secrets); run "
    "<code>python -m opsresearch connect-tavily</code> once; approve merges and the GitHub Pages publish "
    "(the agent does not publish on its own)."
)


def _csv_rows(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open(newline="", encoding="utf-8") as handle:
        return sum(1 for row in csv.DictReader(handle) if (row.get("app") or "").strip())


def human_handoffs(repo: Path, records: list[dict[str, Any]], verification: dict[str, Any]) -> list[dict[str, Any]]:
    """Per-run points where the workflow stops for a human, with counts measured from this run's data."""
    data = repo / "data"
    exhausted = sum(
        1
        for r in records
        if any(x.startswith("verification loops exhausted") for x in r.get("needs_human_reasons") or [])
    )
    crashed = sum(
        1 for r in records if any("crashed" in x or "pipeline error" in x for x in r.get("needs_human_reasons") or [])
    )
    contradicted_now = sum(
        1 for r in records for v in (r.get("verifier_verdicts") or {}).values() if v == "contradicted"
    )
    gated = sum(1 for r in records if r.get("self_serve") in GATED_ACCESS or r.get("gate_type") in GATED_TYPES)
    quote_only = sum(
        1
        for r in records
        for f, tag in (r.get("field_confidence") or {}).items()
        if tag == "quote-verified" and f in HIGH_STAKES
    )
    quote_only_apps = sum(
        1
        for r in records
        if any(tag == "quote-verified" and f in HIGH_STAKES for f, tag in (r.get("field_confidence") or {}).items())
    )
    gold_rows = _csv_rows(data / "gold" / "gold_sample.csv")
    return [
        {
            "name": "Which company is this?",
            "key": "domain_overrides",
            "count": _csv_rows(data / "domain_overrides.csv"),
            "count_label": "overrides in use",
            "trigger": "The agent only accepts pages on hosts derived from the task's hint "
            "(<code>url_is_official</code>). "
            "There is no automatic confidence score: when the hint is ambiguous (a rebrand, a generic name, "
            "or a hinted host with no API docs) the row comes back mostly <code>unknown</code>.",
            "decides": "The official homepage and docs URL for the entity.",
            "edits": "<code>data/domain_overrides.csv</code> (app, homepage, docs_url, extra_source, note)",
            "flows_back": "Rerun that app (<code>research-one</code> or <code>run --pass 1 --only APP</code>). "
            'The record gets <code>provenance.source = human_override</code> and the tag "human override".',
        },
        {
            "name": "Verification loops could not settle a field",
            "key": "loops_exhausted",
            "count": exhausted,
            "count_label": f"rows ({crashed} more are pass-2 crash rows; needs_human total: "
            f'<b data-count="needs_human">{sum(1 for r in records if r.get("needs_human"))}</b>)',
            "trigger": "In pass 2 a field is still <i>contradicted</i> or <i>insufficient</i> after "
            "<code>MAX_LOOPS = 2</code> re-research loops, so <code>needs_human = true</code> with the reason "
            '"verification loops exhausted: FIELDS". Rows whose pass 2 crashed also stay needs_human.',
            "decides": "Whether a quoted page really settles the field, or it should stay <code>unknown</code>.",
            "edits": "Reads <code>needs_human_reasons</code> in <code>data/results.json</code> and the findings in "
            "<code>data/runs/APP_pass2.json</code>. A better source goes into "
            "<code>domain_overrides.csv</code> (extra_source); cells are never hand-edited.",
            "flows_back": "Delete that app's checkpoint and rerun <code>run --pass 2 --resume</code> "
            "or <code>pass3 --resume</code>.",
        },
        {
            "name": "The verifier contradicted a claim",
            "key": "contradicted",
            "count": contradicted_now,
            "count_label": f"cells demoted now ({verification.get('status', {}).get('contradicted', 0)} "
            "contradicted findings across the pass-2 loops)",
            "trigger": "The second model or the MCP registry returned <i>contradicted</i>. Only this verdict demotes "
            "a claim to <code>unknown</code>; the verdict is stored per field in <code>verifier_verdicts</code>.",
            "decides": "Who is right: the quoted doc or the verifier.",
            "edits": "The run log for that app (<code>data/runs/</code>); a better doc URL goes in "
            "<code>domain_overrides.csv</code>.",
            "flows_back": "Rerun with <code>--resume</code> after deleting the app's checkpoint.",
        },
        {
            "name": "Access is gated",
            "key": "gated",
            "count": gated,
            "count_label": "rows with a gate",
            "trigger": "<code>self_serve</code> is partner_gated, contact_sales, admin_approval or paid_plan_required, "
            "or <code>gate_type</code> is not none/unknown. The fixed rule then sets buildability to no or "
            "yes_with_caveats.",
            "decides": "Whether the gate is real for Composio (apply to the partner program, talk to sales, "
            "a trial that needs a card).",
            "edits": "Nothing in the data: the rule's verdict stays agent output; the decision goes in the "
            "review notes or the PR.",
            "flows_back": "If the docs moved, add the new URL to <code>domain_overrides.csv</code> and rerun.",
        },
        {
            "name": "High-stakes field backed by a quote only",
            "key": "quote_only_high_stakes",
            "count": quote_only,
            "count_label": f"cells in {quote_only_apps} apps (self_serve, gate_type, auth_methods)",
            "trigger": "<code>field_confidence = quote-verified</code>: the quote is on the official page, "
            "but no second model said <i>supported</i> (quota-limited, or it judged insufficient).",
            "decides": "Spot-check the linked quote before relying on the cell.",
            "edits": "Nothing; the tag stays until a second model confirms it.",
            "flows_back": "<code>pass3 --resume</code> after deleting the checkpoint, once model quotas allow.",
        },
        {
            "name": "Gold-label audit (evaluation time)",
            "key": "gold_audit",
            "count": gold_rows,
            "count_label": "apps in the gold sample, AI-drafted, human review pending",
            "trigger": "Before an accuracy number is trusted. The labels' notes say they were AI-drafted.",
            "decides": "Whether each gold cell is right, using the evidence URL per field.",
            "edits": "The labelling Google Sheet, then <code>import-gold</code> into "
            "<code>data/gold/gold_labels.csv</code>.",
            "flows_back": "<code>python -m opsresearch eval</code>, then re-render the page.",
        },
    ]


def _gold_labelled_by_ai(path: Path) -> bool:
    """True when the gold labels say an AI assistant made them (labeller note on any row)."""
    if not path.exists():
        return False
    with path.open(newline="", encoding="utf-8") as handle:
        return any(
            marker in (row.get("notes") or "").lower()
            for row in csv.DictReader(handle)
            for marker in ("ai-drafted", "assistant-labelled")
        )


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
    verification = verification_stats(data / "runs")
    pass2 = _records(_load(data / "results_pass2.json"))
    return {
        "generated": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "records": rows,
        "n": len(records),
        "patterns": patterns,
        "patterns_json": json.dumps(patterns, default=str),
        "evals": evals,
        "gold_by_ai": gold_by_ai,
        "verification": verification,
        "handoffs": human_handoffs(repo, records, verification),
        "delta": pass_delta(pass1, records) if pass1 else None,
        "pass3": pass3_summary(pass1, pass2, records, stats.get(3), data / "runs"),
        "needs_human": [r for r in rows if r.get("needs_human")],
        "derived_count": sum(1 for r in rows if r["derived"]),
        "overrides": [r for r in rows if r["human_override"]],
        "stats": stats,
        "one_time_setup": ONE_TIME_SETUP,
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
