"""PO-31: a short, data-derived reason for every unknown cell. Never invented: each rule reads run data."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from opsresearch.pass3 import get_field, is_unknown

FIELDS = [
    "one_liner",
    "auth_methods",
    "self_serve",
    "gate_type",
    "api_surface.rest",
    "api_surface.graphql",
    "existing_mcp",
    "composio_has_toolkit",
    "buildability",
    "main_blocker",
]
RULE_FIELDS = ("buildability", "main_blocker")
ACCESS_FIELDS = ("self_serve", "gate_type")


def _had_quoted_value(prior: dict[str, Any] | None, field: str) -> bool:
    if not prior or is_unknown(get_field(prior, field)):
        return False
    names = {field, "api_surface"} if field.startswith("api_surface.") else {field}
    return any(e["field"] in names and e["source"] == "doc" for e in prior.get("evidence") or [])


def _research_node(log3: dict[str, Any] | None, field: str) -> dict[str, Any] | None:
    for node in (log3 or {}).get("nodes") or []:
        if node.get("node") == "reresearch3" and (field in (node.get("fields") or []) or node.get("skipped")):
            return node
    return None


def derive_unknown_reasons(
    record: dict[str, Any],
    prior: dict[str, Any] | None,
    log3: dict[str, Any] | None,
    *,
    pages: list[str],
) -> dict[str, str]:
    verdicts = record.get("verifier_verdicts") or {}
    crashed = any("crashed" in r or "pipeline error" in r for r in record.get("needs_human_reasons") or [])
    out: dict[str, str] = {}
    for field in FIELDS:
        if not is_unknown(get_field(record, field)):
            continue
        if verdicts.get(field) == "contradicted":
            out[field] = "verifier contradicted"
            continue
        if field in RULE_FIELDS:
            api = record["api_surface"]
            if is_unknown(record["self_serve"]):
                out[field] = "dep:self_serve"
            elif is_unknown(api["rest"]) and is_unknown(api["graphql"]):
                out[field] = "dep:api_surface"
            else:
                out[field] = "inputs don't fit the rule"
            continue
        if _had_quoted_value(prior, field):
            out[field] = "quote not on page"
            continue
        node = _research_node(log3, field)
        if node is not None:
            if node.get("skipped"):
                out[field] = "re-research skipped (time)"
            elif not node.get("docs"):
                out[field] = "no docs page found"
            elif not node.get("model") and node.get("errors"):
                out[field] = "re-research hit model quota"
            else:
                out[field] = "no quote found in docs"
            continue
        if crashed:
            out[field] = "pass-2 quota crash, no recheck"
            continue
        if field in ACCESS_FIELDS and not any("pricing" in u.lower() for u in pages):
            out[field] = "no pricing page"
            continue
        out[field] = "no quote found in docs"
    # Rule-derived fields: chain to the input's own reason so the caption names the root cause.
    for field in RULE_FIELDS:
        value = out.get(field, "")
        if value.startswith("dep:"):
            source = value[4:]
            root = out.get("self_serve" if source == "self_serve" else "api_surface.rest", "no quote found in docs")
            out[field] = f"{source} unknown: {root}"
    return out


def root_reason(reason: str) -> tuple[str, bool]:
    """('re-research hit model quota', True) for 'self_serve unknown: re-research hit model quota'."""
    head, sep, tail = reason.partition(" unknown: ")
    if sep and head in ("self_serve", "api_surface"):
        return tail, True
    return reason, False


def tally(records: list[dict[str, Any]]) -> list[tuple[str, int, int]]:
    """(root reason, cells, of which derived) sorted by cells."""
    counts: Counter = Counter()
    derived: Counter = Counter()
    for r in records:
        for field, reason in (r.get("unknown_reason") or {}).items():
            if field == "one_liner":  # not shown on the page, so not counted
                continue
            root, is_derived = root_reason(reason)
            counts[root] += 1
            derived[root] += is_derived
    return [(k, v, derived[k]) for k, v in counts.most_common()]


def annotate(repo: Path, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fill unknown_reason on every record from pass-1 records, pass-3 logs and the page cache."""
    from opsresearch.discover import cached_page_urls
    from opsresearch.fetch import app_slug

    data = repo / "data"
    p1_path = data / "results_pass1.json"
    p1 = {r["app"]: r for r in json.loads(p1_path.read_text())["records"]} if p1_path.exists() else {}
    out = []
    for record in records:
        log_path = data / "runs" / f"{app_slug(record['app'])}_pass3.json"
        log3 = json.loads(log_path.read_text()) if log_path.exists() else None
        pages = cached_page_urls(repo / "cache", record["app"])
        new = dict(record)
        new["unknown_reason"] = derive_unknown_reasons(record, p1.get(record["app"]), log3, pages=pages)
        out.append(new)
    return out
