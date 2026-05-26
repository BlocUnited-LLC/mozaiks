from __future__ import annotations

from pathlib import Path

import pytest

from mozaiksai.control_plane import dry_run
from mozaiksai.control_plane.scoped_execution import ScopedRefinementChange, ScopedRefinementResult
from mozaiksai.control_plane.staged_coding_worker import (
    StagedCodingWorkerChange,
    StagedCodingWorkerResult,
    build_scoped_changes_from_worker_result,
    run_deterministic_staged_coding_worker,
    select_staged_coding_worker_reason,
)
from mozaiksai.control_plane.staging import create_refinement_staging_workspace

REPO_ROOT = Path(__file__).resolve().parents[1]


def _source_bundle(tmp_path: Path) -> Path:
    source = tmp_path / 'source_app_bundle'
    (source / 'ui/pages').mkdir(parents=True)
    (source / 'modules/projects/backend').mkdir(parents=True)
    (source / 'config/secrets').mkdir(parents=True)
    (source / 'ui/pages/dashboard.yaml').write_text('title: Dashboard\n', encoding='utf-8')
    (source / 'modules/projects/backend/service.py').write_text("VALUE = 'original'\n", encoding='utf-8')
    (source / 'config/secrets/provider_credentials.json').write_text('{"api_key": "secret"}\n', encoding='utf-8')
    return source


def _build_plan(tmp_path: Path, *, request_id: str = 'req_worker_001', affected_paths: list[str] | None = None):
    return dry_run.build_refinement_execution_plan_from_route(
        request='Update the dashboard title.',
        artifact_kind='app_bundle',
        change_class='patch',
        workflow_id='AppGenerator',
        workflow_sequence='app_revision',
        affected_workflows=['AppGenerator'],
        affected_declarative_families=['app_bundle'],
        affected_bundle_paths=affected_paths or ['ui/pages/dashboard.yaml'],
        scope_summary='Scoping a narrow staged coding worker change.',
        app_id='sample_app',
        request_id=request_id,
        execution_mode='staged',
        staging_base_path=tmp_path / '.refinement_staging',
    )


def _stage(tmp_path: Path, *, request_id: str = 'req_worker_001', affected_paths: list[str] | None = None):
    source = _source_bundle(tmp_path)
    plan = _build_plan(tmp_path, request_id=request_id, affected_paths=affected_paths)
    staging_result = create_refinement_staging_workspace(
        plan,
        source_bundle_path=source,
        staging_root=tmp_path / '.refinement_staging',
    )
    return source, plan, staging_result


def _worker_result(
    *,
    request_id: str = 'req_worker_001',
    source: str = 'deterministic',
    changes: list[StagedCodingWorkerChange] | None = None,
):
    return StagedCodingWorkerResult(
        request_id=request_id,
        source=source,
        changes=changes or [
            StagedCodingWorkerChange(
                path='ui/pages/dashboard.yaml',
                new_content='title: Updated Dashboard\n',
                reason='worker update',
            )
        ],
    )


def _statuses(result: ScopedRefinementResult) -> dict[str, str]:
    return {file.path: file.status for file in result.changed_files}


def test_deterministic_worker_converts_path_content_to_scoped_change() -> None:
    worker_result = _worker_result()

    scoped_changes = build_scoped_changes_from_worker_result(worker_result)

    assert scoped_changes == [
        ScopedRefinementChange(
            path='ui/pages/dashboard.yaml',
            new_content='title: Updated Dashboard\n',
            reason='worker update',
        )
    ]


def test_staged_worker_reason_uses_plan_rationale_before_summary() -> None:
    plan = {"summary": "Summary reason", "rationale": "Rationale reason"}

    reason = select_staged_coding_worker_reason(plan=plan, fallback_reason="Fallback reason")

    assert reason == "Rationale reason"


def test_staged_worker_reason_falls_back_to_summary_when_rationale_missing() -> None:
    plan = {"summary": "Summary reason"}

    reason = select_staged_coding_worker_reason(plan=plan, fallback_reason="Fallback reason")

    assert reason == "Summary reason"


def test_staged_worker_reason_falls_back_to_generic_reason_when_plan_text_missing() -> None:
    reason = select_staged_coding_worker_reason(plan={}, fallback_reason="Fallback reason")

    assert reason == "Fallback reason"


def test_staged_worker_reason_rejects_secret_looking_text() -> None:
    plan = {
        "summary": "Summary reason",
        "rationale": "Contains api_key and must not be surfaced.",
    }

    reason = select_staged_coding_worker_reason(plan=plan, fallback_reason="Fallback reason")

    assert reason == "Summary reason"
    assert "api_key" not in reason.lower()


def test_deterministic_worker_updates_staged_file_via_scoped_execution(tmp_path: Path) -> None:
    source, plan, staging_result = _stage(tmp_path)

    result = run_deterministic_staged_coding_worker(plan, staging_result, _worker_result())

    assert (Path(staging_result.staging_area) / 'workspace/ui/pages/dashboard.yaml').read_text(encoding='utf-8') == 'title: Updated Dashboard\n'
    assert _statuses(result)['ui/pages/dashboard.yaml'] == 'updated'
    assert (source / 'ui/pages/dashboard.yaml').read_text(encoding='utf-8') == 'title: Dashboard\n'


def test_source_file_remains_unchanged(tmp_path: Path) -> None:
    source, plan, staging_result = _stage(tmp_path)
    before = (source / 'ui/pages/dashboard.yaml').read_text(encoding='utf-8')

    run_deterministic_staged_coding_worker(plan, staging_result, _worker_result())

    assert (source / 'ui/pages/dashboard.yaml').read_text(encoding='utf-8') == before


def test_path_outside_affected_bundle_paths_is_skipped(tmp_path: Path) -> None:
    _, plan, staging_result = _stage(tmp_path)

    result = run_deterministic_staged_coding_worker(
        plan,
        staging_result,
        _worker_result(
            changes=[
                StagedCodingWorkerChange(
                    path='modules/projects/backend/service.py',
                    new_content="VALUE = 'updated'\n",
                    reason='outside scope',
                )
            ],
        ),
    )

    assert _statuses(result)['modules/projects/backend/service.py'] == 'skipped_unsafe'


def test_unsafe_path_is_skipped(tmp_path: Path) -> None:
    _, plan, staging_result = _stage(tmp_path)

    result = run_deterministic_staged_coding_worker(
        plan,
        staging_result,
        _worker_result(
            changes=[
                StagedCodingWorkerChange(
                    path='../outside.txt',
                    new_content='bad\n',
                    reason='unsafe',
                )
            ],
        ),
    )

    assert result.changed_files[0].status == 'skipped_unsafe'


def test_secret_path_is_skipped(tmp_path: Path) -> None:
    _, plan, staging_result = _stage(tmp_path, affected_paths=['config/secrets/provider_credentials.json'])

    result = run_deterministic_staged_coding_worker(
        plan,
        staging_result,
        _worker_result(
            changes=[
                StagedCodingWorkerChange(
                    path='config/secrets/provider_credentials.json',
                    new_content='{}\n',
                    reason='secret',
                )
            ],
        ),
    )

    assert _statuses(result)['config/secrets/provider_credentials.json'] == 'skipped_secret'


def test_request_id_mismatch_fails(tmp_path: Path) -> None:
    _, plan, staging_result = _stage(tmp_path)

    with pytest.raises(ValueError, match='request_id must match'):
        run_deterministic_staged_coding_worker(
            plan,
            staging_result,
            _worker_result(request_id='req_other'),
        )


def test_worker_result_cannot_bypass_scoped_execution(monkeypatch, tmp_path: Path) -> None:
    _, plan, staging_result = _stage(tmp_path)
    captured: dict[str, object] = {}

    def _fake_apply(*, plan, staging_result, changes, allow_new_files=False):  # noqa: ANN001
        captured['plan'] = plan
        captured['staging_result'] = staging_result
        captured['changes'] = list(changes)
        captured['allow_new_files'] = allow_new_files
        return ScopedRefinementResult(
            request_id=plan.request_id,
            staging_area=Path(staging_result.staging_area).as_posix(),
            changed_files=[],
            result_path=(Path(staging_result.staging_area) / 'execution_result.json').as_posix(),
        )

    monkeypatch.setattr(
        'mozaiksai.control_plane.staged_coding_worker.apply_scoped_refinement_changes',
        _fake_apply,
    )

    result = run_deterministic_staged_coding_worker(plan, staging_result, _worker_result())

    assert captured['plan'] is plan
    assert captured['staging_result'] is staging_result
    assert isinstance(captured['changes'], list)
    assert isinstance(captured['changes'][0], ScopedRefinementChange)
    assert captured['allow_new_files'] is False
    assert isinstance(result, ScopedRefinementResult)


def test_no_llm_call_by_default() -> None:
    source = (REPO_ROOT / 'mozaiksai' / 'control_plane' / 'staged_coding_worker.py').read_text(encoding='utf-8')

    assert 'LLMChangeClassifier' not in source
    assert 'OpenAI' not in source
    assert 'execute_workflow' not in source


def test_no_appgenerator_import_or_execution() -> None:
    source = (REPO_ROOT / 'mozaiksai' / 'control_plane' / 'staged_coding_worker.py').read_text(encoding='utf-8')

    assert 'AppGenerator' not in source
    assert 'factory_app.workflows' not in source


def test_no_proprietary_examples() -> None:
    combined = '\n'.join(
        [
            (REPO_ROOT / 'mozaiksai' / 'control_plane' / 'staged_coding_worker.py').read_text(encoding='utf-8'),
            (REPO_ROOT / 'tests' / 'test_refinement_staged_coding_worker.py').read_text(encoding='utf-8'),
        ]
    ).lower()
    forbidden_terms = ['app' + ' zero', 'app_' + 'zero', 'mozaiks-' + 'app', 'bloc' + 'united']

    assert not any(term in combined for term in forbidden_terms)
