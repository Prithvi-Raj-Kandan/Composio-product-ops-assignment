"""Per-app LangGraph. Pass 1 extracts. Pass 2 verifies and re-researches failing fields."""

from __future__ import annotations

import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict
from urllib.parse import quote

from langgraph.graph import END, StateGraph

from opsresearch.discover import SearchClient, discover, select_search_backend
from opsresearch.extract import extract_record, merge_fields
from opsresearch.fetch import CachedDoc, FetchClient, HttpxFetchClient, app_slug, fetch_url
from opsresearch.llm import LlmClient, build_verifier_client, extraction_clients
from opsresearch.ratelimit import RateLimiter, get_rate_limiter
from opsresearch.schema import SCHEMA_VERSION, AppRecord
from opsresearch.seed import (
    AppSeed,
    DomainOverride,
    ResolvedDomain,
    load_overrides,
    resolve_domain,
    url_is_official,
)
from opsresearch.verify import CROSS_SCHEMA as CROSS_MODEL_SCHEMA
from opsresearch.verify import (
    MCP_REGISTRY_URL,
    ComposioToolkitClient,
    Finding,
    HttpRegistryClient,
    apply_oracle,
    apply_registry,
    check_quotes,
    cross_model_prompt,
    failing_fields,
    oracle_findings,
    parse_cross_model,
    registry_findings,
    slug_candidates,
)

MAX_LOOPS = 2


class ResearchState(TypedDict):
    app_name: str
    category: str
    hint: str
    source_id: int
    pass_num: int
    allowed_hosts: list[str]
    path_prefix: str
    path_scoped_host: str
    display_domain: str
    domain_source: str
    seed_urls: list[str]
    path_scopes: list[list[str]]
    domain_needs_human: bool
    domain_note: str
    hits: list[dict[str, str]]
    docs: list[dict[str, Any]]
    new_docs: list[dict[str, Any]]
    record: dict[str, Any]
    failing_fields: list[str]
    loops: int
    needs_human: bool
    needs_human_reasons: list[str]
    run_log: list[dict[str, Any]]
    search_backend: str


@dataclass
class ResearchDeps:
    search: SearchClient
    fetch: FetchClient
    llm_clients: list[LlmClient]
    limiter: RateLimiter
    oracle: Any
    registry: Any
    overrides: dict[str, DomainOverride]
    cache_dir: Path
    cross_model: LlmClient | None


def _resolved_from_state(state: ResearchState) -> ResolvedDomain:
    return ResolvedDomain(
        allowed_hosts=frozenset(state["allowed_hosts"]),
        path_prefix=state["path_prefix"],
        path_scoped_host=state["path_scoped_host"],
        path_scopes=tuple((pair[0], pair[1]) for pair in state["path_scopes"]),
        needs_human=state["domain_needs_human"],
        note=state["domain_note"],
        source=state["domain_source"],
        display_domain=state["display_domain"],
        seed_urls=tuple(state["seed_urls"]),
    )


def _docs_from_state(rows: list[dict[str, Any]]) -> list[CachedDoc]:
    return [
        CachedDoc(
            url=row["url"],
            text=row["text"],
            path=row.get("path") or "",
            cache_hit=bool(row.get("cache_hit")),
        )
        for row in rows
    ]


def _doc_row(doc: CachedDoc) -> dict[str, Any]:
    return {"url": doc.url, "text": doc.text, "path": doc.path, "cache_hit": doc.cache_hit}


def _log(state: ResearchState, node: str, **detail: Any) -> list[dict[str, Any]]:
    entry: dict[str, Any] = {"node": node}
    entry.update(detail)
    return state["run_log"] + [entry]


def _fetch_all(urls: list[str], app: str, deps: ResearchDeps) -> tuple[list[dict[str, Any]], list[str]]:
    """Fetch each page; a 403 or timeout drops that page and is logged, it does not kill the app."""
    docs: list[dict[str, Any]] = []
    failed: list[str] = []
    for url in urls:
        try:
            doc = fetch_url(url, app, deps.cache_dir, deps.fetch)
        except Exception:
            failed.append(url)
            continue
        if doc.text.strip():
            docs.append(_doc_row(doc))
        else:
            failed.append(url)
    return docs, failed


