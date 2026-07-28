from __future__ import annotations

from pathlib import Path

from mozaiksai.core.app_context.health import evaluate_context_graph_health
from mozaiksai.core.app_context.scan_policy import (
    collect_source_scan_file_map,
    default_context_graph_scan_policy,
    select_source_file_map,
)


def test_context_graph_scan_prioritizes_product_code_before_tests_and_scripts(tmp_path: Path) -> None:
    repo = tmp_path / "app"
    (repo / "tests").mkdir(parents=True)
    (repo / "scripts").mkdir()
    (repo / "app" / "modules" / "wallet" / "backend").mkdir(parents=True)
    (repo / "app" / "services" / "payments").mkdir(parents=True)

    for index in range(80):
        (repo / "tests" / f"test_{index}.py").write_text("def test_wallet_checkout():\n    assert True\n", encoding="utf-8")
    (repo / "scripts" / "wallet_probe.py").write_text("def checkout_probe():\n    pass\n", encoding="utf-8")
    (repo / "app" / "modules" / "wallet" / "backend" / "handler.py").write_text(
        "def checkout(payload):\n    return payload\n",
        encoding="utf-8",
    )
    (repo / "app" / "services" / "payments" / "entitlements.py").write_text(
        "def grant_entitlement(user_id):\n    return user_id\n",
        encoding="utf-8",
    )

    result = collect_source_scan_file_map(
        [("", repo)],
        policy=default_context_graph_scan_policy({"max_files": 50}),
    )

    paths = list(result.file_map)
    assert paths[:2] == [
        "app/modules/wallet/backend/handler.py",
        "app/services/payments/entitlements.py",
    ]
    assert result.health["limit_reached"] is True
    assert result.health["selected_by_priority"]["app_modules"] == 1


def test_context_graph_scan_skips_secret_sensitive_paths(tmp_path: Path) -> None:
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / ".env").write_text("OPENAI_API_KEY=raw", encoding="utf-8")
    (repo / "app.py").write_text("def main():\n    return True\n", encoding="utf-8")

    result = collect_source_scan_file_map([("", repo)])

    assert "app.py" in result.file_map
    assert ".env" not in result.file_map
    assert result.health["skipped"]["sensitive_path"] == 1
    assert "context_graph_sensitive_files_skipped:1" in result.warnings


def test_select_source_file_map_applies_same_policy_to_artifact_maps() -> None:
    result = select_source_file_map(
        {
            "tests/test_wallet.py": "def test_checkout():\n    assert True\n",
            "app/modules/wallet/backend/handler.py": "def checkout(payload):\n    return payload\n",
            "app/config/secrets.yaml": "names:\n  - API_KEY\n",
            "README.txt": "not indexed",
        },
        policy=default_context_graph_scan_policy({"max_files": 2}),
        source="artifact_zip",
    )

    assert list(result.file_map) == ["app/modules/wallet/backend/handler.py", "tests/test_wallet.py"]
    assert result.health["source"] == "artifact_zip"
    assert result.health["skipped"]["sensitive_path"] == 1
    assert result.health["skipped"]["unsupported_extension"] == 1


def test_context_graph_health_reports_coverage_warnings() -> None:
    report = evaluate_context_graph_health(
        {
            "selected_file_count": 5,
            "candidate_file_count": 10,
            "limit_reached": True,
            "selected_by_priority": {"docs": 5},
            "selected_by_extension": {".md": 5},
            "skipped": {"sensitive_path": 2},
        }
    )

    assert report.status == "blocked"
    assert "context_graph_limit_reached_before_core_code" in report.blockers
    assert "context_graph_sensitive_paths_skipped" in report.warnings
    assert report.coverage["core_surface_file_count"] == 0

