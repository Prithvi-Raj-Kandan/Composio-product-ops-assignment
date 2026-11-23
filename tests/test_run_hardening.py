"""PO-9: model defaults that exist today, usage/cost ledger, per-call search fallback."""

from opsresearch.discover import FallbackSearch, SearchHit
from opsresearch.llm import (
    DEFAULT_GROQ_MODEL,
    UsageLedger,
    extraction_clients,
    extraction_model_names,
)


def test_extract_models_come_from_env_with_live_defaults() -> None:
    names = extraction_model_names({})
    assert names[0].startswith("gemini-3")
    assert extraction_model_names({"OPSRESEARCH_EXTRACT_MODELS": "a, b"}) == ["a", "b"]
    clients = extraction_clients({"GEMINI_API_KEY": "x", "GROQ_API_KEY": "y", "OPSRESEARCH_EXTRACT_MODELS": "a,b"})
    assert [c.model_name for c in clients] == ["a", "b", DEFAULT_GROQ_MODEL]


def test_usage_ledger_estimates_cost_at_list_price() -> None:
    ledger = UsageLedger(prices={"m": (1.0, 2.0)})
    ledger.add("m", 1_000_000, 500_000)
    ledger.add("m", 0, 500_000)
    ledger.add("free-model", 10, 10)
    summary = ledger.summary()
    assert summary["models"]["m"]["calls"] == 2
    assert summary["models"]["m"]["cost_usd"] == 3.0
    assert summary["models"]["free-model"]["cost_usd"] is None
    assert summary["cost_usd_known"] == 3.0
    assert summary["unpriced_models"] == ["free-model"]


class _Boom:
    name = "composio"

    def search(self, query, *, max_results=5):
        raise RuntimeError("no credits")


class _Ok:
    name = "tavily"

    def __init__(self):
        self.calls = 0

    def search(self, query, *, max_results=5):
        self.calls += 1
        return [SearchHit(url="https://example.com/docs", title="t", snippet="s")]


def test_fallback_search_uses_the_next_backend_per_call() -> None:
    ok = _Ok()
    search = FallbackSearch([_Boom(), ok])
    hits = search.search("x")
    assert hits[0].url == "https://example.com/docs"
    assert ok.calls == 1
    assert search.used == {"tavily": 1}
