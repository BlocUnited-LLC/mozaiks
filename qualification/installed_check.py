"""Task-only archive/origin receipt; production resolution stays in mozaiksai.resources."""
import hashlib
import json
import sys
import sysconfig
import tarfile
import zipfile
from pathlib import Path

from mozaiksai.resources import resolve_factory_app_root, resolve_web_shell_root

source, output = map(Path, sys.argv[1:])
installed = Path(sysconfig.get_paths()["purelib"])
shell, factory = resolve_web_shell_root(), resolve_factory_app_root()
assert shell.is_relative_to(installed) and factory.is_relative_to(installed)
wheel, = (source / "dist").glob("*.whl")
sdist, = (source / "dist").glob("*.tar.gz")
revision = json.loads((installed / "mozaiksai/_build_revision.json").read_text())
assert revision == json.loads((source / "mozaiksai/_build_revision.json").read_text())
paths = [
    "web_shell/playwright/generated-ui/generated-app.real-host.acceptance.spec.js",
    "web_shell/playwright/generated-ui/paired-app.real-host.acceptance.spec.js",
    "web_shell/playwright/fixtures/generatedUiRealHost.js",
    "web_shell/playwright.generated-ui.config.js",
    "factory_app/workflows/AppGenerator/smoke_identity_realm.json",
]
hashes = {}
with zipfile.ZipFile(wheel) as zipped, tarfile.open(sdist) as archived:
    prefix = archived.getnames()[0].split("/")[0] + "/"
    for path in paths:
        expected = (source / path).read_bytes()
        assert zipped.read(path) == archived.extractfile(prefix + path).read() == (installed / path).read_bytes() == expected
        hashes[path] = hashlib.sha256(expected).hexdigest()
    for name in zipped.namelist():
        if name.endswith("/") or ".dist-info/" in name:
            continue
        assert (installed / name).read_bytes() == zipped.read(name), name
assert not (Path.cwd() / "factory_app").exists()
assert not (Path.cwd() / "mozaiksai").exists()
result = {"revision": revision, "installed_root": str(installed), "resources": hashes,
          "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
          "sdist_sha256": hashlib.sha256(sdist.read_bytes()).hexdigest(),
          "all_wheel_payload_bytes_match": True}
(output / "installed-package.json").write_text(json.dumps(result, indent=2))
print("Exact installed wheel/sdist resources and normal package resolution verified")
