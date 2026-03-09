# ==============================================================================
# FILE: models.py
# DESCRIPTION: 
# ==============================================================================
"""Unified Pydantic schemas + rollup for ChatSessions & WorkflowSummaries.

This module now ONLY defines:
    * Pydantic models (v2) for persisted chat messages & sessions
    * Pydantic models for workflow roll‑up (aggregate) documents
    * A lightweight manager to compute & upsert summary docs

Deliberate simplifications / removals:
    * No duplication of normalized event persistence (that lives in AG2PersistenceManager -> WorkflowStats collection)
    * No conflicting unique indexes on fields also used by per‑event rows
    * Summary documents stored in the `WorkflowStats` collection as deterministic
    * rollup documents (prefixed with `mon_`) to avoid adding extra top-level
    * collections. WorkflowStats therefore contains three logical types:
    *   - per-session metrics docs: `metrics_{chat_id}` (real-time usage/metrics)
    *   - append-only normalized event rows (audit/trace)
    *   - pre-computed rollup summaries: `mon_{app_id}_{workflow_name}` (legacy: app_id)

Key Collections (post‑refactor):
    ChatSessions        : One doc per chat (messages + minimal usage + status)
    WorkflowStats       : (Managed elsewhere) Append‑only normalized event rows (+ single metrics doc w/out sequence)
    WorkflowSummaries   : One aggregated rollup per (app_id, workflow_name)

ChatSessions Stored Fields (superset; some optional):
    _id, app_id (+ legacy app_id), workflow_name, user_id, status, created_at, last_updated_at,
    completed_at?, trace_id?, duration_sec (float),
    usage_prompt_tokens_final?, usage_completion_tokens_final?, usage_total_tokens_final?,
    usage_total_cost_final?, usage_summary_raw?, messages[]

WorkflowSummaryDoc Stored Fields:
    _id, app_id (+ legacy app_id), workflow_name, overall_avg, chat_sessions, agents

NOTE: We intentionally keep rollup computation *read‑only* over ChatSessions;
            token & cost fields are copied from flattened usage_* finals in sessions.
"""

import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Mapping
from collections import defaultdict
from enum import IntEnum, Enum
from pydantic import BaseModel, Field, ConfigDict, model_validator
from pymongo.errors import DuplicateKeyError

from mozaiksai.core.core_config import get_mongo_client
from mozaiksai.core.multitenant import build_app_scope_filter, coalesce_app_id
from logs.logging_config import get_workflow_logger

logger = get_workflow_logger("chat_workflow_models")

# ===========================================
# EXACT PYDANTIC MODELS (MATCH SPECIFICATION)
# ===========================================

class Role(str, Enum):
    user = "user"
    assistant = "assistant"


class WorkflowStatus(IntEnum):
    """Numeric workflow/chat session status.
    0 = in progress, 1 = completed. Additional states removed per simplification.
    Stored as small integers in MongoDB.
    """
    IN_PROGRESS = 0
    COMPLETED = 1

    def __str__(self) -> str:  # for logging
        return "completed" if self.value == 1 else "in_progress"


class AggregateAverages(BaseModel):
    """Average metrics block (durations in seconds, costs in USD)."""
    model_config = ConfigDict(extra="forbid")
    avg_duration_sec: float = Field(0, ge=0)
    avg_prompt_tokens: int = Field(0, ge=0)
    avg_completion_tokens: int = Field(0, ge=0)
    avg_total_tokens: int = Field(0, ge=0)
    avg_cost_total_usd: float = Field(0, ge=0)


class ChatSessionStats(BaseModel):
    """Per-session metrics snapshot used inside rollups."""
    model_config = ConfigDict(extra="forbid")
    duration_sec: float = Field(0, ge=0)
    prompt_tokens: int = Field(0, ge=0)
    completion_tokens: int = Field(0, ge=0)
    total_tokens: int = Field(0, ge=0)
    cost_total_usd: float = Field(0, ge=0)

    def model_post_init(self, _):  # type: ignore[override]
        if self.total_tokens != self.prompt_tokens + self.completion_tokens:
            # Auto-correct
            object.__setattr__(self, 'total_tokens', self.prompt_tokens + self.completion_tokens)


