"""PO-31: every unknown cell carries a short reason derived from run data."""

from __future__ import annotations

from opsresearch.schema import AppRecord
from opsresearch.unknown_reason import derive_unknown_reasons, tally


def _rec(**kw) -> dict:
    return AppRecord(app="Acme", category="CRM", **kw).model_dump(mode="json")


def test_contradicted_wins():
    rec = _rec()
    rec["verifier_verdicts"] = {"self_serve": "contradicted"}
    assert derive_unknown_reasons(rec, None, None, pages=["https://acme.com/pricing"])["self_serve"] == (
        "verifier contradicted"
    )


def test_rule_fields_name_the_unknown_input():
    out = derive_unknown_reasons(_rec(), None, None, pages=[])
    assert out["buildability"] == "depends on unknown self_serve"
    assert out["main_blocker"] == "depends on unknown self_serve"


def test_no_docs_page_found_from_reresearch_log():
    log3 = {"nodes": [{"node": "reresearch3", "fields": ["auth_methods"], "docs": 0}]}
    assert derive_unknown_reasons(_rec(), None, log3, pages=[])["auth_methods"] == "no docs page found"


def test_model_quota_and_no_quote():
    log3 = {
        "nodes": [
            {
                "node": "reresearch3",
                "fields": ["auth_methods", "self_serve"],
                "docs": 4,
                "errors": ["openai/gpt-oss-20b: RuntimeError"],
            }
        ]
    }
    out = derive_unknown_reasons(_rec(), None, log3, pages=["https://acme.com/pricing"])
    assert out["auth_methods"] == "re-research hit model quota"
    log3["nodes"][0].pop("errors")
    log3["nodes"][0]["model"] = "m"
    out = derive_unknown_reasons(_rec(), None, log3, pages=["https://acme.com/pricing"])
    assert out["auth_methods"] == "no quote found in docs"


def test_no_pricing_page_and_crash():
    rec = _rec()
    out = derive_unknown_reasons(rec, None, None, pages=["https://acme.com/docs"])
    assert out["self_serve"] == "no pricing page"
    rec["needs_human_reasons"] = ["pass 2 extraction crashed (model quota); ..."]
    p1 = _rec()
    out = derive_unknown_reasons(rec, p1, None, pages=["https://acme.com/pricing"])
    assert out["self_serve"] == "pass-2 quota crash, no recheck"


def test_pass1_quote_not_on_page():
    p1 = _rec(
        self_serve="self_serve_free",
        evidence=[{"field": "self_serve", "url": "https://acme.com/p", "quote": "Free forever plan", "source": "doc"}],
    )
    out = derive_unknown_reasons(_rec(), p1, None, pages=["https://acme.com/pricing"])
    assert out["self_serve"] == "quote not on page"


def test_known_fields_get_no_reason_and_tally():
    rec = _rec(
        self_serve="self_serve_free",
        evidence=[{"field": "self_serve", "url": "https://acme.com/p", "quote": "Free forever plan", "source": "doc"}],
    )
    out = derive_unknown_reasons(rec, None, None, pages=[])
    assert "self_serve" not in out
    assert tally([{"unknown_reason": {"a": "x", "b": "x", "c": "y"}}])[0] == ("x", 2)
