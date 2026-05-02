from __future__ import annotations

from mozaiksai.core import core_config


def test_get_mongo_client_reuses_cached_client_until_closed(monkeypatch) -> None:
    created = []

    class _FakeClient:
        def __init__(self, conn_str: str) -> None:
            self.conn_str = conn_str
            self.closed = False
            created.append(self)

        def close(self) -> None:
            self.closed = True

    monkeypatch.setenv("MONGO_URI", "mongodb://example")
    monkeypatch.setattr(core_config, "AsyncIOMotorClient", _FakeClient)

    core_config.close_mongo_client()
    first = core_config.get_mongo_client()
    second = core_config.get_mongo_client()
    core_config.close_mongo_client()
    third = core_config.get_mongo_client()
    core_config.close_mongo_client()

    assert first is second
    assert first is not third
    assert len(created) == 2
    assert created[0].closed is True
    assert created[1].closed is True