class SessionMetricsDoc(BaseModel):
    """Real-time metrics tracking for WorkflowStats collection.
    
    Created alongside ChatSession for immediate usage tracking.
    """
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: str = Field(alias="_id")  # format: "metrics_{chat_id}"
    chat_id: str
    app_id: str
    workflow_name: str
    user_id: str
    created_at: datetime
    last_updated_at: datetime
    # Real-time accumulating metrics
    prompt_tokens: int = Field(0, ge=0)
    completion_tokens: int = Field(0, ge=0)
    total_tokens: int = Field(0, ge=0)
    cost_total_usd: float = Field(0.0, ge=0)
    # Per-agent breakdown for dynamic agent tracking
    agent_metrics: Dict[str, ChatSessionStats] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _coerce_scope(cls, data: Any):  # noqa: ANN001
        if isinstance(data, dict) and not data.get("app_id"):
            for key in ("appId", "AppId", "AppID"):
                if data.get(key):
                    data = dict(data)
                    data["app_id"] = data.get(key)
                    break
        return data

    @property
    def _id(self) -> str:  # noqa: D401
        return self.id


class AgentAggregate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    avg: AggregateAverages
    sessions: Dict[str, ChatSessionStats] = Field(default_factory=dict)


class WorkflowSummaryDoc(BaseModel):
    """Rollup document stored in `WorkflowSummaries` collection.

    _id pattern: mon_{app_id}_{workflow_name}
    (Deterministic; one summary per (app, workflow)).
    """
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: str = Field(alias="_id")
    app_id: str
    user_id: Optional[str] = None
    workflow_name: str
    # When the summary was last updated (datetime stored as UTC)
    last_updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    # Provide a concrete instance (all zero defaults) instead of default_factory for stricter type checker happiness
    overall_avg: AggregateAverages = AggregateAverages(
        avg_duration_sec=0,
        avg_prompt_tokens=0,
        avg_completion_tokens=0,
        avg_total_tokens=0,
        avg_cost_total_usd=0.0,
    )
    chat_sessions: Dict[str, ChatSessionStats] = Field(default_factory=dict)
    agents: Dict[str, AgentAggregate] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _coerce_scope(cls, data: Any):  # noqa: ANN001
        if isinstance(data, dict) and not data.get("app_id"):
            for key in ("appId", "AppId", "AppID"):
                if data.get(key):
                    data = dict(data)
                    data["app_id"] = data.get(key)
                    break
        return data

    @property
    def _id(self) -> str:  # noqa: D401
        return self.id


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Role
    content: str
    timestamp: datetime
    event_type: str = Field("message.created")
    event_id: str
    is_user_proxy: bool = False
    agent_name: Optional[str] = None


class ChatSessionDoc(BaseModel):
    """Canonical ChatSessions document schema.

    Optional usage / duration fields are present only after workflow completes
    or usage updates occur.
    """
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: str = Field(alias="_id")
    app_id: str
    workflow_name: str
    user_id: str
    status: WorkflowStatus  # stored as int (0/1)
    created_at: datetime
    last_updated_at: datetime
    completed_at: Optional[datetime] = None
    trace_id: Optional[str] = None
    # Pause support: for low balance scenarios where chat stays resumable
    paused: bool = False
    pause_reason: Optional[str] = None
    paused_at: Optional[datetime] = None
    # Real-time accumulating usage fields (kept in-session for rollup independence)
    usage_prompt_tokens_final: int = 0
    usage_completion_tokens_final: int = 0
    usage_total_tokens_final: int = 0
    usage_total_cost_final: float = 0.0
    # messages and timestamps only; token/cost fields are stored in WorkflowStats
    messages: List[ChatMessage] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _coerce_scope(cls, data: Any):  # noqa: ANN001
        if isinstance(data, dict) and not data.get("app_id"):
            for key in ("appId", "AppId", "AppID"):
                if data.get(key):
                    data = dict(data)
                    data["app_id"] = data.get(key)
                    break
        return data

    @property
    def _id(self) -> str:  # noqa: D401
        return self.id

# ===========================================
# MONGODB PERSISTENCE LAYER & DAO FUNCTIONS
# ===========================================

