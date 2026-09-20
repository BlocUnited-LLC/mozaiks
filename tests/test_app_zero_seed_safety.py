"""App Zero seed safety.

The seed script writes to a developer's real local database, so the property
that matters is that ``--purge`` can only ever remove rows the script itself
created. A bug here destroys real work, and the failure is silent.
"""

from __future__ import annotations

import ast
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "seed_app_zero.py"


def _source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_seed_script_exists_and_parses() -> None:
    ast.parse(_source())


def test_every_delete_is_tag_scoped() -> None:
    """No unfiltered delete may exist anywhere in the script."""
    source = _source()
    tree = ast.parse(source)

    deletes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"delete_many", "delete_one", "drop", "drop_database"}
    ]
    assert deletes, "expected the purge path to issue a delete"
    assert not [d for d in deletes if d.func.attr in {"drop", "drop_database"}], (
        "dropping a collection or database would destroy untagged developer data"
    )
    for call in deletes:
        assert call.args, f"{call.func.attr} called with no filter — would delete everything"
        rendered = ast.dump(call.args[0])
        assert "seed_tag" in rendered, (
            f"{call.func.attr} filter does not reference seed_tag; purge must match "
            "only rows this script wrote"
        )


def test_seed_tag_is_applied_to_written_rows() -> None:
    source = _source()
    assert 'SEED_TAG = "app-zero"' in source
    # Both write paths must stamp the tag, or purge cannot reclaim what they wrote.
    assert '"seed_tag": SEED_TAG' in source, "app records must carry the tag"
    assert "metadata={\"seed_tag\": SEED_TAG}" in source, "metric rows must carry the tag"


def test_databases_are_resolved_not_hardcoded() -> None:
    """Metric rows must land where the analytics reader looks.

    Hardcoding a database name here silently wrote rows the reader could never
    see, which is exactly the bug this guards against.
    """
    source = _source()
    assert "_default_database_name()" in source, (
        "the app database must be resolved the way the runtime resolves it"
    )
    assert "MongoPersistenceContext(app_id=app_id)" in source, (
        "passing an explicit database_name here decouples the seed from the reader"
    )


def test_kpi_event_names_match_the_metric_registry() -> None:
    """kpi.<metric_id> names come from the registry, not invention."""
    from mozaiksai.core.metrics.definitions import (
        KPI_SNAPSHOT_EVENT_PREFIX,
        build_default_metric_registry,
    )

    known = {metric.metric_id for metric in build_default_metric_registry().metrics}
    source = _source()
    for metric_id in ("mrr", "paying_users"):
        assert f'"{KPI_SNAPSHOT_EVENT_PREFIX}{metric_id}"' in source, (
            f"expected the seed to write {KPI_SNAPSHOT_EVENT_PREFIX}{metric_id}"
        )
        assert metric_id in known, f"{metric_id} is not a registered metric id"


def test_script_runs_from_a_plain_checkout() -> None:
    """`python scripts/seed_app_zero.py` must work without PYTHONPATH.

    Python puts the script's own directory on sys.path, not the repo root, so
    `import mozaiksai` fails from a checkout unless the script adds the root
    itself. Verifying with PYTHONPATH set hides this entirely.
    """
    source = _source()
    assert "sys.path.insert(0, str(_REPO_ROOT))" in source, (
        "the script must put the repo root on sys.path so a plain checkout works"
    )


def test_script_imports_without_pythonpath(tmp_path) -> None:
    """Actually execute it the way a developer would, with PYTHONPATH cleared."""
    import os
    import subprocess
    import sys

    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    # --help exercises import and argument wiring without touching a database.
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        cwd=str(SCRIPT.parents[1]),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, f"script failed to start:\n{result.stderr}"
    assert "--purge" in result.stdout
