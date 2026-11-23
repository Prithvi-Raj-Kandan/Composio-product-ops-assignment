from opsresearch import __version__
from opsresearch.cli import main


def test_package_version() -> None:
    assert __version__ == "0.1.0"


def test_help_lists_commands(capsys) -> None:
    try:
        code = main(["--help"])
    except SystemExit as exc:
        code = exc.code
    captured = capsys.readouterr()
    assert code == 0
    for name in ("export", "run", "research-one", "eval", "smoke"):
        assert name in captured.out


def test_unimplemented_command_is_explicit() -> None:
    assert main(["export"]) == 2
