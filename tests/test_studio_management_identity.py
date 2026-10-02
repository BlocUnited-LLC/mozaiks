"""Management navigation and Factory execution use their explicit identities."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROUTES = Path(__file__).resolve().parents[1] / "factory_app/app/admin/pages/dashboardRoutes.js"


def test_dashboard_links_preserve_distinct_management_factory_target_and_host_ids():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required for Studio route behavior checks")
    script = f"""
        import assert from 'node:assert/strict';
        import {{ buildDashboardWorkflowHref as href, resolveStudioApp }} from {json.dumps(ROUTES.as_uri())};
        const registeredApp = {{ app_id: 'bakery-target', build_registry_id: 'factory-build', chat_app_id: 'execution-host' }};
        const input = {{ appId: 'commercial-record', registeredApp, panel: {{}}, build: {{}} }};
        const launch = new URL(href({{ ...input, action: {{ type: 'workflow_sequence', target: 'app_build', id: 'continue' }} }}), 'http://localhost');
        assert.equal(launch.searchParams.get('app_id'), 'execution-host');
        assert.equal(launch.searchParams.get('build_registry_id'), 'factory-build');
        assert.equal(launch.searchParams.get('sequence'), 'app_build');
        assert.equal(href({{ ...input, action: {{ type: 'route', target: '/apps/:appId/branding' }} }}), '/apps/commercial-record/branding');
        assert.equal(resolveStudioApp([registeredApp], 'bakery-target', 'factory-build'), registeredApp);
        assert.throws(() => resolveStudioApp([registeredApp], 'commercial-record', 'factory-build'), /not found/);
        assert.throws(() => resolveStudioApp([registeredApp], 'bakery-target', 'another-build'), /association/);
        // Standalone OSS callers keep route identity when no external association is supplied.
        assert.equal(resolveStudioApp([registeredApp], 'bakery-target'), registeredApp);
        const standalone = new URL(href({{ appId: 'standalone', panel: {{}}, build: {{}} }}), 'http://localhost');
        assert.equal(standalone.searchParams.get('app_id'), 'standalone');
        assert.equal(standalone.searchParams.has('build_registry_id'), false);
        assert.equal(href({{ panel: {{}}, build: {{}}, action: {{ type: 'route', target: '/apps' }} }}), '/apps');
    """
    completed = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr
