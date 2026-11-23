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

AMBIGUOUS = [
    "fanbasis",
    "iPayX",
    "Paygent Connect",
    "Sherlock",
    "Waterfall.io",
    "Mermaid CLI",
    "higgsfield",
    "YouTube Transcript",
]


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


def test_overrides_are_marked_for_a_human() -> None:
    overrides = load_overrides(OVERRIDES)
    for name in AMBIGUOUS:
        item = overrides[name.lower()]
        assert item.needs_human
        assert item.official_domain
        assert item.status == "needs human confirm"


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
    assert sherlock_domain.needs_human
    assert url_is_official("https://github.com/sherlock-project/sherlock", sherlock_domain)
    assert url_is_official("https://github.com/sherlock-project/sherlock/README.md", sherlock_domain)
    assert not url_is_official("https://github.com/other/repo", sherlock_domain)
    assert not url_is_official("https://medium.com/sherlock", sherlock_domain)


def test_shared_suffix_is_not_widened() -> None:
    app = AppSeed(1, "Finance and Fintech", "Binance", "binance-docs.github.io")
    resolved = resolve_domain(app, {})
    assert url_is_official("https://binance-docs.github.io/apidocs", resolved)
    assert not url_is_official("https://other-project.github.io/apidocs", resolved)
