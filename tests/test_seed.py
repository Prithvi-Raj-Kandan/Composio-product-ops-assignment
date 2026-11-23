from pathlib import Path

import pytest
from opsresearch.seed import (
    ARCHETYPE_FAMILIES,
    GOLD_SEED,
    AppSeed,
    archetype_family,
    load_apps,
    load_overrides,
    resolve_domain,
    sample_gold,
    url_is_official,
)

ROOT = Path(__file__).resolve().parents[1]
APPS = ROOT / "data" / "apps.csv"
GOLD = ROOT / "data" / "gold" / "gold_sample.csv"
OVERRIDES = ROOT / "data" / "domain_overrides.csv"

OVERRIDE_ROWS = {
    "ipayx": {
        "homepage": "https://www.ipayx.ai",
        "docs_url": "https://www.ipayx.ai/developers",
        "extra_source": "https://github.com/iPAYX-Technologies/mcp-fx-audit",
        "note": "Has OSS MCP repo",
    },
    "fanbasis": {
        "homepage": "https://commas.com",
        "docs_url": "https://commasdocs.com/",
        "extra_source": "https://commas.com/fanbasis-is-now-commas",
        "note": "Rebranded to Commas",
    },
    "paygent connect": {
        "homepage": "https://www.gopaygent.com",
        "docs_url": "https://www.gopaygent.com/developers/docs",
        "extra_source": "https://www.nmi.com/",
        "note": "GoPaygent white-label on NMI gateway",
    },
    "waterfall.io": {
        "homepage": "https://waterfall.io",
        "docs_url": "https://docs.waterfall.io/v1/introduction",
        "extra_source": "",
        "note": "",
    },
    "higgsfield": {
        "homepage": "https://higgsfield.ai",
        "docs_url": "https://docs.higgsfield.ai/docs",
        "extra_source": "",
        "note": "",
    },
    "youtube transcript": {
        "homepage": "https://transcriptapi.com",
        "docs_url": "https://transcriptapi.com/docs/",
        "extra_source": "",
        "note": "Third-party TranscriptAPI",
    },
}


def test_sampler_reproduces_the_frozen_gold_set() -> None:
    apps = load_apps(APPS)
    assert len(apps) == 100
    first = sample_gold(apps, GOLD, seed=GOLD_SEED)
    second = sample_gold(apps, GOLD, seed=GOLD_SEED)
    assert [(row.app, row.split, row.archetype) for row in first] == [
        (row.app, row.split, row.archetype) for row in second
    ]
    assert len(first) == 20
    categories = {app.category for app in apps}
    for category in categories:
        rows = [row for row in first if row.category == category]
        assert len(rows) == 2
        assert {row.split for row in rows} == {"dev", "test"}
    families = {archetype_family(row.archetype) for row in first}
    assert set(ARCHETYPE_FAMILIES) <= families


def test_other_seed_is_refused() -> None:
    apps = load_apps(APPS)
    with pytest.raises(ValueError, match="frozen"):
        sample_gold(apps, GOLD, seed=1)


def test_override_sheet_has_the_human_columns_and_rows() -> None:
    text = OVERRIDES.read_text(encoding="utf-8")
    header = text.splitlines()[0]
    assert header == "app,homepage,docs_url,extra_source,note"
    overrides = load_overrides(OVERRIDES)
    assert set(overrides) == set(OVERRIDE_ROWS)
    for key, expected in OVERRIDE_ROWS.items():
        item = overrides[key]
        assert item.homepage == expected["homepage"]
        assert item.docs_url == expected["docs_url"]
        assert item.extra_source == expected["extra_source"]
        assert item.note == expected["note"]


def test_domain_filter_rejects_third_party_pages() -> None:
    apps = {app.app: app for app in load_apps(APPS)}
    overrides = load_overrides(OVERRIDES)
    stripe = next(app for app in apps.values() if app.app == "Stripe")
    resolved = resolve_domain(stripe, overrides)
    assert url_is_official("https://docs.stripe.com/api", resolved)
    assert url_is_official("https://stripe.com/docs", resolved)
    assert not url_is_official("https://medium.com/stripe-api-auth", resolved)
    assert not url_is_official("https://dev.to/stripe", resolved)
    assert not url_is_official("https://stripe.com.evil.com/docs", resolved)
    assert not url_is_official("https://notstripe.com/docs", resolved)

    github = apps["GitHub"]
    github_domain = resolve_domain(github, overrides)
    assert url_is_official("https://docs.github.com/rest", github_domain)
    assert not url_is_official("https://medium.com/github-api", github_domain)

    sherlock = apps["Sherlock"]
    sherlock_domain = resolve_domain(sherlock, overrides)
    assert sherlock_domain.source == "hint"
    assert url_is_official("https://github.com/sherlock-project/sherlock", sherlock_domain)
    assert url_is_official("https://github.com/sherlock-project/sherlock/README.md", sherlock_domain)
    assert not url_is_official("https://github.com/other/repo", sherlock_domain)
    assert not url_is_official("https://medium.com/sherlock", sherlock_domain)


def test_human_override_wins_over_the_hint() -> None:
    apps = {app.app: app for app in load_apps(APPS)}
    overrides = load_overrides(OVERRIDES)

    fanbasis = resolve_domain(apps["fanbasis"], overrides)
    assert fanbasis.source == "human_override"
    assert fanbasis.note == "Rebranded to Commas"
    assert url_is_official("https://commas.com/pricing", fanbasis)
    assert url_is_official("https://commasdocs.com/guide", fanbasis)
    assert url_is_official("https://commas.com/fanbasis-is-now-commas", fanbasis)
    assert not url_is_official("https://fanbasis.com/docs", fanbasis)
    assert fanbasis.seed_urls[0] == "https://commas.com"

    ipayx = resolve_domain(apps["iPayX"], overrides)
    assert ipayx.source == "human_override"
    assert url_is_official("https://www.ipayx.ai/developers", ipayx)
    assert url_is_official("https://github.com/iPAYX-Technologies/mcp-fx-audit", ipayx)
    assert url_is_official("https://github.com/iPAYX-Technologies/mcp-fx-audit/blob/main/README.md", ipayx)
    assert not url_is_official("https://github.com/someone/other-mcp", ipayx)
    assert url_is_official("https://ipayx.ai/developers", ipayx)

    paygent = resolve_domain(apps["Paygent Connect"], overrides)
    assert url_is_official("https://www.gopaygent.com/developers/docs", paygent)
    assert url_is_official("https://www.nmi.com/products", paygent)
    assert not url_is_official("https://paygent.example/docs", paygent)

    youtube = resolve_domain(apps["YouTube Transcript"], overrides)
    assert youtube.source == "human_override"
    assert youtube.note == "Third-party TranscriptAPI"
    assert url_is_official("https://transcriptapi.com/docs/api", youtube)
    assert not url_is_official("https://developers.google.com/youtube", youtube)


def test_shared_suffix_is_not_widened() -> None:
    app = AppSeed(1, "Finance and Fintech", "Binance", "binance-docs.github.io")
    resolved = resolve_domain(app, {})
    assert url_is_official("https://binance-docs.github.io/apidocs", resolved)
    assert not url_is_official("https://other-project.github.io/apidocs", resolved)
