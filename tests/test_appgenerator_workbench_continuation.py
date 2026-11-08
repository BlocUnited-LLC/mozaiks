"""A pending workbench cannot export or complete a superseded generation."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.test_appgenerator_generate_and_download_persistence import _Context
from tests.test_appgenerator_generate_and_download_persistence import (
    generate_and_download_module as module,
)


@pytest.mark.parametrize('action,selection', [(a, s) for a in ['download_complete', 'export_to_github'] for s in ['foreign_child', 'superseded', 'forged_disposition', 'unchanged']] + [('export_to_github', 'changed_during_export')])
def test_workbench_rechecks_selection_before_export_and_ready(monkeypatch, tmp_path, action, selection):
    monkeypatch.setenv('MOZAIKS_GENERATED_ARTIFACTS_PATH', str(tmp_path/'generated'))
    monkeypatch.setattr(module, '_inject_agent_context_env', AsyncMock())
    monkeypatch.setattr(module, 'get_latest_workflow_export', AsyncMock(return_value=None))
    registry = AsyncMock(return_value={'success': True})
    monkeypatch.setattr(module.AppRegistryService, 'update_build_status', registry)
    original = SimpleNamespace(id='original-parent')
    registered = {}
    async def register(**kwargs):
        if selection == 'unchanged':
            import hashlib

            from mozaiksai.core.artifacts.models import BuildRecord
            binding = kwargs['context_variables'].get('run_build_binding')
            registered['artifact'] = BuildRecord(
                id=original.id, app_id=binding['target_app_id'], build_family='app_bundle', build_key='app_bundle',
                version_number=1, lineage_root_id='workflow-root',
                commit_metadata={'author_user_id': 'owner', 'metadata': {**binding, 'bundle_name': kwargs['bundle_name']}},
                files_manifest=[{'path': f"{kwargs['bundle_name']}/{kwargs['bundle_name']}.zip", 'content_type': 'application/zip',
                                 'sha256': hashlib.sha256(kwargs['zip_path'].read_bytes()).hexdigest()}],
            )
            registered['registry'] = {'app_id': binding['target_app_id'], 'chat_app_id': 'factory-test',
                                      'current_build_run': {**binding, 'artifact_version_id': original.id}}
        kwargs['context_variables'].set('artifact_version_id', original.id)
        return registered.get('artifact', original)
    monkeypatch.setattr(module, '_register_app_bundle_artifact_version', register)
    async def accepted_snapshot(*, files, context_variables, **_kwargs):
        from factory_app.workflows.AppGenerator.tools.task_integrity import artifact_snapshot_digest
        context_variables.set('generated_files', files)
        context_variables.set('app_build_plan', {'build_tasks': []})
        accepted={'passed': True, 'status': 'passed', 'bundle_scan': {'errors': []},
                  'validation_evidence': {'completed': ['bundle_scan'], 'failed': []},
                  'snapshot_digest': artifact_snapshot_digest(context_variables, files)}
        for key,value in {'app_bundle_acceptance_result': accepted, 'app_bundle_acceptance_status':'passed',
                          'app_validation_status':'passed','integration_tests_passed':True}.items():
            context_variables.set(key,value)
        return accepted
    monkeypatch.setattr(module,'run_app_bundle_acceptance_gate',accepted_snapshot)
    resolver = AsyncMock(return_value=(None, {}, original))
    if selection == 'superseded':
        resolver.side_effect = ValueError('workbench_build_superseded')
    elif selection == 'changed_during_export':
        resolver.side_effect = [(None, {}, original), ValueError('workbench_build_superseded')]
    if selection == 'unchanged':
        from factory_app.workflows._shared.platform import current_build
        monkeypatch.setattr(current_build.AppRegistryService, 'get_app_record', AsyncMock(side_effect=lambda **_: {'app': registered['registry']}))
        monkeypatch.setattr(current_build, 'get_artifact_store', lambda: SimpleNamespace(get_build_record=AsyncMock(side_effect=lambda **_: registered['artifact'])))
    else:
        monkeypatch.setattr(module, 'resolve_current_build_artifact', resolver)
    ui = AsyncMock(return_value={'status': 'superseded' if selection == 'forged_disposition' else 'completed','action':action,
                                'artifact_version_id':'selected-child' if selection == 'foreign_child' else original.id,'download_accepted':True})
    monkeypatch.setattr(module,'use_ui_tool',ui)
    export = AsyncMock(return_value={'success':True})
    monkeypatch.setattr(module,'export_app_code_to_github',export)
    context=_Context({'chat_id':'chat','app_id':'app','build_id':'build','user_id':'owner',
                      'generated_files':{'app.json':'{"app_id":"app"}'}})
    result=asyncio.run(module.generate_and_download({},'Bundle ready.',context_variables=context))
    assert ui.await_args.kwargs['payload']['artifact_version_id']=='original-parent'
    assert context.get('artifact_version_id')=='original-parent'
    expected = 'ready' if selection == 'unchanged' else 'superseded' if selection in {'superseded', 'changed_during_export'} else 'blocked'
    assert result['outcome'] == expected
    if action == 'export_to_github' and selection in {'unchanged', 'changed_during_export'}:
        export.assert_awaited_once()
    else:
        export.assert_not_awaited()
    assert context.get('app_download_ready') is (expected == 'ready')
    if expected != 'ready':
        assert 'bundle_zip' not in result and 'bundle_dir' not in result
        assert context.get('generated_app_zip') is None
        assert context.get('download_result') is None
    if expected == 'superseded':
        assert 'superseded' in context.get('app_build_failure_message')
    if selection != 'unchanged':
        assert resolver.await_args.kwargs == {'app_id': 'factory-test', 'user_id': 'owner', 'error_prefix': 'workbench'}


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [None, "new_build", "foreign_owner", "stale", "manifest"])
async def test_post_wait_guard_uses_real_current_build_owner(monkeypatch, change):
    from factory_app.workflows._shared.platform import current_build
    from mozaiksai.core.artifacts.models import BuildRecord
    context = _Context({"app_id": "target", "build_id": "original", "artifact_version_id": "parent"})
    binding = context.get("run_build_binding")
    record = {"app_id": "target", "chat_app_id": "factory-test", "current_build_run": {**binding, "artifact_version_id": "parent"}}
    artifact = BuildRecord(
        id="parent", app_id="target", build_family="app_bundle", build_key="app_bundle",
        version_number=1, lineage_root_id="workflow-bundle",
        commit_metadata={"author_user_id": "owner", "metadata": {**binding, "bundle_name": "Original"}},
        files_manifest=[{"path": "Original/Original.zip", "content_type": "application/zip", "sha256": "a" * 64}],
    )
    if change == "new_build":
        record["current_build_run"].update(build_id="refinement", phase="refinement", artifact_version_id="child")
    elif change == "foreign_owner":
        artifact.commit_metadata.author_user_id = "other"
    elif change == "stale":
        artifact.lifecycle_status = "stale"
    elif change == "manifest":
        artifact.files_manifest = []
    monkeypatch.setattr(current_build.AppRegistryService, "get_app_record", AsyncMock(return_value={"app": record}))
    monkeypatch.setattr(current_build, "get_artifact_store", lambda: SimpleNamespace(get_build_record=AsyncMock(return_value=artifact)))
    result = await module._check_workbench_selection(
        context, app_id="factory-test", user_id="owner", artifact_version_id="parent",
        response={"status": "completed", "data": {"artifact_version_id": "parent"}},
    )
    if change is None:
        assert result is None
    else:
        assert result["outcome"] == ("superseded" if change == "new_build" else "blocked")
        assert context.get("app_download_ready") is False


@pytest.mark.asyncio
async def test_superseded_workbench_ends_actual_ag2_graph_without_success(monkeypatch):
    import json

    from ag2 import Agent
    from ag2.knowledge import MemoryKnowledgeStore

    from mozaiksai.core.adapters.ag2_network_runner import AG2NetworkRunner, AG2NetworkRunnerRequest
    from mozaiksai.core.ports.orchestration import RunStatus
    from mozaiksai.core.workflow.agents.factory import (
        ContextVariablesBridge,
        _workflow_tool_invocation,
    )
    from mozaiksai.core.workflow.orchestration_patterns import _run_complete_event
    from tests.test_run_termination import _appgenerator_contract

    rules, policy, orchestrator = _appgenerator_contract()
    initial = _Context({"app_id": "target", "build_id": "original"}).data
    bridge = ContextVariablesBridge(initial, authority_policy=policy)
    turns = []
    class TestAgent(Agent):
        async def ask(self, *args, **kwargs):
            turns.append(self.name)
            return SimpleNamespace(body=json.dumps({"status": "checking"}))
    names = {n for rule in rules for n in (rule["source_agent"], rule["target_agent"]) if n not in {"user", "terminate"}}
    agents = {name: TestAgent(name, prompt="Test workbench completion") for name in names}
    for agent in agents.values():
        agent._mozaiks_context_bridge = bridge
    monkeypatch.setattr(module, "resolve_current_build_artifact", AsyncMock(side_effect=ValueError("workbench_build_superseded")))
    async def output_hook(agent_name, envelope):
        assert agent_name == "DownloadAgent"
        with _workflow_tool_invocation(bridge):
            outcome = await module._check_workbench_selection(
                bridge, app_id="factory-test", user_id="owner", artifact_version_id="parent", response={"status": "completed"},
            )
            bridge.set("app_bundle_export_outcome", outcome["outcome"])
    result = await AG2NetworkRunner().run(AG2NetworkRunnerRequest(
        workflow_name="AppGenerator", chat_id="superseded-workbench", app_id="factory-test",
        agents=agents, initial_agent_name="DownloadAgent", initial_message="Finish review",
        transition_rules=rules, context_variables=initial, agent_output_handler=output_hook,
        context_authority_policy=policy, failure_message_key=orchestrator["failure_message_key"],
        knowledge_store=MemoryKnowledgeStore(), idle_timeout_seconds=30,
    ))
    assert turns == ["DownloadAgent"]
    assert result.status is RunStatus.FAILED
    assert result.close_reason == "workflow_failed"
    assert result.live_run is None
    assert "superseded" in result.failure_message
    event = _run_complete_event(workflow_name="AppGenerator", chat_id="superseded-workbench", runner_result=result, pause_agent=None)
    assert event["status"] == "failed" and event["error"] == result.failure_message
