"""Artifact attachment helpers (shared runtime layer).

Responsibilities (shared runtime infrastructure):
  - Bundle attachment injection into event payloads
  - File iteration for bundled artifacts
  - Chat upload handling

These are runtime-level concerns that exist independently of any specific
workflow (consumed by all workflows that deal with file attachments).
"""

from __future__ import annotations

import base64
import logging
import os
from collections.abc import Iterator
from typing import Any

logger = logging.getLogger(__name__)


async def inject_bundle_attachments_into_payload(
    payload: dict[str, Any],
    *,
    bundle_dir: str | None = None,
    attachment_keys: list[str] | None = None,
) -> dict[str, Any]:
    """Inject file attachments from a bundle directory into *payload*.

    For each file discovered under *bundle_dir*, add a base64-encoded entry
    keyed by the filename under ``payload["attachments"]``.

    Parameters
    ----------
    payload:
        The event payload dict to mutate in place.
    bundle_dir:
        Filesystem path to the bundle directory.  When *None* or empty the
        payload is returned unchanged.
    attachment_keys:
        Optional whitelist of filenames to include.  When *None* all files
        found in *bundle_dir* are included.

    Returns
    -------
    dict
        The (possibly mutated) *payload*.
    """
    if not bundle_dir or not os.path.isdir(bundle_dir):
        return payload

    attachments: dict[str, str] = {}
    for filename in sorted(os.listdir(bundle_dir)):
        if attachment_keys is not None and filename not in attachment_keys:
            continue
        filepath = os.path.join(bundle_dir, filename)
        if not os.path.isfile(filepath):
            continue
        try:
            with open(filepath, "rb") as fh:
                data = fh.read()
            attachments[filename] = base64.b64encode(data).decode("ascii")
        except OSError as exc:
            logger.warning("Failed to read attachment %s: %s", filepath, exc)

    if attachments:
        payload.setdefault("attachments", {}).update(attachments)
    return payload


def iter_bundle_attachment_files(
    bundle_dir: str,
) -> Iterator[tuple[str, bytes]]:
    """Yield ``(filename, content_bytes)`` for each file in *bundle_dir*.

    Parameters
    ----------
    bundle_dir:
        Filesystem path to the bundle directory.

    Yields
    ------
    tuple[str, bytes]
        Pairs of ``(filename, raw_bytes)`` for every regular file in the
        directory, sorted by name.
    """
    if not os.path.isdir(bundle_dir):
        return

    for filename in sorted(os.listdir(bundle_dir)):
        filepath = os.path.join(bundle_dir, filename)
        if not os.path.isfile(filepath):
            continue
        try:
            with open(filepath, "rb") as fh:
                yield filename, fh.read()
        except OSError as exc:
            logger.warning("Skipping unreadable file %s: %s", filepath, exc)


async def handle_chat_upload(
    *,
    file_bytes: bytes,
    filename: str,
    chat_id: str | None = None,
    user_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Process a chat file upload and return an artifact reference dict.

    This is a minimal implementation that stores the upload metadata and
    returns a reference suitable for inclusion in a chat event payload.
    Full blob-storage integration (S3, Azure Blob, etc.) is expected to
    be provided by the consuming platform via a storage adapter.

    Returns
    -------
    dict
        ``{"artifact_id", "filename", "size", "content_type", ...}``
    """
    import mimetypes
    from uuid import uuid4

    content_type, _ = mimetypes.guess_type(filename)
    artifact_id = str(uuid4())

    return {
        "artifact_id": artifact_id,
        "filename": filename,
        "size": len(file_bytes),
        "content_type": content_type or "application/octet-stream",
        "chat_id": chat_id,
        "user_id": user_id,
        "metadata": metadata or {},
    }


__all__ = [
    "inject_bundle_attachments_into_payload",
    "iter_bundle_attachment_files",
    "handle_chat_upload",
]
