"""Layer 1.5 — Kernel: workflow orchestration, decomposition, and coordination.

This layer is engine-agnostic. It operates on DomainEvents and RunRequests,
delegating actual execution to the engine layer via OrchestrationPort.
"""

from mozaiksai.kernel.decomposition import (
    AgentSignalDecomposition,
    ConfigDrivenDecomposition,
    DecompositionContext,
    DecompositionPlan,
    DecompositionStrategy,
    ExecutionMode,
    SubTask,
)
from mozaiksai.kernel.merge import (
    ChildResult,
    ConcatenateMerge,
    MergeContext,
    MergeResult,
    MergeStrategy,
    StructuredMerge,
)
from mozaiksai.kernel.orchestrator import (
    OrchestratorRun,
    RunState,
    UniversalOrchestrator,
    get_universal_orchestrator,
    reset_universal_orchestrator,
)
