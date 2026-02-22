"""Async SQLAlchemy engine and session factory primitives."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from mozaiks.core.config import Settings, get_settings

_ENGINE: AsyncEngine | None = None
_SESSION_FACTORY: async_sessionmaker[AsyncSession] | None = None
_ENGINE_LOCK = asyncio.Lock()


def _resolve_database_url(
    settings: Settings | None,
    database_url: str | None,
) -> str:
    if database_url:
        return database_url
    resolved_settings = settings or get_settings()
    return resolved_settings.DATABASE_URL


async def get_async_engine(
    settings: Settings | None = None,
    *,
    database_url: str | None = None,
) -> AsyncEngine:
    """Return a lazily initialized async SQLAlchemy engine."""

    global _ENGINE, _SESSION_FACTORY

    if _ENGINE is not None:
        return _ENGINE

    async with _ENGINE_LOCK:
        if _ENGINE is not None:
            return _ENGINE

        resolved_settings = settings or get_settings()
        url = _resolve_database_url(resolved_settings, database_url)

        _ENGINE = create_async_engine(
            url,
            pool_pre_ping=True,
            pool_size=resolved_settings.DB_POOL_SIZE,
            max_overflow=resolved_settings.DB_POOL_MAX_OVERFLOW,
            pool_timeout=resolved_settings.DB_POOL_TIMEOUT,
            pool_recycle=resolved_settings.DB_POOL_RECYCLE,
            echo=resolved_settings.DB_ECHO,
        )
        _SESSION_FACTORY = async_sessionmaker(
            bind=_ENGINE,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )

    return _ENGINE


async def get_session_factory(
    settings: Settings | None = None,
    *,
    database_url: str | None = None,
) -> async_sessionmaker[AsyncSession]:
    """Return a lazily initialized async session factory."""

    global _SESSION_FACTORY
    if _SESSION_FACTORY is not None:
        return _SESSION_FACTORY

    await get_async_engine(settings=settings, database_url=database_url)
    if _SESSION_FACTORY is None:
        raise RuntimeError("Session factory failed to initialize.")
    return _SESSION_FACTORY


@asynccontextmanager
async def session_scope(
    settings: Settings | None = None,
    *,
    database_url: str | None = None,
    transactional: bool = True,
) -> AsyncIterator[AsyncSession]:
    """Provide an async session scope for DB work."""

    session_factory = await get_session_factory(settings=settings, database_url=database_url)
    async with session_factory() as session:
        if transactional:
            async with session.begin():
                yield session
            return

        try:
            yield session
        finally:
            await session.close()


async def get_async_session(
    settings: Settings | None = None,
    *,
    database_url: str | None = None,
) -> AsyncIterator[AsyncSession]:
    """Yield an async session suitable for dependency injection."""

    async with session_scope(
        settings=settings,
        database_url=database_url,
        transactional=False,
    ) as session:
        yield session


async def dispose_engine() -> None:
    """Dispose the global engine and reset session factory state."""

    global _ENGINE, _SESSION_FACTORY

    if _ENGINE is not None:
        await _ENGINE.dispose()
    _ENGINE = None
    _SESSION_FACTORY = None


__all__ = ["dispose_engine", "get_async_engine", "get_async_session", "get_session_factory", "session_scope"]
