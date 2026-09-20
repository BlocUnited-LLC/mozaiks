"""AppPlanAgent can only copy owned_paths from contracts it is actually shown.

`AppBuildTask.owned_paths` is documented as "copy exact paths from FILE
CONTRACTS CONTEXT". That context is assembled from `_PLANNING_CONTRACT_ORDER`,
which listed six of the seven task contracts in `file_contracts.yaml`.
`subscription_config` was the one left out — added to the data file on
2026-06-14, five weeks after the tuple was written, and never added to it.

The planner was still told `subscription_config` was legal vocabulary, so it
planned the task and had no path to give it. A live greenfield run
(`build_314a761d`, OSS `50270b84`) emitted `owned_paths: []` three times and
died with the whole attempt budget spent:

    Build task '6' uses task_type 'subscription_config' but owns [].
    Subscription config tasks may only own config/subscriptions.yaml.

`TestFileContractsIntegrity` already asserts these contracts exist in the data
file. Nothing asserted they reach the agent told to copy from them, which is
why a five-week-old gap surfaced in a live run instead of in CI.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from factory_app.workflows.AppGenerator.tools.app_build_plan import _validate_build_tasks
from factory_app.workflows.AppGenerator.tools.hook_file_contract_context import (
    _ALLOWED_TASK_TYPES,
    _FILE_CONTRACTS_PATH,
    _PLANNING_CONTRACT_ORDER,
    _build_file_contracts_body,
    _load_yaml,
)

SUBSCRIPTION_PATH = "config/subscriptions.yaml"


def _file_contracts() -> dict:
    contracts = _load_yaml(_FILE_CONTRACTS_PATH)
    assert isinstance(contracts, dict), f"file contracts did not load from {_FILE_CONTRACTS_PATH}"
    return contracts


def _task_contracts() -> dict:
    return _file_contracts()["task_contracts"]


def _planner_body() -> str:
    """The file-contract context as AppPlanAgent actually receives it."""
    agent = SimpleNamespace(name="AppPlanAgent", context_variables={})
    return _build_file_contracts_body(agent, _file_contracts())


def _subscription_task(owned_paths: list[str]) -> dict:
    return {
        "task_id": "6",
        "task_type": "subscription_config",
        "surface_kind": "app_policy",
        "capability_pack_id": None,
        "initial_agent": "ConfigMiddlewareAgent",
        "execution_target": "generator",
        "description": "Generate the app subscription contract.",
        "initial_message": "Generate config/subscriptions.yaml.",
        "owned_paths": owned_paths,
        "depends_on": [],
        "acceptance_criteria": [],
    }


# --------------------------------------------------------------------------
# The contract-surface integrity guard
# --------------------------------------------------------------------------


def test_every_task_contract_is_surfaced_to_the_planner():
    """The guard that would have caught this the day the contract landed.

    Equality, not containment, in both directions: a contract missing from the
    tuple is invisible to the planner, and a tuple entry missing from the data
    file silently renders nothing. Excluding a contract from the planner on
    purpose should be a deliberate edit here, not a silent omission there.
    """
    assert set(_task_contracts()) == set(_PLANNING_CONTRACT_ORDER), (
        "every task contract in file_contracts.yaml must be surfaced to AppPlanAgent; "
        "the planner is instructed to copy owned_paths from this context and cannot "
        "copy a contract it was never shown"
    )


def test_planning_contract_order_has_no_duplicates():
    assert len(_PLANNING_CONTRACT_ORDER) == len(set(_PLANNING_CONTRACT_ORDER))


@pytest.mark.parametrize("contract_name", sorted(_load_yaml(_FILE_CONTRACTS_PATH)["task_contracts"]))
def test_each_contract_required_outputs_reach_the_planner(contract_name: str):
    """Surfacing the name is not enough — the paths are what get copied."""
    body = _planner_body()
    assert contract_name in body, f"{contract_name} contract block missing from planner context"
    for output in _task_contracts()[contract_name].get("required_outputs") or []:
        if "*" in str(output):  # glob outputs are patterns, not copyable paths
            continue
        assert str(output) in body, f"{contract_name} required output {output} not shown to planner"


# --------------------------------------------------------------------------
# The specific regression
# --------------------------------------------------------------------------


def test_subscription_config_is_surfaced_with_its_path():
    body = _planner_body()
    assert "subscription_config" in body
    assert SUBSCRIPTION_PATH in body, (
        "the planner is told subscription_config is legal vocabulary; without this "
        "path it plans the task owning nothing and the run dies at admission"
    )


def test_subscription_config_was_already_legal_vocabulary():
    """Why the omission was silent: the task type was always offered.

    The planner had every reason to plan the task and no way to path it.
    """
    assert "subscription_config" in _ALLOWED_TASK_TYPES


def test_contract_declares_exactly_the_path_admission_requires():
    """The data and the validator already agreed; only delivery was missing."""
    assert _task_contracts()["subscription_config"]["required_outputs"] == [SUBSCRIPTION_PATH]


# --------------------------------------------------------------------------
# Admission is unchanged — this PR surfaces a contract, it does not relax a gate
# --------------------------------------------------------------------------


def test_task_owning_the_contract_path_passes_admission():
    required = _task_contracts()["subscription_config"]["required_outputs"]
    _validate_build_tasks([_subscription_task(list(required))])


def test_task_owning_nothing_is_still_rejected():
    """The live failure, reproduced. Surfacing the contract must not soften this."""
    with pytest.raises(ValueError) as excinfo:
        _validate_build_tasks([_subscription_task([])])
    message = str(excinfo.value)
    assert "owns []" in message
    assert SUBSCRIPTION_PATH in message


@pytest.mark.parametrize(
    "owned_paths",
    [
        pytest.param(["app/config/subscriptions.yaml"], id="app-prefixed"),
        pytest.param(["config/subscriptions.yml"], id="wrong-extension"),
        pytest.param([SUBSCRIPTION_PATH, "config/targets.json"], id="extra-path"),
        pytest.param(["modules/billing/contracts/subscriptions.yaml"], id="module-local"),
    ],
)
def test_near_miss_paths_remain_rejected(owned_paths: list[str]):
    """Ownership validation stays exactly as narrow as it was."""
    with pytest.raises(ValueError):
        _validate_build_tasks([_subscription_task(owned_paths)])