class ChatWorkflowManager:
    """Lightweight rollup manager (summary aggregation only).

    Responsibilities:
      * Read ChatSessions documents
      * Compute per-workflow rollup
    * Upsert into `WorkflowStats` as deterministic rollup documents (prefixed
    * with `mon_`). We intentionally colocate rollups, metrics and event rows
    * in the same logical collection but distinguish them by `_id` naming
    * conventions to keep the storage footprint small and queries fast.
    """

    def __init__(self):
        self.client: Any = None
        self.db: Any = None
        self.chat_sessions: Any = None  # set in _ensure_client
        self.workflow_summaries: Any = None  # set in _ensure_client
        self._init_lock = asyncio.Lock()

    async def _ensure_client(self):
        if self.client is not None:
            return
        async with self._init_lock:
            if self.client is not None:
                return
            self.client = get_mongo_client()
            self.db = self.client["MozaiksAI"]
            self.chat_sessions = self.db["ChatSessions"]
            # Store rollup summaries in WorkflowStats collection to keep only
            # two top-level collections: ChatSessions and WorkflowStats
            self.workflow_summaries = self.db["WorkflowStats"]
            await self._ensure_indexes()

    async def _ensure_indexes(self):
        try:
            assert self.chat_sessions is not None
            # Check existing indexes to avoid conflicts
            existing_indexes = await self.chat_sessions.list_indexes().to_list(length=None)
            index_names = [idx["name"] for idx in existing_indexes]
            
            # workflow_summaries points to WorkflowStats now
            # ChatSessions (query accelerators) - use consistent naming
            if not any(name in ["idx_ent_wf_created", "cs_ent_wf_created"] for name in index_names):
                await self.chat_sessions.create_index([
                    ("app_id", 1), ("workflow_name", 1), ("created_at", -1)
                ], name="cs_ent_wf_created")

            if "cs_app_wf_created" not in index_names:
                await self.chat_sessions.create_index([
                    ("app_id", 1), ("workflow_name", 1), ("created_at", -1)
                ], name="cs_app_wf_created")
                
            if not any(name in ["idx_status", "cs_status_created"] for name in index_names):
                await self.chat_sessions.create_index([
                    ("status", 1), ("created_at", -1)
                ], name="cs_status_created")
                
            if "cs_trace_id" not in index_names:
                await self.chat_sessions.create_index("trace_id", name="cs_trace_id")
                
            # We use WorkflowStats for both normalized events and for
            # summary/docs distinguished by their _id (no `sequence` field).
            # Avoid creating a unique index here to prevent collisions with
            # normalized event rows which use different keys.
        except Exception as e:  # pragma: no cover
            logger.warning(f"Index ensure issue: {e}")

    # ===============================
    # CHAT SESSIONS DAO FUNCTIONS
    # ===============================
    
    async def create_chat_session(self, session_data: Mapping[str, Any]) -> ChatSessionDoc:
        await self._ensure_client()
        doc = ChatSessionDoc.model_validate(dict(session_data))
        try:
            await self.chat_sessions.insert_one(doc.model_dump(by_alias=True))
            return doc
        except DuplicateKeyError as e:  # noqa: PERF203
            raise ValueError(f"Session {doc._id} already exists") from e
    
    async def get_chat_session(self, session_id: str) -> Optional[ChatSessionDoc]:
        """Get chat session by ID."""
        await self._ensure_client()
        
        doc = await self.chat_sessions.find_one({"_id": session_id})
        return ChatSessionDoc.model_validate(doc) if doc else None
    
    async def append_message(self, session_id: str, message: Mapping[str, Any]) -> bool:
        await self._ensure_client()
        msg = ChatMessage.model_validate(dict(message))
        assert self.chat_sessions is not None
        res = await self.chat_sessions.update_one(
            {"_id": session_id},
            {"$push": {"messages": msg.model_dump(by_alias=True)}, "$set": {"last_updated_at": datetime.now(timezone.utc)}}
        )
        return res.modified_count > 0
    
    async def complete_chat_session(self, session_id: str) -> bool:
        await self._ensure_client()
        now = datetime.now(timezone.utc)
        assert self.chat_sessions is not None
        res = await self.chat_sessions.update_one(
            {"_id": session_id},
            {"$set": {"status": WorkflowStatus.COMPLETED, "completed_at": now, "last_updated_at": now}}
        )
        return res.modified_count > 0

    # ===============================
    # ROLLUP/AGGREGATION SERVICE
    # ===============================
    
    async def compute_workflow_rollup(
        self,
        app_id: str,
        workflow_name: str,
    ) -> WorkflowSummaryDoc:
        await self._ensure_client()
        resolved_app_id = coalesce_app_id(app_id=app_id)
        if not resolved_app_id:
            raise ValueError("app_id is required")
        assert self.chat_sessions is not None
        cursor = self.chat_sessions.find({
            "workflow_name": workflow_name,
            "status": WorkflowStatus.COMPLETED,
            **build_app_scope_filter(str(resolved_app_id)),
        })
        sessions: List[Dict[str, Any]] = [doc async for doc in cursor]
        if not sessions:
            return WorkflowSummaryDoc(
                _id=f"mon_{resolved_app_id}_{workflow_name}",
                app_id=str(resolved_app_id),
                workflow_name=workflow_name,
            )
        overall_sessions: Dict[str, ChatSessionStats] = {}
        # per_agent_totals will hold lists of (sid, ChatSessionStats) so we can map back by id
        per_agent_totals: Dict[str, List[tuple]] = defaultdict(list)

        for sess in sessions:
            sid = sess["_id"]
            created = sess.get("created_at")
            completed = sess.get("completed_at")
            dur = float((completed - created).total_seconds()) if created and completed else 0.0
            prompt = int(sess.get("usage_prompt_tokens_final") or 0)
            completion = int(sess.get("usage_completion_tokens_final") or 0)
            total = int(sess.get("usage_total_tokens_final") or (prompt + completion))
            cost = float(sess.get("usage_total_cost_final") or 0.0)

            stats = ChatSessionStats(
                duration_sec=dur,
                prompt_tokens=prompt,
                completion_tokens=completion,
                total_tokens=total,
                cost_total_usd=cost,
            )
            overall_sessions[sid] = stats

            agents_in_session = [m.get("agent_name") for m in sess.get("messages", []) if m.get("role") == "assistant" and m.get("agent_name")]
            for agent in agents_in_session:
                per_agent_totals[agent].append((sid, stats))

        # Overall averages
        n = len(overall_sessions)
        overall_avg = AggregateAverages(
            avg_duration_sec=(sum(s.duration_sec for s in overall_sessions.values()) / n) if n else 0.0,
            avg_prompt_tokens=int(sum(s.prompt_tokens for s in overall_sessions.values()) / n) if n else 0,
            avg_completion_tokens=int(sum(s.completion_tokens for s in overall_sessions.values()) / n) if n else 0,
            avg_total_tokens=int(sum(s.total_tokens for s in overall_sessions.values()) / n) if n else 0,
            avg_cost_total_usd=(sum(s.cost_total_usd for s in overall_sessions.values()) / n) if n else 0.0,
        )

        agents_rollup: Dict[str, AgentAggregate] = {}
        for agent, sid_stats_pairs in per_agent_totals.items():
            stats_list = [s for (_sid, s) in sid_stats_pairs]
            an = len(stats_list)
            agent_avg = AggregateAverages(
                avg_duration_sec=(sum(s.duration_sec for s in stats_list) / an) if an else 0.0,
                avg_prompt_tokens=int(sum(s.prompt_tokens for s in stats_list) / an) if an else 0,
                avg_completion_tokens=int(sum(s.completion_tokens for s in stats_list) / an) if an else 0,
                avg_total_tokens=int(sum(s.total_tokens for s in stats_list) / an) if an else 0,
                avg_cost_total_usd=(sum(s.cost_total_usd for s in stats_list) / an) if an else 0.0,
            )
            # Build a sessions dict keyed by session id with the ChatSessionStats
            session_map = {sid: stats for (sid, stats) in sid_stats_pairs}
            agents_rollup[agent] = AgentAggregate(avg=agent_avg, sessions=session_map)

        # user_id: try to surface a representative user (first session's user_id)
        representative_user = sessions[0].get("user_id") if sessions else None
        return WorkflowSummaryDoc(
            _id=f"mon_{resolved_app_id}_{workflow_name}",
            app_id=str(resolved_app_id),
            user_id=representative_user,
            workflow_name=workflow_name,
            last_updated_at=datetime.now(timezone.utc),
            overall_avg=overall_avg,
            chat_sessions=overall_sessions,
            agents=agents_rollup,
        )
    
    async def upsert_workflow_summary(self, summary: WorkflowSummaryDoc) -> WorkflowSummaryDoc:
        await self._ensure_client()
        assert self.workflow_summaries is not None
        await self.workflow_summaries.replace_one(
            {"_id": summary._id}, summary.model_dump(by_alias=True), upsert=True
        )
        return summary
    
    async def get_workflow_summary(
        self,
        app_id: str,
        workflow_name: str,
    ) -> WorkflowSummaryDoc:
        await self._ensure_client()
        resolved_app_id = coalesce_app_id(app_id=app_id)
        if not resolved_app_id:
            raise ValueError("app_id is required")
        summary_id = f"mon_{resolved_app_id}_{workflow_name}"
        assert self.workflow_summaries is not None
        doc = await self.workflow_summaries.find_one({"_id": summary_id})
        if doc:
            return WorkflowSummaryDoc.model_validate(doc)
        summary = await self.compute_workflow_rollup(app_id=resolved_app_id, workflow_name=workflow_name)
        return await self.upsert_workflow_summary(summary)

