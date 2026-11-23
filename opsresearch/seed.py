"""Load the app list, domain overrides, and the frozen gold sample."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

GOLD_SEED = 20261123

PATH_SCOPED_HOSTS = {"github.com", "gitlab.com", "bitbucket.org"}
SHARED_SUFFIXES = {
    "github.io",
    "githubusercontent.com",
    "readthedocs.io",
    "gitbook.io",
    "vercel.app",
    "netlify.app",
    "blogspot.com",
    "medium.com",
    "notion.site",
}

ARCHETYPE_FAMILIES = (
    "clean-public-docs",
    "graphql-first",
    "gated",
    "js-or-login-walled",
    "no-api",
    "mcp",
    "ambiguous",
)


@dataclass(frozen=True)
class AppSeed:
    source_id: int
    category: str
    app: str
    hint: str


@dataclass(frozen=True)
class DomainOverride:
    """A human's resolution of an ambiguous app. These URLs win over the hint."""

    app: str
    homepage: str
    docs_url: str
    extra_source: str
    note: str

    def urls(self) -> list[str]:
        return [url.strip() for url in (self.homepage, self.docs_url, self.extra_source) if url.strip()]


@dataclass(frozen=True)
class GoldApp:
    app: str
    category: str
    split: str
    archetype: str


@dataclass(frozen=True)
class ResolvedDomain:
    allowed_hosts: frozenset[str]
    path_prefix: str
    path_scoped_host: str
    needs_human: bool
    note: str
    source: str
    display_domain: str
    seed_urls: tuple[str, ...] = ()
    path_scopes: tuple[tuple[str, str], ...] = ()


@dataclass
class HostRule:
    hosts: set[str] = field(default_factory=set)
    path_prefix: str = ""
    path_scoped_host: str = ""


def load_apps(path: Path) -> list[AppSeed]:
    path = Path(path)
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    apps = []
    for row in rows:
        apps.append(
            AppSeed(
                source_id=int(row["id"]),
                category=row["category"].strip(),
                app=row["app"].strip(),
                hint=row["hint"].strip(),
            )
        )
    return apps


def load_overrides(path: Path) -> dict[str, DomainOverride]:
    path = Path(path)
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    found = {}
    for row in rows:
        item = DomainOverride(
            app=row["app"].strip(),
            homepage=row.get("homepage", "").strip(),
            docs_url=row.get("docs_url", "").strip(),
            extra_source=row.get("extra_source", "").strip(),
            note=row.get("note", "").strip(),
        )
        found[item.app.lower()] = item
    return found


def _strip_www(host: str) -> str:
    return host.lower().removeprefix("www.").rstrip(".")


def registrable_hosts(host: str) -> set[str]:
    host = _strip_www(host)
    if not host:
        return set()
    parts = host.split(".")
    parent = ".".join(parts[-2:]) if len(parts) >= 2 else host
    if parent in SHARED_SUFFIXES or host in SHARED_SUFFIXES:
        return {host}
    if len(parts) <= 2:
        return {host}
    return {host, parent}


def _hint_tokens(hint: str) -> list[tuple[str, str]]:
    """Return (host, path) pairs mentioned in a hint, including parentheticals."""
    found: list[tuple[str, str]] = []
    pattern = re.compile(r"(?:https?://)?([a-z0-9.-]+\.[a-z]{2,})(/[^\s)]*)?", re.IGNORECASE)
    for match in pattern.finditer(hint):
        host = _strip_www(match.group(1))
        path = match.group(2) or ""
        path = path.rstrip(".,)")
        if path in {"", "/"}:
            path = ""
        found.append((host, path))
    return found


def _url_host_path(url: str) -> tuple[str, str]:
    raw = url if "://" in url else f"https://{url}"
    parsed = urlparse(raw)
    host = _strip_www(parsed.hostname or "")
    path = parsed.path or ""
    if path in {"", "/"}:
        path = ""
    return host, path


def resolve_domain(app: AppSeed, overrides: dict[str, DomainOverride]) -> ResolvedDomain:
    override = overrides.get(app.app.lower())
    if override is not None:
        hosts: set[str] = set()
        scopes: list[tuple[str, str]] = []
        for url in override.urls():
            host, path = _url_host_path(url)
            if not host:
                continue
            if host in PATH_SCOPED_HOSTS and path:
                hosts.add(host)
                scopes.append((host, path))
            else:
                hosts |= registrable_hosts(host)
        homepage_host, _ = _url_host_path(override.homepage) if override.homepage else ("", "")
        prefix = scopes[0][1] if scopes else ""
        scoped = scopes[0][0] if scopes else ""
        return ResolvedDomain(
            allowed_hosts=frozenset(hosts),
            path_prefix=prefix,
            path_scoped_host=scoped,
            path_scopes=tuple(scopes),
            needs_human=False,
            note=override.note,
            source="human_override",
            display_domain=homepage_host or (sorted(hosts, key=len)[0] if hosts else ""),
            seed_urls=tuple(override.urls()),
        )
    tokens = _hint_tokens(app.hint)
    hosts: set[str] = set()
    prefix = ""
    scoped = ""
    for host, path in tokens:
        hosts |= registrable_hosts(host)
        if path and host in PATH_SCOPED_HOSTS and not prefix:
            prefix = path
            scoped = host
            hosts = {host}
    if not hosts:
        raise ValueError(f"no domain in hint for {app.app!r}: {app.hint!r}")
    display = sorted(hosts, key=len)[0]
    scopes = ((scoped, prefix),) if prefix and scoped else ()
    return ResolvedDomain(
        allowed_hosts=frozenset(hosts),
        path_prefix=prefix,
        path_scoped_host=scoped,
        path_scopes=scopes,
        needs_human=False,
        note="",
        source="hint",
        display_domain=display,
        seed_urls=(),
    )


