from opsresearch.schema import AppRecord
from opsresearch.verify import (
    check_quotes,
    classify_registry,
    normalize_toolkit,
    oracle_findings,
    parse_cross_model,
    registry_findings,
)

QUOTE = "Developers create a key in the dashboard."
PAGE = {"https://example.com/docs": "Intro. Developers create a key in the dashboard. Next."}


def _record(**overrides) -> AppRecord:
    payload = {
        "app": "Example",
        "category": "CRM and Sales",
        "one_liner": "unknown",
        "auth_methods": ["oauth2"],
        "evidence": [{"field": "auth_methods", "url": "https://example.com/docs", "quote": QUOTE, "source": "doc"}],
    }
    payload.update(overrides)
    return AppRecord.model_validate(payload)


def test_quote_check_accepts_a_real_quote_and_rejects_a_known_bad_one() -> None:
    good = check_quotes(_record(), PAGE)
    assert good[0].status == "supported"
    bad = _record(
        evidence=[
            {
                "field": "auth_methods",
                "url": "https://example.com/docs",
                "quote": "Contact sales before any key is issued.",
                "source": "doc",
            }
        ]
    )
    findings = check_quotes(bad, PAGE)
    assert findings[0].status == "insufficient"
    assert findings[0].verifier == "quote"


def test_oracle_flags_disagreement_and_contradicts_a_false_negative() -> None:
    snapshot = normalize_toolkit(
        {
            "slug": "hubspot",
            "toolkit_type": "api",
            "composio_managed_auth_schemes": ["OAUTH2"],
            "auth_config_details": [{"mode": "OAUTH2"}, {"mode": "API_KEY"}],
            "docs_url": "https://developers.hubspot.com",
            "meta": {"tools_count": 51},
        },
        "hubspot",
    )
    assert snapshot.auth_modes == ("oauth2", "api_key")
    assert snapshot.tools_count == 51
    record = _record(composio_has_toolkit="no")
    # composio no needs evidence or it becomes unknown. Add oracle-less doc evidence.
    record = AppRecord.model_validate(
        {
            "app": "HubSpot",
            "category": "CRM and Sales",
            "composio_has_toolkit": "no",
            "auth_methods": ["basic"],
            "evidence": [
                {"field": "composio_has_toolkit", "url": "https://example.com/docs", "quote": QUOTE, "source": "doc"},
                {"field": "auth_methods", "url": "https://example.com/docs", "quote": QUOTE, "source": "doc"},
            ],
        }
    )
    findings = oracle_findings(record, snapshot)
    statuses = {(item.field, item.status) for item in findings}
    assert ("composio_has_toolkit", "contradicted") in statuses
    assert ("auth_methods", "flag") in statuses
    missing = oracle_findings(_record(composio_has_toolkit="yes"), None)
    # yes without evidence was coerced to unknown, so this is a fill not a contradiction.
    assert missing[0].field == "composio_has_toolkit"


def test_oracle_contradicts_yes_when_the_toolkit_is_missing() -> None:
    record = AppRecord.model_validate(
        {
            "app": "Nope",
            "category": "CRM and Sales",
            "composio_has_toolkit": "yes",
            "evidence": [
                {
                    "field": "composio_has_toolkit",
                    "url": "https://example.com/docs",
                    "quote": QUOTE,
                    "source": "doc",
                }
            ],
        }
    )
    findings = oracle_findings(record, None)
    assert findings[0].status == "contradicted"


def test_registry_official_requires_the_vendor_domain() -> None:
    vendor = {"github.com"}
    payload = {
        "servers": [
            {
                "server": {
                    "name": "com.example/github-tools",
                    "websiteUrl": "https://example.com",
                    "description": "community github tools",
                }
            },
            {
                "server": {
                    "name": "com.github/github",
                    "websiteUrl": "https://github.com/github/github-mcp-server",
                }
            },
        ]
    }
    kind, url, quote = classify_registry(payload, vendor)
    assert kind == "official"
    assert url is not None and "github.com" in url
    assert "com.github/github" in quote
    community_only = {"servers": [payload["servers"][0]]}
    assert classify_registry(community_only, vendor)[0] == "community"
    assert classify_registry({"servers": []}, vendor)[0] == "none"


def test_registry_contradicts_an_official_claim_when_the_hit_is_third_party() -> None:
    record = AppRecord.model_validate(
        {
            "app": "Example",
            "category": "CRM and Sales",
            "existing_mcp": "official",
            "evidence": [{"field": "existing_mcp", "url": "https://example.com/docs", "quote": QUOTE, "source": "doc"}],
        }
    )
    findings = registry_findings(record, "community")
    assert findings[0].status == "contradicted"


def test_cross_model_parses_verdicts_and_rejects_junk() -> None:
    findings = parse_cross_model(
        {"verdicts": [{"field": "self_serve", "status": "contradicted", "reason": "the page says free"}]}
    )
    assert findings[0].status == "contradicted"
    junk = parse_cross_model({"nope": True})
    assert junk[0].status == "insufficient"
    bad_status = parse_cross_model({"verdicts": [{"field": "self_serve", "status": "maybe", "reason": "x"}]})
    assert bad_status[0].status == "insufficient"
