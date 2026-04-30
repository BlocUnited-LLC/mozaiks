from __future__ import annotations

from pathlib import Path


def _workspace() -> Path:
    return Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (_workspace() / relative_path).read_text(encoding="utf-8")


def test_runtime_host_uses_lifespan_registration() -> None:
    source = _read("mozaiksai/hosts/runtime.py")

    assert '@app.on_event("startup")' not in source
    assert '@app.on_event("shutdown")' not in source
    assert 'async def runtime_lifespan' in source
    assert 'register_app_lifespan(app, runtime_lifespan)' in source


def test_platform_host_uses_lifespan_registration() -> None:
    source = _read("mozaiksai/hosts/platform.py")

    assert '@app.on_event("startup")' not in source
    assert '@app.on_event("shutdown")' not in source
    assert 'async def platform_lifespan' in source
    assert 'runtime_app.register_app_lifespan(app, platform_lifespan)' in source