"""Command line entrypoint. Later tickets register the real subcommands."""

from __future__ import annotations

import argparse

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
        help="Write the JSON Schema plus results.json and results.csv.",
    )
    export_parser.set_defaults(func=_not_yet("export"))

    run_parser = sub.add_parser("run", help="Research every app for pass 1 or pass 2.")
    run_parser.add_argument("--apps", default="data/apps.csv")
    run_parser.add_argument("--pass", dest="pass_num", type=int, choices=(1, 2))
    run_parser.add_argument("--only", default="")
    run_parser.add_argument("--concurrency", type=int, default=4)
    run_parser.set_defaults(func=_not_yet("run"))

    one_parser = sub.add_parser("research-one", help="Research a single app by name or URL.")
    one_parser.add_argument("target")
    one_parser.add_argument("--append", action="store_true")
    one_parser.add_argument("--pass", dest="pass_num", type=int, choices=(1, 2), default=2)
    one_parser.set_defaults(func=_not_yet("research-one"))

    eval_parser = sub.add_parser("eval", help="Score pass 1 and pass 2 against the gold labels.")
    eval_parser.add_argument("--split", choices=("dev", "test"), required=False)
    eval_parser.set_defaults(func=_not_yet("eval"))

    smoke_parser = sub.add_parser("smoke", help="Check each configured service on one app.")
    smoke_parser.add_argument("--app", default="HubSpot")
    smoke_parser.set_defaults(func=_not_yet("smoke"))
    return parser


def _not_yet(name: str):
    def _run(_args: argparse.Namespace) -> int:
        print(f"{name} is not implemented yet")
        return 2

    return _run


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 0
    return int(func(args))
