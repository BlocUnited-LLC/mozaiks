from __future__ import annotations
"""Lean Performance Manager aligned with new ChatSessions schema.

Maintains minimal in-memory metrics and updates session duration / a few flattened usage fields.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List, Union

from logs.logging_config import get_workflow_logger
from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager
from mozaiksai.core.data.models import WorkflowStatus

logger = get_workflow_logger("performance_manager")
perf_logger = get_workflow_logger("performance")


@dataclass
class PerformanceConfig:
    flush_interval_sec: int = 0
    enabled: bool = True

@dataclass
class ChatPerfState:
    chat_id: str
    app_id: str
    workflow_name: str
    user_id: str
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    ended_at: Optional[datetime] = None
    agent_turns: int = 0
    tool_calls: int = 0
    errors: int = 0
    last_turn_duration_sec: Optional[float] = None
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_cost: float = 0.0

class PerformanceManager:
    def __init__(self, config: Optional[PerformanceConfig] = None):
        self.config = config or PerformanceConfig()
        self._states: Dict[str, ChatPerfState] = {}
        self._lock = asyncio.Lock()
        self._flush_task: Optional[asyncio.Task] = None
        self._persistence = AG2PersistenceManager()
        self._chat_coll = None
        self._agent_turn_duration = None
        self._workflow_duration = None
        self.initialized = False

    # --------------------------------------------------
    # Snapshot helpers (in-memory only, no DB dependency)
    # --------------------------------------------------
    async def snapshot_chat(self, chat_id: str) -> Optional[Dict[str, Any]]:
        """Return an in-memory snapshot for a single chat (no DB reads).

        Includes derived runtime duration (seconds) whether or not workflow ended.
        Returns None if chat_id not tracked yet.
        """
        async with self._lock:
            st = self._states.get(chat_id)
            if not st:
                return None
            ended_at = st.ended_at or datetime.now(timezone.utc)
            runtime_sec = (ended_at - st.started_at).total_seconds()
            return {
                "chat_id": st.chat_id,
                "app_id": st.app_id,
                "workflow_name": st.workflow_name,
                "user_id": st.user_id,
                "started_at": st.started_at.isoformat(),
                "ended_at": st.ended_at.isoformat() if st.ended_at else None,
                "runtime_sec": runtime_sec,
                "agent_turns": st.agent_turns,
                "tool_calls": st.tool_calls,
                "errors": st.errors,
                "last_turn_duration_sec": st.last_turn_duration_sec,
                "prompt_tokens": st.total_prompt_tokens,
                "completion_tokens": st.total_completion_tokens,
                "cost": st.total_cost,
            }

    async def snapshot_all(self) -> List[Dict[str, Any]]:
        async with self._lock:
            ids = list(self._states.keys())
        out: List[Dict[str, Any]] = []
        for cid in ids:
            snap = await self.snapshot_chat(cid)
            if snap:
                out.append(snap)
        return out

    async def aggregate(self) -> Dict[str, Any]:
        """Aggregate simple counters across all tracked chats for quick polling."""
        snaps = await self.snapshot_all()
        total_agent_turns = sum(s["agent_turns"] for s in snaps)
        total_tool_calls = sum(s["tool_calls"] for s in snaps)
        total_errors = sum(s["errors"] for s in snaps)
        total_prompt_tokens = sum(s["prompt_tokens"] for s in snaps)
        total_completion_tokens = sum(s["completion_tokens"] for s in snaps)
        total_cost = sum(s["cost"] for s in snaps)
        active_chats = sum(1 for s in snaps if s["ended_at"] is None)
        return {
            "active_chats": active_chats,
            "tracked_chats": len(snaps),
            "total_agent_turns": total_agent_turns,
            "total_tool_calls": total_tool_calls,
            "total_errors": total_errors,
            "total_prompt_tokens": total_prompt_tokens,
            "total_completion_tokens": total_completion_tokens,
            "total_cost": total_cost,
            "chats": snaps,
        }

    async def _get_coll(self):
        if self._chat_coll is None:
            await self._persistence.persistence._ensure_client()
            client_obj = self._persistence.persistence.client
            if client_obj is None:
                raise RuntimeError("Mongo client unavailable")
            # Updated collection name
            self._chat_coll = client_obj["MozaiksAI"]["ChatSessions"]
        return self._chat_coll

    async def initialize(self):
        if self.initialized:
            logger.info("🔍 PERF_INIT: Performance manager already initialized, skipping")
            return
        
        logger.info("🔧 PERF_INIT: Starting performance manager initialization")

        self._agent_turn_duration = None
        self._workflow_duration = None

        if self.config.flush_interval_sec > 0:
            logger.info(f"🔧 PERF_INIT: Starting periodic flush task (interval={self.config.flush_interval_sec}s)")
            self._flush_task = asyncio.create_task(self._periodic_flush())
        else:
            logger.info("🔧 PERF_INIT: Periodic flush disabled (interval=0)")
            
        self.initialized = True
        logger.info("✅ PERF_INIT: Performance manager initialization completed")

    async def _periodic_flush(self):
        while True:
            await asyncio.sleep(self.config.flush_interval_sec)
            async with self._lock:
                ids = list(self._states.keys())
            for cid in ids:
                await self.flush(cid)

    async def record_workflow_start(self, chat_id: str, app_id: str, workflow_name: str, user_id: str):
        async with self._lock:
            if chat_id not in self._states:
                self._states[chat_id] = ChatPerfState(
                    chat_id=chat_id,
                    app_id=app_id,
                    workflow_name=workflow_name,
                    user_id=user_id,
                )
        # Delegate creation to AG2PersistenceManager (single source of truth)
        try:
            await self._persistence.create_chat_session(chat_id, app_id, workflow_name, user_id)
        except Exception:
            # Fallback: direct minimal upsert if persistence manager path changes
            coll = await self._get_coll()
            now = datetime.now(timezone.utc)
            await coll.update_one(
                {"_id": chat_id, "app_id": app_id},
                {"$setOnInsert": {
                    "_id": chat_id,
                    "app_id": app_id,
                    "workflow_name": workflow_name,
                    "user_id": user_id,
                    "status": 0,
                    "created_at": now,
                    "last_updated_at": now,
                    "duration_sec": 0.0,
                    "messages": []
                }},
                upsert=True,
            )
        perf_logger.info("workflow_start", chat_id=chat_id, workflow=workflow_name)

    async def attach_trace_id(self, chat_id: str, trace_id: str):
        if not trace_id or trace_id == '0' * 32:
            # Ignore invalid trace ids; caller should provide fallback
            perf_logger.warning(f"ignored_zero_trace_id chat_id={chat_id}")
            return
        coll = await self._get_coll()
        await coll.update_one({"_id": chat_id}, {"$set": {"trace_id": trace_id}})

    async def record_agent_turn(self, chat_id: str, agent_name: str, duration_sec: float, model: Optional[str], prompt_tokens: int = 0, completion_tokens: int = 0, cost: float = 0.0):
        st_ref = self._states.get(chat_id)
        async with self._lock:
            st = self._states.get(chat_id)
            if not st:
                return
            st.agent_turns += 1
            st.last_turn_duration_sec = duration_sec
        perf_logger.info(
            "agent_turn",
            chat_id=chat_id,
            workflow=(st_ref.workflow_name if st_ref else None),
            app_id=(st_ref.app_id if st_ref else None),
            agent=agent_name,
            model=(model or "unknown"),
            prompt_tokens=int(prompt_tokens),
            completion_tokens=int(completion_tokens),
            total_tokens=int(prompt_tokens + completion_tokens),
            cost_usd=float(cost),
            duration_sec=float(duration_sec),
        )

        if prompt_tokens > 0 or completion_tokens > 0 or cost > 0:
            await self.record_usage_delta(
                chat_id=chat_id,
                agent_name=agent_name,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost=cost,
                duration_sec=duration_sec,
            )

    async def record_usage_delta(
        self,
        *,
        chat_id: str,
        agent_name: str,
        model: Optional[str],
        prompt_tokens: int,
        completion_tokens: int,
        cost: float,
        duration_sec: float = 0.0,
    ) -> None:
        st_ref = self._states.get(chat_id)
        if not st_ref:
            return
        async with self._lock:
            st = self._states.get(chat_id)
            if not st:
                return
            if prompt_tokens:
                st.total_prompt_tokens += int(prompt_tokens)
            if completion_tokens:
                st.total_completion_tokens += int(completion_tokens)
            if cost:
                st.total_cost += float(cost)
        perf_logger.debug(
            "usage_delta_recorded",
            chat_id=chat_id,
            agent=agent_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost=cost,
            duration_sec=float(duration_sec),
        )

    async def record_tool_call(self, chat_id: str, tool_name: str, success: bool):
        """Increment tool call counters.

        Duration/error attribution removed; upstream callers may choose their own
        measurement strategy if needed. We only count global success vs error totals.
        """
        async with self._lock:
            st = self._states.get(chat_id)
            if not st:
                return
            st.tool_calls += 1
            if not success:
                st.errors += 1
        perf_logger.debug("tool_call", chat_id=chat_id, tool=tool_name, success=success)

    async def record_workflow_end(self, chat_id: str, status: Union[int, str, WorkflowStatus]):
        async with self._lock:
            st = self._states.get(chat_id)
            if not st:
                return
            st.ended_at = datetime.now(timezone.utc)

        # record duration metric
        if self._workflow_duration:
            duration = (st.ended_at - st.started_at).total_seconds()  # type: ignore[arg-type]
            # store status as string in prframnce attributes to avoid serializer issues
            self._workflow_duration.record(duration, {"workflow_name": st.workflow_name, "status": str(status)})

        # Persist final status / end time prior to duration flush
        coll = await self._get_coll()

        # Normalize status to WorkflowStatus enum if passed as string
        # Normalize status to WorkflowStatus numeric
        if isinstance(status, WorkflowStatus):
            status_enum = status
        elif isinstance(status, int):
            status_enum = WorkflowStatus.COMPLETED if status == 1 else WorkflowStatus.IN_PROGRESS
        else:
            status_enum = WorkflowStatus.COMPLETED if str(status).lower() == "completed" else WorkflowStatus.IN_PROGRESS

        # Only update the DB status if it differs from current stored value to avoid duplicate writes
        update_doc: Dict[str, Any] = {}
        update_doc["ended_at"] = st.ended_at
        update_doc["last_updated_at"] = datetime.now(timezone.utc)

        try:
            existing = await coll.find_one({"_id": chat_id}, {"status": 1})
            existing_status = existing.get("status") if existing else None
        except Exception:
            existing_status = None

        if existing_status is None or int(existing_status) != int(status_enum):
            update_doc["status"] = int(status_enum)

        if update_doc:
            await coll.update_one({"_id": chat_id}, {"$set": update_doc})

        # Final flush to persist computed duration_sec
        await self.flush(chat_id)

        # Best-effort workflow summary refresh (does not block)
        if status_enum == WorkflowStatus.COMPLETED:
            try:  # pragma: no cover
                from mozaiksai.core.data.models import refresh_workflow_rollup_by_id
                summary_id = f"mon_{st.app_id}_{st.workflow_name}"
                asyncio.create_task(refresh_workflow_rollup_by_id(summary_id))
            except Exception:
                pass

    async def flush(self, chat_id: str):
        async with self._lock:
            st = self._states.get(chat_id)
            if not st:
                return
            runtime_sec = ((st.ended_at or datetime.now(timezone.utc)) - st.started_at).total_seconds()
        coll = await self._get_coll()
        await coll.update_one({"_id": chat_id}, {"$set": {"duration_sec": runtime_sec, "last_updated_at": datetime.now(timezone.utc)}})


class _PerformanceManagerSingleton:
    _instance: Optional[PerformanceManager] = None
    _lock = asyncio.Lock()
    
    @classmethod
    async def get_instance(cls) -> PerformanceManager:
        if cls._instance is None:
            logger.info("🔧 PERF_SINGLETON: Creating new PerformanceManager instance")
            async with cls._lock:
                if cls._instance is None:
                    logger.info("🔧 PERF_SINGLETON: Double-checked lock - creating instance")
                    cls._instance = PerformanceManager()
                    await cls._instance.initialize()
                    logger.info("✅ PERF_SINGLETON: PerformanceManager singleton created and initialized")
                else:
                    logger.info("🔍 PERF_SINGLETON: Instance already created by another coroutine")
        else:
            logger.info("🔍 PERF_SINGLETON: Using existing PerformanceManager instance")
        return cls._instance

async def get_performance_manager() -> PerformanceManager:
    return await _PerformanceManagerSingleton.get_instance()

