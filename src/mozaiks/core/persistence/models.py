from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mozaiks.core.db.base import KernelBase


def _json_type() -> JSON:
    return JSON().with_variant(JSONB(), "postgresql")


class RunRecord(KernelBase):
    __tablename__ = "runs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    last_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    workflow_name: Mapped[str] = mapped_column(String(255), nullable=False)
    workflow_version: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", _json_type(), nullable=False, default=dict)


class RunEventRecord(KernelBase):
    __tablename__ = "run_events"
    __table_args__ = (UniqueConstraint("run_id", "seq", name="uq_run_events_run_id_seq"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column("payload", _json_type(), nullable=False, default=dict)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column("metadata", _json_type(), nullable=True)


class RunCheckpointRecord(KernelBase):
    __tablename__ = "run_checkpoints"
    __table_args__ = (
        UniqueConstraint("run_id", "checkpoint_key", name="uq_run_checkpoints_run_key"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id", ondelete="CASCADE"), index=True)
    checkpoint_key: Mapped[str] = mapped_column(String(255), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column("payload", _json_type(), nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class RunArtifactRecord(KernelBase):
    __tablename__ = "run_artifacts"
    __table_args__ = (
        UniqueConstraint("uri", "checksum", name="uq_run_artifacts_uri_checksum"),
    )

    artifact_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id", ondelete="CASCADE"), index=True)
    seq: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    artifact_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    uri: Mapped[str] = mapped_column(String(2048), nullable=False, index=True)
    checksum: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(32), nullable=False, default="1.0.0", server_default="1.0.0")
    media_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content_base64: Mapped[str | None] = mapped_column(Text(), nullable=True)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column("metadata", _json_type(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
