"""PO-17: Tavily's monthly credits ran out mid-run. Search results are cached, and with no search the
graph reads the page cache from earlier runs and probes common doc paths on the official domain."""

import json

from opsresearch.discover import CachedSearch, SearchHit, probe_urls
from opsresearch.fetch import cache_path
from opsresearch.graph import run_app
from tests.test_graph import DOCS, Judge, _app, _deps


class Counting:
    name = "tavily"

    def __init__(self):
        self.calls = 0

    def search(self, query, *, max_results=5):
        self.calls += 1
        return [SearchHit(url="https://acme.dev/docs")]


def test_search_results_are_cached_per_query(tmp_path) -> None:
    inner = Counting()
    search = CachedSearch(inner, tmp_path)
    search.search("acme api")
    hits = search.search("acme api")
    assert inner.calls == 1
    assert hits[0].url == "https://acme.dev/docs"
    assert search.name == "tavily+cache"


def test_probe_urls_stay_on_the_official_host() -> None:
    urls = probe_urls("acme.dev", ["self_serve"])
    assert urls and all(u.startswith("https://acme.dev/") for u in urls)
    assert any("pricing" in u for u in urls)


def test_dead_search_falls_back_to_the_page_cache(tmp_path) -> None:
    class Dead:
        name = "tavily"

        def search(self, query, *, max_results=5):
            raise RuntimeError("usage limit")

    path = cache_path(tmp_path, "Acme", "https://acme.dev/auth")
    path.parent.mkdir(parents=True)
    path.write_text("---\nurl: https://acme.dev/auth\nfetched_at: 2026-11-23T00:00:00Z\n---\n" + DOCS)
    deps = _deps(tmp_path, Judge())
    deps.search = Dead()
    record, log = run_app(_app(), 1, deps)
    disc = next(n for n in log["nodes"] if n["node"] == "discover")
    assert "https://acme.dev/auth" in disc["urls"]
    assert disc["page_cache"] >= 1
    assert json.dumps(log)
