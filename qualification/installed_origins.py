"""Task-local pytest hook: attest actual loaded production origins, not acceptance."""
import hashlib
import json
import os
import sys
import sysconfig
from pathlib import Path


def pytest_sessionfinish(session, exitstatus):
    installed = Path(sysconfig.get_paths()["purelib"]).resolve()
    roots = {"mozaiks", "mozaiksai", "mozaiks_cli", "factory_app"}
    origins = {}
    for name, module in list(sys.modules.items()):
        if name.split(".")[0] not in roots or not getattr(module, "__file__", None):
            continue
        path = Path(module.__file__).resolve()
        assert path.is_relative_to(installed), (name, str(path))
        origins[name] = {"path": str(path.relative_to(installed)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    assert len(origins) > 100
    Path(os.environ["F042B_ORIGIN_RECEIPT"]).write_text(json.dumps(origins, indent=2))
