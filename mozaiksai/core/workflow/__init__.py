"""Workflow Management Package
================================
Public entry points for running and registering workflows.

Exports (stable surface):
 - run_workflow_orchestration: Execute a workflow instance (async orchestration engine)
 - create_ag2_pattern: Factory for AG2 patterns
 - register_workflow / get_workflow_handler: Registry for custom workflow handlers
 - get_workflow_transport / get_workflow_tools / workflow_status_summary: Introspection utilities
 - initialize_workflow_ui_components: Optional UI component bootstrap
 - add_initialization_coroutine / get_initialization_coroutines / run_initializers: Startup hooks

Internal symbols are intentionally not re-exported to keep namespace clean.
"""

from .orchestration_patterns import (
	run_workflow_orchestration,
	create_ag2_pattern,
)

from .workflow_manager import (
	register_workflow,
	get_workflow_handler,
	get_workflow_transport,
	workflow_status_summary,
	get_workflow_tools,
)

__all__ = [
	# Orchestration engine / patterns
	"run_workflow_orchestration",
	"create_ag2_pattern",
	# Registry APIs
	"register_workflow",
	"get_workflow_handler",
	"get_workflow_transport",
	"workflow_status_summary",
	"get_workflow_tools",
]

