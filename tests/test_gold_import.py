"""PO-18: map the human labelling sheet (Google Sheet export) onto data/gold/gold_labels.csv."""

import csv

from opsresearch.gold_import import import_sheet, map_row

HEADER = "app,category,split,archetype,hint_url,docs_url_found,auth_methods,self_serve,api_type,api_breadth,existing_mcp,buildable_today,main_blocker,evidence_url,labeller_notes,minutes_spent"


def _row(**kw):
    base = dict.fromkeys(HEADER.split(","), "")
    base.update({"app": "Acme", "category": "CRM", "split": "dev", "archetype": "x"})
    base.update(kw)
    return base


def test_maps_free_text_auth_and_dropdowns() -> None:
    out, unmapped = map_row(
        _row(
            auth_methods="OAuth2, API key",
            self_serve="Free",
            api_type="REST + GraphQL",
            api_breadth="Broad",
            existing_mcp="Official",
            buildable_today="Yes",
            main_blocker="",
            evidence_url="https://acme.dev/auth",
        )
    )
    assert out["auth_methods"] == "api_key|oauth2"
    assert out["self_serve"] == "self_serve_free"
    assert (out["api_rest"], out["api_graphql"]) == ("yes", "yes")
    assert out["api_breadth"] == "broad"
    assert out["existing_mcp"] == "official"
    assert out["buildability"] == "yes"
    assert out["main_blocker"] == "none"
    assert out["doc_auth"] == "https://acme.dev/auth"
    assert unmapped == []


def test_blocker_phrases_and_blank_rows() -> None:
    out, _ = map_row(
        _row(self_serve="partner_gated", buildable_today="No", main_blocker="partner approval", api_type="REST")
    )
    assert out["main_blocker"] == "partner_program"
    assert (out["api_rest"], out["api_graphql"]) == ("yes", "no")
    blank, _ = map_row(_row())
    assert all(blank[k] == "" for k in ["auth_methods", "self_serve", "main_blocker", "buildability"])


def test_unmappable_values_are_reported_not_guessed() -> None:
    out, unmapped = map_row(_row(self_serve="it depends"))
    assert out["self_serve"] == ""
    assert unmapped == [("Acme", "self_serve", "it depends")]


def test_import_writes_the_gold_file(tmp_path) -> None:
    sheet = tmp_path / "sheet.csv"
    with sheet.open("w", newline="") as h:
        w = csv.DictWriter(h, fieldnames=HEADER.split(","))
        w.writeheader()
        w.writerow(_row(self_serve="Trial"))
    out = tmp_path / "gold.csv"
    report = import_sheet(sheet, out)
    rows = list(csv.DictReader(out.open()))
    assert rows[0]["self_serve"] == "self_serve_trial"
    assert report["rows"] == 1


def test_per_field_evidence_columns_and_labeller_are_kept() -> None:
    row = _row(
        auth_methods="API key",
        self_serve="Free",
        evidence_url="https://acme.dev/auth",
        docs_url_found="https://acme.dev",
        labeller_notes="unsure on breadth",
        minutes_spent="2",
    )
    row.update(
        {
            "labeller": "assistant-labelled (Grok Bot), not human-audited",
            "evidence_api_url": "https://acme.dev/api",
            "evidence_mcp_url": "https://acme.dev/mcp",
            "evidence_buildability_url": "https://acme.dev/pricing",
        }
    )
    out, unmapped = map_row(row)
    assert out["doc_auth"] == "https://acme.dev/auth"
    assert out["doc_api"] == "https://acme.dev/api"
    assert out["doc_mcp"] == "https://acme.dev/mcp"
    assert out["doc_buildability"] == "https://acme.dev/pricing"
    assert out["notes"].startswith("assistant-labelled (Grok Bot), not human-audited")
    assert "unsure on breadth" in out["notes"] and "2 min" in out["notes"]
    assert unmapped == []


def test_evidence_columns_fall_back_to_docs_url_when_absent() -> None:
    out, _ = map_row(_row(self_serve="Free", docs_url_found="https://acme.dev", evidence_url="https://acme.dev/auth"))
    assert out["doc_api"] == "https://acme.dev"
    assert out["doc_mcp"] == "https://acme.dev"
    assert out["doc_buildability"] == "https://acme.dev/auth"
