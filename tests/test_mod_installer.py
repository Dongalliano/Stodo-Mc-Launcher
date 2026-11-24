import io
import os
import tempfile
import unittest
import uuid
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from stodo_launcher import backend, gui, mod_installer, updater


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

    def test_updater_compares_numeric_versions_and_skips_equal_versions(self):
        self.assertTrue(updater.is_newer_version("1.10.0", "1.9.9"))
        self.assertFalse(updater.is_newer_version("1.0.0", "1.0"))
        self.assertFalse(updater.is_newer_version("1.0.0", "1.0.1"))
        with self.assertRaises(updater.UpdateError):
            updater.is_newer_version("latest", "1.0.0")

        with patch.object(updater, "get_latest_version", return_value="1.0.0"), patch.object(
            updater, "download_update",
        ) as download:
            self.assertIsNone(updater.prepare_update("1.0", Path("StodoMcLauncher.exe"), Mock()))

        download.assert_not_called()
        status_updates = []
        staged_path = Path("StodoMcLauncher.update.exe")
        with patch.object(updater, "get_latest_version", return_value="1.0.1"), patch.object(
            updater, "download_update", return_value=staged_path,
        ) as download:
            result = updater.prepare_update(
                "1.0.0",
                Path("StodoMcLauncher.exe"),
                status_updates.append,
            )

        self.assertEqual(result, ("1.0.1", staged_path))
        download.assert_called_once_with(Path("StodoMcLauncher.exe"))
        self.assertEqual(status_updates, ["Lade Launcher-Version 1.0.1 herunter ..."])

    def test_updater_downloads_and_validates_a_windows_executable(self):
        executable = bytearray(128)
        executable[:2] = b"MZ"
        executable[0x3C:0x40] = (64).to_bytes(4, "little")
        executable[64:68] = b"PE\0\0"

        class FakeResponse(io.BytesIO):
            @property
            def headers(self):
                return {"Content-Length": str(len(executable))}

            def __enter__(self):
                return self

            def __exit__(self, *_):
                self.close()

        response = FakeResponse(executable)
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "StodoMcLauncher.exe"
            with patch.object(updater.urllib.request, "urlopen", return_value=response):
                staged = updater.download_update(target)
            try:
                self.assertTrue(updater._is_windows_executable(staged))
                self.assertEqual(staged.read_bytes(), executable)
                self.assertEqual(staged.parent, target.parent)
            finally:
                staged.unlink()

    def test_updater_removes_an_invalid_download(self):
        class FakeResponse(io.BytesIO):
            headers = {}

            def __enter__(self):
                return self

            def __exit__(self, *_):
                self.close()

        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "StodoMcLauncher.exe"
            with patch.object(
                updater.urllib.request,
                "urlopen",
                return_value=FakeResponse(b"<html>not an executable</html>"),
            ):
                with self.assertRaises(updater.UpdateError):
                    updater.download_update(target)

            self.assertEqual(list(Path(temp_dir).iterdir()), [])

    def test_updater_environment_clears_stale_pyinstaller_state(self):
        with patch.dict(
            os.environ,
            {
                "_PYI_APPLICATION_HOME_DIR": "C:\\old-extraction",
                "PYINSTALLER_STRICT_UNPACK_MODE": "1",
            },
        ):
            environment = updater._replacement_environment()

        self.assertNotIn("_PYI_APPLICATION_HOME_DIR", environment)
        self.assertNotIn("PYINSTALLER_STRICT_UNPACK_MODE", environment)
        self.assertEqual(environment["PYINSTALLER_RESET_ENVIRONMENT"], "1")

    def test_updater_schedules_a_unicode_powershell_helper(self):
        command = []
        launch_options = {}

        def capture_command(arguments, **kwargs):
            command.extend(arguments)
            launch_options.update(kwargs)

        with patch.object(updater.shutil, "which", return_value="powershell.exe"), patch.object(
            updater.subprocess, "Popen", side_effect=capture_command,
        ):
            updater.schedule_replacement(
                Path("StodoMcLauncher.update.exe"),
                Path("StodoMcLauncher.exe"),
            )

        script_path = Path(command[command.index("-File") + 1])
        try:
            script = script_path.read_text(encoding="utf-8-sig")
            self.assertIn("Move-Item -LiteralPath", script)
            self.assertIn("-Verb RunAs", script)
            self.assertIn("$env:PYINSTALLER_RESET_ENVIRONMENT = '1'", script)
            self.assertIn("Schreibrechte", script)
            self.assertEqual(
                launch_options["env"]["PYINSTALLER_RESET_ENVIRONMENT"],
                "1",
            )
        finally:
            script_path.unlink()

    @unittest.skipUnless(os.name == "nt", "Windows-only updater process behavior")
    def test_frozen_updater_clears_the_pyinstaller_dll_directory(self):
        command = []
        launch_options = {}

        def capture_command(arguments, **kwargs):
            command.extend(arguments)
            launch_options.update(kwargs)

        kernel32 = Mock()
        with patch.object(updater.sys, "frozen", True, create=True), patch.object(
            updater.sys, "_MEIPASS", "C:\\old-extraction", create=True,
        ), patch.object(
            updater.ctypes, "WinDLL", return_value=kernel32,
        ), patch.object(
            updater.shutil, "which", return_value="powershell.exe",
        ), patch.object(
            updater.subprocess, "Popen", side_effect=capture_command,
        ):
            updater.schedule_replacement(
                Path("StodoMcLauncher.update.exe"),
                Path("StodoMcLauncher.exe"),
            )

        kernel32.SetDllDirectoryW.assert_called_once_with(None)
        self.assertEqual(
            launch_options["env"]["PYINSTALLER_RESET_ENVIRONMENT"],
            "1",
        )
        Path(command[command.index("-File") + 1]).unlink()

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

    def test_footer_displays_launcher_version_in_the_bottom_right(self):
        launcher = object.__new__(gui.StodoLauncher)
        launcher.content = Mock()
        launcher.current_version = "1.0.2"
        launcher.state = "Bereit zum Spielen"
        status_label = Mock()
        version_label = Mock()
        with patch.object(gui.tk, "Frame", return_value=Mock()), patch.object(
            gui.tk, "Canvas", return_value=Mock(),
        ), patch.object(
            gui.tk, "Label", side_effect=[status_label, version_label],
        ) as labels:
            launcher._make_footer()

        self.assertEqual(labels.call_args_list[1].kwargs["text"], "Version 1.0.2")
        version_label.pack.assert_called_once_with(
            side="right",
            padx=(7, 25),
            pady=12,
        )

    def test_background_image_fills_canvas_and_stays_behind_copy(self):
        launcher = object.__new__(gui.StodoLauncher)
        launcher.root = Mock()
        launcher._background_image = gui.Image.new("RGB", (117, 50))
        canvas = Mock()
        event = Mock(widget=canvas, width=200, height=100)
        photo = Mock()

        with patch.object(gui.ImageTk, "PhotoImage", return_value=photo) as photo_image:
            launcher._draw_background(event)

        self.assertEqual(photo_image.call_args.args[0].size, (200, 100))
        canvas.delete.assert_called_once_with("background")
        canvas.create_image.assert_called_once_with(
            0, 0, image=photo, anchor="nw", tags="background",
        )
        canvas.tag_lower.assert_called_once_with("background")
        canvas.tag_raise.assert_called_once_with("copy")

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

        self.assertEqual(entries, ["sodium", "lithium"])

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

    def test_listed_mod_is_still_queued_as_a_dependency(self):
        versions = {
            "parent": {
                "project_id": "parent-id",
                "dependencies": [
                    {"dependency_type": "required", "project_id": "listed-id"},
                ],
            },
            "listed-dependency": {"project_id": "listed-id", "dependencies": []},
            "listed-id": {"project_id": "listed-id", "dependencies": []},
        }

        with tempfile.TemporaryDirectory() as output_dir, patch.object(
            mod_installer,
            "find_version",
            side_effect=lambda project, _mc_version: versions[project],
        ) as find_version, patch.object(mod_installer, "download"):
            failed = mod_installer.install_mods(
                ["parent", "listed-dependency 2.0"],
                "1.21.1",
                Path(output_dir),
            )

        self.assertEqual(failed, [])
        self.assertEqual(
            [call.args[0] for call in find_version.call_args_list],
            ["parent", "listed-dependency", "listed-id"],
        )
        self.assertTrue(all(len(call.args) == 2 for call in find_version.call_args_list))


if __name__ == "__main__":
    unittest.main()
