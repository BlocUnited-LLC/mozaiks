"""Minimal process environment for imported source operations."""

from __future__ import annotations

import os
from pathlib import Path

_HOST_PROCESS_KEYS = ("PATH", "PATHEXT", "SystemRoot", "WINDIR", "COMSPEC")


def source_process_env(workspace_root: Path) -> dict[str, str]:
    """Keep host credentials and runtime configuration out of source processes."""
    env = {key: os.environ[key] for key in _HOST_PROCESS_KEYS if os.environ.get(key)}
    isolated_root = str(workspace_root.resolve())
    env.update(
        HOME=isolated_root,
        USERPROFILE=isolated_root,
        APPDATA=isolated_root,
        LOCALAPPDATA=isolated_root,
        XDG_CONFIG_HOME=isolated_root,
        TMP=isolated_root,
        TEMP=isolated_root,
        TMPDIR=isolated_root,
    )
    return env
