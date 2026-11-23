import shutil
from pathlib import Path

from opsresearch.eval_harness import compare_passes, evaluate, score_split, write_gold_template
from opsresearch.seed import load_apps, load_gold

ROOT = Path(__file__).resolve().parents[1]


def test_template_rows_are_blank_and_cover_the_gold_set(tmp_path) -> None:
    apps = load_apps(ROOT / "data" / "apps.csv")
    gold = load_gold(ROOT / "data" / "gold" / "gold_sample.csv")
    path = tmp_path / "gold_labels.csv"
    write_gold_template(apps, gold, path)
    text = path.read_text(encoding="utf-8")
    assert text.startswith("app,category,split,archetype,auth_methods,")
    assert "doc_auth" in text.splitlines()[0]
    assert text.count("\n") == 21
    assert ",oauth2," not in text
    assert ",self_serve_free," not in text


def test_pass2_accuracy_is_higher_and_errors_are_tagged() -> None:
    labels = [
        {
            "app": "Acme",
            "split": "dev",
            "auth_methods": "api_key",
            "self_serve": "self_serve_free",
            "api_rest": "yes",
            "api_graphql": "no",
            "api_breadth": "medium",
            "existing_mcp": "none",
            "buildability": "yes",
            "main_blocker": "none",
        }
    ]
    pass1 = [
        {
            "app": "Acme",
            "auth_methods": ["unknown"],
            "self_serve": "contact_sales",
            "api_surface": {"rest": "yes", "graphql": "no", "breadth": "medium"},
            "existing_mcp": "none",
            "buildability": "yes",
            "main_blocker": "none",
        }
    ]
    pass2 = [
        {
            "app": "Acme",
            "auth_methods": ["api_key"],
            "self_serve": "self_serve_free",
            "api_surface": {"rest": "yes", "graphql": "no", "breadth": "medium"},
            "existing_mcp": "none",
            "buildability": "yes",
            "main_blocker": "none",
        }
    ]
    first = score_split(labels, pass1, "dev", pass_num=1)
    second = score_split(labels, pass2, "dev", pass_num=2)
    assert second["accuracy"] > first["accuracy"]
    assert second["accuracy"] == 1
    kinds = {error["taxonomy"] for error in first["errors"]}
    assert "underclaim" in kinds
    assert "exact_miss" in kinds
    comparison = compare_passes(first, second)
    assert comparison["delta"] > 0
    assert comparison["fields"]["self_serve"]["delta"] == 1


def test_unlabelled_template_does_not_invent_accuracy(tmp_path) -> None:
    data = tmp_path / "data"
    (data / "gold").mkdir(parents=True)
    shutil.copy(ROOT / "data" / "apps.csv", data / "apps.csv")
    shutil.copy(ROOT / "data" / "gold" / "gold_sample.csv", data / "gold" / "gold_sample.csv")
    write_gold_template(
        load_apps(data / "apps.csv"),
        load_gold(data / "gold" / "gold_sample.csv"),
        data / "gold" / "gold_labels.csv",
    )
    report = evaluate(tmp_path, "dev")
    assert report["pass1"]["accuracy"] is None
    assert report["pass2"]["accuracy"] is None
    assert report["comparison"]["delta"] is None
    assert report["pass2"]["unlabelled_cells"] > 0
    assert (data / "eval" / "report_dev.json").exists()


def test_partial_auth_set_is_not_a_full_hit() -> None:
    labels = [{"app": "Acme", "split": "test", "auth_methods": "api_key|oauth2", "self_serve": ""}]
    records = [{"app": "Acme", "auth_methods": ["oauth2"], "self_serve": "unknown"}]
    scored = score_split(labels, records, "test", pass_num=2)
    assert scored["per_field"]["auth_methods"]["accuracy"] == 0
    assert scored["errors"][0]["taxonomy"] == "partial"
    assert scored["per_field"]["self_serve"]["labelled"] == 0
