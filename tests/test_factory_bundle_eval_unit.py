"""Tests for generation-time bundle evaluation.

The suite exists to make Factory changes measurable before anything is hosted,
so the tests are built on synthetic bundles rather than the real corpus — they
must not start failing because someone regenerates `generated/`.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from factory_app.eval.bundle_eval import (
    diff_runs,
    discover_bundles,
    load_run,
    run_corpus,
    save_run,
)
from factory_app.eval.bundle_scorers import Bundle, score_bundle


def _write_bundle(
    root: Path,
    *,
    name: str = "demo",
    actions: list[dict] | None = None,
    plan_capabilities: list[str] | None = None,
    ui_endpoints: list[str] | None = None,
    with_dockerfile: bool = True,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "app.json").write_text(json.dumps({"name": name, "version": "1.0.0"}), encoding="utf-8")
    if with_dockerfile:
        (root / "Dockerfile").write_text("FROM python:3.13\n", encoding="utf-8")

    (root / "config").mkdir(exist_ok=True)
    (root / "config" / "subscriptions.yaml").write_text(
        yaml.safe_dump({
            "schema_version": "mozaiks.subscriptions.v1",
            "default_plan_id": "free",
            "plans": [
                {"plan_id": "free", "label": "Free", "capabilities": []},
                {"plan_id": "growth", "label": "Growth",
                 "capabilities": plan_capabilities if plan_capabilities is not None else []},
            ],
        }),
        encoding="utf-8",
    )

    mod = root / "modules" / "billing_portal"
    mod.mkdir(parents=True, exist_ok=True)
    (mod / "module.yaml").write_text(
        yaml.safe_dump({
            "schema_version": "mozaiks.module.v1",
            "module": {"id": "billing_portal", "handler": "backend.handler:H"},
            "actions": actions if actions is not None else [{"id": "get_status", "handler_method": "get_status"}],
        }),
        encoding="utf-8",
    )

    pages = root / "ui" / "pages"
    pages.mkdir(parents=True, exist_ok=True)
    endpoints = ui_endpoints if ui_endpoints is not None else [
        "/api/modules/billing_portal/get_status"
    ]
    (pages / "billing.yaml").write_text(
        yaml.safe_dump({"sections": [{"config": {"api_endpoint": e}} for e in endpoints]}),
        encoding="utf-8",
    )
    return root


def _by_key(root: Path) -> dict[str, object]:
    return {fb.key: fb for fb in score_bundle(root)}


# ── the commercially important scorers ────────────────────────────────────


def test_ungated_plan_capability_is_counted_not_failed(tmp_path):
    """A plan capability nothing gates is reported, not failed.

    OSS asserts no such invariant: the capability may be enforced outside module
    dispatch, or reserved for a module not yet generated. Failing on it would
    also punish a catalog that lists a permission id, which lives in a different
    namespace from entitlement_gate entirely.
    """
    root = _write_bundle(
        tmp_path / "b1",
        actions=[{"id": "get_status", "handler_method": "get_status"}],
        plan_capabilities=["billing_portal.read"],
    )
    fb = _by_key(root)["ungated_plan_capabilities"]
    assert fb.score is None          # informational, never a gate
    assert fb.value == 1.0
    assert fb.detail["ungated"] == ["billing_portal.read"]


def test_capability_sold_and_gated_passes(tmp_path):
    root = _write_bundle(
        tmp_path / "b2",
        actions=[{"id": "get_status", "handler_method": "get_status",
                  "entitlement_gate": "billing_portal.read"}],
        plan_capabilities=["billing_portal.read"],
    )
    scores = _by_key(root)
    assert scores["ungated_plan_capabilities"].value == 0.0
    assert scores["gated_capabilities_declared"].score == 1.0
    assert scores["action_gate_coverage"].value == 1.0


def test_gate_referencing_unsold_capability_fails(tmp_path):
    """The inverse invariant: a gate naming a capability no plan grants locks
    the action for everyone."""
    root = _write_bundle(
        tmp_path / "b3",
        actions=[{"id": "get_status", "handler_method": "get_status",
                  "entitlement_gate": "billing_portal.write"}],
        plan_capabilities=["billing_portal.read"],
    )
    fb = _by_key(root)["gated_capabilities_declared"]
    assert fb.score == 0.0
    assert fb.detail["orphaned"] == ["billing_portal.write"]


def test_ui_endpoint_pointing_at_missing_action_fails(tmp_path):
    """A dead button: UI wired to a handler the bundle never declares."""
    root = _write_bundle(
        tmp_path / "b4",
        actions=[{"id": "get_status", "handler_method": "get_status"}],
        ui_endpoints=["/api/modules/billing_portal/does_not_exist"],
    )
    fb = _by_key(root)["ui_endpoints_resolve"]
    assert fb.score == 0.0
    assert "does_not_exist" in fb.detail["broken"][0]


def test_ui_endpoints_resolve_when_declared(tmp_path):
    root = _write_bundle(tmp_path / "b5")
    assert _by_key(root)["ui_endpoints_resolve"].score == 1.0


# ── robustness ────────────────────────────────────────────────────────────


def test_malformed_yaml_is_reported_not_raised(tmp_path):
    root = _write_bundle(tmp_path / "b6")
    (root / "config" / "subscriptions.yaml").write_text("plans: [unclosed\n", encoding="utf-8")
    scores = _by_key(root)
    assert scores["bundle_parses"].score == 0.0
    # The rest of the run still completes.
    assert scores["has_app_manifest"].score == 1.0


def test_missing_artifacts_score_zero_without_crashing(tmp_path):
    root = tmp_path / "empty"
    root.mkdir()
    (root / "app.json").write_text("{}", encoding="utf-8")
    scores = _by_key(root)
    assert scores["has_app_manifest"].score == 0.0
    assert scores["has_subscription_catalog"].score == 0.0
    assert scores["ui_pages_present"].score == 0.0
    assert scores["has_dockerfile"].score == 0.0
    assert scores["action_gate_coverage"].value is None  # no actions to divide by


def test_bundle_exposes_derived_views(tmp_path):
    root = _write_bundle(
        tmp_path / "b7",
        actions=[{"id": "a", "entitlement_gate": "cap.one"}],
        plan_capabilities=["cap.one", "cap.two"],
    )
    bundle = Bundle(root)
    assert bundle.gated_capabilities() == {"cap.one"}
    assert bundle.plan_capabilities() == {"cap.one", "cap.two"}
    assert bundle.name == "demo"


# ── corpus, persistence, diff ─────────────────────────────────────────────


def test_discover_finds_nested_bundles(tmp_path):
    _write_bundle(tmp_path / "generated" / "app-a" / "build-1")
    _write_bundle(tmp_path / "generated" / "app-a" / "build-2")
    found = discover_bundles(tmp_path / "generated")
    assert len(found) == 2


def test_run_and_persist_round_trips(tmp_path):
    _write_bundle(tmp_path / "corpus" / "b1")
    run = run_corpus(discover_bundles(tmp_path / "corpus"), run_id="v1")
    path = save_run(run, tmp_path / "runs")
    loaded = load_run(path)
    assert loaded["run_id"] == "v1"
    assert loaded["bundle_count"] == 1
    assert "gated_capabilities_declared" in loaded["aggregates"]


def test_diff_detects_regression(tmp_path):
    """The CI gate: a Factory change that ungates a sold capability must show up
    as a regression rather than being discovered by a customer."""
    good = _write_bundle(
        tmp_path / "c1" / "b1",
        actions=[{"id": "get_status", "entitlement_gate": "billing_portal.read"}],
        plan_capabilities=["billing_portal.read"],
    )
    baseline = run_corpus([good], run_id="factory-v7").to_dict()

    # Same bundle id, gate now names a capability no plan grants — the action is
    # locked for everyone. Exactly what a bad Factory change looks like.
    _write_bundle(
        tmp_path / "c1" / "b1",
        actions=[{"id": "get_status", "entitlement_gate": "billing_portal.write"}],
        plan_capabilities=["billing_portal.read"],
    )
    current = run_corpus([good], run_id="factory-v8").to_dict()

    delta = diff_runs(current, baseline)
    keys = {(r["bundle"], r["scorer"]) for r in delta.regressions}
    assert ("b1", "gated_capabilities_declared") in keys
    assert delta.pass_rate_deltas["gated_capabilities_declared"] == -1.0
    assert "regression" in delta.summary()


def test_diff_detects_improvement_and_reports_no_regression(tmp_path):
    bad = _write_bundle(
        tmp_path / "c2" / "b1",
        actions=[{"id": "get_status", "entitlement_gate": "billing_portal.write"}],
        plan_capabilities=["billing_portal.read"],
    )
    baseline = run_corpus([bad], run_id="v1").to_dict()

    _write_bundle(
        tmp_path / "c2" / "b1",
        actions=[{"id": "get_status", "entitlement_gate": "billing_portal.read"}],
        plan_capabilities=["billing_portal.read"],
    )
    current = run_corpus([bad], run_id="v2").to_dict()

    delta = diff_runs(current, baseline)
    assert not delta.regressions
    assert {"bundle": "b1", "scorer": "gated_capabilities_declared"} in delta.improvements
    assert "no regressions" in delta.summary()


def test_diff_ignores_bundles_not_in_both_runs(tmp_path):
    """A corpus that grew between runs must not read as mass improvement."""
    _write_bundle(tmp_path / "c3" / "b1")
    baseline = run_corpus(discover_bundles(tmp_path / "c3"), run_id="v1").to_dict()
    _write_bundle(tmp_path / "c3" / "b2")
    current = run_corpus(discover_bundles(tmp_path / "c3"), run_id="v2").to_dict()

    delta = diff_runs(current, baseline)
    assert delta.only_in_current == ["b2"]
    assert not delta.regressions


def test_diff_gates_pass_to_errored_transitions(tmp_path):
    """A scorer that passed on baseline and produces no score now (errored or
    lost its input) must gate as a regression — a raising scorer must not keep
    CI green while its coverage silently disappears."""
    _write_bundle(tmp_path / "c4" / "b1")
    baseline = run_corpus(discover_bundles(tmp_path / "c4"), run_id="v1").to_dict()
    current = run_corpus(discover_bundles(tmp_path / "c4"), run_id="v2").to_dict()

    # Simulate the scorer erroring on the current run: score and value gone.
    for feedback in current["bundles"]["b1"]:
        if feedback["key"] == "gated_capabilities_declared":
            feedback["score"] = None
            feedback["value"] = None
            feedback["comment"] = ""

    delta = diff_runs(current, baseline)
    keys = {(r["bundle"], r["scorer"]) for r in delta.regressions}
    assert ("b1", "gated_capabilities_declared") in keys

    # Baseline None (value-only or already-errored scorers) still never gates.
    for feedback in baseline["bundles"]["b1"]:
        if feedback["key"] == "gated_capabilities_declared":
            feedback["score"] = None
    delta = diff_runs(current, baseline)
    assert not delta.regressions


def test_non_utf8_artifact_degrades_to_scorer_failure(tmp_path):
    """A stray non-UTF-8 byte in one generated file must not crash the run —
    Bundle is constructed outside the per-scorer error boundary."""
    root = _write_bundle(tmp_path / "b8")
    (root / "config" / "subscriptions.yaml").write_bytes(b"plans:\n  - id: caf\xe9\n")
    scores = _by_key(root)
    assert scores["bundle_parses"].score == 0.0
    # The rest of the run still completes.
    assert scores["has_app_manifest"].score == 1.0
