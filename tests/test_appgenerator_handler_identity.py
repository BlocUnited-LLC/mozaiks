"""Handler repair follows the accepted module contract without inventing actions."""

from copy import deepcopy

import yaml

from factory_app.workflows.AppGenerator.tools.app_validation import (
    validate_module_implementation_contract,
)


def _files() -> dict[str, str]:
    operations = ("login_user", "logout_user", "list_user_sessions")
    contract = {
        "module": {
            "id": "user_authentication",
            "handler": "backend.handler:UserAuthenticationModule",
        },
        "actions": [
            {"id": name, "handler_method": name, "input_schema": {"type": "object", "properties": {}}}
            for name in operations
        ],
    }
    return {
        "modules/user_authentication/module.yaml": yaml.safe_dump(contract),
        "modules/user_authentication/backend/handler.py": (
            "from . import service\n\n"
            "class UserSessionHandler:\n"
            "    async def login_user(self, ctx, **params):\n"
            "        return await service.login_user(ctx, **params)\n"
            "    async def logout_user(self, ctx, **params):\n"
            "        return await service.logout_user(ctx, **params)\n"
        ),
    }


def test_user_authentication_handler_identity_mismatch_is_rejected():
    files = _files()
    before = deepcopy(files)

    result = validate_module_implementation_contract(files)

    assert result["passed"] is False
    assert any(
        failure["test"] == "module_handler_class_exists"
        and "UserAuthenticationModule" in failure["error"]
        for failure in result["failed_tests"]
    )
    assert files == before


def test_handler_class_rename_cannot_hide_a_missing_declared_action():
    files = _files()
    handler = "modules/user_authentication/backend/handler.py"
    files[handler] = files[handler].replace("class UserSessionHandler:", "class UserAuthenticationModule:")

    result = validate_module_implementation_contract(files)

    assert result["passed"] is False
    assert any(
        failure["test"] == "module_action_handler_method_missing"
        and "list_user_sessions" in failure["error"]
        for failure in result["failed_tests"]
    )


def test_complete_contract_bound_handler_repair_preserves_the_contract():
    files = _files()
    contract = files["modules/user_authentication/module.yaml"]
    handler = "modules/user_authentication/backend/handler.py"
    files[handler] = files[handler].replace("class UserSessionHandler:", "class UserAuthenticationModule:")
    files[handler] += (
        "    async def list_user_sessions(self, ctx, **params):\n"
        "        return await service.list_user_sessions(ctx, **params)\n"
    )

    result = validate_module_implementation_contract(files)

    assert result["passed"] is True, result
    assert files["modules/user_authentication/module.yaml"] == contract