# ===========================================
# GLOBAL MANAGER INSTANCE & API FUNCTIONS
# ===========================================

# Global instance (lazy singleton style)
chat_workflow_manager = ChatWorkflowManager()

# Convenience API functions
async def create_session(session_data: Dict[str, Any]) -> ChatSessionDoc:
    """Create new chat session."""
    return await chat_workflow_manager.create_chat_session(session_data)

async def get_session(session_id: str) -> Optional[ChatSessionDoc]:
    """Get chat session by ID."""
    return await chat_workflow_manager.get_chat_session(session_id)

async def append_message_to_session(session_id: str, message: Dict[str, Any]) -> bool:
    """Append message to session."""
    return await chat_workflow_manager.append_message(session_id, message)

async def complete_session(session_id: str) -> bool:
    """Mark session completed (reason removed)."""
    return await chat_workflow_manager.complete_chat_session(session_id)

async def get_workflow_summary(app_id: str, workflow_name: str) -> WorkflowSummaryDoc:
    """Get workflow summary (computes rollup if needed)."""
    return await chat_workflow_manager.get_workflow_summary(app_id=app_id, workflow_name=workflow_name)

async def refresh_workflow_rollup(app_id: str, workflow_name: str) -> WorkflowSummaryDoc:
    """Force refresh of workflow rollup."""
    summary = await chat_workflow_manager.compute_workflow_rollup(app_id=app_id, workflow_name=workflow_name)
    return await chat_workflow_manager.upsert_workflow_summary(summary)


