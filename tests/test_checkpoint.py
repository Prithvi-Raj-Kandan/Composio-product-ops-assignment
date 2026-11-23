"""PO-21: each finished app is checkpointed, and --resume skips apps already done in this pass."""

from opsresearch.graph import load_checkpoints, run_many
from opsresearch.seed import AppSeed
from tests.test_graph import Judge, _deps


def test_checkpoint_and_resume(tmp_path) -> None:
    apps = [AppSeed(1, "CRM", "Acme", "acme.dev"), AppSeed(2, "CRM", "Other", "other.dev")]
    ck = tmp_path / "ck"
    run_many(apps[:1], 1, lambda: _deps(tmp_path / "cache", Judge()), checkpoint_dir=ck)
    done = load_checkpoints(ck, 1)
    assert set(done) == {"acme"}
    calls = []

    def factory():
        calls.append(1)
        return _deps(tmp_path / "cache", Judge())

    results = run_many(apps, 1, factory, checkpoint_dir=ck, resume=True)
    assert [r.app for r, _ in results] == ["Acme", "Other"]
    assert len(calls) == 1
