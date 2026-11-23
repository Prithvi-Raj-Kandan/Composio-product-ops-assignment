"""Score pass 1 and pass 2 against the gold labels. Empty labels are not accuracy."""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from opsresearch.schema import SCORED_FIELDS
from opsresearch.seed import AppSeed, GoldApp, load_apps, load_gold, validate_gold

LABEL_COLUMNS = [
    "app",
    "category",
    "split",
    "archetype",
    *SCORED_FIELDS,
    "doc_auth",
    "doc_self_serve",
    "doc_api",
    "doc_mcp",
    "doc_buildability",
    "notes",
]


def write_gold_template(apps: list[AppSeed], gold: list[GoldApp], path: Path) -> None:
    by_name = {app.app: app for app in apps}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=LABEL_COLUMNS)
        writer.writeheader()
        for row in gold:
            if row.app not in by_name:
                raise ValueError(f"{row.app} is not in the app list")
            blank = {column: "" for column in LABEL_COLUMNS}
            blank["app"] = row.app
            blank["category"] = row.category
            blank["split"] = row.split
            blank["archetype"] = row.archetype
            writer.writerow(blank)


def load_labels(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def normalize_label(field: str, value: str | None) -> str:
    text = (value or "").strip()
    if text == "":
        return ""
    if field == "auth_methods":
        parts = [part.strip() for part in text.replace(",", "|").split("|") if part.strip()]
        return "|".join(sorted(parts))
    return text


def prediction(record: dict[str, Any] | None, field: str) -> str:
    if record is None:
        return ""
    if field == "auth_methods":
        methods = record.get("auth_methods") or []
        if not methods or methods == ["unknown"]:
            return "unknown"
        return "|".join(sorted(str(item) for item in methods))
    if field == "api_rest":
        return str(record["api_surface"]["rest"])
    if field == "api_graphql":
        return str(record["api_surface"]["graphql"])
    if field == "api_breadth":
        return str(record["api_surface"]["breadth"])
    return str(record.get(field, ""))


def score_pair(gold: str, pred: str) -> tuple[bool, str]:
    if pred == "":
        return False, "missing_prediction"
    if gold == pred:
        return True, "match"
    if pred == "unknown" and gold != "unknown":
        return False, "underclaim"
    if gold == "unknown" and pred != "unknown":
        return False, "overclaim"
    gold_parts = set(gold.split("|")) if "|" in gold or "|" in pred else set()
    pred_parts = set(pred.split("|")) if gold_parts else set()
    if gold_parts and pred_parts and gold_parts & pred_parts:
        return False, "partial"
    return False, "exact_miss"


def _index(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row["app"]).lower(): row for row in records}


def score_split(
    labels: list[dict[str, str]],
    records: list[dict[str, Any]] | None,
    split: str,
    *,
    pass_num: int | None,
) -> dict[str, Any]:
    rows = [row for row in labels if row.get("split") == split]
    indexed = _index(records or [])
    per_field: dict[str, Any] = {}
    errors = []
    labelled = 0
    correct = 0
    unlabelled = 0
    missing_pass = records is None
    for field in SCORED_FIELDS:
        hits = 0
        counted = 0
        for row in rows:
            gold = normalize_label(field, row.get(field))
            if gold == "":
                unlabelled += 1
                continue
            counted += 1
            labelled += 1
            pred = "" if missing_pass else prediction(indexed.get(row["app"].lower()), field)
            ok, kind = score_pair(gold, pred)
            if ok:
                hits += 1
                correct += 1
            else:
                errors.append(
                    {
                        "app": row["app"],
                        "field": field,
                        "taxonomy": kind,
                        "predicted": pred,
                        "gold": gold,
                        "pass": pass_num,
                    }
                )
        per_field[field] = {
            "labelled": counted,
            "correct": hits,
            "accuracy": None if counted == 0 else hits / counted,
        }
    taxonomy = dict(Counter(error["taxonomy"] for error in errors))
    return {
        "split": split,
        "pass": pass_num,
        "missing_results": missing_pass,
        "labelled_cells": labelled,
        "correct_cells": correct,
        "unlabelled_cells": unlabelled,
        "accuracy": None if labelled == 0 else correct / labelled,
        "per_field": per_field,
        "taxonomy": taxonomy,
        "errors": errors,
    }


def compare_passes(pass1: dict[str, Any], pass2: dict[str, Any], pass3: dict[str, Any] | None = None) -> dict[str, Any]:
    fields = {}
    for field in SCORED_FIELDS:
        before = pass1["per_field"][field]["accuracy"]
        after = pass2["per_field"][field]["accuracy"]
        delta = None if before is None or after is None else after - before
        fields[field] = {"pass1": before, "pass2": after, "delta": delta}
        fields[field]["pass3"] = pass3["per_field"][field]["accuracy"] if pass3 else None
    overall_delta = None
    if pass1["accuracy"] is not None and pass2["accuracy"] is not None:
        overall_delta = pass2["accuracy"] - pass1["accuracy"]
    return {
        "split": pass1["split"],
        "pass1_accuracy": pass1["accuracy"],
        "pass2_accuracy": pass2["accuracy"],
        "pass3_accuracy": pass3["accuracy"] if pass3 else None,
        "delta": overall_delta,
        "fields": fields,
    }


def _load_pass(path: Path) -> list[dict[str, Any]] | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        return list(payload.get("records") or [])
    if isinstance(payload, list):
        return payload
    raise ValueError(f"unreadable results file {path}")


def evaluate(repo: Path, split: str) -> dict[str, Any]:
    labels = load_labels(repo / "data" / "gold" / "gold_labels.csv")
    apps = load_apps(repo / "data" / "apps.csv")
    gold = load_gold(repo / "data" / "gold" / "gold_sample.csv")
    validate_gold(apps, gold)
    labelled_apps = {row["app"] for row in labels}
    gold_apps = {row.app for row in gold if row.split == split}
    if labelled_apps != {row.app for row in gold}:
        raise ValueError("gold_labels.csv apps do not match gold_sample.csv")
    pass1_records = _load_pass(repo / "data" / "results_pass1.json")
    pass2_records = _load_pass(repo / "data" / "results_pass2.json")
    pass1 = score_split(labels, pass1_records, split, pass_num=1)
    pass2 = score_split(labels, pass2_records, split, pass_num=2)
    pass3_records = _load_pass(repo / "data" / "results_pass3.json")
    pass3 = score_split(labels, pass3_records, split, pass_num=3) if pass3_records is not None else None
    report = {
        "split": split,
        "gold_apps": sorted(gold_apps),
        "pass1": pass1,
        "pass2": pass2,
        **({"pass3": pass3} if pass3 else {}),
        "comparison": compare_passes(pass1, pass2, pass3),
        "note": (
            "Accuracy is null when a cell has no human label or a pass file is missing. "
            "The harness does not invent a score."
        ),
    }
    out = repo / "data" / "eval" / f"report_{split}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report
