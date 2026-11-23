"""PO-10 patterns. pandas computes every number; the LLM only words a headline from those numbers.

A headline sentence that carries a number not present in the computed tables is rejected and the
deterministic headline is used instead.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

import pandas as pd

SELF_SERVE_OPEN = {"self_serve_free", "self_serve_trial"}
GATED = {"partner_gated", "contact_sales", "admin_approval", "paid_plan_required"}
OUTREACH_BLOCKERS = {"partner_program", "contact_sales", "enterprise_only", "platform_review"}
BUILDABLE = {"yes", "yes_with_caveats"}


FRAME_COLUMNS = [
    "app",
    "category",
    "auth_methods",
    "self_serve",
    "gate_type",
    "rest",
    "graphql",
    "breadth",
    "existing_mcp",
    "composio_has_toolkit",
    "buildability",
    "main_blocker",
    "needs_human",
    "human_override",
]


def records_frame(records: Iterable[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for r in records:
        api = r.get("api_surface") or {}
        rows.append(
            {
                "app": r.get("app"),
                "category": r.get("category"),
                "auth_methods": [str(a) for a in (r.get("auth_methods") or ["unknown"])],
                "self_serve": r.get("self_serve", "unknown"),
                "gate_type": r.get("gate_type", "unknown"),
                "rest": api.get("rest", "unknown"),
                "graphql": api.get("graphql", "unknown"),
                "breadth": api.get("breadth", "unknown"),
                "existing_mcp": r.get("existing_mcp", "unknown"),
                "composio_has_toolkit": r.get("composio_has_toolkit", "unknown"),
                "buildability": r.get("buildability", "unknown"),
                "main_blocker": r.get("main_blocker", "unknown"),
                "needs_human": bool(r.get("needs_human")),
                "human_override": (r.get("provenance") or {}).get("source") == "human_override",
            }
        )
    return pd.DataFrame(rows, columns=FRAME_COLUMNS)


def _counts(series: pd.Series) -> dict[str, int]:
    return {str(k): int(v) for k, v in series.value_counts().items()}


def _crosstab(df: pd.DataFrame, col: str) -> dict[str, dict[str, int]]:
    if df.empty:
        return {}
    table = pd.crosstab(df["category"], df[col])
    return {str(cat): {str(k): int(v) for k, v in row.items() if v} for cat, row in table.iterrows()}


def compute_patterns(df: pd.DataFrame) -> dict[str, Any]:
    n = int(len(df))
    auth = df.explode("auth_methods")
    known_auth = auth[auth["auth_methods"] != "unknown"]
    open_mask = df["self_serve"].isin(SELF_SERVE_OPEN)
    gated_mask = df["self_serve"].isin(GATED)
    build_mask = df["buildability"].isin(BUILDABLE)
    easy = df[build_mask & open_mask & (df["composio_has_toolkit"] != "yes")]
    outreach = df[df["self_serve"].isin(GATED - {"paid_plan_required"}) | df["main_blocker"].isin(OUTREACH_BLOCKERS)]
    blockers = df[~df["main_blocker"].isin({"none", "unknown"})]
    by_cat = []
    for cat, group in df.groupby("category"):
        size = len(group)
        by_cat.append(
            {
                "category": str(cat),
                "apps": int(size),
                "self_serve_open": int(group["self_serve"].isin(SELF_SERVE_OPEN).sum()),
                "gated": int(group["self_serve"].isin(GATED).sum()),
                "unknown": int((group["self_serve"] == "unknown").sum()),
                "buildable": int(group["buildability"].isin(BUILDABLE).sum()),
                "in_composio": int((group["composio_has_toolkit"] == "yes").sum()),
            }
        )

    def rows(frame: pd.DataFrame) -> list[dict[str, str]]:
        cols = ["app", "category", "self_serve", "main_blocker", "composio_has_toolkit", "buildability"]
        return frame[cols].sort_values("app").to_dict(orient="records")

    return {
        "totals": {
            "apps": n,
            "self_serve_open": int(open_mask.sum()),
            "gated": int(gated_mask.sum()),
            "unknown_self_serve": int((df["self_serve"] == "unknown").sum()),
            "buildable": int(build_mask.sum()),
            "not_buildable": int((df["buildability"] == "no").sum()),
            "unknown_buildability": int((df["buildability"] == "unknown").sum()),
            "in_composio": int((df["composio_has_toolkit"] == "yes").sum()),
            "not_in_composio": int((df["composio_has_toolkit"] == "no").sum()),
            "official_mcp": int((df["existing_mcp"] == "official").sum()),
            "any_mcp": int(df["existing_mcp"].isin({"official", "community"}).sum()),
            "needs_human": int(df["needs_human"].sum()),
            "human_override": int(df["human_override"].sum()),
            "unknown_auth": int((df["auth_methods"].apply(lambda a: a == ["unknown"])).sum()),
            "easy_wins": int(len(easy)),
            "needs_outreach": int(len(outreach)),
            "graphql": int((df["graphql"] == "yes").sum()),
            "rest": int((df["rest"] == "yes").sum()),
        },
        "auth_counts": _counts(known_auth["auth_methods"]),
        "auth_by_category": _crosstab(auth, "auth_methods"),
        "self_serve_counts": _counts(df["self_serve"]),
        "self_serve_by_category": _crosstab(df, "self_serve"),
        "blocker_counts": _counts(blockers["main_blocker"]),
        "mcp_counts": _counts(df["existing_mcp"]),
        "composio_counts": _counts(df["composio_has_toolkit"]),
        "buildability_counts": _counts(df["buildability"]),
        "breadth_counts": _counts(df["breadth"]),
        "by_category": sorted(by_cat, key=lambda r: r["category"]),
        "easy_wins": rows(easy),
        "needs_outreach": rows(outreach),
    }


def _allowed_numbers(p: dict[str, Any]) -> tuple[set[int], set[int]]:
    ints: set[int] = set()

    def walk(value: Any) -> None:
        if isinstance(value, bool):
            return
        if isinstance(value, int):
            ints.add(value)
        elif isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    walk({k: v for k, v in p.items() if k not in {"easy_wins", "needs_outreach"}})
    ints.add(len(p.get("easy_wins", [])))
    ints.add(len(p.get("needs_outreach", [])))
    total = p["totals"]["apps"] or 1
    pcts: set[int] = set()
    for i in ints:
        if 0 <= i <= total:
            pct = i / total * 100
            pcts.update({int(pct), round(pct), int(pct) + 1})
    for cat in p.get("by_category", []):
        size = cat["apps"] or 1
        for key in ("self_serve_open", "gated", "buildable", "in_composio", "unknown"):
            pct = cat[key] / size * 100
            pcts.update({int(pct), round(pct), int(pct) + 1})
    return ints, pcts


NUMBER = re.compile(r"(\d+(?:\.\d+)?)(\s*%|\s*percent)?")


def fact_check(text: str, p: dict[str, Any]) -> list[str]:
    """Return every number in text that the computed tables do not contain."""
    ints, pcts = _allowed_numbers(p)
    bad: list[str] = []
    for match in NUMBER.finditer(text):
        raw, pct = match.group(1), match.group(2)
        value = float(raw)
        if pct:
            if round(value) not in pcts:
                bad.append(raw)
        elif not value.is_integer() or int(value) not in ints:
            bad.append(raw)
    return bad


def deterministic_bullets(p: dict[str, Any]) -> list[str]:
    t = p["totals"]
    n = t["apps"]
    auth = p["auth_counts"]
    top_auth = sorted(auth.items(), key=lambda kv: -kv[1])[:2]
    bullets = []
    if top_auth:
        bullets.append(
            "Auth: "
            + " and ".join(f"{name} appears in {count} of {n} apps" for name, count in top_auth)
            + f"; {t['unknown_auth']} apps had no auth method the agent could prove."
        )
    bullets.append(
        f"Access: {t['self_serve_open']} of {n} apps let a developer get credentials alone (free or trial), "
        f"{t['gated']} are gated (paid plan, admin, partner or sales), and {t['unknown_self_serve']} stay unknown."
    )
    if p["blocker_counts"]:
        name, count = max(p["blocker_counts"].items(), key=lambda kv: kv[1])
        bullets.append(f"Most common blocker: {name} ({count} apps).")
    bullets.append(
        f"Easy wins: {t['easy_wins']} apps are self-serve, buildable, and not yet a Composio toolkit. "
        f"Needs outreach: {t['needs_outreach']} apps sit behind a partner or sales gate."
    )
    bullets.append(
        f"MCP: {t['official_mcp']} apps ship an official MCP server; Composio already covers {t['in_composio']} of {n}."
    )
    return bullets


HEADLINE_SCHEMA = {
    "type": "object",
    "properties": {"bullets": {"type": "array", "items": {"type": "string"}}},
    "required": ["bullets"],
}


def headline(p: dict[str, Any], client: Any | None = None) -> dict[str, Any]:
    fallback = deterministic_bullets(p)
    if client is None:
        return {"bullets": fallback, "source": "deterministic", "rejected_numbers": []}
    facts = {k: v for k, v in p.items() if k not in {"auth_by_category", "self_serve_by_category"}}
    prompt = (
        "You write the headline for a research case study on 100 apps for an agent-tools company.\n"
        "Write 4 or 5 short plain-English bullets on the patterns: which auth dominates, which categories "
        "are self-serve vs gated, the most common blocker, easy wins vs what needs outreach, MCP coverage.\n"
        "Use ONLY numbers that appear in the JSON below (counts, or a percentage of the total). "
        'Do not round, estimate, or add any other number. Return JSON {"bullets": [...]}.\n\n'
        + json.dumps(facts, default=str)
    )
    try:
        raw = client.complete_json(prompt, HEADLINE_SCHEMA)
        bullets = [str(b) for b in raw.get("bullets", []) if str(b).strip()]
    except Exception as exc:
        return {
            "bullets": fallback,
            "source": "deterministic_fallback",
            "rejected_numbers": [],
            "error": type(exc).__name__,
        }
    rejected = sorted({n for b in bullets for n in fact_check(b, p)})
    if rejected or not bullets:
        return {"bullets": fallback, "source": "deterministic_fallback", "rejected_numbers": rejected}
    return {"bullets": bullets, "source": getattr(client, "model_name", "llm"), "rejected_numbers": []}
