"""PO-15: buildability and main_blocker are derived by a fixed rule from evidenced fields when the model leaves them unknown."""

from opsresearch.extract import derive_verdict
from opsresearch.schema import AppRecord

Q_AUTH = "Authenticate every request with OAuth 2.0 tokens."
Q_SS = "Sign up for a free developer account to get API keys."
Q_API = "The REST API reference lists every endpoint."
Q_GATE = "API access is available only to approved partners."


def _rec(**kw) -> AppRecord:
    ev = [
        {"field": "auth_methods", "url": "https://a.dev/auth", "quote": Q_AUTH, "source": "doc"},
        {"field": "api_surface.rest", "url": "https://a.dev/api", "quote": Q_API, "source": "doc"},
    ]
    ev += kw.pop("extra_ev", [])
    base = {
        "app": "A",
        "category": "CRM",
        "auth_methods": ["oauth2"],
        "api_surface": {"rest": "yes", "graphql": "unknown", "breadth": "unknown", "styles": ["rest"]},
        "evidence": ev,
    }
    base.update(kw)
    return AppRecord.model_validate(base)


def test_self_serve_with_api_derives_yes() -> None:
    rec = _rec(
        self_serve="self_serve_free",
        extra_ev=[{"field": "self_serve", "url": "https://a.dev/s", "quote": Q_SS, "source": "doc"}],
    )
    out = derive_verdict(rec)
    assert out.buildability.value == "yes"
    assert out.main_blocker.value == "none"
    assert any(e.field == "buildability" and e.source.value == "derived_rule" for e in out.evidence)


def test_partner_gate_derives_no_with_partner_blocker() -> None:
    rec = _rec(
        self_serve="partner_gated",
        extra_ev=[{"field": "self_serve", "url": "https://a.dev/p", "quote": Q_GATE, "source": "doc"}],
    )
    out = derive_verdict(rec)
    assert out.buildability.value == "no"
    assert out.main_blocker.value == "partner_program"


def test_unknown_access_stays_unknown() -> None:
    out = derive_verdict(_rec())
    assert out.buildability.value == "unknown"
    assert out.main_blocker.value == "unknown"


def test_model_answer_with_evidence_is_kept() -> None:
    rec = _rec(
        self_serve="self_serve_free",
        buildability="yes_with_caveats",
        extra_ev=[
            {"field": "self_serve", "url": "https://a.dev/s", "quote": Q_SS, "source": "doc"},
            {"field": "buildability", "url": "https://a.dev/s", "quote": Q_SS, "source": "doc"},
        ],
    )
    assert derive_verdict(rec).buildability.value == "yes_with_caveats"
