"""Windows single-instance protection for the launcher GUI."""

import ctypes
import os

MUTEX_NAME = "Local\\StodoMcLauncher"
ERROR_ALREADY_EXISTS = 183


def acquire_single_instance(mutex_name: str = MUTEX_NAME) -> int | None:
    if os.name != "nt":
        return 1

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_mutex = kernel32.CreateMutexW
    create_mutex.argtypes = (ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p)
    create_mutex.restype = ctypes.c_void_p

    ctypes.set_last_error(0)
    handle = create_mutex(None, False, mutex_name)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())

    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (ctypes.c_void_p,)
        close_handle.restype = ctypes.c_bool
        if not close_handle(handle):
            raise ctypes.WinError(ctypes.get_last_error())
        return None

    return handle
