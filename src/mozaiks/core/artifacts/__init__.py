"""Artifact helpers for the shared runtime layer.

Re-exports from :mod:`mozaiks.core.artifacts.attachments`.
"""

from mozaiks.core.artifacts.attachments import (
    handle_chat_upload,
    inject_bundle_attachments_into_payload,
    iter_bundle_attachment_files,
)

__all__ = [
    "inject_bundle_attachments_into_payload",
    "iter_bundle_attachment_files",
    "handle_chat_upload",
]
