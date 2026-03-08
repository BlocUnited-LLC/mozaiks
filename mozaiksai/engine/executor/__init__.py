"""Engine executor package — AG2 invocation and agent/pattern lifecycle.

The primary export is :class:`GroupChatExecutor` which encapsulates:
* Workflow config loading
* Chat resume / initialize
* Agent creation and tool binding
* AG2 pattern creation and context wiring
* AG2 group-chat launch (resume vs. new-run)

Downstream code receives a :class:`PreparedRun` and iterates
``prepared_run.response.events`` through the kernel pipeline.
"""

from mozaiksai.engine.executor.groupchat_executor import (
    GroupChatExecutor,
    PreparedRun,
)

__all__ = [
    "GroupChatExecutor",
    "PreparedRun",
]
