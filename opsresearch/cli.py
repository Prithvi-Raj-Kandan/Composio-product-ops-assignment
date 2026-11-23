"""Command line for research, export, eval, and smoke."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from opsresearch import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="opsresearch",
        description="Research auth, access, and API shape for a list of apps.",
    )
    parser.add_argument("--version", action="version", version=f"opsresearch {__version__}")
    sub = parser.add_subparsers(dest="command")

    export_parser = sub.add_parser(
        "export",
        help="Write schema/app_record.schema.json, data/results.json, and data/results.csv.",
    )
    export_parser.add_argument("--from", dest="source", default=None, help="Results JSON to publish.")
    export_parser.add_argument("--repo", default=".")
    export_parser.set_defaults(func=cmd_export)

    run_parser = sub.add_parser("run", help="Research every app for pass 1 or pass 2.")
    run_parser.add_argument("--apps", default="data/apps.csv")
    run_parser.add_argument("--pass", dest="pass_num", type=int, choices=(1, 2), required=True)
    run_parser.add_argument("--only", default="")
    run_parser.add_argument("--concurrency", type=int, default=4)
    run_parser.add_argument("--repo", default=".")
    run_parser.set_defaults(func=cmd_run)

    one_parser = sub.add_parser("research-one", help="Research a single app by name or URL.")
    one_parser.add_argument("target")
    one_parser.add_argument("--append", action="store_true")
    one_parser.add_argument("--pass", dest="pass_num", type=int, choices=(1, 2), default=2)
    one_parser.add_argument("--repo", default=".")
    one_parser.set_defaults(func=cmd_research_one)

    eval_parser = sub.add_parser("eval", help="Score pass 1 and pass 2 against the gold labels.")
    eval_parser.add_argument("--split", choices=("dev", "test"))
    eval_parser.add_argument("--repo", default=".")
    eval_parser.set_defaults(func=cmd_eval)

    smoke_parser = sub.add_parser("smoke", help="Check each configured service on one app.")
    smoke_parser.add_argument("--app", default="HubSpot")
    smoke_parser.set_defaults(func=cmd_smoke)
    return parser


def cmd_export(args: argparse.Namespace) -> int:
    from opsresearch.export import export_bundle

    source = Path(args.source) if args.source else None
    result = export_bundle(repo=Path(args.repo), source=source)
    print(
        f"schema {result['schema']}\njson {result['json']} ({result['record_count']} records)\ncsv {result['csv']}"
    )
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from opsresearch.export import export_bundle
    from opsresearch.graph import build_deps, run_many, select_apps, write_pass, write_run_log
    from opsresearch.seed import load_apps

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    root = Path(args.repo)
    apps = select_apps(load_apps(Path(args.apps)), args.only)
    overrides = root / "data" / "domain_overrides.csv"
    cache = root / "cache"

    def factory():
        return build_deps(cache, overrides)

    results = run_many(apps, args.pass_num, factory, concurrency=args.concurrency)
    records = [record for record, _log in results]
    pass_path = root / "data" / f"results_pass{args.pass_num}.json"
    write_pass(pass_path, records, args.pass_num)
    log_dir = root / "data" / "runs"
    for record, log in results:
        write_run_log(log_dir, record.app, args.pass_num, log)
    export_bundle(repo=root, source=pass_path)
    print(f"wrote {len(records)} records to {pass_path}")
    return 0


def cmd_research_one(args: argparse.Namespace) -> int:
    from opsresearch.export import export_bundle
    from opsresearch.graph import append_record, build_deps, resolve_target, run_app, write_run_log
    from opsresearch.seed import load_apps, load_overrides

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    root = Path(args.repo)
    apps_path = root / "data" / "apps.csv"
    apps = load_apps(apps_path) if apps_path.exists() else []
    overrides = load_overrides(root / "data" / "domain_overrides.csv")
    app = resolve_target(apps, args.target, overrides)
    deps = build_deps(root / "cache", root / "data" / "domain_overrides.csv")
    record, log = run_app(app, args.pass_num, deps)
    write_run_log(root / "data" / "runs", record.app, args.pass_num, log)
    print(record.model_dump_json(indent=2))
    if args.append:
        path = root / "data" / "results.json"
        append_record(path, record)
        export_bundle(repo=root, source=path)
        print(f"appended {record.app} to {path}")
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    from opsresearch.eval_harness import evaluate

    root = Path(args.repo)
    splits = [args.split] if args.split else ["dev", "test"]
    for split in splits:
        report = evaluate(root, split)
        comparison = report["comparison"]
        print(
            f"{split}: pass1={comparison['pass1_accuracy']} pass2={comparison['pass2_accuracy']} "
            f"delta={comparison['delta']} labelled_pass2={report['pass2']['labelled_cells']}"
        )
    return 0


def cmd_smoke(args: argparse.Namespace) -> int:
    from opsresearch.smoke import live_probes, run_smoke

    _rows, code, table = run_smoke(args.app, probes=live_probes(args.app))
    print(table)
    return code


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 0
    return int(func(args))
