import io
import os
import tempfile
import unittest
import uuid
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from stodo_launcher import backend, gui, mod_installer


class LauncherTests(unittest.TestCase):
    def test_frozen_backend_uses_a_separate_pyinstaller_extraction(self):
        status_file = Path("launcher.status")
        with patch.object(gui.sys, "frozen", True, create=True):
            environment = gui._backend_process_environment(status_file)

        self.assertEqual(environment["PYINSTALLER_RESET_ENVIRONMENT"], "1")
        self.assertEqual(
            environment[gui.STATUS_FILE_ENV],
            str(status_file),
        )

        with patch.object(gui.sys, "frozen", False, create=True):
            environment = gui._backend_process_environment(status_file)

        self.assertNotIn("PYINSTALLER_RESET_ENVIRONMENT", environment)
        self.assertEqual(environment[gui.STATUS_FILE_ENV], str(status_file))

    def test_frozen_backend_uses_a_stable_working_directory(self):
        extraction_dir = Path("temporary-extraction")
        with patch.object(gui.sys, "frozen", True, create=True), patch.object(
            gui, "ROOT_DIR", extraction_dir,
        ):
            working_directory = gui._backend_working_directory()

        self.assertEqual(working_directory, Path.home())
        self.assertNotEqual(working_directory, extraction_dir)

        with patch.object(gui.sys, "frozen", False, create=True), patch.object(
            gui, "ROOT_DIR", extraction_dir,
        ):
            self.assertEqual(gui._backend_working_directory(), extraction_dir)

    def test_backend_status_updates_the_temporary_status_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            status_file = Path(temp_dir) / "launcher.status"
            status_file.touch()
            with patch.dict(
                backend.os.environ,
                {backend.STATUS_FILE_ENV: str(status_file)},
            ):
                backend.notify_status("Node.js ist installiert")

            self.assertEqual(status_file.read_text(encoding="utf-8"), "Node.js ist installiert")

    def test_play_button_is_gray_and_disabled_while_game_is_running(self):
        launcher = object.__new__(gui.StodoLauncher)
        launcher.game_running = True
        launcher.launching = False
        launcher.process = None
        launcher.game_status_error = False
        launcher.fabric_versions = ["fabric-loader-1.21.1"]
        launcher.play_button = Mock()

        launcher._sync_play_button()

        options = launcher.play_button.configure.call_args.kwargs
        self.assertEqual(options["state"], "disabled")
        self.assertEqual(options["text"], "SPIEL LÄUFT")
        self.assertEqual(options["bg"], gui.COLORS["disabled"])

    @unittest.skipUnless(os.name == "nt", "Windows mutex behavior")
    def test_game_mutex_is_detected_and_prevents_a_second_launch(self):
        mutex_name = "Local\\StodoMcLauncherTest_" + uuid.uuid4().hex
        with patch.object(backend, "GAME_MUTEX_NAME", mutex_name), patch.object(
            gui, "GAME_MUTEX_NAME", mutex_name,
        ):
            handle = backend._create_game_mutex()
            try:
                self.assertTrue(gui._minecraft_is_running())
                with self.assertRaises(SystemExit):
                    backend._create_game_mutex()
            finally:
                backend._close_game_mutex(handle)

            self.assertFalse(gui._minecraft_is_running())

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
