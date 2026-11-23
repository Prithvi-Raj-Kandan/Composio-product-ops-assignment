import pytest
from opsresearch.schema import AppRecord, AuthMethod, app_record_json_schema
from pydantic import ValidationError


def _evidence(field: str, quote: str = "Developers create a key in the dashboard.") -> dict:
    return {
        "field": field,
        "url": "https://example.com/docs",
        "quote": quote,
        "source": "doc",
    }


def _valid() -> dict:
    quote = "Developers create a key in the dashboard."
    fields = [
        "one_liner",
        "auth_methods",
        "self_serve",
        "gate_type",
        "gate_detail",
        "api_surface",
        "existing_mcp",
        "composio_has_toolkit",
        "buildability",
        "main_blocker",
        "blocker_detail",
    ]
    return {
        "app": "Example",
        "category": "CRM and Sales",
        "one_liner": "Example is a CRM for small teams.",
        "auth_methods": ["oauth2", "api_key"],
        "self_serve": "self_serve_free",
        "gate_type": "none",
        "gate_detail": "A key can be created without sales.",
        "api_surface": {"rest": "yes", "graphql": "no", "breadth": "broad", "styles": ["rest"]},
        "existing_mcp": "none",
        "composio_has_toolkit": "no",
        "buildability": "yes",
        "main_blocker": "none",
        "blocker_detail": "No blocker is described in the docs.",
        "evidence": [_evidence(field, quote) for field in fields],
        "confidence": 0.4,
    }


def test_valid_record_keeps_concrete_values() -> None:
    record = AppRecord.model_validate(_valid())
    assert record.auth_methods == [AuthMethod.oauth2, AuthMethod.api_key]
    assert record.self_serve.value == "self_serve_free"
    assert record.api_surface.rest.value == "yes"
    assert record.confidence == 0.4


def test_missing_evidence_forces_unknown() -> None:
    payload = _valid()
    payload["evidence"] = []
    record = AppRecord.model_validate(payload)
    assert record.one_liner == "unknown"
    assert record.auth_methods == [AuthMethod.unknown]
    assert record.self_serve.value == "unknown"
    assert record.api_surface.rest.value == "unknown"
    assert record.api_surface.graphql.value == "unknown"
    assert record.api_surface.breadth.value == "unknown"
    assert record.existing_mcp.value == "unknown"
    assert record.composio_has_toolkit.value == "unknown"
    assert record.buildability.value == "unknown"
    assert record.main_blocker.value == "unknown"
    assert "auth_methods" in record.unknown_reasons
    assert "no evidence" in record.unknown_reasons["auth_methods"]


def test_partial_api_evidence_clears_only_the_unsupported_part() -> None:
    payload = _valid()
    payload["evidence"] = [
        _evidence("api_surface.rest"),
        _evidence("one_liner"),
        _evidence("auth_methods"),
        _evidence("self_serve"),
        _evidence("existing_mcp"),
        _evidence("composio_has_toolkit"),
        _evidence("buildability"),
        _evidence("main_blocker"),
    ]
    record = AppRecord.model_validate(payload)
    assert record.api_surface.rest.value == "yes"
    assert record.api_surface.graphql.value == "unknown"
    assert "api_surface.graphql" in record.unknown_reasons


def test_short_quote_does_not_count() -> None:
    payload = _valid()
    payload["evidence"] = [_evidence("auth_methods", quote="API")]
    record = AppRecord.model_validate(payload)
    assert record.auth_methods == [AuthMethod.unknown]


def test_invalid_enum_is_rejected() -> None:
    payload = _valid()
    payload["auth_methods"] = ["oauth3"]
    with pytest.raises(ValidationError):
        AppRecord.model_validate(payload)


def test_confidence_out_of_range_is_rejected() -> None:
    payload = _valid()
    payload["confidence"] = 1.5
    with pytest.raises(ValidationError):
        AppRecord.model_validate(payload)


def test_committed_schema_matches_the_model() -> None:
    import json
    from pathlib import Path

    committed = json.loads(Path("schema/app_record.schema.json").read_text(encoding="utf-8"))
    assert committed == app_record_json_schema()


def test_json_schema_lists_stable_enums() -> None:
    schema = app_record_json_schema()
    blob = str(schema)
    for value in ("oauth2", "self_serve_free", "partner_gated", "yes_with_caveats", "unknown"):
        assert value in blob
    assert schema["title"] == "AppRecord"
    assert "app" in schema["properties"]
    assert "evidence" in schema["properties"]
