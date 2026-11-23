"""PO-18: map the labelling sheet onto data/gold/gold_labels.csv.

The sheet uses friendly dropdowns and free text (e.g. "OAuth2, API key", "partner approval").
Values that do not map are left blank (unlabelled) and reported; they are never guessed.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

from opsresearch.eval_harness import LABEL_COLUMNS

AUTH = {
    "oauth2": "oauth2",
    "oauth 2": "oauth2",
    "oauth 2.0": "oauth2",
    "oauth2.0": "oauth2",
    "oauth": "oauth2",
    "oauth1": "oauth1",
    "oauth 1": "oauth1",
    "oauth 1.0a": "oauth1",
    "api key": "api_key",
    "api_key": "api_key",
    "apikey": "api_key",
    "api keys": "api_key",
    "basic": "basic",
    "basic auth": "basic",
    "bearer": "bearer_token",
    "token": "bearer_token",
    "bearer/token": "bearer_token",
    "bearer token": "bearer_token",
    "bearer_token": "bearer_token",
    "pat": "bearer_token",
    "personal access token": "bearer_token",
    "jwt": "jwt",
    "session": "session",
    "none": "none",
    "no auth": "none",
    "unknown": "unknown",
}
SELF_SERVE = {
    "self_serve_free": "self_serve_free",
    "free": "self_serve_free",
    "self-serve free": "self_serve_free",
    "self serve free": "self_serve_free",
    "self_serve_trial": "self_serve_trial",
    "trial": "self_serve_trial",
    "free trial": "self_serve_trial",
    "self-serve trial": "self_serve_trial",
    "self serve trial": "self_serve_trial",
    "paid_plan_required": "paid_plan_required",
    "paid": "paid_plan_required",
    "paid plan": "paid_plan_required",
    "paid plan required": "paid_plan_required",
    "admin_approval": "admin_approval",
    "admin approval": "admin_approval",
    "partner_gated": "partner_gated",
    "partner": "partner_gated",
    "partner gated": "partner_gated",
    "partner-gated": "partner_gated",
    "contact_sales": "contact_sales",
    "contact sales": "contact_sales",
    "sales": "contact_sales",
    "unknown": "unknown",
}
MCP = {"official": "official", "community": "community", "none": "none", "no": "none", "unknown": "unknown"}
BREADTH = {"narrow": "narrow", "medium": "medium", "broad": "broad", "unknown": "unknown"}
BUILD = {
    "yes": "yes",
    "no": "no",
    "unknown": "unknown",
    "yes_with_caveats": "yes_with_caveats",
    "yes with caveats": "yes_with_caveats",
    "caveats": "yes_with_caveats",
    "partial": "yes_with_caveats",
    "maybe": "yes_with_caveats",
}
BLOCKER_RULES = [
    ("partner", "partner_program"),
    ("sales", "contact_sales"),
    ("no public api", "no_public_api"),
    ("no api", "no_public_api"),
    ("login", "login_walled_docs"),
    ("review", "platform_review"),
    ("approval", "platform_review"),
    ("enterprise", "enterprise_only"),
    ("paid", "enterprise_only"),
    ("unclear", "unclear_entity"),
    ("ambiguous", "unclear_entity"),
    ("none", "none"),
    ("unknown", "unknown"),
]


def _key(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _lookup(table: dict[str, str], raw: str, app: str, field: str, unmapped: list) -> str:
    key = _key(raw)
    if not key:
        return ""
    if key in table:
        return table[key]
    unmapped.append((app, field, raw.strip()))
    return ""


def _api(raw: str, app: str, unmapped: list) -> tuple[str, str]:
    key = _key(raw)
    if not key:
        return "", ""
    if key == "unknown":
        return "unknown", "unknown"
    rest, gql = "rest" in key, "graphql" in key
    if rest or gql:
        return ("yes" if rest else "no"), ("yes" if gql else "no")
    if any(word in key for word in ("none", "no api", "no public", "sdk", "cli", "mcp only", "mcp-only")):
        return "no", "no"
    unmapped.append((app, "api_type", raw.strip()))
    return "", ""


def map_row(row: dict[str, str]) -> tuple[dict[str, str], list[tuple[str, str, str]]]:
    app = row.get("app", "")
    unmapped: list[tuple[str, str, str]] = []
    out = dict.fromkeys(LABEL_COLUMNS, "")
    for col in ("app", "category", "split", "archetype"):
        out[col] = row.get(col, "")
    parts = [p for p in re.split(r"[,;|+]| and ", row.get("auth_methods", "")) if p.strip()]
    methods = []
    for part in parts:
        value = _lookup(AUTH, part, app, "auth_methods", unmapped)
        if value and value not in methods:
            methods.append(value)
    out["auth_methods"] = "|".join(sorted(methods))
    out["self_serve"] = _lookup(SELF_SERVE, row.get("self_serve", ""), app, "self_serve", unmapped)
    out["api_rest"], out["api_graphql"] = _api(row.get("api_type", ""), app, unmapped)
    out["api_breadth"] = _lookup(BREADTH, row.get("api_breadth", ""), app, "api_breadth", unmapped)
    mcp_raw = _key(row.get("existing_mcp", ""))
    mcp_raw = "official" if mcp_raw.startswith("yes") and "community" not in mcp_raw else mcp_raw
    out["existing_mcp"] = _lookup(MCP, mcp_raw, app, "existing_mcp", unmapped)
    out["buildability"] = _lookup(BUILD, row.get("buildable_today", ""), app, "buildability", unmapped)
    blocker = _key(row.get("main_blocker", ""))
    labelled = any(out[f] for f in ("self_serve", "buildability", "auth_methods"))
    if blocker:
        out["main_blocker"] = next((enum for word, enum in BLOCKER_RULES if word in blocker), "")
        if not out["main_blocker"]:
            unmapped.append((app, "main_blocker", row.get("main_blocker", "").strip()))
    elif labelled:
        out["main_blocker"] = "none"  # the sheet says: blank if none
    evidence = row.get("evidence_url", "").strip()
    docs = row.get("docs_url_found", "").strip()
    out["doc_auth"] = evidence
    out["doc_self_serve"] = evidence
    out["doc_api"] = (row.get("evidence_api_url") or "").strip() or docs
    out["doc_mcp"] = (row.get("evidence_mcp_url") or "").strip() or docs
    out["doc_buildability"] = (row.get("evidence_buildability_url") or "").strip() or evidence or docs
    labeller = (row.get("labeller") or "").strip()
    notes = row.get("labeller_notes", "").strip()
    minutes = row.get("minutes_spent", "").strip()
    out["notes"] = "; ".join(x for x in [labeller, notes, f"{minutes} min" if minutes else ""] if x)
    return out, unmapped


def import_sheet(sheet_csv: Path, out_csv: Path) -> dict:
    with Path(sheet_csv).open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    mapped, unmapped = [], []
    for row in rows:
        if not row.get("app"):
            continue
        out, bad = map_row(row)
        mapped.append(out)
        unmapped.extend(bad)
    with Path(out_csv).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=LABEL_COLUMNS)
        writer.writeheader()
        writer.writerows(mapped)
    labelled = sum(1 for r in mapped if r["self_serve"] or r["auth_methods"] or r["buildability"])
    return {"rows": len(mapped), "labelled_rows": labelled, "unmapped": unmapped}
