from __future__ import annotations

from pathlib import Path

import pytest

from mozaiksai.core.runtime.app.metrics_loader import (
    MetricsConfig,
    MetricsConfigLoadError,
    load_metrics_config,
)
from mozaiksai.core.runtime.app.paths import (
    CANONICAL_APP_CONFIG_FILES,
    noncanonical_app_config_paths,
)

VALID_CONFIG = """
schema_version: mozaiks.metrics.v1
label: Test analytics
default_funnel_id: activation
funnels:
  - funnel_id: activation
    label: Activation
    subject: actor
    steps:
      - step_id: signed_up
        label: Signed up
        event_name: user.signed_up
      - step_id: created_project
        label: Created a project
        event_name: project.created
      - step_id: paid
        label: Paid
        event_name: subscription.activated
"""


def _write(tmp_path: Path, text: str) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "metrics.yaml").write_text(text, encoding="utf-8")
    return tmp_path


def test_absent_file_returns_none(tmp_path: Path) -> None:
    assert load_metrics_config(tmp_path) is None


def test_valid_config_parses_funnels(tmp_path: Path) -> None:
    config = load_metrics_config(_write(tmp_path, VALID_CONFIG))
    assert isinstance(config, MetricsConfig)
    assert [funnel.funnel_id for funnel in config.funnels] == ["activation"]
    funnel = config.default_funnel
    assert funnel is not None
    assert [step.step_id for step in funnel.steps] == ["signed_up", "created_project", "paid"]
    assert funnel.subject_field == "actor_id"


def test_invalid_schema_version_fails_closed(tmp_path: Path) -> None:
    bad = VALID_CONFIG.replace("mozaiks.metrics.v1", "mozaiks.metrics.v9")
    with pytest.raises(MetricsConfigLoadError, match="schema_version"):
        load_metrics_config(_write(tmp_path, bad))


def test_duplicate_funnel_ids_rejected(tmp_path: Path) -> None:
    duplicated = VALID_CONFIG + """
  - funnel_id: activation
    label: Duplicate
    steps:
      - step_id: one
        label: One
        event_name: one.happened
      - step_id: two
        label: Two
        event_name: two.happened
"""
    with pytest.raises(MetricsConfigLoadError, match="duplicate funnel_id"):
        load_metrics_config(_write(tmp_path, duplicated))


def test_funnel_requires_at_least_two_steps(tmp_path: Path) -> None:
    short = """
schema_version: mozaiks.metrics.v1
funnels:
  - funnel_id: tiny
    label: Tiny
    steps:
      - step_id: only
        label: Only
        event_name: only.step
"""
    with pytest.raises(MetricsConfigLoadError):
        load_metrics_config(_write(tmp_path, short))


def test_unknown_default_funnel_rejected(tmp_path: Path) -> None:
    bad_default = VALID_CONFIG.replace(
        "default_funnel_id: activation", "default_funnel_id: missing"
    )
    with pytest.raises(MetricsConfigLoadError, match="default_funnel_id"):
        load_metrics_config(_write(tmp_path, bad_default))


def test_unknown_keys_rejected(tmp_path: Path) -> None:
    with_extra = VALID_CONFIG + "\nrandom_extra_key: true\n"
    with pytest.raises(MetricsConfigLoadError):
        load_metrics_config(_write(tmp_path, with_extra))


def test_non_object_yaml_rejected(tmp_path: Path) -> None:
    with pytest.raises(MetricsConfigLoadError, match="YAML object"):
        load_metrics_config(_write(tmp_path, "- just\n- a list\n"))


def test_metrics_config_is_a_canonical_bundle_path() -> None:
    assert "config/metrics.yaml" in CANONICAL_APP_CONFIG_FILES
    assert noncanonical_app_config_paths(["config/metrics.yaml"]) == []
