from pathlib import Path, PureWindowsPath
import os
import stat
from contextlib import contextmanager

def resolve_in_root(root: Path, relative: str, *, allow_missing_leaf=False) -> Path:
    base = root.resolve(strict=True)
    requested = Path(relative)
    windows = PureWindowsPath(relative)
    if not relative or requested.is_absolute() or windows.drive or windows.root or '..' in requested.parts or ':' in relative:
        raise PermissionError('path must be relative to the workspace')
    target = base / requested
    cursor = base
    for part in requested.parts:
        cursor = cursor / part
        if cursor.exists() or cursor.is_symlink():
            if cursor.is_symlink() or cursor.is_junction():
                raise PermissionError('links and junctions are not allowed')
    resolved = target.resolve(strict=not allow_missing_leaf)
    if resolved != base and base not in resolved.parents:
        raise PermissionError('path escapes workspace')
    if allow_missing_leaf and not resolved.parent.is_dir():
        raise FileNotFoundError('parent directory must exist')
    return resolved

def reject_link(path: Path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0):
        raise PermissionError('reparse point rejected')

@contextmanager
def guarded_path(root, relative, *, allow_missing_leaf=False):
    """On Windows lock ancestors against rename while accessing the leaf."""
    handles=[]
    try:
        target=resolve_in_root(root,relative,allow_missing_leaf=allow_missing_leaf)
        if os.name=='nt':
            import ctypes
            from ctypes import wintypes
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            create=kernel.CreateFileW
            create.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
            create.restype=wintypes.HANDLE
            close=kernel.CloseHandle; close.argtypes=[wintypes.HANDLE]; close.restype=wintypes.BOOL
            base=Path(root).resolve()
            directories=[base]
            current=base
            for part in target.parent.relative_to(base).parts:
                current=current/part; directories.append(current)
            for directory in directories:
                handle=create(str(directory),0,3,None,3,0x02000000|0x00200000,None)
                if handle==wintypes.HANDLE(-1).value: raise OSError(ctypes.get_last_error(),'cannot lock workspace directory')
                handles.append((close,handle)); reject_link(directory)
        target=resolve_in_root(root,relative,allow_missing_leaf=allow_missing_leaf)
        yield target
    finally:
        for close,handle in reversed(handles): close(handle)
