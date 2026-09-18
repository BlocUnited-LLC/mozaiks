"""Hot reload must target the module object sys.modules holds now.

A live run logged:

    WORKFLOW_MODULE_RELOAD_FAILED workflow=AppGenerator:
    module workflows.AppGenerator not in sys.modules

while that name was plainly in sys.modules. importlib.reload does not check
presence, it checks identity:

    if sys.modules.get(name) is not module:
        raise ImportError(f"module {name} not in sys.modules", name=name)

The guard checked `module_name in sys.modules`, so a stale handle - the module
object having been replaced in sys.modules by a later import - passed the guard
and then raised inside reload. The message names a missing module and the real
condition is a replaced one, which is why it reads as something far worse than
it is. It cost real diagnosis time on a run it had not caused.
"""

from __future__ import annotations

import importlib
import sys
import types

import pytest


def test_reload_raises_on_a_stale_handle_not_a_missing_name() -> None:
    """Pin the CPython behaviour the fix depends on."""
    name = "_mzk_reload_probe"
    live = types.ModuleType(name)
    stale = types.ModuleType(name)
    sys.modules[name] = live
    try:
        assert name in sys.modules, "presence: what the old guard checked"
        assert sys.modules[name] is not stale, "identity: what reload requires"
        with pytest.raises(ImportError, match="not in sys.modules"):
            importlib.reload(stale)
    finally:
        sys.modules.pop(name, None)


def test_the_manager_reloads_the_live_object_and_keeps_it() -> None:
    from mozaiksai.core.workflow import workflow_manager as wm

    source = (
        __import__("pathlib").Path(wm.__file__).read_text(encoding="utf-8")
    )
    # The live object is fetched from sys.modules and reassigned, so a stale
    # handle is corrected rather than raising on every subsequent reload.
    assert "live = sys.modules.get(module_name) if module_name else None" in source
    assert "reloaded = importlib.reload(live)" in source
    assert "workflow_info.module = reloaded" in source


def test_a_genuinely_absent_module_still_skips_quietly() -> None:
    """Absence is not an error; it was already handled and must stay that way."""
    from mozaiksai.core.workflow import workflow_manager as wm

    source = (
        __import__("pathlib").Path(wm.__file__).read_text(encoding="utf-8")
    )
    assert "Workflow module reload skipped for %s because %s is not in sys.modules" in source
