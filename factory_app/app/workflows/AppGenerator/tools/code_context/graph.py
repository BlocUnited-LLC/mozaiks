"""
Code Context Knowledge Graph (FalkorDB)

Stores extracted code context in a graph to enable semantic relationships
between files, symbols, and model dependencies.

This is optional and best-effort. If FalkorDB isn't available or configured,
all operations are no-ops.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, Iterable, Optional

logger = logging.getLogger(__name__)

try:
    from falkordb.asyncio import FalkorDB  # type: ignore
    FALKOR_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    FalkorDB = None  # type: ignore
    FALKOR_AVAILABLE = False


class GraphConfig:
    def __init__(
        self,
        host: str,
        port: int,
        password: Optional[str],
        graph_name: str,
    ) -> None:
        self.host = host
        self.port = port
        self.password = password
        self.graph_name = graph_name

    @classmethod
    def from_env(cls) -> "GraphConfig":
        return cls(
            host=str(os.getenv("MOZAIKS_FALKOR_HOST") or "localhost").strip(),
            port=int(os.getenv("MOZAIKS_FALKOR_PORT") or "6380"),
            password=(os.getenv("MOZAIKS_FALKOR_PASSWORD") or None),
            graph_name=str(os.getenv("MOZAIKS_FALKOR_GRAPH") or "code_context").strip(),
        )


class CodeContextGraph:
    def __init__(self, config: Optional[GraphConfig] = None) -> None:
        self._config = config or GraphConfig.from_env()
        self._db = None
        self._graph = None
        self._ready = False

    @property
    def enabled(self) -> bool:
        raw = os.getenv("MOZAIKS_CODE_GRAPH_ENABLED")
        if raw is None:
            return False
        return str(raw).strip().lower() in ("1", "true", "yes", "y", "on")

    @property
    def ready(self) -> bool:
        return self._ready

    async def initialize(self) -> None:
        if not self.enabled or not FALKOR_AVAILABLE:
            return
        try:
            self._db = FalkorDB(
                host=self._config.host,
                port=self._config.port,
                password=self._config.password,
            )
            self._graph = self._db.select_graph(self._config.graph_name)
            await self._graph.query("RETURN 1")
            await self._ensure_indexes()
            self._ready = True
            logger.info("Code context graph initialized (graph=%s)", self._config.graph_name)
        except Exception as exc:  # pragma: no cover
            logger.warning("Code context graph init failed: %s", exc)
            self._ready = False

    async def _ensure_indexes(self) -> None:
        if not self._graph:
            return
        statements = [
            "CREATE INDEX ON :App(id)",
            "CREATE INDEX ON :Workspace(id)",
            "CREATE INDEX ON :File(path)",
            "CREATE INDEX ON :Symbol(name)",
            "CREATE INDEX ON :Module(name)",
        ]
        for stmt in statements:
            try:
                await self._graph.query(stmt)
            except Exception:
                continue

    def enqueue_upsert(
        self,
        *,
        app_id: str,
        workspace_id: str,
        version_hash: str,
        extracted: Dict[str, Dict[str, Any]],
    ) -> None:
        if not self.enabled or not FALKOR_AVAILABLE:
            return

        async def _run() -> None:
            if not self._ready:
                await self.initialize()
            if not self._ready:
                return
            await self._upsert_contexts(app_id, workspace_id, version_hash, extracted)

        _schedule_async(_run())

    async def _upsert_contexts(
        self,
        app_id: str,
        workspace_id: str,
        version_hash: str,
        extracted: Dict[str, Dict[str, Any]],
    ) -> None:
        if not self._graph:
            return

        await self._graph.query(
            "MERGE (a:App {id: $app_id}) "
            "MERGE (w:Workspace {id: $workspace_id}) "
            "MERGE (a)-[:HAS_WORKSPACE]->(w)",
            {"app_id": str(app_id), "workspace_id": str(workspace_id)},
        )

        for path, ctx in extracted.items():
            language = str(ctx.get("language") or "").lower()
            ctx_type = str(ctx.get("context_type") or "unknown")
            await self._graph.query(
                "MERGE (f:File {path: $path}) "
                "SET f.language = $language, f.context_type = $context_type, f.version_hash = $version_hash "
                "WITH f "
                "MATCH (w:Workspace {id: $workspace_id}) "
                "MERGE (w)-[:HAS_FILE]->(f)",
                {
                    "path": str(path),
                    "language": language,
                    "context_type": ctx_type,
                    "version_hash": str(version_hash),
                    "workspace_id": str(workspace_id),
                },
            )

            for imp in _as_list(ctx.get("imports")):
                mod = str(imp.get("module") or imp.get("module_name") or imp)
                if not mod:
                    continue
                await self._graph.query(
                    "MERGE (m:Module {name: $name}) "
                    "WITH m "
                    "MATCH (f:File {path: $path}) "
                    "MERGE (f)-[:IMPORTS]->(m)",
                    {"name": mod, "path": str(path)},
                )

            for sym in _iter_symbols(ctx):
                await self._graph.query(
                    "MERGE (s:Symbol {name: $name}) "
                    "SET s.kind = $kind "
                    "WITH s "
                    "MATCH (f:File {path: $path}) "
                    "MERGE (f)-[:DECLARES]->(s)",
                    {"name": sym["name"], "kind": sym["kind"], "path": str(path)},
                )

            for rel in _iter_relationships(ctx):
                await self._graph.query(
                    "MERGE (a:Symbol {name: $source}) "
                    "MERGE (b:Symbol {name: $target}) "
                    "MERGE (a)-[:RELATES_TO {type: $type}]->(b)",
                    rel,
                )

    async def summarize_for_agent(
        self,
        *,
        app_id: str,
        workspace_id: str,
        context_types: Iterable[str],
        limit: int = 12,
    ) -> str:
        if not self._ready or not self._graph:
            return ""

        try:
            result = await self._graph.query(
                "MATCH (w:Workspace {id: $workspace_id})-[:HAS_FILE]->(f:File) "
                "WHERE f.context_type IN $types "
                "RETURN f.path AS path, f.context_type AS context_type "
                "LIMIT $limit",
                {
                    "workspace_id": str(workspace_id),
                    "types": list(context_types),
                    "limit": int(limit),
                },
            )
        except Exception:
            return ""

        records = getattr(result, "result_set", None) or []
        if not records:
            return ""

        lines = ["Graph summary (related files):"]
        for rec in records:
            try:
                path = rec[0]
                ctx = rec[1]
                lines.append(f"- {path} ({ctx})")
            except Exception:
                continue
        return "\n".join(lines)


def _schedule_async(coro: Any) -> None:
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(coro)
    except RuntimeError:
        try:
            asyncio.run(coro)
        except Exception:
            pass


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _iter_symbols(ctx: Dict[str, Any]) -> Iterable[Dict[str, str]]:
    for kind in ("classes", "functions", "symbols"):
        for item in _as_list(ctx.get(kind)):
            name = str(item.get("name") or item.get("identifier") or item)
            if name:
                yield {"name": name, "kind": kind.rstrip("s")}


def _iter_relationships(ctx: Dict[str, Any]) -> Iterable[Dict[str, str]]:
    for cls in _as_list(ctx.get("classes")):
        for field in _as_list(cls.get("fields")):
            target = str(field.get("type") or "").strip()
            rel = str(field.get("relationship") or "").strip()
            if target and rel and target[0].isupper():
                yield {"source": str(cls.get("name") or ""), "target": target, "type": rel}
