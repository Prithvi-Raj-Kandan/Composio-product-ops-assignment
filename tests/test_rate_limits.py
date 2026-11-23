"""PO-16: free-tier limits. Per-model pacing, a verifier fallback chain, compact verifier prompts."""

from opsresearch.llm import model_limiter, verifier_clients
from opsresearch.schema import AppRecord
from opsresearch.verify import Finding, cross_model_prompt, failing_fields


def test_each_gemini_model_gets_its_own_pacing() -> None:
    a = model_limiter("gemini-x", {"OPSRESEARCH_GEMINI_RPM": "12"})
    b = model_limiter("gemini-y", {"OPSRESEARCH_GEMINI_RPM": "12"})
    assert a is not b
    assert a is model_limiter("gemini-x")
    assert abs(a.min_interval_s - 5.0) < 1e-9


def test_verifier_chain_is_groq_then_a_different_gemini() -> None:
    names = [c.model_name for c in verifier_clients({"GROQ_API_KEY": "g", "GEMINI_API_KEY": "k"})]
    assert names[0] == "openai/gpt-oss-120b"
    assert names[1] == "gemini-3.1-flash-lite"
    assert [c.model_name for c in verifier_clients({"GEMINI_API_KEY": "k"})] == ["gemini-3.1-flash-lite"]


def test_verifier_prompt_uses_a_window_around_the_quote() -> None:
    quote = "Create an API key in Settings without contacting sales."
    page = ("filler " * 2000) + quote + (" tail" * 2000)
    rec = AppRecord.model_validate(
        {
            "app": "A",
            "category": "C",
            "self_serve": "self_serve_free",
            "evidence": [{"field": "self_serve", "url": "https://a.dev/k", "quote": quote, "source": "doc"}],
        }
    )
    prompt = cross_model_prompt(rec, {"https://a.dev/k": page})
    assert quote in prompt
    assert len(prompt) < 6000


def test_a_verifier_outage_is_not_a_failing_field() -> None:
    findings = [
        Finding(verifier="cross_model", field="record", status="insufficient", detail="verifier error X"),
        Finding(verifier="cross_model", field="self_serve", status="contradicted", detail="no"),
    ]
    assert failing_fields(findings) == ["self_serve"]
