"""Pass-1 extraction. The model may say unknown. A quote that is not in the docs does not count."""

from __future__ import annotations

import json
from typing import Any

from opsresearch.fetch import CachedDoc
from opsresearch.llm import LlmClient, complete_with_fallback
from opsresearch.ratelimit import RateLimiter
from opsresearch.schema import AppRecord, app_record_json_schema, unknown_record
from opsresearch.textmatch import quote_in_text

RUBRIC = """
Answer only from the pages below. If a page does not say it, use "unknown".
Never guess. A gated or partner-only API is a valid finding when a page says so.
Every concrete value needs evidence: a url copied from the page header and a verbatim quote
of at least 8 characters that appears in that page.
Enums:
- auth_methods: oauth2, oauth1, api_key, basic, bearer_token, jwt, session, none, unknown
- self_serve: self_serve_free, self_serve_trial, paid_plan_required, admin_approval,
  partner_gated, contact_sales, unknown
- gate_type: none, paid_plan, admin_approval, partner, contact_sales, platform_review,
  login_wall, unknown
- api_surface.rest and api_surface.graphql: yes, no, unknown
- api_surface.breadth: narrow (under 20 endpoints), medium (20-99), broad (100+), unknown
- api_surface.styles: rest, graphql, soap, sdk_only, cli_only, none, unknown
- existing_mcp: official, community, none, unknown
- composio_has_toolkit: yes, no, unknown
- buildability: yes, yes_with_caveats, no, unknown
- main_blocker: none, partner_program, contact_sales, no_public_api, login_walled_docs,
  unclear_entity, platform_review, enterprise_only, unknown
evidence[].source is "doc". confidence is a number from 0 to 1 or null.
""".strip()


def extraction_prompt(
    app: str,
    category: str,
    docs: list[CachedDoc],
    *,
    only_fields: list[str] | None = None,
) -> str:
    pages = []
    for index, doc in enumerate(docs, start=1):
        text = doc.text
        clipped = False
        if len(text) > 8000:
            text = text[:8000]
            clipped = True
        note = " (truncated)" if clipped else ""
        pages.append(f"## Page {index}{note}\nURL: {doc.url}\n{text}")
    focus = ""
    if only_fields:
        focus = (
            "Re-answer only these fields: "
            + ", ".join(only_fields)
            + ". Leave every other field unknown.\n"
        )
    return (
        f"App: {app}\nGiven category: {category}\n"
        "The category is provided by the task list. Do not invent a different one.\n"
        f"{focus}{RUBRIC}\n\n" + "\n\n".join(pages)
    )


def _overlay(app: str, category: str, data: dict[str, Any]) -> dict[str, Any]:
    base = unknown_record(app, category).model_dump(mode="json")
    for key, value in data.items():
        if key in base and key not in {"app", "category"}:
            base[key] = value
    if base.get("confidence") in {"unknown", ""}:
        base["confidence"] = None
    return base


def drop_unsupported_quotes(record: AppRecord, docs: list[CachedDoc]) -> AppRecord:
    blob = "\n".join(doc.text for doc in docs)
    kept = []
    for item in record.evidence:
        if item.source.value != "doc":
            kept.append(item)
            continue
        if quote_in_text(item.quote, blob):
            kept.append(item)
    if len(kept) == len(record.evidence):
        return record
    data = record.model_dump(mode="json")
    data["evidence"] = [item.model_dump(mode="json") for item in kept]
    return AppRecord.model_validate(data)


def extract_record(
    app: str,
    category: str,
    docs: list[CachedDoc],
    clients: list[LlmClient],
    limiter: RateLimiter,
    *,
    only_fields: list[str] | None = None,
) -> AppRecord:
    prompt = extraction_prompt(app, category, docs, only_fields=only_fields)
    schema = app_record_json_schema()
    errors: list[str] = []
    for client in clients:
        try:
            record = complete_with_fallback(prompt, schema, [client], limiter)
        except Exception as exc:
            errors.append(f"{getattr(client, 'model_name', 'llm')}: {type(exc).__name__}")
            continue
        data, model_name = record
        try:
            merged = _overlay(app, category, data)
            merged["extraction_model"] = model_name
            return drop_unsupported_quotes(AppRecord.model_validate(merged), docs)
        except Exception as exc:
            errors.append(f"{model_name}: {type(exc).__name__}")
    raise RuntimeError("extraction failed: " + "; ".join(errors))


def merge_fields(base: AppRecord, update: AppRecord, fields: list[str]) -> AppRecord:
    data = base.model_dump(mode="json")
    new = update.model_dump(mode="json")
    expanded = set(fields)
    if any(field == "api_surface" or field.startswith("api_surface.") for field in expanded):
        expanded.add("api_surface")

    def covers(field_name: str) -> bool:
        if field_name in expanded:
            return True
        return field_name.startswith("api_surface") and "api_surface" in expanded

    for field in list(expanded):
        if field in new and field != "evidence":
            data[field] = new[field]
    data["evidence"] = [item for item in data["evidence"] if not covers(item["field"])]
    data["evidence"] += [item for item in new["evidence"] if covers(item["field"])]
    reasons = {key: value for key, value in data["unknown_reasons"].items() if not covers(key)}
    for key, value in new["unknown_reasons"].items():
        if covers(key):
            reasons[key] = value
    data["unknown_reasons"] = reasons
    if new.get("extraction_model"):
        data["extraction_model"] = new["extraction_model"]
    return AppRecord.model_validate(data)


def docs_as_json(docs: list[CachedDoc]) -> str:
    return json.dumps([{"url": doc.url, "text": doc.text[:500]} for doc in docs])
