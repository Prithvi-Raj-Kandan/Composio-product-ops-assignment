from opsresearch.discover import (
    HintOnlySearch,
    SearchHit,
    filter_official,
    parse_search_payload,
    select_search_backend,
)
from opsresearch.fetch import fetch_url
from opsresearch.seed import AppSeed, load_apps, load_overrides, resolve_domain


def test_filter_drops_blogs_and_keeps_official_docs() -> None:
    apps = {app.app: app for app in load_apps("data/apps.csv")}
    overrides = load_overrides("data/domain_overrides.csv")
    resolved = resolve_domain(apps["Stripe"], overrides)
    hits = [
        SearchHit(url="https://medium.com/how-stripe-oauth-works", title="blog"),
        SearchHit(url="https://dev.to/stripe-api", title="blog"),
        SearchHit(url="https://news.ycombinator.com/item?id=1", title="hn"),
        SearchHit(url="https://docs.stripe.com/api/authentication", title="docs"),
    ]
    kept = filter_official(hits, resolved)
    assert [hit.url for hit in kept] == ["https://docs.stripe.com/api/authentication"]


def test_hint_only_backend_when_no_keys() -> None:
    backend = select_search_backend(env={}, factories={})
    assert isinstance(backend, HintOnlySearch)
    assert backend.search("stripe api") == []


def test_composio_is_preferred_and_falls_back(monkeypatch) -> None:
    calls = {}

    def composio():
        calls["composio"] = True
        raise RuntimeError("session failed")

    def tavily():
        calls["tavily"] = True
        return HintOnlySearch()

    backend = select_search_backend(
        env={"COMPOSIO_API_KEY": "present", "TAVILY_API_KEY": "present"},
        factories={"composio": composio, "tavily": tavily},
    )
    assert calls["composio"] and calls["tavily"]
    assert backend.name == "hint-only"


def test_parse_tavily_and_exa_shapes() -> None:
    tavily = {"results": [{"url": "https://docs.stripe.com/api", "title": "API", "content": "keys"}]}
    exa = {"results": [{"url": "https://docs.stripe.com/api", "title": "API", "text": "keys"}]}
    assert parse_search_payload(tavily)[0].snippet == "keys"
    assert parse_search_payload(exa)[0].url.startswith("https://docs.stripe.com")


def test_cache_hit_does_not_call_the_network(tmp_path) -> None:
    class Client:
        def __init__(self) -> None:
            self.calls = 0

        def get_text(self, url: str) -> str:
            self.calls += 1
            if self.calls > 1:
                raise AssertionError("second fetch should use the cache")
            return "<html><body><p>API keys are created in the dashboard</p></body></html>"

    client = Client()
    app = AppSeed(1, "Finance and Fintech", "Stripe", "stripe.com/docs/api")
    first = fetch_url("https://docs.stripe.com/api", app.app, tmp_path, client)
    second = fetch_url("https://docs.stripe.com/api", app.app, tmp_path, client)
    assert client.calls == 1
    assert first.cache_hit is False
    assert second.cache_hit is True
    assert "API keys are created in the dashboard" in second.text
    assert (tmp_path / "stripe").is_dir()
