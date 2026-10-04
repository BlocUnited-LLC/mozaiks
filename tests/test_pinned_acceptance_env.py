"""The pinned acceptance environment: explicit host environment, proof, detached host."""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "pinned_acceptance_env.py"
_spec = importlib.util.spec_from_file_location("pinned_acceptance_env", SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
env_script = importlib.util.module_from_spec(_spec)
sys.modules["pinned_acceptance_env"] = env_script
_spec.loader.exec_module(env_script)

STALE_CHECKOUT = Path("C:/stale-checkout") if os.name == "nt" else Path("/stale-checkout")
WHEEL = "mozaiks-0.1.12-py3-none-any.whl"


# --------------------------------------------------------------------------- host environment


def test_every_path_variable_points_into_the_install_and_nothing_else_is_inherited(tmp_path: Path) -> None:
    packages = tmp_path / "venv" / "Lib" / "site-packages"
    evidence = tmp_path / "evidence"
    caller = {
        "PATH": "/usr/bin", "SYSTEMROOT": "C:/Windows", "OPENAI_API_KEY": "from-the-shell",
        "MOZAIKS_FACTORY_APP_PATH": str(STALE_CHECKOUT / "factory_app"), "PYTHONPATH": str(STALE_CHECKOUT),
    }
    file_values = {
        "MONGO_URI": "mongodb://db.example", "AUTH_ENABLED": "false",
        "MOZAIKS_FACTORY_APP_PATH": str(STALE_CHECKOUT / "factory_app"), "PYTHONPATH": str(STALE_CHECKOUT),
    }
    environment, replaced = env_script.host_environment(
        file_values=file_values, paths=env_script.install_paths(packages, evidence), caller=caller,
    )

    assert environment["MOZAIKS_FACTORY_APP_PATH"] == str(packages / "factory_app")
    assert environment["MOZAIKS_WORKFLOWS_PATH"] == str(packages / "factory_app" / "workflows")
    assert environment["MOZAIKS_CHAT_UI_PATH"] == str(packages / "mozaiks_chat_ui")
    assert environment["MOZAIKS_WEB_SHELL_PATH"] == str(packages / "web_shell")
    assert environment["MOZAIKS_GENERATED_ARTIFACTS_PATH"] == str(evidence / "generated")
    assert environment["PYTHON_DOTENV_DISABLED"] == "1"
    assert (environment["MONGO_URI"], environment["AUTH_ENABLED"], environment["PATH"]) == (
        "mongodb://db.example", "false", "/usr/bin",
    )
    assert "PYTHONPATH" not in environment
    assert "OPENAI_API_KEY" not in environment
    assert replaced == ["MOZAIKS_FACTORY_APP_PATH", "PYTHONPATH"]


def test_env_files_are_plain_key_value_lines(tmp_path: Path) -> None:
    path = tmp_path / "host.env"
    path.write_text(
        "# the host\nexport MONGO_URI=mongodb://127.0.0.1:27309\nAUTH_ENABLED = 'false'\n\nNAME=\"a b\"\n",
        encoding="utf-8",
    )
    assert env_script.parse_env_file(path) == {
        "MONGO_URI": "mongodb://127.0.0.1:27309", "AUTH_ENABLED": "false", "NAME": "a b",
    }
    path.write_text("not a variable\n", encoding="utf-8")
    with pytest.raises(env_script.PreparationError, match="KEY=VALUE"):
        env_script.parse_env_file(path)


# --------------------------------------------------------------------------- proof evaluation


def clean_proof(tmp_path: Path) -> dict[str, Any]:
    venv = tmp_path / "venv"
    site = venv / "Lib" / "site-packages"
    evidence = tmp_path / "evidence"
    base = tmp_path / "base-python"
    return {
        "host_import": {"ok": True},
        "cwd": str(evidence),
        "python": {"base_prefix": str(base)},
        "packages": {
            name: {"origin": str(site / name / "__init__.py"), "locations": [str(site / name)], "loaded_from": None}
            for name in env_script.EXPECTED_TOP_LEVEL
        },
        "resolved_paths": {
            "factory_app": str(site / "factory_app"),
            "factory_workflows": str(site / "factory_app" / "workflows"),
            "chat_ui": str(site / "mozaiks_chat_ui"),
            "web_shell": str(site / "web_shell"),
        },
        "packs": [{"id": "mozaikspay", "path": str(site / "factory_app" / "build_context" / "mozaikspay")}],
        "environment_paths": {
            "MOZAIKS_FACTORY_APP_PATH": str(site / "factory_app"),
            "MOZAIKS_GENERATED_ARTIFACTS_PATH": str(evidence / "generated"),
        },
        "sys_path": ["", str(base / "Lib"), str(venv), str(site)],
        "runtime_check_database": {"configured": True, "reachable": True, "error": None},
        "distribution": {"direct_url": json.dumps({"url": f"file:///C:/candidates/{WHEEL}"})},
    }


def evaluate(proof: dict[str, Any], tmp_path: Path) -> list[str]:
    return env_script.evaluate_proof(
        proof, venv_dir=tmp_path / "venv", evidence_dir=tmp_path / "evidence", workspace=None, candidate_wheel=WHEEL,
    )


def test_a_clean_proof_has_no_violations(tmp_path: Path) -> None:
    assert evaluate(clean_proof(tmp_path), tmp_path) == []


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (lambda p: p["packages"]["factory_app"].update(loaded_from=str(STALE_CHECKOUT / "factory_app" / "__init__.py")),
         "package factory_app resolves outside the venv"),
        (lambda p: p["packages"].pop("logs"), "package logs does not resolve"),
        (lambda p: p["resolved_paths"].update(factory_app=str(STALE_CHECKOUT / "factory_app")),
         "the factory_app root resolves outside the venv"),
        (lambda p: p["packs"].append({"id": "stale", "path": str(STALE_CHECKOUT / "factory_app" / "build_context" / "stale")}),
         "capability pack stale resolves outside the venv"),
        (lambda p: p.update(packs=[]), "no capability pack resolves"),
        (lambda p: p["environment_paths"].update(MOZAIKS_BUILD_CONTEXT_PATH=str(STALE_CHECKOUT / "build_context")),
         "MOZAIKS_BUILD_CONTEXT_PATH points outside"),
        (lambda p: p["sys_path"].append(str(STALE_CHECKOUT)), "sys.path holds a directory outside"),
        (lambda p: p["runtime_check_database"].update(configured=False), "export stays blocked"),
        (lambda p: p["runtime_check_database"].update(reachable=False, error="ServerSelectionTimeoutError"),
         "does not answer (ServerSelectionTimeoutError)"),
        (lambda p: p["distribution"].update(direct_url=json.dumps({"url": "file:///C:/other/mozaiks-0.1.11-py3-none-any.whl"})),
         "is not the candidate"),
        (lambda p: p.update(host_import={"ok": False, "error": "ImportError: no app"}), "the host module did not import"),
    ],
)
def test_each_departure_from_the_pinned_install_is_a_violation(tmp_path: Path, change: Any, expected: str) -> None:
    proof = clean_proof(tmp_path)
    change(proof)
    violations = evaluate(proof, tmp_path)
    assert any(expected in violation for violation in violations), violations


