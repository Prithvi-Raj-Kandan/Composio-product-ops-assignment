"""PO-10: patterns are computed by pandas; the LLM only words a headline that must pass a numeric fact-check."""

from opsresearch.patterns import compute_patterns, fact_check, headline, records_frame


def _rec(app, cat, auth, ss, build, blocker, mcp="none", composio="no"):
    return {
        "app": app,
        "category": cat,
        "auth_methods": auth,
        "self_serve": ss,
        "gate_type": "none",
        "api_surface": {"rest": "yes", "graphql": "no", "breadth": "broad", "styles": ["rest"]},
        "existing_mcp": mcp,
        "composio_has_toolkit": composio,
        "buildability": build,
        "main_blocker": blocker,
        "needs_human": False,
        "evidence": [],
    }


RECORDS = [
    _rec("A", "CRM", ["oauth2", "api_key"], "self_serve_free", "yes", "none", "official", "yes"),
    _rec("B", "CRM", ["oauth2"], "contact_sales", "no", "contact_sales"),
    _rec("C", "Finance", ["api_key"], "self_serve_trial", "yes", "none"),
    _rec("D", "Finance", ["unknown"], "unknown", "unknown", "unknown"),
]


def test_counts_are_deterministic() -> None:
    p = compute_patterns(records_frame(RECORDS))
    assert p["totals"]["apps"] == 4
    assert p["auth_counts"]["oauth2"] == 2
    assert p["auth_counts"]["api_key"] == 2
    assert p["self_serve_by_category"]["CRM"]["self_serve_free"] == 1
    assert p["blocker_counts"]["contact_sales"] == 1
    assert [row["app"] for row in p["easy_wins"]] == ["C"]
    assert [row["app"] for row in p["needs_outreach"]] == ["B"]
    assert p["totals"]["unknown_self_serve"] == 1


def test_fact_check_rejects_a_wrong_number() -> None:
    p = compute_patterns(records_frame(RECORDS))
    assert fact_check("OAuth2 shows up in 2 of 4 apps.", p) == []
    assert fact_check("OAuth2 shows up in 7 of 4 apps.", p) == ["7"]
    assert fact_check("50% of apps use OAuth2.", p) == []


def test_headline_falls_back_when_the_llm_invents_numbers() -> None:
    p = compute_patterns(records_frame(RECORDS))

    class Liar:
        model_name = "liar"

        def complete_json(self, prompt, schema):
            return {"bullets": ["OAuth2 is used by 97 apps."]}

    out = headline(p, client=Liar())
    assert out["source"] == "deterministic_fallback"
    assert out["rejected_numbers"] == ["97"]
    assert all("97" not in b for b in out["bullets"])
