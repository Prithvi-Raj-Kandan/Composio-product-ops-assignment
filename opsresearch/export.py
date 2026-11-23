"""Write the agent-facing JSON bundle and the human-facing CSV."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from opsresearch.schema import SCHEMA_VERSION, AppRecord, app_record_json_schema

CSV_COLUMNS = [
    "app",
    "category",
    "one_liner",
    "auth_methods",
    "self_serve",
    "gate_type",
    "gate_detail",
    "api_rest",
    "api_graphql",
    "api_breadth",
    "api_styles",
    "existing_mcp",
    "mcp_url",
    "composio_has_toolkit",
    "composio_slug",
    "composio_toolkit_type",
    "composio_tools_count",
    "buildability",
    "main_blocker",
    "blocker_detail",
    "confidence",
    "needs_human",
    "needs_human_reasons",
    "unknown_reasons",
    "evidence_json",
    "hint_url",
    "official_domain",
    "provenance_source",
    "source_id",
    "extraction_model",
    "verifier_model",
    "pass_id",
]


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _blank(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def record_to_row(record: dict[str, Any]) -> dict[str, str]:
    surface = record["api_surface"]
    row = {
        "app": record["app"],
        "category": record["category"],
        "one_liner": record["one_liner"],
        "auth_methods": "|".join(record["auth_methods"]),
        "self_serve": record["self_serve"],
        "gate_type": record["gate_type"],
        "gate_detail": record["gate_detail"],
        "api_rest": surface["rest"],
        "api_graphql": surface["graphql"],
        "api_breadth": surface["breadth"],
        "api_styles": "|".join(surface["styles"]),
        "existing_mcp": record["existing_mcp"],
        "mcp_url": _blank(record.get("mcp_url")),
        "composio_has_toolkit": record["composio_has_toolkit"],
        "composio_slug": _blank(record.get("composio_slug")),
        "composio_toolkit_type": _blank(record.get("composio_toolkit_type")),
        "composio_tools_count": _blank(record.get("composio_tools_count")),
        "buildability": record["buildability"],
        "main_blocker": record["main_blocker"],
        "blocker_detail": record["blocker_detail"],
        "confidence": _blank(record.get("confidence")),
        "needs_human": "true" if record["needs_human"] else "false",
        "needs_human_reasons": "|".join(record.get("needs_human_reasons") or []),
        "unknown_reasons": json.dumps(record.get("unknown_reasons") or {}, sort_keys=True),
        "evidence_json": json.dumps(record.get("evidence") or [], sort_keys=True),
        "hint_url": _blank(record.get("hint_url")),
        "official_domain": _blank(record.get("official_domain")),
        "provenance_source": _blank((record.get("provenance") or {}).get("source")),
        "source_id": _blank(record.get("source_id")),
        "extraction_model": _blank(record.get("extraction_model")),
        "verifier_model": _blank(record.get("verifier_model")),
        "pass_id": _blank(record.get("pass_id")),
    }
    return {key: row[key] for key in CSV_COLUMNS}


def _records_from_payload(payload: Any) -> tuple[list[dict[str, Any]], int | None]:
    pass_id = None
    rows: Any
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict) and "records" in payload:
        rows = payload["records"]
        pass_id = payload.get("pass")
    else:
        raise ValueError("results file must be a list or an object with a records array")
    normalized = []
    for row in rows:
        record = AppRecord.model_validate(row)
        normalized.append(record.model_dump(mode="json"))
    return normalized, pass_id


def load_records(path: Path) -> tuple[list[dict[str, Any]], int | None]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return _records_from_payload(payload)


def default_source(repo: Path) -> Path | None:
    data = repo / "data"
    for name in ("results_pass2.json", "results.json", "results_pass1.json"):
        candidate = data / name
        if candidate.exists():
            return candidate
    return None


def export_bundle(
    *,
    repo: Path | None = None,
    source: Path | None = None,
    results_json: Path | None = None,
    results_csv: Path | None = None,
    schema_path: Path | None = None,
) -> dict[str, Any]:
    root = repo or repo_root()
    schema_file = schema_path or (root / "schema" / "app_record.schema.json")
    json_file = results_json or (root / "data" / "results.json")
    csv_file = results_csv or (root / "data" / "results.csv")
    _write_json(schema_file, app_record_json_schema())

    chosen = source
    if chosen is None:
        chosen = default_source(root)
    pass_id: int | None = None
    if chosen is None:
        records: list[dict[str, Any]] = []
    else:
        records, pass_id = load_records(chosen)
        if pass_id is None and records and records[0].get("pass_id") is not None:
            pass_id = records[0]["pass_id"]

    envelope = {
        "schema_version": SCHEMA_VERSION,
        "json_schema": "schema/app_record.schema.json",
        "pass": pass_id,
        "record_count": len(records),
        "records": records,
    }
    _write_json(json_file, envelope)
    csv_file.parent.mkdir(parents=True, exist_ok=True)
    with csv_file.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for record in records:
            writer.writerow(record_to_row(record))
    return {
        "schema": str(schema_file),
        "json": str(json_file),
        "csv": str(csv_file),
        "record_count": len(records),
        "pass": pass_id,
    }