def test_the_proof_command_fails_when_packages_load_from_outside_the_venv(tmp_path: Path) -> None:
    # The real proof program in this interpreter, whose mozaiks is the source
    # checkout's editable install: every package is outside the stand-in venv.
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    venv = tmp_path / "venv"
    venv.mkdir()
    env_file = tmp_path / "host.env"
    env_file.write_text(f"MONGO_URI={os.environ.get('MONGO_URI') or 'mongodb://127.0.0.1:1'}\n", encoding="utf-8")
    (evidence / "host.json").write_text(json.dumps({
        "evidence_dir": str(evidence), "venv_dir": str(venv), "python": sys.executable,
        "host_app": "mozaiksai.resources:resolve_factory_app_root", "cwd": str(evidence), "workspace": None,
        "candidate": None, "env_file": str(env_file), "env_file_variables": ["MONGO_URI"], "replaced": [],
        "paths": env_script.install_paths(venv / "site-packages", evidence),
    }), encoding="utf-8")

    assert env_script.main(["proof", "--evidence-dir", str(evidence)]) == 1
    report = json.loads((evidence / "environment-proof.json").read_text(encoding="utf-8"))
    assert report["passed"] is False
    assert report["proof"]["host_import"] == {"ok": True}
    for name in ("mozaiksai", "factory_app", "mozaiks_cli"):
        assert any(v.startswith(f"package {name} resolves outside the venv") for v in report["violations"]), name
    assert any(v.startswith("the factory_app root resolves outside the venv") for v in report["violations"])
    # Names only: the env file's values never reach the evidence.
    assert "MONGO_URI" in report["environment_variables"]
    assert "mongodb://" not in (evidence / "environment-proof.json").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- the detached host


