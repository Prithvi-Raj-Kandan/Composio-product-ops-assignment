"""Pass 3: honest unknown reduction (policy fix, Composio metadata oracle, probe re-research)."""

from __future__ import annotations

from pathlib import Path

from opsresearch.fetch import cache_path
from opsresearch.pass3 import (
    apply_composio_metadata,
    count_unknowns,
    demote_contradicted,
    final_verdicts,
    find_toolkit,
    loop0_verdicts,
    merge_new_fields,
    pass3_record,
    probe_targets,
    restore_from_prior,
)
from opsresearch.schema import AppRecord

URL = "https://docs.acme.com/auth"
QUOTE = "Authenticate every request with an API key from your dashboard."
PRICING = "https://acme.com/pricing"
PRICING_QUOTE = "Start for free, no credit card required."


def _page(cache: Path, app: str, url: str, text: str) -> None:
    path = cache_path(cache, app, url)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nurl: {url}\nfetched_at: x\n---\n\n{text}", encoding="utf-8")


def _p1() -> dict:
    return AppRecord.model_validate(
        {
            "app": "Acme",
            "category": "CRM",
            "auth_methods": ["api_key"],
            "self_serve": "self_serve_free",
            "api_surface": {"rest": "yes", "graphql": "unknown", "breadth": "unknown", "styles": ["unknown"]},
            "official_domain": "acme.com",
            "evidence": [
                {"field": "auth_methods", "url": URL, "quote": QUOTE, "source": "doc"},
                {"field": "self_serve", "url": PRICING, "quote": PRICING_QUOTE, "source": "doc"},
                {"field": "api_surface.rest", "url": URL, "quote": "The Acme REST API uses JSON.", "source": "doc"},
            ],
            "pass_id": 1,
        }
    ).model_dump(mode="json")


def _log(statuses: dict[str, str], loops: int = 1) -> dict:
    nodes = []
    for loop in range(loops):
        nodes.append(
            {
                "node": "verify",
                "loop": loop,
                "findings": [
                    {"verifier": "cross_model", "field": f, "status": s, "detail": ""} for f, s in statuses.items()
                ],
            }
        )
    return {"nodes": nodes}


def test_insufficient_but_quote_on_page_is_restored_as_quote_verified(tmp_path):
    _page(tmp_path, "Acme", URL, f"Intro. {QUOTE} The Acme REST API uses JSON.")
    _page(tmp_path, "Acme", PRICING, PRICING_QUOTE)
    p2 = AppRecord(app="Acme", category="CRM").model_dump(mode="json")
    verdicts = loop0_verdicts(_log({"auth_methods": "insufficient", "self_serve": "insufficient"}))
    out, filled = restore_from_prior(p2, _p1(), verdicts, tmp_path)
    assert out["auth_methods"] == ["api_key"]
    assert out["self_serve"] == "self_serve_free"
    assert out["field_confidence"]["auth_methods"] == "quote-verified"
    assert out["verifier_verdicts"]["auth_methods"] == "insufficient"
    assert "auth_methods" in filled and "self_serve" in filled
    AppRecord.model_validate(out)


def test_contradicted_claim_is_not_restored(tmp_path):
    _page(tmp_path, "Acme", URL, QUOTE)
    _page(tmp_path, "Acme", PRICING, PRICING_QUOTE)
    p2 = AppRecord(app="Acme", category="CRM").model_dump(mode="json")
    verdicts = loop0_verdicts(_log({"self_serve": "contradicted"}))
    out, filled = restore_from_prior(p2, _p1(), verdicts, tmp_path)
    assert out["self_serve"] == "unknown"
    assert "self_serve" not in filled


def test_quote_missing_from_page_is_not_restored(tmp_path):
    _page(tmp_path, "Acme", URL, "nothing relevant here at all")
    p2 = AppRecord(app="Acme", category="CRM").model_dump(mode="json")
    out, filled = restore_from_prior(p2, _p1(), {}, tmp_path)
    assert out["auth_methods"] == ["unknown"]
    assert "auth_methods" not in filled


