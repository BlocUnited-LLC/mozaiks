from __future__ import annotations

import json

import pytest

from mozaiks_cli.commands import migrations as migrations_command
from mozaiks_cli.main import create_parser


def _report(*, blockers: bool = False, unknown: bool = False) -> dict:
    status = "failed" if blockers else ("paused" if unknown else "applied")
    return {
        "summary": {
            "total": 1,
            "applied": 0 if blockers or unknown else 1,
            "in_progress": 0,
            "failed": 1 if blockers else 0,
            "unknown": 1 if unknown else 0,
        },
        "items": [
            {
                "app_id": "app_1",
                "migration_id": "m_001",
                "status": status,
                "migration_hash": "hash",
                "applied_at": None if blockers or unknown else "2026-01-01T00:00:00Z",
                "failed_at": "2026-01-01T00:00:00Z" if blockers else None,
                "error_message": "index failed" if blockers else None,
                "failed_operation_index": 1 if blockers else None,
                "is_blocker": blockers,
                "unknown_status": unknown,
            }
        ],
        "has_blockers": blockers,
        "has_unknown_statuses": unknown,
    }


def test_migrations_status_command_prints_summary(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    async def fake_report(**_kwargs):
        return _report()

    monkeypatch.setattr(migrations_command, "get_migration_health_report", fake_report)
    args = create_parser().parse_args(["migrations", "status"])

    code = migrations_command.run(args)

    assert code == 0
    output = capsys.readouterr().out
    assert "Migration health:" in output
    assert "applied:" in output
    assert "app_1 | m_001 | applied" in output


def test_migrations_status_json_outputs_valid_json(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    async def fake_report(**_kwargs):
        return _report()

    monkeypatch.setattr(migrations_command, "get_migration_health_report", fake_report)
    args = create_parser().parse_args(["migrations", "status", "--json"])

    code = migrations_command.run(args)

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["total"] == 1
    assert payload["items"][0]["migration_id"] == "m_001"


def test_migrations_status_passes_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    async def fake_report(**kwargs):
        calls.append(kwargs)
        return _report()

    monkeypatch.setattr(migrations_command, "get_migration_health_report", fake_report)
    args = create_parser().parse_args(
        [
            "migrations",
            "status",
            "--app-id",
            "app_1",
            "--status",
            "failed",
            "--limit",
            "25",
            "--database-name",
            "custom_history",
        ]
    )

    code = migrations_command.run(args)

    assert code == 0
    assert calls == [
        {
            "app_id": "app_1",
            "status": "failed",
            "database_name": "custom_history",
            "limit": 25,
        }
    ]


def test_migrations_status_returns_one_for_blockers(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    async def fake_report(**_kwargs):
        return _report(blockers=True)

    monkeypatch.setattr(migrations_command, "get_migration_health_report", fake_report)
    args = create_parser().parse_args(["migrations", "status"])

    code = migrations_command.run(args)

    assert code == 1
    output = capsys.readouterr().out
    assert "blockers:    yes" in output
    assert "index failed" in output


def test_migrations_status_returns_one_for_unknown_status(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_report(**_kwargs):
        return _report(unknown=True)

    monkeypatch.setattr(migrations_command, "get_migration_health_report", fake_report)
    args = create_parser().parse_args(["migrations", "status"])

    assert migrations_command.run(args) == 1


def test_migrations_status_connection_error_returns_two_without_secret(
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    async def fail_report(**_kwargs):
        raise RuntimeError("mongodb://user:secret@example")

    monkeypatch.setattr(migrations_command, "get_migration_health_report", fail_report)
    args = create_parser().parse_args(["migrations", "status"])

    code = migrations_command.run(args)

    captured = capsys.readouterr()
    assert code == 2
    assert "RuntimeError" in captured.err
    assert "secret" not in captured.err
    assert "mongodb://" not in captured.err


def test_migrations_status_does_not_call_mutation_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_report(**_kwargs):
        return _report()

    def fail_mutation(*_args, **_kwargs):
        raise AssertionError("migration status must not apply migrations")

    monkeypatch.setattr(migrations_command, "get_migration_health_report", fake_report)
    monkeypatch.setattr(migrations_command, "apply_data_migrations", fail_mutation, raising=False)
    args = create_parser().parse_args(["migrations", "status"])

    assert migrations_command.run(args) == 0
