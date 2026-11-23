"""PO-12/PO-13: the workflows exist and wire the right inputs, secrets, and permissions."""

from pathlib import Path

import yaml

WF = Path(".github/workflows")


def _load(name: str) -> dict:
    data = yaml.safe_load((WF / name).read_text(encoding="utf-8"))
    data["on"] = data.get("on", data.get(True))
    return data


def test_research_one_is_a_manual_trigger_with_a_target() -> None:
    wf = _load("research-one.yml")
    inputs = wf["on"]["workflow_dispatch"]["inputs"]
    assert inputs["target"]["required"] is True
    env = wf["jobs"]["research"]["env"]
    for key in ["COMPOSIO_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "TAVILY_API_KEY"]:
        assert env[key] == "${{ secrets." + key + " }}"
    steps = " ".join(str(s.get("run", "")) for s in wf["jobs"]["research"]["steps"])
    assert "research-one" in steps and "--append" in steps
    assert wf["jobs"]["deploy"]["uses"] == "./.github/workflows/pages.yml"


def test_pages_builds_without_keys_and_deploys() -> None:
    wf = _load("pages.yml")
    assert "workflow_call" in wf["on"]
    assert wf["permissions"]["pages"] == "write"
    build = " ".join(str(s.get("run", "")) for s in wf["jobs"]["build"]["steps"])
    assert "render --no-llm" in build
    assert "secrets" not in (WF / "pages.yml").read_text()
