"""Hosts must start and stop through a lifespan, not deprecated event hooks.

Asserted against the composed app objects rather than the text of the host
modules. A source scan only catches the spellings it was told to look for, and
it pins code to a file: moving a host breaks the test, while a handler
registered by any other syntax slips through.
"""

from __future__ import annotations

import pytest

from mozaiksai.hosts import platform as platform_host
from mozaiksai.hosts import runtime as runtime_host

HOSTS = (("runtime", runtime_host), ("platform", platform_host))
HOST_IDS = [name for name, _ in HOSTS]


@pytest.mark.parametrize("name,module", HOSTS, ids=HOST_IDS)
def test_host_registers_no_startup_or_shutdown_event_handlers(name, module) -> None:
    router = module.app.router

    assert router.on_startup == [], (
        f"{name} host registered startup event handlers: "
        f"{[getattr(handler, '__name__', handler) for handler in router.on_startup]}"
    )
    assert router.on_shutdown == [], (
        f"{name} host registered shutdown event handlers: "
        f"{[getattr(handler, '__name__', handler) for handler in router.on_shutdown]}"
    )


@pytest.mark.parametrize("name,module", HOSTS, ids=HOST_IDS)
def test_host_has_a_lifespan_context(name, module) -> None:
    assert module.app.router.lifespan_context is not None, (
        f"{name} host has no lifespan context, so its startup and shutdown "
        "work would never run"
    )
