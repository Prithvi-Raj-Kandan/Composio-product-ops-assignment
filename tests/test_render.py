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
    for anchor in ["headline", "patterns", "matrix", "agent", "try", "honesty", "for-agents"]:
        assert f'id="{anchor}"' in html
    assert 'id="verification"' not in html
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
    assert "Gold labels not scored yet" not in html
    assert "<td>Accuracy</td>" not in html


def test_accuracy_shown_when_eval_exists(tmp_path) -> None:
    html = (render_site(_repo(tmp_path, with_eval=True), tmp_path / "site", llm=False) / "index.html").read_text()
    # Gold agreement lives in the README now, not on the page.
    assert "50.0%" not in html and "75.0%" not in html
    assert "README (Verification)" in html


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


def _gold(repo, note):
    gold = repo / "data" / "gold"
    gold.mkdir(parents=True, exist_ok=True)
    (gold / "gold_labels.csv").write_text(f"app,split,self_serve,notes\nAcme,dev,self_serve_free,{note}\n")


def test_ai_made_gold_labels_are_disclosed_next_to_accuracy(tmp_path) -> None:
    repo = _repo(tmp_path, with_eval=True)
    _gold(repo, "AI-drafted from official docs; human review pending; 2 min")
    html = (render_site(repo, tmp_path / "site", llm=False) / "index.html").read_text()
    assert "drafted by an AI model from official docs, independently of this pipeline" in html
    assert "human review is pending" in html
    assert "hand-labelled" not in html
    assert "Grok" not in html
    assert '<div class="warnbox"><b>These gold labels' not in html


def test_human_gold_labels_are_not_called_ai(tmp_path) -> None:
    repo = _repo(tmp_path, with_eval=True)
    _gold(repo, "checked the pricing page")
    html = (render_site(repo, tmp_path / "site", llm=False) / "index.html").read_text()
    assert "drafted by an AI model" not in html


def test_pass3_before_after_and_confidence_tags(tmp_path) -> None:
    repo = _repo(tmp_path)
    data = repo / "data"
    payload = json.loads((data / "results.json").read_text())
    p2 = json.loads(json.dumps(payload))
    for r in p2["records"]:
        r["self_serve"] = "unknown"
    (data / "results_pass2.json").write_text(json.dumps(p2))
    payload["pass"] = 3
    payload["records"][0]["field_confidence"] = {"self_serve": "quote-verified", "auth_methods": "composio_metadata"}
    (data / "results.json").write_text(json.dumps(payload))
    (data / "run_stats_pass3.json").write_text(
        json.dumps(
            {
                "pass": 3,
                "apps": 3,
                "wall_clock_s": 60,
                "fields_filled_by_step": {"policy_restore": 3, "composio_metadata": 1},
                "usage": {"models": {"m": {"calls": 2}}, "cost_usd_known": 0.0},
            }
        )
    )
    html = (render_site(repo, tmp_path / "site", llm=False) / "index.html").read_text()
    assert 'id="pass3"' in html
    assert "Pass 1 → pass 2 → pass 3" in html
    assert "quote-verified" in html and "composio_metadata" in html
    assert "policy_restore" in html
    assert "Only <b>contradicted</b> demotes" in html


def test_pass3_note_states_verification_facts(tmp_path) -> None:
    repo = _repo(tmp_path)
    data = repo / "data"
    payload = json.loads((data / "results.json").read_text())
    p2 = json.loads(json.dumps(payload))
    p2["records"][1]["needs_human_reasons"] = ["pipeline error: RuntimeError"]
    (data / "results_pass2.json").write_text(json.dumps(p2))
    payload["records"][0]["field_confidence"] = {"self_serve": "quote-verified", "gate_type": "verifier-supported"}
    payload["records"][0]["verifier_verdicts"] = {"self_serve": "not_run", "gate_type": "supported"}
    (data / "results.json").write_text(json.dumps(payload))
    (data / "run_stats_pass3.json").write_text(
        json.dumps({"pass": 3, "apps": 3, "wall_clock_s": 1, "usage": {"models": {}, "cost_usd_known": 0.0}})
    )
    (data / "runs" / "acme_pass3.json").write_text(
        json.dumps(
            {"app": "Acme", "nodes": [{"node": "reresearch3", "kept": ["self_serve", "gate_type"], "verifier": "m"}]}
        )
    )
    html = (render_site(repo, tmp_path / "site", llm=False) / "index.html").read_text()
    assert "1 of 2 re-research claims are quote-verified only" in html
    assert "1 apps crashed in pass 2" in html
    assert "and a second model checked them" not in html


def test_human_section_describes_per_run_handoffs_with_counts(tmp_path) -> None:
    repo = _repo(tmp_path)
    data = repo / "data"
    (data / "domain_overrides.csv").write_text(
        "app,homepage,docs_url,extra_source,note\nGamma,https://gamma.io,https://docs.gamma.io,,rebrand\n"
    )
    payload = json.loads((data / "results.json").read_text())
    payload["records"][0]["field_confidence"] = {"self_serve": "quote-verified", "auth_methods": "quote-verified"}
    payload["records"][0]["verifier_verdicts"] = {"gate_type": "contradicted"}
    payload["records"][1]["needs_human_reasons"] = ["verification loops exhausted: self_serve"]
    (data / "results.json").write_text(json.dumps(payload))
    html = (render_site(repo, tmp_path / "site", llm=False) / "index.html").read_text()
    assert "Where the agent stops and a human decides" in html
    assert "Where a human was needed" not in html
    assert "data/domain_overrides.csv" in html and "--resume" in html
    assert "MAX_LOOPS = 2" in html
    assert 'data-count="domain_overrides">1<' in html
    assert 'data-count="needs_human">1<' in html
    assert 'data-count="loops_exhausted">1<' in html
    assert 'data-count="contradicted">1<' in html
    assert 'data-count="gated">1<' in html  # Beta is contact_sales
    assert 'data-count="quote_only_high_stakes">2<' in html
    assert "Gold-label audit" in html
    assert "One-time setup" in html and "connect-tavily" in html