def test_final_contradicted_value_is_demoted():
    rec = _p1()
    verdicts = final_verdicts(_log({"self_serve": "contradicted", "auth_methods": "supported"}, loops=2))
    assert verdicts["self_serve"] == "contradicted"
    assert verdicts["auth_methods"] == "supported"
    assert demote_contradicted(rec, verdicts) == ["self_serve"]
    assert rec["self_serve"] == "unknown"
    assert rec["auth_methods"] == ["api_key"]
    assert all(e["field"] != "self_serve" for e in rec["evidence"])


def test_composio_metadata_fills_toolkit_and_auth():
    rec = AppRecord(app="Acme", category="CRM").model_dump(mode="json")
    meta = {"slug": "acme", "toolkit_type": "api", "auth_schemes": ["OAUTH2", "API_KEY"], "meta": {"tools_count": 12}}
    out, filled = apply_composio_metadata(rec, meta, tried=["acme"])
    assert out["composio_has_toolkit"] == "yes"
    assert out["auth_methods"] == ["oauth2", "api_key"]
    assert out["field_confidence"]["auth_methods"] == "composio_metadata"
    assert {e["source"] for e in out["evidence"]} == {"composio_metadata"}
    assert set(filled) == {"composio_has_toolkit", "auth_methods"}
    AppRecord.model_validate(out)


def test_composio_not_found_is_no_with_evidence():
    rec = AppRecord(app="Acme", category="CRM").model_dump(mode="json")
    out, filled = apply_composio_metadata(rec, None, tried=["acme", "acmeinc"])
    assert out["composio_has_toolkit"] == "no"
    assert out["auth_methods"] == ["unknown"]
    assert "acme" in out["evidence"][0]["quote"]


def test_find_toolkit_uses_search_name_match():
    class Fake:
        def get(self, slug):
            return None

        def search(self, name):
            return [{"slug": "other", "name": "Other"}, {"slug": "acme_crm", "name": "Acme"}]

    meta, tried = find_toolkit("Acme", Fake())
    assert meta["slug"] == "acme_crm"


def test_probe_targets_official_only():
    urls = probe_targets(["acme.com", "https://docs.acme.com/start"])
    assert "https://acme.com/pricing" in urls
    assert "https://docs.acme.com/authentication" in urls
    assert all("acme.com" in u for u in urls)


def test_merge_new_fields_only_fills_unknowns():
    base = _p1()
    update = dict(base)
    update = AppRecord.model_validate(
        {
            "app": "Acme",
            "category": "CRM",
            "self_serve": "contact_sales",
            "api_surface": {"rest": "unknown", "graphql": "no", "breadth": "unknown", "styles": ["unknown"]},
            "evidence": [
                {"field": "self_serve", "url": PRICING, "quote": "Talk to sales to begin.", "source": "doc"},
                {"field": "api_surface.graphql", "url": URL, "quote": "We do not offer GraphQL.", "source": "doc"},
            ],
        }
    ).model_dump(mode="json")
    out, filled = merge_new_fields(base, update, ["api_surface.graphql", "self_serve"])
    assert out["self_serve"] == "self_serve_free"
    assert out["api_surface"]["graphql"] == "no"
    assert out["api_surface"]["rest"] == "yes"
    assert filled == ["api_surface.graphql"]
    assert out["field_confidence"]["api_surface.graphql"] == "quote-verified"


def test_pass3_record_rederives_buildability(tmp_path):
    _page(tmp_path, "Acme", URL, f"{QUOTE} The Acme REST API uses JSON.")
    _page(tmp_path, "Acme", PRICING, PRICING_QUOTE)
    p2 = AppRecord(
        app="Acme", category="CRM", needs_human=True, needs_human_reasons=["pipeline error: RuntimeError"]
    ).model_dump(mode="json")
    out, steps = pass3_record(p2, _p1(), {"nodes": []}, tmp_path, toolkit=(None, ["acme"]))
    assert out["buildability"] == "yes"
    assert out["main_blocker"] == "none"
    assert out["field_confidence"]["buildability"] == "derived_rule"
    assert steps["policy_restore"] >= 2
    assert out["pass_id"] == 3
    assert not any("pipeline error" in r for r in out["needs_human_reasons"])


def test_count_unknowns():
    recs = [AppRecord(app="A", category="c").model_dump(mode="json"), _p1()]
    counts = count_unknowns(recs)
    assert counts["self_serve"] == 1
    assert counts["auth_methods"] == 1
