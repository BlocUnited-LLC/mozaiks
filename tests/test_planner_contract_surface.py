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

The subscription_config task has since been removed outright: assembly writes
config/subscriptions.yaml from the approved contract, so there is no task to
surface. The generic guards below still hold for every remaining contract.
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
# config/subscriptions.yaml is not model work
# --------------------------------------------------------------------------


def test_subscription_config_is_no_longer_a_task_type():
    assert "subscription_config" not in _ALLOWED_TASK_TYPES
    assert "subscription_config" not in _task_contracts()
    assert "subscription_config" not in _planner_body()


def test_no_remaining_contract_offers_the_subscriptions_path():
    """The planner copies owned_paths from these contracts; none may offer this one."""
    for name, contract in _task_contracts().items():
        outputs = [*(contract.get("required_outputs") or []), *(contract.get("optional_outputs") or [])]
        assert SUBSCRIPTION_PATH not in outputs, name


def test_a_subscription_config_task_is_rejected_at_admission():
    task = {
        "task_id": "6", "task_type": "subscription_config", "capability_pack_id": None,
        "initial_agent": "ConfigMiddlewareAgent", "owned_paths": [SUBSCRIPTION_PATH], "depends_on": [],
    }
    with pytest.raises(ValueError, match="unsupported task_type 'subscription_config'"):
        _validate_build_tasks([task])
