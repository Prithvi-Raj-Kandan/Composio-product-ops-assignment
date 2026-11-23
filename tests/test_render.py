"""PO-11: the case-study page renders offline from fixture data and stays honest."""

import json

from opsresearch.render import render_site


def _rec(app, ss="self_serve_free", needs=False, override=False):
    return {
        "app": app,
        "category": "CRM and Sales",
        "one_liner": f"{app} does things",
        "auth_methods": ["oauth2"],
        "self_serve": ss,
        "gate_type": "none",
        "gate_detail": "unknown",
        "api_surface": {"rest": "yes", "graphql": "no", "breadth": "broad", "styles": ["rest"]},
        "existing_mcp": "official",
        "mcp_url": None,
        "composio_has_toolkit": "no",
        "buildability": "yes",
        "main_blocker": "none",
        "blocker_detail": "unknown",
        "evidence": [
            {
                "field": "auth_methods",
                "url": f"https://{app.lower()}.dev/auth",
                "quote": "Use OAuth 2.0",
                "source": "doc",
            }
        ],
        "needs_human": needs,
        "needs_human_reasons": ["verification loops exhausted: self_serve"] if needs else [],
        "provenance": {"source": "human_override" if override else "search"},
        "unknown_reasons": {},
    }


def _repo(tmp_path, with_eval=False):
    data = tmp_path / "data"
    (data / "runs").mkdir(parents=True)
    recs = [_rec("Acme"), _rec("Beta", "contact_sales", needs=True), _rec("Gamma", override=True)]
    payload = {"pass": 2, "record_count": 3, "records": recs}
    (data / "results.json").write_text(json.dumps(payload))
    (data / "results_pass1.json").write_text(json.dumps({**payload, "pass": 1}))
    (data / "results.csv").write_text("app\nAcme\n")
    (tmp_path / "schema").mkdir()
    (tmp_path / "schema" / "app_record.schema.json").write_text("{}")
    (data / "runs" / "acme_pass2.json").write_text(
        json.dumps(
            {
                "app": "Acme",
                "loops": 1,
                "nodes": [
                    {
                        "node": "verify",
                        "failing": ["self_serve"],
                        "findings": [{"verifier": "cross_model", "field": "self_serve", "status": "contradicted"}],
                    },
                    {
                        "node": "verify",
                        "failing": [],
                        "findings": [{"verifier": "cross_model", "field": "self_serve", "status": "supported"}],
                    },
                ],
            }
        )
    )
    if with_eval:
        (data / "eval").mkdir()
        rep = {
            "split": "dev",
            "pass1": {
                "accuracy": 0.5,
                "labelled_cells": 8,
                "correct_cells": 4,
                "errors": [
                    {"app": "Acme", "field": "self_serve", "predicted": "x", "gold": "y", "taxonomy": "wrong_value"}
                ],
            },
            "pass2": {"accuracy": 0.75, "labelled_cells": 8, "correct_cells": 6, "errors": []},
            "comparison": {"pass1_accuracy": 0.5, "pass2_accuracy": 0.75, "delta": 0.25, "fields": {}},
        }
        (data / "eval" / "report_dev.json").write_text(json.dumps(rep))
    return tmp_path


def test_page_has_every_section_and_every_app(tmp_path) -> None:
    out = render_site(_repo(tmp_path), tmp_path / "site", llm=False)
    html = (out / "index.html").read_text()
    for anchor in ["headline", "patterns", "matrix", "agent", "verification", "try", "honesty", "for-agents"]:
        assert f'id="{anchor}"' in html
    for app in ["Acme", "Beta", "Gamma"]:
        assert app in html
    assert "human override" in html.lower()
    assert "needs human" in html.lower()
    assert "Rival" not in html and "CortexOne" not in html
    for name in ["results.json", "results.csv", "llms.txt", "app_record.schema.json", "patterns.json"]:
        assert (out / name).exists()


def test_json_ld_parses(tmp_path) -> None:
    out = render_site(_repo(tmp_path), tmp_path / "site", llm=False)
    html = (out / "index.html").read_text()
    start = html.index('<script type="application/ld+json">') + len('<script type="application/ld+json">')
    data = json.loads(html[start : html.index("</script>", start)])
    assert data["@type"] == "Dataset"
    assert data["distribution"]


def test_no_accuracy_number_without_labels(tmp_path) -> None:
    html = (render_site(_repo(tmp_path), tmp_path / "site", llm=False) / "index.html").read_text()
    assert "Gold labels not scored yet" in html


def test_accuracy_shown_when_eval_exists(tmp_path) -> None:
    html = (render_site(_repo(tmp_path, with_eval=True), tmp_path / "site", llm=False) / "index.html").read_text()
    assert "50.0%" in html and "75.0%" in html


def test_rule_verdicts_are_tagged(tmp_path) -> None:
    repo = _repo(tmp_path)
    payload = json.loads((repo / "data" / "results.json").read_text())
    payload["records"][0]["evidence"].append(
        {"field": "buildability", "url": "https://acme.dev/auth", "quote": "Use OAuth 2.0", "source": "derived_rule"}
    )
    (repo / "data" / "results.json").write_text(json.dumps(payload))
    html = (render_site(repo, tmp_path / "site", llm=False) / "index.html").read_text()
    assert ">rule</span>" in html
    assert "1 buildability verdicts were derived" in html
