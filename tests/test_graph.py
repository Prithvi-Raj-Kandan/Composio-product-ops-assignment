from opsresearch.discover import SearchHit
from opsresearch.graph import ResearchDeps, run_app, run_many, select_apps
from opsresearch.ratelimit import RateLimiter
from opsresearch.seed import AppSeed

DOCS = "Acme requires an enterprise contract before any API key is issued."
FREE = "Developers create a free API key in the Acme dashboard without talking to sales."


class Search:
    name = "fake-search"

    def search(self, query: str, *, max_results: int = 5) -> list[SearchHit]:
        if query.startswith("Acme API pricing"):
            return [SearchHit(url="https://acme.dev/pricing")]
        return [SearchHit(url="https://acme.dev/docs")]


class Fetch:
    def get_text(self, url: str) -> str:
        if url.endswith("/pricing"):
            return FREE
        return DOCS


class Extractor:
    model_name = "fake-extract"

    def complete_json(self, prompt: str, schema: dict) -> dict:
        if FREE in prompt:
            quote = FREE
            value = "self_serve_free"
            url = "https://acme.dev/pricing"
        else:
            quote = DOCS
            value = "contact_sales"
            url = "https://acme.dev/docs"
        return {
            "self_serve": value,
            "evidence": [{"field": "self_serve", "url": url, "quote": quote, "source": "doc"}],
        }


class Judge:
    def __init__(self, always_contradict: bool = False) -> None:
        self.model_name = "fake-judge"
        self.always_contradict = always_contradict
        self.calls = 0

    def complete_json(self, prompt: str, schema: dict) -> dict:
        self.calls += 1
        status = "contradicted"
        if not self.always_contradict and '"self_serve": "self_serve_free"' in prompt:
            status = "supported"
        return {"verdicts": [{"field": "self_serve", "status": status, "reason": status}]}


class Oracle:
    def __init__(self) -> None:
        self.calls = 0

    def lookup(self, app: str):
        self.calls += 1
        return None


class Registry:
    def search(self, app: str) -> dict:
        return {"servers": []}


def _deps(tmp_path, judge: Judge) -> ResearchDeps:
    return ResearchDeps(
        search=Search(),
        fetch=Fetch(),
        llm_clients=[Extractor()],
        limiter=RateLimiter(0, clock=lambda: 0.0, sleep=lambda _s: None),
        oracle=Oracle(),
        registry=Registry(),
        overrides={},
        cache_dir=tmp_path,
        cross_model=judge,
    )


def _app() -> AppSeed:
    return AppSeed(1, "CRM and Sales", "Acme", "acme.dev")


def test_human_override_is_stored_on_the_record(tmp_path) -> None:
    from opsresearch.seed import load_apps, load_overrides

    apps = {app.app: app for app in load_apps("data/apps.csv")}
    deps = _deps(tmp_path, Judge())
    deps.overrides = load_overrides("data/domain_overrides.csv")
    record, log = run_app(apps["fanbasis"], 1, deps)
    assert record.provenance.source == "human_override"
    assert log["nodes"][0]["node"] == "resolve_domain"
    assert log["nodes"][0]["source"] == "human_override"
    assert record.official_domain == "commas.com"


def test_contradicted_field_is_reresearched_and_fixed(tmp_path) -> None:
    judge = Judge()
    record, log = run_app(_app(), 2, _deps(tmp_path, judge))
    assert record.self_serve.value == "self_serve_free"
    assert record.needs_human is False
    assert log["loops"] == 1
    nodes = [entry["node"] for entry in log["nodes"]]
    assert "verify" in nodes
    assert "reresearch" in nodes
    assert any(entry["node"] == "verify" and "self_serve" in entry.get("failing", []) for entry in log["nodes"])


def test_exhausted_loops_set_needs_human(tmp_path) -> None:
    judge = Judge(always_contradict=True)
    record, log = run_app(_app(), 2, _deps(tmp_path, judge))
    assert record.needs_human is True
    assert any("verification loops exhausted" in reason for reason in record.needs_human_reasons)
    assert log["loops"] == 2
    assert judge.calls == 3


def test_pass1_does_not_call_verifiers(tmp_path) -> None:
    class ExplodingOracle:
        def lookup(self, app: str):
            raise AssertionError("pass 1 must not call the oracle")

    deps = _deps(tmp_path, Judge())
    deps.oracle = ExplodingOracle()
    deps.cross_model = None
    record, log = run_app(_app(), 1, deps)
    assert record.self_serve.value == "contact_sales"
    nodes = [entry["node"] for entry in log["nodes"]]
    assert "verify" not in nodes
    assert nodes[-1] == "accept"


def test_only_filter_and_concurrency(tmp_path) -> None:
    apps = [
        AppSeed(1, "CRM and Sales", "Acme", "acme.dev"),
        AppSeed(2, "CRM and Sales", "Other", "other.dev"),
    ]
    chosen = select_apps(apps, "acme")
    assert [app.app for app in chosen] == ["Acme"]

    def factory():
        return _deps(tmp_path / "cache", Judge())

    results = run_many(apps, 1, factory, concurrency=2)
    assert [record.app for record, _log in results] == ["Acme", "Other"]


def test_a_failed_page_fetch_is_skipped_not_fatal(tmp_path) -> None:
    class FlakyFetch:
        def get_text(self, url: str) -> str:
            if url.endswith("/pricing"):
                raise RuntimeError("403")
            return DOCS

    class TwoHits(Search):
        def search(self, query: str, *, max_results: int = 5):
            return [SearchHit(url="https://acme.dev/docs"), SearchHit(url="https://acme.dev/pricing")]

    deps = _deps(tmp_path, Judge())
    deps.fetch = FlakyFetch()
    deps.search = TwoHits()
    record, log = run_app(_app(), 1, deps)
    fetch_entry = next(entry for entry in log["nodes"] if entry["node"] == "fetch")
    assert fetch_entry["pages"] == 1
    assert fetch_entry["failed"] == ["https://acme.dev/pricing"]


def test_an_app_that_crashes_becomes_an_unknown_needs_human_row(tmp_path) -> None:
    class DeadSearch(Search):
        def search(self, query: str, *, max_results: int = 5):
            raise RuntimeError("search down")

    def factory():
        deps = _deps(tmp_path / "cache", Judge())
        deps.search = DeadSearch()
        deps.llm_clients = []
        return deps

    results = run_many([_app()], 2, factory, concurrency=1)
    record, log = results[0]
    assert record.needs_human is True
    assert record.self_serve.value == "unknown"
    assert any("pipeline error" in reason for reason in record.needs_human_reasons)
    assert "error" in log


def test_pass2_starts_from_the_pass1_extraction(tmp_path) -> None:
    class Counting(Extractor):
        calls = 0

        def complete_json(self, prompt, schema):
            Counting.calls += 1
            return super().complete_json(prompt, schema)

    first, _ = run_app(_app(), 1, _deps(tmp_path, Judge()))
    deps = _deps(tmp_path, Judge())
    counting = Counting()
    deps.llm_clients = [counting]
    deps.prior = {"acme": first.model_dump(mode="json")}
    record, log = run_app(_app(), 2, deps)
    extract = next(n for n in log["nodes"] if n["node"] == "extract")
    assert extract["reused"] == "pass1"
    # one re-extraction for the contradicted field, none for the initial extract
    assert Counting.calls == 1
    assert record.pass_id == 2
    assert record.self_serve.value == "self_serve_free"