def test_a_detached_host_outlives_its_start_call_and_stops_by_pid(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    started = time.monotonic()
    pid = env_script.spawn_detached(
        [sys.executable, "-c", "import time; time.sleep(300)"],
        environment=dict(os.environ), cwd=tmp_path, log_path=evidence / "host.log",
    )
    assert time.monotonic() - started < 10
    try:
        time.sleep(0.5)
        alive, description = env_script.process_state(pid)
        assert alive and env_script.belongs_to(description, sys.executable), description
        (evidence / "host.json").write_text(json.dumps({"pid": pid, "python": sys.executable}), encoding="utf-8")

        assert env_script.main(["stop", "--evidence-dir", str(evidence)]) == 0
        assert env_script.process_state(pid)[0] is False
        assert json.loads((evidence / "host-stopped.json").read_text(encoding="utf-8"))["result"] == "stopped"
        # A second stop finds nothing to stop.
        assert env_script.main(["stop", "--evidence-dir", str(evidence)]) == 0
        assert json.loads((evidence / "host-stopped.json").read_text(encoding="utf-8"))["result"] == "already stopped"
    finally:
        if env_script.process_state(pid)[0]:
            env_script.terminate(pid)


def test_stop_refuses_a_pid_that_is_not_the_recorded_host(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    pid = env_script.spawn_detached(
        [sys.executable, "-c", "import time; time.sleep(300)"],
        environment=dict(os.environ), cwd=tmp_path, log_path=evidence / "host.log",
    )
    try:
        time.sleep(0.5)
        (evidence / "host.json").write_text(
            json.dumps({"pid": pid, "python": str(tmp_path / "another-venv" / "python")}), encoding="utf-8",
        )
        assert env_script.main(["stop", "--evidence-dir", str(evidence)]) == 1
        assert json.loads((evidence / "host-stopped.json").read_text(encoding="utf-8"))["result"] == "refused"
        assert env_script.process_state(pid)[0] is True
    finally:
        env_script.terminate(pid)


# --------------------------------------------------------------------------- preparation


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=driver@example.com", "-c", "user.name=driver",
         "-c", "core.autocrlf=false", *args],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "tracked.txt").write_text("committed", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-q", "-m", "one")
    return repo, _git(repo, "rev-parse", "HEAD")


def test_a_commit_is_exported_from_git_and_never_from_the_working_tree(tmp_path: Path) -> None:
    repo, commit = _repo(tmp_path)
    (repo / "tracked.txt").write_text("edited in the working tree", encoding="utf-8")
    (repo / "untracked.txt").write_text("never committed", encoding="utf-8")

    assert env_script.resolve_commit(repo, commit[:10]) == commit
    source = env_script.export_commit(repo, commit, tmp_path / "build" / "source")
    assert (source / "tracked.txt").read_text(encoding="utf-8") == "committed"
    assert not (source / "untracked.txt").exists()


def test_a_workspace_must_be_at_the_named_commit_without_tracked_changes(tmp_path: Path) -> None:
    repo, commit = _repo(tmp_path)
    record = env_script._workspace_record(repo, commit[:12], runner=subprocess.run)
    assert record is not None and record["head"] == commit and record["uncommitted_changes"] is False
    with pytest.raises(env_script.PreparationError, match="not 0000000"):
        env_script._workspace_record(repo, "0000000", runner=subprocess.run)
    (repo / "tracked.txt").write_text("edited", encoding="utf-8")
    with pytest.raises(env_script.PreparationError, match="uncommitted changes"):
        env_script._workspace_record(repo, commit, runner=subprocess.run)


def test_pip_is_bootstrapped_from_its_bundled_wheel_when_ensurepip_fails(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    bundled = "C:/Python/Lib/ensurepip/_bundled/pip-24.0-py3-none-any.whl"

    def runner(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[1:3] == ["-m", "venv"] and "--without-pip" not in command:
            return subprocess.CompletedProcess(command, 1, "", "ensurepip failed")
        stdout = bundled + "\n" if command[1] == "-c" else ""
        return subprocess.CompletedProcess(command, 0, stdout, "")

    venv = tmp_path / "venv"
    python = env_script.create_venv("base-python", venv, runner=runner)
    assert python == env_script.venv_python(venv)
    assert calls[1] == ["base-python", "-m", "venv", "--without-pip", str(venv)]
    assert calls[3] == [str(python), f"{bundled}/pip", "install", "--no-index", bundled]


def test_child_output_is_read_as_utf8_whatever_the_console_code_page() -> None:
    # The host logs emoji; decoding with the Windows code page killed the reader thread.
    program = "import sys; sys.stderr.buffer.write('\\U0001f9e9 ready'.encode('utf-8')); print('ok')"
    result = env_script._run([sys.executable, "-c", program])
    assert result.stdout.strip() == "ok"
    assert result.stderr == "\U0001f9e9 ready"


def test_start_refuses_a_host_without_a_runtime_check_database_before_installing_anything(tmp_path: Path) -> None:
    env_file = tmp_path / "host.env"
    env_file.write_text("AUTH_ENABLED=false\n", encoding="utf-8")
    evidence = tmp_path / "evidence"
    code = env_script.main([
        "start", "--evidence-dir", str(evidence), "--oss-wheel", str(tmp_path / WHEEL), "--env-file", str(env_file),
    ])
    assert code == 2
    assert not (evidence / "venv").exists()


def test_start_refuses_an_evidence_folder_that_was_already_used(tmp_path: Path) -> None:
    env_file = tmp_path / "host.env"
    env_file.write_text("MONGO_URI=mongodb://127.0.0.1:27309\n", encoding="utf-8")
    evidence = tmp_path / "evidence"
    (evidence / "venv").mkdir(parents=True)
    code = env_script.main([
        "start", "--evidence-dir", str(evidence), "--oss-wheel", str(tmp_path / WHEEL), "--env-file", str(env_file),
    ])
    assert code == 2


def test_start_takes_exactly_one_candidate(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        env_script.build_parser().parse_args(["start", "--evidence-dir", str(tmp_path), "--env-file", "x"])
    with pytest.raises(SystemExit):
        env_script.build_parser().parse_args([
            "start", "--evidence-dir", str(tmp_path), "--env-file", "x", "--oss-sha", "abc", "--oss-wheel", "w.whl",
        ])


def test_the_script_imports_nothing_from_the_packages_it_proves() -> None:
    forbidden = {"mozaiksai", "factory_app", "logs", "mozaiks", "mozaiks_cli", "mozaiks_chat_ui", "web_shell", "scripts"}
    imported: set[str] = set()
    for node in ast.walk(ast.parse(SCRIPT_PATH.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(str(node.module).split(".")[0])
    assert imported & forbidden == set()