def test_breadth_is_off_the_page_but_in_the_data(tmp_path) -> None:
    repo = _repo(tmp_path, with_eval=True)
    rep_path = repo / "data" / "eval" / "report_dev.json"
    rep = json.loads(rep_path.read_text())
    rep["comparison"]["fields"] = {
        "api_breadth": {"pass1": 0.1, "pass2": 0.1},
        "self_serve": {"pass1": 0.2, "pass2": 0.3},
    }
    rep_path.write_text(json.dumps(rep))
    out = render_site(repo, tmp_path / "site", llm=False)
    html = (out / "index.html").read_text()
    assert "<th>Breadth</th>" not in html
    assert ">self_serve<" in html
    assert "API breadth is kept in the data but left off the page" in html
    assert '"breadth"' in (out / "results.json").read_text()


def test_readme_has_calm_verification_section() -> None:
    from pathlib import Path

    readme = Path("README.md").read_text(encoding="utf-8")
    assert "## Verification" in readme
    assert "AI-drafted from official docs, independently of the pipeline; reviewed and confirmed by a human" in readme
    assert "review pending" not in readme.lower()
    assert "review pending" not in Path("HUMAN_IN_THE_LOOP.md").read_text(encoding="utf-8").lower()
    for name in ("README.md", "HUMAN_IN_THE_LOOP.md"):
        assert "Grok" not in Path(name).read_text(encoding="utf-8")


def test_reviewed_gold_labels_are_described_as_confirmed(tmp_path) -> None:
    repo = _repo(tmp_path, with_eval=True)
    _gold(repo, '"AI-drafted from official docs, independently of the pipeline; reviewed and confirmed by a human"')
    html = (render_site(repo, tmp_path / "site", llm=False) / "index.html").read_text()
    assert "reviewed and confirmed by a human" in html
    assert "review is pending" not in html and "review pending" not in html


def test_unknown_cells_show_reason_caption_and_tally(tmp_path) -> None:
    repo = _repo(tmp_path)
    data = repo / "data"
    payload = json.loads((data / "results.json").read_text())
    payload["records"][0]["self_serve"] = "unknown"
    payload["records"][0]["unknown_reason"] = {
        "self_serve": "no pricing page",
        "buildability": "self_serve unknown: no pricing page",
    }
    payload["records"][1]["unknown_reason"] = {"gate_type": "verifier contradicted"}
    (data / "results.json").write_text(json.dumps(payload))
    html = (render_site(repo, tmp_path / "site", llm=False) / "index.html").read_text()
    assert '<div class="ur">no pricing page</div>' in html
    assert 'id="unknown-reasons"' in html
    assert "<td>no pricing page</td><td>2</td><td>1</td>" in html
    assert '<div class="ur">self_serve unknown: no pricing page</div>' in html


def test_unknown_one_liner_shows_nothing(tmp_path) -> None:
    repo = _repo(tmp_path)
    data = repo / "data"
    payload = json.loads((data / "results.json").read_text())
    payload["records"][0]["one_liner"] = "unknown"
    payload["records"][0]["unknown_reason"] = {"one_liner": "only in one liner", "self_serve": "no pricing page"}
    (data / "results.json").write_text(json.dumps(payload))
    html = (render_site(repo, tmp_path / "site", llm=False) / "index.html").read_text()
    assert '<b>Acme</b><div class="small">unknown</div>' not in html
    assert "only in one liner" not in html
    assert "<td>no pricing page</td>" in html


def test_pass3_notes_targeted_rerun(tmp_path) -> None:
    repo = _repo(tmp_path)
    data = repo / "data"
    payload = json.loads((data / "results.json").read_text())
    payload["records"][0]["field_confidence"] = {"self_serve": "quote-verified"}
    (data / "results.json").write_text(json.dumps(payload))
    (data / "results_pass2.json").write_text(json.dumps(payload))
    (data / "run_stats_pass3.json").write_text(
        json.dumps(
            {
                "pass": 3,
                "apps": 3,
                "wall_clock_s": 1,
                "usage": {"models": {}, "cost_usd_known": 0.0},
                "targeted_rerun": {
                    "at": "2026-11-23 22:21 IST",
                    "wall_clock_s": 1800,
                    "cells_retried": 233,
                    "cells_filled": 40,
                    "filled_by_tag": {"verifier-supported": 30, "quote-verified": 10},
                },
            }
        )
    )
    html = (render_site(repo, tmp_path / "site", llm=False) / "index.html").read_text()
    assert "Targeted rerun" in html
    assert "2026-11-23 22:21 IST" in html and "30 min" in html
    assert "233 cells" in html and "40 filled" in html
