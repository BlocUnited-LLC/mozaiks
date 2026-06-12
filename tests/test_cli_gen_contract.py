from __future__ import annotations

from pathlib import Path

from mozaiks_cli.main import create_parser


def _workspace() -> Path:
    return Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (_workspace() / relative_path).read_text(encoding="utf-8")


def test_gen_parser_accepts_validation_strategy() -> None:
    args = create_parser().parse_args(
        [
            "gen",
            "app",
            "--prompt",
            "Build a finance operations workspace with approval routing.",
            "--validation-strategy",
            "local",
        ]
    )

    assert args.command == "gen"
    assert args.mode == "app"
    assert args.validation_strategy == "local"


def test_gen_command_threads_validation_strategy_into_context() -> None:
    source = _read("mozaiks_cli/commands/gen.py")

    assert '"app_validation_strategy": validation_strategy' in source
    assert "default_app_validation_strategy" in source
    assert "normalize_app_validation_strategy" in source
    assert "Validation strategy" in source

