from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_run_studio_waits_for_backend_before_frontend() -> None:
    script = _read("scripts/run-studio.ps1")

    assert "BackendReadyTimeoutSeconds" in script
    assert "SkipBackendWait" in script
    assert "Wait-ForHttpOk" in script
    assert "http://localhost:$BackendPort/api/shell-config" in script
    assert "Stop-ListeningPorts -Ports @($BackendPort, $FrontendPort)" in script
    assert "clean-runtime-artifacts.ps1" in script
    assert "-IncludeMainLogs" in script

    clean_start = script.rindex("Clear-PreviousRunFiles")
    backend_start = script.index("Start-Process -FilePath $shellExe")
    readiness_wait = script.index('Wait-ForHttpOk -Name "backend shell config"')
    frontend_start = script.index('& "$ScriptDir/run-frontend.ps1" @frontendParams')

    assert clean_start < backend_start < readiness_wait < frontend_start


def test_run_infra_fails_early_when_docker_is_unavailable() -> None:
    script = _read("scripts/run-infra.ps1")

    assert "Assert-DockerAvailable" in script
    assert "Get-Command docker" in script
    assert "docker info" in script
    assert "Docker Desktop is not reachable" in script
    assert "docker compose version" in script
    assert "-SkipInfra" in script


def test_run_backend_surfaces_infra_and_python_setup_failures() -> None:
    script = _read("scripts/run-backend.ps1")

    assert '& "$PSScriptRoot/run-infra.ps1" -Profile $InfraProfile' in script
    assert "if ($LASTEXITCODE -ne 0)" in script
    assert '.venv/Scripts/python.exe' in script
    assert "Get-Command python" in script
    assert 'python -m pip install -e ".[dev]"' in script


def test_run_frontend_requires_node_dependencies_before_vite() -> None:
    script = _read("scripts/run-frontend.ps1")

    assert "Get-Command npm" in script
    assert "web_shell" in script
    assert "package.json" in script
    assert "node_modules" in script
    assert "npm --prefix web_shell ci" in script

    dependency_check = script.index("Get-Command npm")
    vite_start = script.index("npm --prefix web_shell run dev")
    assert dependency_check < vite_start


def test_local_studio_smoke_checks_create_transition_contract() -> None:
    script = _read("scripts/smoke-studio-local.ps1")

    assert "$BackendUrl/health" in script
    assert "$BackendUrl/api/shell-config" in script
    assert "$BackendUrl/api/transitions/app_type_selector" in script
    assert "$FrontendUrl/apps" in script
    assert "$FrontendUrl/create" in script
    assert "create-app header action must route to /create" in script
    assert "app_type_selector must be dismissible" in script
    assert "app_type_selector dismiss_to must be /apps" in script
    assert "create app transition overlay can return to Apps" in script


def test_local_setup_docs_match_current_repo_dev_scripts() -> None:
    docs = _read("docs/local-setup.md")
    setup_skill = _read(".agents/skills/setup/SKILL.md")

    assert "http://localhost:8000/health" in docs
    assert "http://localhost:8000/api/shell-config" in docs
    assert ".\\scripts\\smoke-studio-local.ps1" in docs
    assert "run-studio.sh" not in docs
    assert "run-backend.sh" not in docs
    assert "run-frontend.sh" not in docs

    assert "http://localhost:8000/health" in setup_skill
    assert "http://localhost:8000/api/health" not in setup_skill
