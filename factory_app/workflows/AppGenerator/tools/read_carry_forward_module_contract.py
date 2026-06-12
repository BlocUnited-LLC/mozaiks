"""
AG2 tool adapter: read_carry_forward_module_contract for AppPlanAgent.

Thin wrapper that exposes the control-plane carry-forward module contract
reader as an AG2 callable tool with autogen dependency-injection annotations.

AppPlanAgent may call this tool during ``conceptual_replan`` to inspect
specific contract files (module.yaml, contracts/*.yaml, runtime_extensions.yaml)
from a carry-forward candidate in the previous app bundle workspace.

**Read-only.** Does not copy or merge files. Does not read backend Python
source. Does not modify any artifact or workspace.

See full implementation at:
``factory_app/control_plane/tools/read_carry_forward_module_contract.py``
"""
from typing import Annotated, Any

from autogen.tools.dependency_injection import Field

from factory_app.control_plane.tools.read_carry_forward_module_contract import (
    read_carry_forward_module_contract as _core,
)


async def read_carry_forward_module_contract(
    module_id: Annotated[
        str,
        Field(
            description=(
                "Module directory name (e.g. 'notifications', 'billing_portal') "
                "to inspect from the previous app bundle workspace. Must be a "
                "plain module id with no path separators."
            )
        ),
    ],
    files: Annotated[
        list[str] | None,
        Field(
            description=(
                "Optional list of contract filenames to read. "
                "Allowed values: 'module.yaml', 'runtime_extensions.yaml', "
                "'contracts/events.yaml', 'contracts/reactions.yaml', "
                "'contracts/notifications.yaml', 'contracts/settings.yaml', "
                "'contracts/admin.yaml', 'contracts/profile.yaml'. "
                "When null, all allowed files present in the workspace are returned. "
                "Backend Python files (backend/*.py) are disallowed in this phase."
            )
        ),
    ] = None,
    *,
    context_variables: Annotated[
        Any | None,
        Field(description="AG2-injected workflow context variables."),
    ] = None,
) -> dict[str, Any]:
    """Return selected contract files from the previous app_bundle for a module.

    Read-only. Never raises. Returns empty files + warnings on any failure.
    """
    return await _core(
        module_id=module_id,
        files=files,
        context_variables=dict(context_variables) if context_variables else None,
    )

