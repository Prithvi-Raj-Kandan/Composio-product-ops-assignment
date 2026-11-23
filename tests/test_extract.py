from opsresearch.extract import extract_record, extraction_prompt
from opsresearch.fetch import CachedDoc
from opsresearch.llm import choose_verifier_backend, verifier_fallback_message
from opsresearch.ratelimit import RateLimiter, with_retries


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def test_rate_limiter_spaces_calls() -> None:
    clock = Clock()
    limiter = RateLimiter(0.5, clock=clock.monotonic, sleep=clock.sleep)
    limiter.wait()
    limiter.wait()
    assert clock.slept == [0.5]


def test_retries_then_succeeds() -> None:
    clock = Clock()
    limiter = RateLimiter(0, clock=clock.monotonic, sleep=clock.sleep)
    state = {"n": 0}

    def fn() -> str:
        state["n"] += 1
        if state["n"] < 3:
            raise RuntimeError("nope")
        return "ok"

    assert with_retries(fn, limiter, attempts=4) == "ok"
    assert state["n"] == 3


def _limiter() -> RateLimiter:
    return RateLimiter(0, clock=lambda: 0.0, sleep=lambda _seconds: None)


def _doc() -> CachedDoc:
    return CachedDoc(
        url="https://example.com/docs",
        text="Developers create a free API key in the Example dashboard.",
        path="",
        cache_hit=True,
    )


class FakeLlm:
    def __init__(self, payload: dict, name: str = "fake-gemini") -> None:
        self.payload = payload
        self.model_name = name
        self.calls = 0

    def complete_json(self, prompt: str, schema: dict) -> dict:
        self.calls += 1
        assert "unknown" in prompt
        assert "Example" in prompt
        return self.payload


def test_extraction_parses_and_keeps_a_quote_from_the_doc() -> None:
    quote = "Developers create a free API key in the Example dashboard."
    llm = FakeLlm(
        {
            "one_liner": "Example issues API keys.",
            "auth_methods": ["api_key"],
            "self_serve": "self_serve_free",
            "evidence": [
                {"field": "one_liner", "url": "https://example.com/docs", "quote": quote, "source": "doc"},
                {"field": "auth_methods", "url": "https://example.com/docs", "quote": quote, "source": "doc"},
                {"field": "self_serve", "url": "https://example.com/docs", "quote": quote, "source": "doc"},
            ],
        }
    )
    record = extract_record("Example", "CRM and Sales", [_doc()], [llm], _limiter())
    assert record.auth_methods[0].value == "api_key"
    assert record.self_serve.value == "self_serve_free"
    assert record.extraction_model == "fake-gemini"
    assert record.one_liner == "Example issues API keys."


def test_missing_quote_yields_unknown() -> None:
    llm = FakeLlm({"auth_methods": ["oauth2"], "self_serve": "contact_sales", "evidence": []})
    record = extract_record("Example", "CRM and Sales", [_doc()], [llm], _limiter())
    assert record.auth_methods[0].value == "unknown"
    assert record.self_serve.value == "unknown"


def test_quote_not_in_the_doc_is_dropped() -> None:
    llm = FakeLlm(
        {
            "auth_methods": ["oauth2"],
            "evidence": [
                {
                    "field": "auth_methods",
                    "url": "https://example.com/docs",
                    "quote": "Contact sales to receive an OAuth client.",
                    "source": "doc",
                }
            ],
        }
    )
    record = extract_record("Example", "CRM and Sales", [_doc()], [llm], _limiter())
    assert record.auth_methods[0].value == "unknown"


def test_fallback_client_is_used_when_the_first_fails() -> None:
    class Boom:
        model_name = "gemini-2.5-flash"

        def complete_json(self, prompt: str, schema: dict) -> dict:
            raise RuntimeError("rate limit")

    quote = "Developers create a free API key in the Example dashboard."
    backup = FakeLlm(
        {
            "auth_methods": ["api_key"],
            "evidence": [
                {"field": "auth_methods", "url": "https://example.com/docs", "quote": quote, "source": "doc"}
            ],
        },
        name="gemini-2.5-flash-lite",
    )
    record = extract_record("Example", "CRM and Sales", [_doc()], [Boom(), backup], _limiter())
    assert record.extraction_model == "gemini-2.5-flash-lite"
    assert backup.calls == 1


def test_groq_verifier_fallback_message() -> None:
    assert verifier_fallback_message({}) is not None
    assert "gemini-3.5-flash" in verifier_fallback_message({})
    assert verifier_fallback_message({"GROQ_API_KEY": "present"}) is None
    assert choose_verifier_backend({}) == ("gemini", "gemini-3.5-flash")
    assert choose_verifier_backend({"GROQ_API_KEY": "x", "GROQ_MODEL": "llama-test"}) == ("groq", "llama-test")


def test_extraction_prompt_differs_from_a_verifier_prompt() -> None:
    prompt = extraction_prompt("Example", "CRM and Sales", [_doc()])
    assert "Answer only from the pages" in prompt
    assert "independent verifier" not in prompt
