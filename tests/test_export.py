import csv
import json

from opsresearch.cli import main
from opsresearch.export import CSV_COLUMNS, export_bundle
from opsresearch.schema import AppRecord, app_record_json_schema


def _record() -> dict:
    quote = "Developers create a key in the dashboard."
    fields = [
        "one_liner",
        "auth_methods",
        "self_serve",
        "api_surface",
        "existing_mcp",
        "buildability",
        "main_blocker",
    ]
    return AppRecord.model_validate(
        {
            "app": "Example",
            "category": "CRM and Sales",
            "one_liner": "Example is a CRM.",
            "auth_methods": ["api_key"],
            "self_serve": "self_serve_free",
            "api_surface": {"rest": "yes", "graphql": "no", "breadth": "medium", "styles": ["rest"]},
            "existing_mcp": "none",
            "buildability": "yes",
            "main_blocker": "none",
            "evidence": [
                {"field": field, "url": "https://example.com/docs", "quote": quote, "source": "doc"} for field in fields
            ],
        }
    ).model_dump(mode="json")


def test_export_writes_schema_json_and_csv(tmp_path) -> None:
    source = tmp_path / "pass.json"
    source.write_text(json.dumps({"pass": 1, "records": [_record()]}), encoding="utf-8")
    result = export_bundle(repo=tmp_path, source=source)
    schema = json.loads((tmp_path / "schema" / "app_record.schema.json").read_text())
    assert schema == app_record_json_schema()
    envelope = json.loads((tmp_path / "data" / "results.json").read_text())
    assert envelope["schema_version"] == "1.0.0"
    assert envelope["json_schema"] == "schema/app_record.schema.json"
    assert envelope["pass"] == 1
    assert envelope["record_count"] == 1
    assert envelope["records"][0]["auth_methods"] == ["api_key"]
    with (tmp_path / "data" / "results.csv").open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert list(rows[0].keys()) == CSV_COLUMNS
    assert rows[0]["app"] == "Example"
    assert rows[0]["auth_methods"] == "api_key"
    assert rows[0]["api_rest"] == "yes"
    assert "https://example.com/docs" in rows[0]["evidence_json"]
    assert result["record_count"] == 1


def test_export_without_results_writes_an_empty_table(tmp_path) -> None:
    export_bundle(repo=tmp_path)
    envelope = json.loads((tmp_path / "data" / "results.json").read_text())
    assert envelope["records"] == []
    assert envelope["pass"] is None
    text = (tmp_path / "data" / "results.csv").read_text(encoding="utf-8")
    assert text.splitlines()[0].split(",")[0] == "app"


def test_export_coerces_a_claim_that_has_no_evidence(tmp_path) -> None:
    source = tmp_path / "pass.json"
    source.write_text(
        json.dumps([{"app": "Bare", "category": "CRM and Sales", "auth_methods": ["oauth2"]}]),
        encoding="utf-8",
    )
    export_bundle(repo=tmp_path, source=source)
    record = json.loads((tmp_path / "data" / "results.json").read_text())["records"][0]
    assert record["auth_methods"] == ["unknown"]


def test_cli_export(tmp_path) -> None:
    source = tmp_path / "in.json"
    source.write_text(json.dumps({"pass": 2, "records": [_record()]}), encoding="utf-8")
    code = main(["export", "--repo", str(tmp_path), "--from", str(source)])
    assert code == 0
    assert (tmp_path / "schema" / "app_record.schema.json").exists()
    assert json.loads((tmp_path / "data" / "results.json").read_text())["pass"] == 2
