import json
from argparse import Namespace

from mozaiks_cli.commands import init_command
from mozaiks_cli.main import create_parser


def _load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_init_parser_leaves_name_unset_when_omitted() -> None:
    args = create_parser().parse_args(["init", "chat"])

    assert args.name is None
    assert args.directory is None
    assert args.starter is False


def test_add_parser_accepts_modules_feature() -> None:
    args = create_parser().parse_args(["add", "modules"])

    assert args.feature == "modules"


def test_init_command_prompts_for_name_when_missing(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("builtins.input", lambda _: "prompted-app")
    monkeypatch.chdir(tmp_path)

    init_command.run(Namespace(preset="chat", name=None, directory=None, starter=False))

    target_dir = tmp_path / "prompted-app"
    app_json = _load_json(target_dir / "app" / "app.json")
    assert app_json["appName"] == "prompted-app"
    assert (target_dir / "app" / "config" / "ai.json").exists()
    assert (target_dir / "app" / "config" / "shell.json").exists()
    assert (target_dir / "app" / "brand" / "theme_config.json").exists()
    assert (target_dir / "app" / "ui" / "route_manifest.json").exists()
    assert (target_dir / "app" / "modules" / "README.md").exists()
    assert (target_dir / "app" / "workflows" / "README.md").exists()
    modules_readme = (target_dir / "app" / "modules" / "README.md").read_text(encoding="utf-8")
    assert "backend/handler.py" in modules_readme
    assert "events.yaml" in modules_readme
    assert not (target_dir / "app" / "workflows" / "HelloWorkflow").exists()


def test_init_command_uses_explicit_directory_when_provided(monkeypatch, tmp_path) -> None:
    target_dir = tmp_path / "my-test-app"
    monkeypatch.setattr("builtins.input", lambda _: "plutus2")

    init_command.run(Namespace(preset="chat", name=None, directory=str(target_dir), starter=False))

    app_json = _load_json(target_dir / "app" / "app.json")
    assert app_json["appName"] == "plutus2"
    assert not (tmp_path / "plutus2").exists()


def test_init_command_starter_scaffold_seeds_entry_workflow(tmp_path) -> None:
    target_dir = tmp_path / "starter-app"

    init_command.run(Namespace(preset="chat", name="starter-app", directory=str(target_dir), starter=True))

    ai_json = _load_json(target_dir / "app" / "config" / "ai.json")
    assert ai_json["chat"]["chat_startup_mode"] == "workflow"
    assert ai_json["workflows"]["entry_point"] == "HelloWorkflow"
    assert (target_dir / "app" / "workflows" / "HelloWorkflow" / "orchestrator.yaml").exists()
