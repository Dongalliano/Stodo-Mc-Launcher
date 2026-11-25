import ctypes
import os
import unittest
import uuid

from stodo_launcher.single_instance import acquire_single_instance


@unittest.skipUnless(os.name == "nt", "Windows named mutexes are required")
class SingleInstanceTests(unittest.TestCase):
    def test_second_acquisition_is_rejected_until_first_handle_closes(self):
        mutex_name = "Local\\StodoMcLauncherTest-" + uuid.uuid4().hex
        first_handle = acquire_single_instance(mutex_name)
        self.assertIsNotNone(first_handle)

        try:
            self.assertIsNone(acquire_single_instance(mutex_name))
        finally:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            close_handle = kernel32.CloseHandle
            close_handle.argtypes = (ctypes.c_void_p,)
            close_handle.restype = ctypes.c_bool
            self.assertTrue(close_handle(first_handle))

        reopened_handle = acquire_single_instance(mutex_name)
        self.assertIsNotNone(reopened_handle)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (ctypes.c_void_p,)
        close_handle.restype = ctypes.c_bool
        self.assertTrue(close_handle(reopened_handle))


if __name__ == "__main__":
    unittest.main()
