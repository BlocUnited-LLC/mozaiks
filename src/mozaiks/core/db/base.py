"""SQLAlchemy declarative base and shared metadata configuration."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import DateTime, MetaData, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class KernelBase(DeclarativeBase):
    """Declarative base for kernel-owned SQLAlchemy models."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map: dict[type, Any] = {
        datetime: DateTime(timezone=True),
    }


class TimestampMixin:
    """Reusable created/updated timestamp columns."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class UUIDPrimaryKeyMixin:
    """Reusable UUID primary key column."""

    id: Mapped[Any] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)


metadata = KernelBase.metadata

__all__ = ["KernelBase", "NAMING_CONVENTION", "TimestampMixin", "UUIDPrimaryKeyMixin", "metadata"]
