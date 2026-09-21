"""Private local files: POSIX modes or a protected, current-user Windows DACL.

Use a trusted local parent directory. These permissions do not defend against
processes running as the same user, administrators, or filesystem path races.
"""

from __future__ import annotations

import os
import secrets
import stat
import tempfile
from pathlib import Path

from .errors import StateError

if os.name == "nt":
    import msvcrt

    import ntsecuritycon
    import pywintypes
    import win32api
    import win32con
    import win32file
    import win32security

    _OS_ERRORS = (OSError, pywintypes.error)
else:
    _OS_ERRORS = (OSError,)


def _windows_user():
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        return win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    finally:
        token.Close()


def _windows_attributes():
    sid = win32security.ConvertSidToStringSid(_windows_user())
    attributes = pywintypes.SECURITY_ATTRIBUTES()
    # P disables inherited grants. Full access is granted only to this user.
    attributes.SECURITY_DESCRIPTOR = (
        win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
            f"O:{sid}D:P(A;OICI;FA;;;{sid})", win32security.SDDL_REVISION_1
        )
    )
    return attributes


def _windows_check(path: Path, handle=None):
    getter = win32security.GetNamedSecurityInfo if handle is None else win32security.GetSecurityInfo
    sd = getter(
        str(path) if handle is None else handle,
        win32security.SE_FILE_OBJECT,
        win32security.OWNER_SECURITY_INFORMATION | win32security.DACL_SECURITY_INFORMATION,
    )
    user = _windows_user()
    dacl = sd.GetSecurityDescriptorDacl()
    if (
        sd.GetSecurityDescriptorOwner() != user
        or not sd.GetSecurityDescriptorControl()[0] & win32security.SE_DACL_PROTECTED
        or dacl is None
        or dacl.GetAceCount() != 1
    ):
        raise StateError("Private storage requires a protected Windows ACL for the current user.")
    (kind, flags), mask, sid = dacl.GetAce(0)
    if (
        kind != win32security.ACCESS_ALLOWED_ACE_TYPE
        or flags & win32security.INHERIT_ONLY_ACE
        or sid != user
        or mask != ntsecuritycon.FILE_ALL_ACCESS
    ):
        raise StateError("Private storage requires access only for the current Windows user.")


def _entry(path: Path):
    if os.name == "nt" and (":" in path.name or path.is_reserved()):
        raise StateError("Private storage requires an ordinary file or directory name.")
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(info.st_mode) or (
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise StateError("Private storage cannot use symbolic links or Windows reparse points.")
    return info


def check_private_file(path: Path, *, missing_ok: bool = False) -> bool:
    """Validate before reading; never silently repair an existing secret file."""
    try:
        info = _entry(path)
        if info is None and missing_ok:
            return False
        if info is None or not stat.S_ISREG(info.st_mode):
            raise StateError("Private storage requires a regular file.")
        if os.name == "nt":
            _windows_check(path)
        elif info.st_mode & 0o077:
            raise StateError("Private storage requires file mode 600 (chmod 600).")
    except _OS_ERRORS:
        raise StateError(
            "Cannot verify private file permissions. Use a trusted local state directory."
        ) from None
    return True


def private_directory(path: Path, *, secure_existing: bool = False):
    """Create a private directory; tighten an existing one only for explicit state storage."""
    info = _entry(path)
    if info is not None and not stat.S_ISDIR(info.st_mode):
        raise StateError("Private storage requires a directory.")
    if info is not None and not secure_existing:
        return
    try:
        if info is None:
            private_directory(path.parent)
        if os.name == "nt":
            attributes = _windows_attributes()
            if info is None:
                win32file.CreateDirectory(str(path), attributes)
            else:
                sd = attributes.SECURITY_DESCRIPTOR
                win32security.SetNamedSecurityInfo(
                    str(path),
                    win32security.SE_FILE_OBJECT,
                    win32security.OWNER_SECURITY_INFORMATION
                    | win32security.DACL_SECURITY_INFORMATION
                    | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
                    sd.GetSecurityDescriptorOwner(),
                    None,
                    sd.GetSecurityDescriptorDacl(),
                    None,
                )
            _windows_check(path)
        else:
            if info is None:
                path.mkdir(mode=0o700)
            else:
                path.chmod(0o700)
    except _OS_ERRORS:
        raise StateError(
            "Cannot protect the state directory. Use a local filesystem with private permissions."
        ) from None


def _private_temp(parent: Path):
    if os.name != "nt":
        fd, tmp = tempfile.mkstemp(dir=parent)
        try:
            os.fchmod(fd, 0o600)
        except BaseException:
            os.close(fd)
            os.unlink(tmp)
            raise
        return fd, tmp
    tmp = parent / (".ismu-" + secrets.token_hex(16) + ".tmp")
    handle = win32file.CreateFile(
        str(tmp),
        win32con.GENERIC_READ | win32con.GENERIC_WRITE,
        0,
        _windows_attributes(),
        win32con.CREATE_NEW,
        win32con.FILE_ATTRIBUTE_NORMAL,
        None,
    )
    try:
        _windows_check(tmp, handle)
        fd = msvcrt.open_osfhandle(int(handle), os.O_WRONLY | os.O_BINARY)
    except BaseException:
        handle.Close()
        tmp.unlink()
        raise
    handle.Detach()  # The file descriptor now owns the Windows handle.
    return fd, str(tmp)


def private_write(path: Path, data: bytes):
    """Atomically replace a file without exposing an intermediate plaintext file."""
    path = Path(path)
    info = _entry(path)
    if info is not None and not stat.S_ISREG(info.st_mode):
        raise StateError("Private storage requires a regular file.")
    private_directory(path.parent)
    fd, tmp = _private_temp(path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
