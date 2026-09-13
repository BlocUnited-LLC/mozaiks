import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("mode", ["app", "selected", "theme", "missing_theme", "invalid_theme"])
def test_workbench_refinement_scope_is_explicit_and_uses_canonical_theme(mode):
    source = (ROOT / "factory_app/workflows/AppGenerator/ui/AppWorkbench.js").read_text(encoding="utf-8")
    callback = source.split("const buildRefinementTriggerPayload = ", 1)[1].split(
        "\n\n  // Shared handler", 1,
    )[0].strip().removesuffix(";")
    constant = source.split("const THEME_FILE_PATH = ", 1)[1].split(";", 1)[0]
    assert "useState(false)" in source
    assert "Limit to selected file" in source
    script = r"""
const assert = require('node:assert/strict');
const { callback, constant, mode } = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const THEME_FILE_PATH = eval(constant);
assert.equal(THEME_FILE_PATH, 'brand/theme_config.json');
const artifactKind = 'app_bundle';
const artifactKey = 'app_bundle';
const artifactVersionId = 'artifact-1';
const refinementRequest = ' Repair edit form and theme. ';
const selectedPath = '.env.example';
const limitToSelectedFile = mode === 'selected';
const scopeFiles = { [selectedPath]: 'unchanged-example' };
const theme = { identity: { name: 'support-desk' }, theme: { primary: 'emerald' } };
const filesMap = mode === 'missing_theme' ? {} : {
  [THEME_FILE_PATH]: mode === 'invalid_theme' ? 'invalid JSON' : JSON.stringify(theme),
};
const validationStrategy = 'skip';
const build = eval('(' + callback + ')');
if (mode === 'invalid_theme') {
  assert.throws(() => build(null, 'theme_config'), SyntaxError);
} else {
  const result = build(null, mode.includes('theme') ? 'theme_config' : null);
  assert.equal(result.refinement_request.artifact_version_id, artifactVersionId);
  assert.equal(result.refinement_request.raw_user_request, refinementRequest.trim());
  if (mode === 'selected') assert.deepEqual(result.coding_request.files, scopeFiles);
  else if (mode === 'theme') {
    assert.deepEqual(result.coding_request.files, filesMap);
    assert.deepEqual(result.refinement_request.extra.parent_theme_config, theme);
  } else assert.equal(result.coding_request, undefined);
}
"""
    result = subprocess.run(
        ["node", "--eval", script], cwd=ROOT, capture_output=True, text=True,
        input=json.dumps({"callback": callback, "constant": constant, "mode": mode}), timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
