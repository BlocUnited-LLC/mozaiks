"""Management navigation and Factory execution use their explicit identities."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROUTES = Path(__file__).resolve().parents[1] / "factory_app/app/admin/pages/dashboardRoutes.js"


def test_dashboard_manifest_request_uses_surface_scope_without_app_identity():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required for Studio route behavior checks")
    script = f"""
        import assert from 'node:assert/strict';
        import {{ fetchDashboardConfig }} from {json.dumps(ROUTES.as_uri())};
        const requests = [];
        const manifest = {{ surface: {{ scope: 'app', portals: [] }} }};
        globalThis.fetch = async (url, options) => {{
            requests.push({{ url, options }});
            return {{ ok: true, json: async () => manifest }};
        }};
        const signal = new AbortController().signal;
        for (const scope of ['app', 'workspace', null]) {{
            const result = await fetchDashboardConfig({{ scope, signal, appId: 'commercial-record' }});
            assert.equal(result, manifest);
            const request = requests.at(-1);
            const url = new URL(request.url, 'http://localhost');
            assert.equal(url.pathname, '/api/studio/dashboard');
            assert.deepEqual([...url.searchParams], scope ? [['scope', scope]] : []);
            assert.equal(request.options.signal, signal);
            assert.equal(request.options.headers.Accept, 'application/json');
        }}
        assert.equal(requests.length, 3);
    """
    completed = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr


def test_studio_app_resolution_checks_the_factory_build_association():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required for Studio route behavior checks")
    script = f"""
        import assert from 'node:assert/strict';
        import {{ resolveStudioApp }} from {json.dumps(ROUTES.as_uri())};
        const registeredApp = {{ app_id: 'bakery-target', build_registry_id: 'factory-build', chat_app_id: 'execution-host' }};
        assert.equal(resolveStudioApp([registeredApp], 'bakery-target', 'factory-build'), registeredApp);
        assert.throws(() => resolveStudioApp([registeredApp], 'commercial-record', 'factory-build'), /not found/);
        assert.throws(() => resolveStudioApp([registeredApp], 'bakery-target', 'another-build'), /association/);
        // Standalone OSS callers keep route identity when no external association is supplied.
        assert.equal(resolveStudioApp([registeredApp], 'bakery-target'), registeredApp);
    """
    completed = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr
