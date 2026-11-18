import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from stodo_launcher import gui, mod_installer


class LauncherTests(unittest.TestCase):
    def test_frozen_backend_uses_a_separate_pyinstaller_extraction(self):
        with patch.object(gui.sys, "frozen", True, create=True):
            environment = gui._backend_process_environment()

        self.assertEqual(environment and environment.get("PYINSTALLER_RESET_ENVIRONMENT"), "1")

        with patch.object(gui.sys, "frozen", False, create=True):
            self.assertIsNone(gui._backend_process_environment())

    def test_parse_entries_normalizes_slugs_and_removes_duplicates(self):
        entries = mod_installer.parse_entries([
            "https://modrinth.com/mod/Sodium",
            "sodium",
            "Lithium 0.14.3",
        ])

        self.assertEqual(entries, [("sodium", None), ("lithium", "0.14.3")])

    def test_invalid_entry_is_reported_as_an_error(self):
        with self.assertRaisesRegex(ValueError, "Ungültiger Mod-Eintrag"):
            mod_installer.parse_entries(["bad/mod/path"])

    def test_failed_mods_are_returned_without_console_output(self):
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as output_dir:
            with patch.object(mod_installer, "find_version", return_value=None):
                with redirect_stdout(output):
                    failed = mod_installer.install_mods(
                        ["clientsort"],
                        "1.21.1",
                        Path(output_dir),
                    )

        self.assertEqual(failed, ["clientsort"])
        self.assertEqual(output.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
