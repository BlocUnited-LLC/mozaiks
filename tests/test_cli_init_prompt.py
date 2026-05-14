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
    assert (target_dir / "requirements.txt").exists()
    assert (target_dir / ".env.example").exists()
    assert (target_dir / ".gitignore").exists()
    assert (target_dir / "README.md").exists()
    assert (target_dir / "AGENTS.md").exists()
    assert (target_dir / "CLAUDE.md").exists()
    assert (target_dir / ".claude" / "rules" / "modules.md").exists()
    assert (target_dir / ".claude" / "skills" / "add-module" / "SKILL.md").exists()
    assert (target_dir / "scripts" / "run-studio.ps1").exists()
    assert (target_dir / "scripts" / "run-backend.ps1").exists()
    assert (target_dir / "scripts" / "run-frontend.ps1").exists()
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


def test_init_command_creates_package_consumer_scaffold(tmp_path) -> None:
    target_dir = tmp_path / "consumer-app"

    init_command.create_scaffold(
        target_dir=target_dir,
        preset="integrated",
        app_name="consumer-app",
        starter=False,
    )

    requirements = (target_dir / "requirements.txt").read_text(encoding="utf-8")
    assert requirements.strip() == f"mozaiks=={init_command._current_mozaiks_version()}"

    env_example = (target_dir / ".env.example").read_text(encoding="utf-8")
    assert "OPENAI_API_KEY=" in env_example
    assert "MONGO_URI=mongodb://localhost:27017/mozaiks" in env_example

    gitignore = (target_dir / ".gitignore").read_text(encoding="utf-8")
    assert ".venv/" in gitignore
    assert ".env" in gitignore
    assert "generated/" in gitignore

    readme = (target_dir / "README.md").read_text(encoding="utf-8")
    assert "python -m pip install -r requirements.txt" in readme
    assert "mozaiks studio --dir . --open" in readme
    assert "installed `mozaiks` package" in readme
    assert "Coding Agent Guidance" in readme

    agents_md = (target_dir / "AGENTS.md").read_text(encoding="utf-8")
    assert "Do not assume a sibling checkout" in agents_md
    assert "app/modules/" in agents_md

    claude_md = (target_dir / "CLAUDE.md").read_text(encoding="utf-8")
    assert "not the Mozaiks framework source repository" in claude_md
    assert ".claude/rules/" in claude_md

    expected_rules = {
        "app-bundle.md",
        "docs.md",
        "frontend.md",
        "modules.md",
        "workflows.md",
    }
    assert expected_rules == {
        path.name for path in (target_dir / ".claude" / "rules").iterdir() if path.is_file()
    }
    modules_rule = (target_dir / ".claude" / "rules" / "modules.md").read_text(encoding="utf-8")
    assert "backend/handler.py" in modules_rule
    assert "service.py" in modules_rule

    expected_skills = {
        "add-module",
        "add-page",
        "create-workflow",
        "docs-maintenance",
        "setup",
    }
    assert expected_skills == {
        path.name for path in (target_dir / ".claude" / "skills").iterdir() if path.is_dir()
    }
    setup_skill = (target_dir / ".claude" / "skills" / "setup" / "SKILL.md").read_text(encoding="utf-8")
    assert "run-studio.ps1" in setup_skill

    backend_script = (target_dir / "scripts" / "run-backend.ps1").read_text(encoding="utf-8")
    assert "mozaiksai.hosts.mozaiks:app" in backend_script
    assert "MOZAIKS_APP_WORKSPACE_PATH" in backend_script

    frontend_script = (target_dir / "scripts" / "run-frontend.ps1").read_text(encoding="utf-8")
    assert "resolve_web_shell_root" in frontend_script
    assert "npm --prefix" in frontend_script

    studio_script = (target_dir / "scripts" / "run-studio.ps1").read_text(encoding="utf-8")
    assert '"studio"' in studio_script
    assert '"--dir"' in studio_script
