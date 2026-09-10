import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("path,expected_calls", [
    ("chat-ui/src/components/RouteRenderer.jsx", 2),
    ("chat-ui/src/pages/ChatPage.js", 2),
    ("chat-ui/src/hooks/useWorkflowStart.js", 1),
])
def test_all_launch_surfaces_use_configured_auth(path: str, expected_calls: int) -> None:
    source = (ROOT / path).read_text(encoding="utf-8")
    calls = re.findall(
        r"authFetch\('/api/(?:transitions/resolve|workflows/trigger)', \{(.*?)\}, \{ auth \}\)",
        source, re.S,
    )
    assert len(calls) == expected_calls
    assert not re.search(r"\bfetch\('/api/(?:transitions/resolve|workflows/trigger)'", source)
    assert "import { authFetch } from '../adapters/api';" in source


def test_launch_auth_helper_forwards_current_adapter_token_and_preserves_response() -> None:
    source = (ROOT / "chat-ui/src/adapters/api.js").read_text(encoding="utf-8")
    # Exercise the actual fetch/token helper definitions without loading browser
    # configuration modules, which require Vite's build-time environment.
    helpers = source[source.index("function getAccessToken("):source.index("export class ApiAdapter")]
    script = """
import assert from 'node:assert/strict';
const platform = { getAccessToken: () => { throw new Error('unexpected storage fallback'); } };
const requests = [];
const response = { ok: true, status: 200 };
globalThis.fetch = async (url, options) => { requests.push({ url, ...options }); return response; };
""" + helpers + """
let token = 'first-token';
const auth = { getAccessToken: () => token };
const options = { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' };
assert.equal(await authFetch('/api/transitions/resolve', options, { auth }), response);
assert.equal(requests[0].headers.Authorization, 'Bearer first-token');
token = 'refreshed-token';
await authFetch('/api/workflows/trigger', options, { auth });
assert.equal(requests[1].headers.Authorization, 'Bearer refreshed-token');
token = null;
await authFetch('/api/transitions/resolve', options, { auth });
assert.equal(requests[2].headers.Authorization, undefined);
assert.equal(options.headers.Authorization, undefined);
assert.ok(requests.every((r) => r.body === '{}' && r.method === 'POST'));
"""
    result = subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        cwd=ROOT, text=True, capture_output=True, check=False, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