def build_graph(deps: ResearchDeps):
    def resolve(state: ResearchState) -> dict[str, Any]:
        return {
            "run_log": _log(
                state,
                "resolve_domain",
                domain=state["display_domain"],
                source=state["domain_source"],
                needs_human=state["domain_needs_human"],
            )
        }

    def discover_node(state: ResearchState) -> dict[str, Any]:
        app = AppSeed(state["source_id"], state["category"], state["app_name"], state["hint"])
        hits = discover(app, _resolved_from_state(state), deps.search)
        return {
            "hits": [hit.model_dump() for hit in hits],
            "search_backend": deps.search.name,
            "run_log": _log(state, "discover", urls=[hit.url for hit in hits], backend=deps.search.name),
        }

    def fetch_node(state: ResearchState) -> dict[str, Any]:
        docs, failed = _fetch_all([hit["url"] for hit in state["hits"]], state["app_name"], deps)
        cache_hits = sum(1 for doc in docs if doc["cache_hit"])
        return {
            "docs": docs,
            "run_log": _log(state, "fetch", pages=len(docs), cache_hits=cache_hits, failed=failed),
        }

    def extract_node(state: ResearchState) -> dict[str, Any]:
        record = extract_record(
            state["app_name"],
            state["category"],
            _docs_from_state(state["docs"]),
            deps.llm_clients,
            deps.limiter,
        )
        data = record.model_dump(mode="json")
        data["hint_url"] = state["hint"]
        data["official_domain"] = state["display_domain"]
        data["provenance"] = {"source": state["domain_source"]}
        data["source_id"] = state["source_id"] or None
        reasons = list(state["needs_human_reasons"])
        needs = state["domain_needs_human"]
        if needs and state["domain_note"] and state["domain_note"] not in reasons:
            reasons.append(state["domain_note"])
        elif needs and "domain override needs human confirm" not in reasons:
            reasons.append("domain override needs human confirm")
        data["needs_human"] = needs
        data["needs_human_reasons"] = reasons
        data["pass_id"] = state["pass_num"]
        record = AppRecord.model_validate(data)
        return {
            "record": record.model_dump(mode="json"),
            "needs_human": needs,
            "needs_human_reasons": reasons,
            "run_log": _log(state, "extract", model=record.extraction_model),
        }

    def verify_node(state: ResearchState) -> dict[str, Any]:
        record = AppRecord.model_validate(state["record"])
        pages = {doc["url"]: doc["text"] for doc in state["docs"]}
        findings: list[Finding] = check_quotes(record, pages)
        slug = slug_candidates(state["app_name"])[0]
        snapshot = None
        try:
            snapshot = deps.oracle.lookup(state["app_name"])
        except Exception as exc:
            findings.append(
                Finding(
                    verifier="oracle",
                    field="composio_has_toolkit",
                    status="insufficient",
                    detail=f"oracle error {type(exc).__name__}",
                )
            )
        else:
            findings.extend(oracle_findings(record, snapshot))
            record = apply_oracle(record, snapshot, slug)
        payload = None
        try:
            payload = deps.registry.search(state["app_name"])
        except Exception as exc:
            findings.append(
                Finding(
                    verifier="registry",
                    field="existing_mcp",
                    status="insufficient",
                    detail=f"registry error {type(exc).__name__}",
                )
            )
        else:
            kind, _, _ = _classify(payload, set(state["allowed_hosts"]))
            findings.extend(registry_findings(record, kind))
            search_url = f"{MCP_REGISTRY_URL}?search={quote(state['app_name'])}"
            record = apply_registry(record, payload, set(state["allowed_hosts"]), search_url=search_url)
        model_name = None
        if deps.cross_model is not None:
            model_name = deps.cross_model.model_name
            try:
                raw = deps.cross_model.complete_json(
                    cross_model_prompt(record, pages),
                    CROSS_MODEL_SCHEMA,
                )
                findings.extend(parse_cross_model(raw))
            except Exception as exc:
                findings.append(
                    Finding(
                        verifier="cross_model",
                        field="record",
                        status="insufficient",
                        detail=f"verifier error {type(exc).__name__}",
                    )
                )
            model_name = deps.cross_model.model_name
            data = record.model_dump(mode="json")
            data["verifier_model"] = model_name
            record = AppRecord.model_validate(data)
        failing = failing_fields(findings)
        return {
            "record": record.model_dump(mode="json"),
            "failing_fields": failing,
            "run_log": _log(
                state,
                "verify",
                loop=state["loops"],
                failing=failing,
                findings=[item.model_dump() for item in findings],
                verifier_model=model_name,
            ),
        }

    def reresearch_node(state: ResearchState) -> dict[str, Any]:
        loops = state["loops"] + 1
        app = AppSeed(state["source_id"], state["category"], state["app_name"], state["hint"])
        hits = discover(
            app,
            _resolved_from_state(state),
            deps.search,
            fields=state["failing_fields"],
        )
        known = {doc["url"] for doc in state["docs"]}
        fresh, failed = _fetch_all([hit.url for hit in hits if hit.url not in known], state["app_name"], deps)
        return {
            "loops": loops,
            "new_docs": fresh,
            "docs": state["docs"] + fresh,
            "hits": [hit.model_dump() for hit in hits],
            "run_log": _log(
                state,
                "reresearch",
                loop=loops,
                fields=list(state["failing_fields"]),
                urls=[hit.url for hit in hits],
                failed=failed,
            ),
        }

    def reextract_node(state: ResearchState) -> dict[str, Any]:
        docs = _docs_from_state(state["new_docs"] or state["docs"])
        update = extract_record(
            state["app_name"],
            state["category"],
            docs,
            deps.llm_clients,
            deps.limiter,
            only_fields=state["failing_fields"],
        )
        merged = merge_fields(AppRecord.model_validate(state["record"]), update, state["failing_fields"])
        return {
            "record": merged.model_dump(mode="json"),
            "run_log": _log(state, "reextract", fields=list(state["failing_fields"]), model=merged.extraction_model),
        }

    def accept_node(state: ResearchState) -> dict[str, Any]:
        reasons = list(state["needs_human_reasons"])
        needs = state["needs_human"]
        if state["pass_num"] == 2 and state["failing_fields"]:
            needs = True
            message = "verification loops exhausted: " + ", ".join(state["failing_fields"])
            if message not in reasons:
                reasons.append(message)
        data = dict(state["record"])
        data["needs_human"] = needs
        data["needs_human_reasons"] = reasons
        data["pass_id"] = state["pass_num"]
        record = AppRecord.model_validate(data)
        return {
            "record": record.model_dump(mode="json"),
            "needs_human": needs,
            "needs_human_reasons": reasons,
            "run_log": _log(state, "accept", needs_human=needs, failing=list(state["failing_fields"])),
        }

    def after_extract(state: ResearchState) -> str:
        if state["pass_num"] == 1:
            return "accept"
        return "verify"

    def after_verify(state: ResearchState) -> str:
        if state["failing_fields"] and state["loops"] < MAX_LOOPS:
            return "reresearch"
        return "accept"

    graph = StateGraph(ResearchState)
    graph.add_node("resolve_domain", resolve)
    graph.add_node("discover", discover_node)
    graph.add_node("fetch", fetch_node)
    graph.add_node("extract", extract_node)
    graph.add_node("verify", verify_node)
    graph.add_node("reresearch", reresearch_node)
    graph.add_node("reextract", reextract_node)
    graph.add_node("accept", accept_node)
    graph.set_entry_point("resolve_domain")
    graph.add_edge("resolve_domain", "discover")
    graph.add_edge("discover", "fetch")
    graph.add_edge("fetch", "extract")
    graph.add_conditional_edges("extract", after_extract, {"verify": "verify", "accept": "accept"})
    graph.add_conditional_edges("verify", after_verify, {"reresearch": "reresearch", "accept": "accept"})
    graph.add_edge("reresearch", "reextract")
    graph.add_edge("reextract", "verify")
    graph.add_edge("accept", END)
    return graph.compile()


def _classify(payload: dict[str, Any], hosts: set[str]) -> tuple[str, str | None, str]:
    from opsresearch.verify import classify_registry

    return classify_registry(payload, hosts)


def initial_state(app: AppSeed, resolved: ResolvedDomain, pass_num: int, backend: str) -> ResearchState:
    reasons: list[str] = []
    if resolved.needs_human:
        reasons.append(resolved.note or "domain override needs human confirm")
    return {
        "app_name": app.app,
        "category": app.category,
        "hint": app.hint,
        "source_id": app.source_id,
        "pass_num": pass_num,
        "allowed_hosts": sorted(resolved.allowed_hosts),
        "path_prefix": resolved.path_prefix,
        "path_scoped_host": resolved.path_scoped_host,
        "display_domain": resolved.display_domain,
        "domain_source": resolved.source,
        "seed_urls": list(resolved.seed_urls),
        "path_scopes": [[host, prefix] for host, prefix in resolved.path_scopes],
        "domain_needs_human": resolved.needs_human,
        "domain_note": resolved.note,
        "hits": [],
        "docs": [],
        "new_docs": [],
        "record": {},
        "failing_fields": [],
        "loops": 0,
        "needs_human": resolved.needs_human,
        "needs_human_reasons": reasons,
        "run_log": [],
        "search_backend": backend,
    }


def run_app(app: AppSeed, pass_num: int, deps: ResearchDeps) -> tuple[AppRecord, dict[str, Any]]:
    resolved = resolve_domain(app, deps.overrides)
    graph = build_graph(deps)
    final = graph.invoke(initial_state(app, resolved, pass_num, deps.search.name))
    record = AppRecord.model_validate(final["record"])
    log = {
        "app": app.app,
        "pass": pass_num,
        "needs_human": final["needs_human"],
        "loops": final["loops"],
        "nodes": final["run_log"],
    }
    return record, log


def _run_app_safely(
    app: AppSeed, pass_num: int, deps_for_app: Callable[[], ResearchDeps]
) -> tuple[AppRecord, dict[str, Any]]:
    """One bad app must not sink the batch. A crash becomes an all-unknown, needs_human row."""
    from opsresearch.schema import unknown_record

    try:
        return run_app(app, pass_num, deps_for_app())
    except Exception as exc:
        record = unknown_record(app.app, app.category)
        data = record.model_dump(mode="json")
        data["needs_human"] = True
        data["needs_human_reasons"] = [f"pipeline error: {type(exc).__name__}"]
        data["pass_id"] = pass_num
        data["source_id"] = app.source_id or None
        data["hint_url"] = app.hint
        record = AppRecord.model_validate(data)
        log = {
            "app": app.app,
            "pass": pass_num,
            "needs_human": True,
            "loops": 0,
            "nodes": [],
            "error": f"{type(exc).__name__}: {str(exc)[:300]}",
        }
        return record, log


def select_apps(apps: list[AppSeed], only: str) -> list[AppSeed]:
    if not only.strip():
        return list(apps)
    wanted = {part.strip().lower() for part in only.split(",") if part.strip()}
    chosen = [app for app in apps if app.app.lower() in wanted]
    missing = wanted - {app.app.lower() for app in chosen}
    if missing:
        raise ValueError(f"unknown apps in --only: {', '.join(sorted(missing))}")
    return chosen


def run_many(
    apps: list[AppSeed],
    pass_num: int,
    deps_for_app: Callable[[], ResearchDeps],
    *,
    concurrency: int = 4,
) -> list[tuple[AppRecord, dict[str, Any]]]:
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")
    results: list[tuple[AppRecord, dict[str, Any]]] = []
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(_run_app_safely, app, pass_num, deps_for_app): app for app in apps}
        for future in as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda item: item[0].app.lower())
    return results


def write_pass(path: Path, records: list[AppRecord], pass_num: int) -> None:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "json_schema": "schema/app_record.schema.json",
        "pass": pass_num,
        "record_count": len(records),
        "records": [record.model_dump(mode="json") for record in records],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def write_run_log(directory: Path, app: str, pass_num: int, log: dict[str, Any]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{app_slug(app)}_pass{pass_num}.json"
    path.write_text(json.dumps(log, indent=2) + "\n", encoding="utf-8")
    return path


def append_record(path: Path, record: AppRecord) -> None:
    records: list[dict[str, Any]] = []
    pass_id = record.pass_id
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            records = list(payload.get("records") or [])
            pass_id = payload.get("pass", pass_id)
        elif isinstance(payload, list):
            records = payload
    dumped = record.model_dump(mode="json")
    records = [row for row in records if str(row.get("app", "")).lower() != record.app.lower()]
    records.append(dumped)
    write_pass_payload = {
        "schema_version": SCHEMA_VERSION,
        "json_schema": "schema/app_record.schema.json",
        "pass": pass_id,
        "record_count": len(records),
        "records": records,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(write_pass_payload, indent=2) + "\n", encoding="utf-8")


def build_deps(cache_dir: Path, overrides_path: Path, env: dict[str, str] | None = None) -> ResearchDeps:
    return ResearchDeps(
        search=select_search_backend(env),
        fetch=HttpxFetchClient(),
        llm_clients=extraction_clients(env),
        limiter=get_rate_limiter(),
        oracle=ComposioToolkitClient(),
        registry=HttpRegistryClient(),
        overrides=load_overrides(overrides_path),
        cache_dir=cache_dir,
        cross_model=build_verifier_client(env),
    )


def resolve_target(apps: list[AppSeed], target: str, overrides: dict[str, DomainOverride]) -> AppSeed:
    text = target.strip()
    if text.startswith("http://") or text.startswith("https://"):
        for app in apps:
            resolved = resolve_domain(app, overrides)
            if url_is_official(text, resolved):
                return app
        from urllib.parse import urlparse

        host = urlparse(text).hostname or text
        return AppSeed(source_id=0, category="unlisted", app=host, hint=host)
    exact = [app for app in apps if app.app.lower() == text.lower()]
    if len(exact) == 1:
        return exact[0]
    partial = [app for app in apps if text.lower() in app.app.lower()]
    if len(partial) == 1:
        return partial[0]
    if len(partial) > 1:
        names = ", ".join(app.app for app in partial[:8])
        raise ValueError(f"more than one app matches {target!r}: {names}")
    return AppSeed(source_id=0, category="unlisted", app=text, hint=text)
