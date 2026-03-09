"""Core Package Root
=====================
Aggregated exports for primary subsystems (workflow, transport, observability, data, events).
Downstream code should prefer importing from specific submodules for clarity, but
these re-exports are provided for convenience and a stable public surface.
"""

from .workflow import (
	run_workflow_orchestration,
	create_ag2_pattern,
	register_workflow,
	get_workflow_handler,
	get_workflow_transport,
	workflow_status_summary,
)
from .transport import SimpleTransport
from .observability import (
	PerformanceManager,
	PerformanceConfig,
	get_performance_manager,
)
from .data import (
	PersistenceManager,
	AG2PersistenceManager,
)
from .events import (
	emit_business_event,
	emit_ui_tool_event, 
	get_event_dispatcher,
)

__all__ = [
	# Workflow
	"run_workflow_orchestration",
	"create_ag2_pattern",
	"register_workflow",
	"get_workflow_handler",
	"get_workflow_transport",
	"workflow_status_summary",
	# Transport
	"SimpleTransport",
	# Observability
	"PerformanceManager",
	"PerformanceConfig",
	"get_performance_manager",
	# Persistence
	"PersistenceManager",
	"AG2PersistenceManager",
	# Events  
	"emit_business_event",
	"emit_ui_tool_event",
	"get_event_dispatcher",
]

