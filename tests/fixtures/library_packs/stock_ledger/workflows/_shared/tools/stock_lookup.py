import json

from mozaiksai.core.workflow.module_tools import dispatch_workflow_module_action


async def stock_lookup(context_variables=None) -> str:
    """Read stock in process with the live user's authority; a failure is reported, never empty data."""
    try:
        result = await dispatch_workflow_module_action("stock_ledger", "list_items", {})
    except PermissionError as exc:
        return json.dumps({"status": "error", "error": str(exc)})
    if not result.success:
        return json.dumps({"status": "error", "error": result.error or "stock_lookup_failed"})
    return json.dumps({"status": "success", "data": result.data})
