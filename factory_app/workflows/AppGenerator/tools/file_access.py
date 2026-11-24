"""Who may use a file, so a file that replaces another can keep it.

POSIX: the mode and, where permitted, the owner and group. Windows: the access
list. A new Windows file inherits its folder's access list, which can be wider
than the one set on the file it replaces.
"""

from __future__ import annotations

import os
import stat
import sys
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FileAccess:
    status: os.stat_result
    access_list: bytes | None = None


def read_access(path: Path) -> FileAccess:
    return FileAccess(status=path.stat(), access_list=_read_access_list(path))


def keep_access(path: Path, previous: FileAccess) -> None:
    """Give ``path`` the access another file had, or raise ``OSError``."""
    if previous.access_list is not None:
        # A file that already has it is left alone: not every filesystem stores access lists.
        if _read_access_list(path) != previous.access_list:
            _write_access_list(path, previous.access_list)
        return
    current, wanted = path.stat(), previous.status
    if sys.platform != "win32" and (current.st_uid, current.st_gid) != (wanted.st_uid, wanted.st_gid):
        with suppress(PermissionError):
            os.chown(path, wanted.st_uid, wanted.st_gid)
    os.chmod(path, stat.S_IMODE(wanted.st_mode))


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _DACL = 0x00000004
    _PROTECTED_DACL = 0x80000000
    _UNPROTECTED_DACL = 0x20000000
    _SE_DACL_PROTECTED = 0x1000
    _ERROR_INSUFFICIENT_BUFFER = 122

    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    _get_security = _advapi32.GetFileSecurityW
    _get_security.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
    ]
    _get_security.restype = wintypes.BOOL
    _set_security = _advapi32.SetFileSecurityW
    _set_security.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    _set_security.restype = wintypes.BOOL

    def _read_access_list(path: Path) -> bytes | None:
        """The file's self-relative security descriptor, holding only its access list."""
        size = wintypes.DWORD(0)
        buffer = None
        while not _get_security(str(path), _DACL, buffer, size.value, ctypes.byref(size)):
            error = ctypes.get_last_error()
            if error != _ERROR_INSUFFICIENT_BUFFER:
                raise ctypes.WinError(error)
            buffer = ctypes.create_string_buffer(size.value)
        return b"" if buffer is None else buffer.raw[: size.value]

    def _write_access_list(path: Path, descriptor: bytes) -> None:
        # SECURITY_DESCRIPTOR_RELATIVE: revision and padding bytes, then the control word.
        protected = int.from_bytes(descriptor[2:4], "little") & _SE_DACL_PROTECTED
        information = _DACL | (_PROTECTED_DACL if protected else _UNPROTECTED_DACL)
        if not _set_security(str(path), information, ctypes.create_string_buffer(descriptor, len(descriptor))):
            raise ctypes.WinError(ctypes.get_last_error())

else:

    def _read_access_list(path: Path) -> bytes | None:
        return None

    def _write_access_list(path: Path, descriptor: bytes) -> None:
        raise OSError(f"{path}: this platform has no Windows access lists")


__all__ = ["FileAccess", "keep_access", "read_access"]