async def refresh_workflow_rollup_by_id(summary_id: str) -> WorkflowSummaryDoc:
    """Refresh rollup using the full summary _id (useful when caller already computes deterministic id).

    Accepts ids like: mon_{app_id}_{workflow_name}
    """
    # Defensive parsing: try to split the id pattern; fall back to compute by app/workflow
    try:
        if summary_id.startswith("mon_"):
            _, rest = summary_id.split("mon_", 1)
            parts = rest.split("_", 1)
            if len(parts) == 2:
                app_id, workflow_name = parts[0], parts[1]
                summary = await chat_workflow_manager.compute_workflow_rollup(app_id=app_id, workflow_name=workflow_name)
                return await chat_workflow_manager.upsert_workflow_summary(summary)
    except Exception:
        pass
    # Fallback: attempt a compute using the whole id as app (unlikely) - compute a best-effort empty summary
    return await chat_workflow_manager.upsert_workflow_summary(WorkflowSummaryDoc(_id=summary_id, app_id="", workflow_name=""))

# Validation helpers
def validate_chat_message(msg_dict: dict) -> ChatMessage:
    """Validate and create ChatMessage from dict."""
    return ChatMessage.model_validate(msg_dict)

def validate_chat_session(session_dict: dict) -> ChatSessionDoc:
    """Validate and create ChatSessionDoc from dict."""
    return ChatSessionDoc.model_validate(session_dict)

def validate_workflow_summary(summary_dict: dict) -> WorkflowSummaryDoc:
    """Validate and create WorkflowSummaryDoc from dict."""
    return WorkflowSummaryDoc.model_validate(summary_dict)
