"""PO-22: the Pages build renders from what is committed, and an empty dataset still renders."""

import json
import subprocess
import sys
from pathlib import Path

from opsresearch.patterns import compute_patterns, records_frame
from opsresearch.render import render_site

REPO = Path(__file__).resolve().parents[1]


def test_committed_results_exist_and_are_tracked() -> None:
    path = REPO / "data" / "results.json"
    assert path.exists()
    tracked = subprocess.run(["git", "ls-files", "--error-unmatch", "data/results.json"], cwd=REPO, capture_output=True)
    assert tracked.returncode == 0, "data/results.json must be committed for the Pages build"
    assert len(json.loads(path.read_text(encoding="utf-8"))) > 0


def test_render_like_ci(tmp_path) -> None:
    out = tmp_path / "site"
    subprocess.run(
        [sys.executable, "-m", "opsresearch", "render", "--no-llm", "--out", str(out)],
        cwd=REPO,
        check=True,
    )
    assert (out / "index.html").stat().st_size > 1000


def test_empty_records_do_not_crash(tmp_path) -> None:
    compute_patterns(records_frame([]))
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "results.json").write_text("[]", encoding="utf-8")
    html = render_site(tmp_path, tmp_path / "site", llm=False)
    assert Path(html).exists()
