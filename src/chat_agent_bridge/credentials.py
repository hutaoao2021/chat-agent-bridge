"""Current-user Windows DPAPI storage, never plaintext credentials."""
import ctypes
from ctypes import wintypes
from pathlib import Path
from .desktop_settings import atomic_bytes


class Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def crypt(data, decrypt=False):
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source, output = Blob(len(data), buffer), Blob()
    api = ctypes.WinDLL('crypt32', use_last_error=True)
    fn = api.CryptUnprotectData if decrypt else api.CryptProtectData
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                   ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    fn.restype = wintypes.BOOL
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise OSError(ctypes.get_last_error(), 'Windows credential encryption failed')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel.LocalFree(output.data)


def protect(data):
    return crypt(data)


def unprotect(data):
    return crypt(data, True)


def save_key(secret, target):
    secret = secret.strip()
    if not secret.startswith('sk-') or len(secret) < 20:
        raise ValueError('请输入完整的 Tunnel API key')
    atomic_bytes(Path(target), protect(secret.encode()))


def read_key(target):
    return unprotect(Path(target).read_bytes()).decode()