def host_allowed(host: str, allowed: frozenset[str] | set[str]) -> bool:
    host = _strip_www(host)
    for item in allowed:
        item = _strip_www(item)
        if host == item or host.endswith("." + item):
            return True
    return False


def url_is_official(url: str, resolved: ResolvedDomain) -> bool:
    raw = url if "://" in url else f"https://{url}"
    parsed = urlparse(raw)
    host = parsed.hostname or ""
    if not host_allowed(host, resolved.allowed_hosts):
        return False
    scopes = resolved.path_scopes
    if not scopes and resolved.path_prefix and resolved.path_scoped_host:
        scopes = ((resolved.path_scoped_host, resolved.path_prefix),)
    bare = _strip_www(host)
    path = parsed.path or "/"
    for scoped_host, prefix in scopes:
        scoped = _strip_www(scoped_host)
        if bare == scoped or bare.endswith("." + scoped):
            return path == prefix or path.startswith(prefix.rstrip("/") + "/")
    return True


def hint_url(app: AppSeed, resolved: ResolvedDomain) -> str | None:
    tokens = _hint_tokens(app.hint)
    if tokens:
        host, path = tokens[0]
        if resolved.path_prefix and host in PATH_SCOPED_HOSTS:
            path = resolved.path_prefix
        elif not path:
            path = ""
        return f"https://{host}{path}"
    if resolved.display_domain:
        return f"https://{resolved.display_domain}{resolved.path_prefix}"
    return None


def load_gold(path: Path) -> list[GoldApp]:
    path = Path(path)
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return [
        GoldApp(
            app=row["app"].strip(),
            category=row["category"].strip(),
            split=row["split"].strip(),
            archetype=row["archetype"].strip(),
        )
        for row in rows
    ]


def archetype_family(tag: str) -> str:
    text = tag.lower()
    if "ambiguous" in text:
        return "ambiguous"
    if "no-api" in text or "oss" in text:
        return "no-api"
    if "mcp" in text:
        return "mcp"
    if "graphql" in text:
        return "graphql-first"
    if "js-or-login" in text or "login-walled" in text:
        return "js-or-login-walled"
    if any(piece in text for piece in ("gated", "contact-sales", "enterprise", "platform-review", "paid-plan")):
        return "gated"
    if "clean-public" in text:
        return "clean-public-docs"
    return "other"


def validate_gold(apps: list[AppSeed], gold: list[GoldApp]) -> None:
    by_name = {app.app: app for app in apps}
    if len(gold) != 20:
        raise ValueError(f"gold sample has {len(gold)} rows, expected 20")
    missing = [row.app for row in gold if row.app not in by_name]
    if missing:
        raise ValueError(f"gold apps missing from apps.csv: {missing}")
    for row in gold:
        if by_name[row.app].category != row.category:
            raise ValueError(f"category mismatch for {row.app}")
        if row.split not in {"dev", "test"}:
            raise ValueError(f"bad split for {row.app}")
        if archetype_family(row.archetype) not in ARCHETYPE_FAMILIES:
            raise ValueError(f"archetype {row.archetype!r} is not in a required family")
    categories = sorted({app.category for app in apps})
    for category in categories:
        rows = [row for row in gold if row.category == category]
        if len(rows) != 2:
            raise ValueError(f"{category} has {len(rows)} gold apps, expected 2")
        splits = {row.split for row in rows}
        if splits != {"dev", "test"}:
            raise ValueError(f"{category} splits are {splits}, expected one dev and one test")
    families = {archetype_family(row.archetype) for row in gold}
    missing_families = [name for name in ARCHETYPE_FAMILIES if name not in families]
    if missing_families:
        raise ValueError(f"gold sample misses archetype families: {missing_families}")


def sample_gold(apps: list[AppSeed], gold_path: Path, *, seed: int = GOLD_SEED) -> list[GoldApp]:
    """Return the frozen gold sample.

    The 20 apps were chosen by hand so the archetypes are covered, then locked.
    A different seed is refused so a replicator cannot silently draw a new set.
    """
    if seed != GOLD_SEED:
        raise ValueError(
            f"gold sample is frozen at seed {GOLD_SEED}; refusing to re-sample with seed {seed}"
        )
    gold = load_gold(gold_path)
    validate_gold(apps, gold)
    return gold
