import json

from opsresearch.graph import append_record
from opsresearch.schema import AppRecord
from opsresearch.smoke import run_smoke


def test_smoke_table_never_prints_key_values() -> None:
    env = {
        "COMPOSIO_API_KEY": "supersecretvalue",
        "GEMINI_API_KEY": "geminisecretvalue",
    }

    def leak() -> str:
        return "model replied geminisecretvalue"

    def fail() -> str:
        raise RuntimeError("supersecretvalue leaked in the exception")

    rows, code, table = run_smoke(
        "HubSpot",
        env=env,
        probes={"composio": fail, "gemini": leak},
    )
    assert code == 1
    assert "supersecretvalue" not in table
    assert "geminisecretvalue" not in table
    assert "[redacted]" in table
    assert "RuntimeError" in table
    groq = next(row for row in rows if row.service == "groq")
    assert groq.status == "skipped"
    assert "second Gemini" in groq.detail
    tavily = next(row for row in rows if row.service == "tavily")
    assert tavily.status == "skipped"


def test_smoke_ok_when_required_services_pass() -> None:
    env = {"COMPOSIO_API_KEY": "abc123456", "GEMINI_API_KEY": "def123456"}
    _rows, code, table = run_smoke(
        "HubSpot",
        env=env,
        probes={"composio": lambda: "lookup returned slug hubspot", "gemini": lambda: "gemini-2.5-flash responded"},
    )
    assert code == 0
    assert "hubspot" in table
    assert "abc123456" not in table


def test_append_replaces_the_same_app(tmp_path) -> None:
    path = tmp_path / "results.json"
    append_record(path, AppRecord(app="A", category="old"))
    append_record(path, AppRecord(app="B", category="keep"))
    append_record(path, AppRecord(app="A", category="new"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    by_app = {row["app"]: row["category"] for row in payload["records"]}
    assert by_app == {"B": "keep", "A": "new"}
