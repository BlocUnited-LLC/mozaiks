"""Immutable audit trail for enterprise compliance.

Every workflow start, module action, and admin operation is recorded
with the authenticated user, timestamp, and a hash of the inputs.
Records are append-only; the collection has no update or delete path.
"""

from mozaiksai.core.audit.audit_logger import AuditLogger, AuditRecord, get_audit_logger

__all__ = ["AuditLogger", "AuditRecord", "get_audit_logger"]
