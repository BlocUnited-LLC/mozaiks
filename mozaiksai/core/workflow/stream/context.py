# ==============================================================================
# FILE: core/workflow/stream/context.py
# DESCRIPTION: Context and state dataclasses for AG2 event stream processing
# ==============================================================================

"""
Stream Context and State

StreamContext: Immutable configuration passed to all event handlers.
StreamState: Mutable state tracked across event processing.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, Optional, Set
from collections import Counter

if TYPE_CHECKING:
    from autogen import ConversableAgent
    from mozaiksai.core.data.persistence import AG2PersistenceManager
    from mozaiksai.core.workflow.execution.lifecycle import LifecycleToolManager
    from mozaiksai.core.workflow.context.derived import DerivedContextManager


@dataclass
class StreamContext:
    """
    Immutable context passed to all event handlers during stream processing.

    Contains references to external services and workflow configuration that
    handlers need but should not modify.
    """

    # Identity
    chat_id: str
    app_id: str
    workflow_name: str
    user_id: Optional[str]

    # AG2 pattern instance (provides group_manager, context_variables)
    pattern: Any

    # External services
    transport: Any  # SimpleTransport instance
    persistence_manager: "AG2PersistenceManager"
    lifecycle_manager: Optional["LifecycleToolManager"]
    derived_context_manager: Optional["DerivedContextManager"]
    perf_mgr: Any  # PerformanceManager instance
    dispatcher: Any  # UnifiedEventDispatcher instance

    # Workflow configuration
    agents: Dict[str, "ConversableAgent"]
    structured_registry: Dict[str, Any]
    structured_agents: Set[str]
    auto_tool_agents: Set[str]
    max_turns: int

    # Logging
    wf_logger: Any
    workflow_name_upper: str

    # Run mode
    resumed_mode: bool = False
    initial_messages: list = field(default_factory=list)

    @property
    def group_manager(self) -> Any:
        """Access AG2 group_manager from pattern."""
        return getattr(self.pattern, "group_manager", None)

    @property
    def context_variables(self) -> Any:
        """Access AG2 ContextVariables from pattern or group_manager."""
        gm = self.group_manager
        if gm and hasattr(gm, "context_variables"):
            return getattr(gm, "context_variables")
        return getattr(self.pattern, "context_variables", None)

    def get_context_snapshot(self) -> Dict[str, Any]:
        """Get a safe snapshot of current context variables."""
        from mozaiksai.core.workflow.messages import safe_context_snapshot
        ctx_vars = self.context_variables
        if ctx_vars:
            return safe_context_snapshot(ctx_vars)
        return {}


@dataclass
class StreamState:
    """
    Mutable state tracked across event processing.

    Handlers can read and modify this state to track progress, correlate
    events, and manage lifecycle transitions.
    """

    # Turn tracking
    turn_agent: Optional[str] = None
    turn_started: Optional[float] = None
    sequence_counter: int = 0
    first_event_logged: bool = False

    # Tool call correlation
    # Maps call_id -> agent name that initiated the call
    tool_call_initiators: Dict[str, str] = field(default_factory=dict)
    # Maps call_id -> tool name for response labeling
    tool_names_by_id: Dict[str, str] = field(default_factory=dict)

    # Schema validation retry tracking (prevents infinite loops)
    schema_retry_tracker: Dict[str, int] = field(default_factory=dict)
    MAX_SCHEMA_RETRIES: int = 2

    # Input request tracking
    # Maps request_id -> respond callback
    pending_input_requests: Dict[str, Any] = field(default_factory=dict)

    # Execution tracking
    executed_agents: Set[str] = field(default_factory=set)

    # Context diffing (verbose mode)
    prev_ctx_snapshot: Dict[str, Any] = field(default_factory=dict)
    verbose_ctx: bool = False

    # Seed message deduplication
    # Tracks initial user messages to avoid echoing them back
    seed_user_messages: Counter = field(default_factory=Counter)

    # Completion state
    run_completed: bool = False
    completion_event: Any = None
    handoff_to_user: bool = False

    # AG2 response object (set during stream initialization)
    response: Any = None

    def record_tool_call(self, call_id: str, agent_name: str, tool_name: str) -> None:
        """Record a tool call for correlation with its response."""
        self.tool_call_initiators[call_id] = agent_name
        self.tool_names_by_id[call_id] = tool_name

    def get_tool_initiator(self, call_id: str) -> Optional[str]:
        """Get the agent that initiated a tool call."""
        return self.tool_call_initiators.get(call_id)

    def get_tool_name(self, call_id: str) -> Optional[str]:
        """Get the tool name for a call ID."""
        return self.tool_names_by_id.get(call_id)

    def should_retry_schema(self, retry_key: str) -> bool:
        """Check if schema validation should be retried."""
        attempts = self.schema_retry_tracker.get(retry_key, 0)
        return attempts < self.MAX_SCHEMA_RETRIES

    def record_schema_retry(self, retry_key: str) -> int:
        """Record a schema retry attempt and return current count."""
        attempts = self.schema_retry_tracker.get(retry_key, 0) + 1
        self.schema_retry_tracker[retry_key] = attempts
        return attempts

    def mark_agent_executed(self, agent_name: str) -> None:
        """Mark an agent as having executed in this run."""
        self.executed_agents.add(agent_name)

    def update_turn(self, agent_name: Optional[str], timestamp: float) -> Optional[str]:
        """
        Update the current turn to a new agent.

        Returns the previous turn agent for lifecycle trigger handling.
        """
        previous_agent = self.turn_agent
        self.turn_agent = agent_name
        self.turn_started = timestamp
        if agent_name:
            self.mark_agent_executed(agent_name)
        return previous_agent

    def is_seed_message(self, content: str, sender: Optional[str]) -> bool:
        """Check if this is a seeded initial message that should be suppressed."""
        if not content:
            return False
        content_key = content.strip()
        sender_lower = (sender or "").lower()
        if content_key and self.seed_user_messages.get(content_key):
            if sender_lower in {"user", "chat_manager", "manager", "agentmanager"}:
                return True
        return False

    def consume_seed_message(self, content: str) -> None:
        """Consume a seed message so it won't be suppressed again."""
        content_key = content.strip()
        if content_key in self.seed_user_messages:
            self.seed_user_messages[content_key] -= 1
            if self.seed_user_messages[content_key] <= 0:
                self.seed_user_messages.pop(content_key, None)

    def to_result_dict(self) -> Dict[str, Any]:
        """Convert final state to result dict for orchestration layer."""
        return {
            "response": self.response,
            "turn_agent": self.turn_agent,
            "turn_started": self.turn_started,
            "sequence_counter": self.sequence_counter,
            "run_completed": self.run_completed,
            "completion_event": self.completion_event,
            "handoff_to_user": self.handoff_to_user,
            "executed_agents": self.executed_agents,
        }
