import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_chat_config_uses_active_host_identity_without_mutating_embedder_config() -> None:
    source = (ROOT / "chat-ui/src/context/ChatUIContext.jsx").read_text(encoding="utf-8")
    callback = re.search(r"const resolvedConfig = useMemo\(\(\) => \{(.*?)\n  \}, \[", source, re.S)
    assert callback is not None
    # Execute the production memo callback without importing JSX or Vite modules.
    script = "import assert from 'node:assert/strict';\n"
    script += "function resolve(uiConfig, navigation) {" + callback.group(1) + "}\n"
    script += """
const base = { appName: 'Embedder', chat: { defaultAppId: 'placeholder', sound: false }, extra: 42 };
const original = JSON.stringify(base);
const host = resolve(base, { appId: 'customer-app', appName: 'Customer App' });
assert.equal(host.chat.defaultAppId, 'customer-app');
assert.equal(host.appName, 'Customer App');
assert.equal(host.chat.sound, false);
assert.equal(host.extra, 42);
assert.equal(JSON.stringify(base), original);
assert.equal(resolve(base, { appId: 'second-app' }).chat.defaultAppId, 'second-app');
assert.equal(resolve(base, null), base);
assert.deepEqual(resolve(null, null), {});
assert.equal(resolve(null, { appId: 'customer-app' }).chat.defaultAppId, 'customer-app');
"""
    result = subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        cwd=ROOT, text=True, capture_output=True, check=False, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_shell_waits_for_identity_and_does_not_invent_a_demo_app() -> None:
    provider = (ROOT / "chat-ui/src/context/ChatUIContext.jsx").read_text(encoding="utf-8")
    shell = (ROOT / "chat-ui/src/app/MozaiksApp.jsx").read_text(encoding="utf-8")
    assert "const navigation = useContext(NavigationContext);" in provider
    assert "[uiConfig, navigation?.appId, navigation?.appName]" in provider
    assert "if (loading || navigation?.loading)" in provider
    assert "if (navigation && !resolvedConfig.chat?.defaultAppId)" in provider
    assert 'role="alert"' in provider
    assert "defaultAppId = null" in shell
    assert "demo-app" not in shell
