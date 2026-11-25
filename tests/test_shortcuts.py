import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from stodo_launcher import gui, shortcuts


class ShortcutTests(unittest.TestCase):
    def test_create_shortcuts_targets_the_installed_executable(self):
        with patch.object(shortcuts.shutil, "which", return_value="powershell.exe"), patch.object(
            shortcuts.subprocess, "run", return_value=subprocess.CompletedProcess([], 0),
        ) as run:
            shortcuts.create_shortcuts(Path("StodoMcLauncher.exe"))

        command = run.call_args.args[0]
        options = run.call_args.kwargs
        self.assertIn("DesktopDirectory", command[-1])
        self.assertIn("Programs", command[-1])
        self.assertIn("$shortcut.Save()", command[-1])
        self.assertEqual(
            options["env"]["STODO_LAUNCHER_SHORTCUT_TARGET"],
            str(Path("StodoMcLauncher.exe").resolve()),
        )
        self.assertEqual(
            options["creationflags"],
            getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def test_powershell_errors_are_reported(self):
        failure = subprocess.CompletedProcess(
            [], 1, stdout="", stderr="Access denied",
        )
        with patch.object(shortcuts.shutil, "which", return_value="powershell.exe"), patch.object(
            shortcuts.subprocess, "run", return_value=failure,
        ):
            with self.assertRaisesRegex(shortcuts.ShortcutError, "Access denied"):
                shortcuts.create_shortcuts(Path("StodoMcLauncher.exe"))

    def test_install_completion_deletes_before_popup_then_starts_launcher(self):
        source = Path("download") / shortcuts.EXE_NAME
        installed = Path("Program Files") / shortcuts.APP_NAME / shortcuts.EXE_NAME
        with patch.object(shortcuts.shutil, "which", return_value="powershell.exe"), patch.object(
            shortcuts.subprocess, "Popen",
        ) as popen:
            shortcuts.schedule_install_completion(source, installed)

        command = popen.call_args.args[0]
        options = popen.call_args.kwargs
        script = command[-1]
        self.assertIn("Get-Process -Id", script)
        self.assertIn("Remove-Item -LiteralPath $source", script)
        self.assertIn("-Verb RunAs", script)
        self.assertIn("-EncodedCommand", script)
        self.assertLess(
            script.index("while (Get-Process"),
            script.index("Remove-Item -LiteralPath $source"),
        )
        self.assertLess(
            script.index("Remove-Item -LiteralPath $source"),
            script.index("[System.Windows.Forms.MessageBox]::Show"),
        )
        self.assertLess(
            script.index("[System.Windows.Forms.MessageBox]::Show"),
            script.index("Start-Process -FilePath $target"),
        )
        self.assertEqual(
            options["env"]["STODO_LAUNCHER_SOURCE_TO_DELETE"],
            str(source.resolve()),
        )
        self.assertEqual(
            options["env"]["STODO_LAUNCHER_INSTALL_TARGET"],
            str(installed.resolve()),
        )
        self.assertEqual(
            options["env"]["STODO_LAUNCHER_SOURCE_PROCESS_ID"],
            str(shortcuts.os.getpid()),
        )

    def test_install_completion_is_not_scheduled_for_installed_executable(self):
        installed = Path("Program Files") / shortcuts.APP_NAME / shortcuts.EXE_NAME
        with patch.object(shortcuts.subprocess, "Popen") as popen:
            shortcuts.schedule_install_completion(installed, installed)

        popen.assert_not_called()

    def test_schedule_uninstall_targets_installed_launcher_and_local_data(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            executable = root / "Program Files" / shortcuts.APP_NAME / shortcuts.EXE_NAME
            local_data = root / "LocalAppData"
            with patch.object(shortcuts.os, "name", "nt"), patch.object(
                shortcuts.sys, "frozen", True, create=True,
            ), patch.object(
                shortcuts.sys, "executable", str(executable),
            ), patch.object(
                shortcuts, "installation_path", return_value=executable,
            ), patch.object(
                shortcuts.shutil, "which", return_value="powershell.exe",
            ), patch.object(shortcuts.subprocess, "Popen") as popen, patch.dict(
                shortcuts.os.environ, {"LOCALAPPDATA": str(local_data)},
            ):
                shortcuts.schedule_uninstall()

            command = popen.call_args.args[0]
            options = popen.call_args.kwargs
            script_path = Path(command[command.index("-File") + 1])
            try:
                self.assertTrue(script_path.read_bytes().startswith(b"\xef\xbb\xbf"))
                script = script_path.read_text(encoding="utf-8")
                self.assertIn("while (Get-Process -Id $LauncherProcessId", script)
                self.assertIn("Set-Location -LiteralPath ([IO.Path]::GetTempPath())", script)
                self.assertIn("Deinstallation abgeschlossen", script)
                self.assertIn("Minecraft-Dateien, Mods und Welten bleiben erhalten.", script)
                self.assertIn("Remove-Item -LiteralPath $target -Recurse -Force", script)
                self.assertIn("GetFolderPath('DesktopDirectory')", script)
                self.assertIn("GetFolderPath('Programs')", script)
                self.assertEqual(
                    command[command.index("-InstallDirectory") + 1],
                    str(executable.parent),
                )
                self.assertEqual(
                    command[command.index("-DataDirectory") + 1],
                    str(local_data / "StodoMcLauncher"),
                )
                self.assertEqual(
                    command[command.index("-LauncherProcessId") + 1],
                    str(shortcuts.os.getpid()),
                )
                self.assertEqual(options["cwd"], tempfile.gettempdir())
            finally:
                try:
                    script_path.unlink()
                except FileNotFoundError:
                    pass

    def test_schedule_uninstall_rejects_non_installed_executable(self):
        with patch.object(shortcuts.os, "name", "nt"), patch.object(
            shortcuts.sys, "frozen", True, create=True,
        ), patch.object(
            shortcuts.sys, "executable", "download/StodoMcLauncher.exe",
        ), patch.object(
            shortcuts, "installation_path",
            return_value=Path("Program Files") / shortcuts.APP_NAME / shortcuts.EXE_NAME,
        ), patch.object(shortcuts.shutil, "which", return_value="powershell.exe"), patch.object(
            shortcuts.subprocess, "Popen",
        ) as popen:
            with self.assertRaisesRegex(shortcuts.ShortcutError, "Installationsordner"):
                shortcuts.schedule_uninstall()

        popen.assert_not_called()

    def test_copy_executable_replaces_the_destination_completely(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "download" / shortcuts.EXE_NAME
            target = Path(temp_dir) / "Program Files" / shortcuts.APP_NAME / shortcuts.EXE_NAME
            source.parent.mkdir()
            source.write_bytes(b"new launcher")
            target.parent.mkdir(parents=True)
            target.write_bytes(b"old launcher")

            shortcuts._copy_executable(source, target)

            self.assertEqual(target.read_bytes(), b"new launcher")
            self.assertTrue(source.exists())
            self.assertEqual(list(target.parent.iterdir()), [target])

    def test_prepare_installation_elevates_then_creates_shortcuts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / shortcuts.APP_NAME / shortcuts.EXE_NAME
            source = Path(temp_dir) / "download" / shortcuts.EXE_NAME
            source.parent.mkdir()
            source.touch()
            with patch.object(shortcuts.os, "name", "nt"), patch.object(
                shortcuts.sys, "frozen", True, create=True,
            ), patch.object(
                shortcuts.sys, "executable", str(source),
            ), patch.object(
                shortcuts, "installation_path", return_value=target,
            ), patch.object(shortcuts, "_install_with_elevation") as install, patch.object(
                shortcuts, "create_shortcuts",
            ) as create:
                result = shortcuts.prepare_installation()

        self.assertEqual(result, (target, True))
        install.assert_called_once_with(target)
        create.assert_called_once_with(target)

    def test_prepare_installation_uses_existing_program_files_copy(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / shortcuts.APP_NAME / shortcuts.EXE_NAME
            target.parent.mkdir()
            target.touch()
            source = Path(temp_dir) / "download" / shortcuts.EXE_NAME
            source.parent.mkdir()
            source.touch()
            with patch.object(shortcuts.os, "name", "nt"), patch.object(
                shortcuts.sys, "frozen", True, create=True,
            ), patch.object(
                shortcuts.sys, "executable", str(source),
            ), patch.object(
                shortcuts, "installation_path", return_value=target,
            ), patch.object(shortcuts, "_install_with_elevation") as install, patch.object(
                shortcuts, "create_shortcuts",
            ) as create:
                result = shortcuts.prepare_installation()

        self.assertEqual(result, (target, False))
        install.assert_not_called()
        create.assert_called_once_with(target)

    def test_installed_launcher_only_refreshes_its_shortcuts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / shortcuts.APP_NAME / shortcuts.EXE_NAME
            with patch.object(shortcuts.os, "name", "nt"), patch.object(
                shortcuts.sys, "frozen", True, create=True,
            ), patch.object(
                shortcuts.sys, "executable", str(target),
            ), patch.object(
                shortcuts, "installation_path", return_value=target,
            ), patch.object(shortcuts, "create_shortcuts") as create:
                result = shortcuts.prepare_installation()

        self.assertIsNone(result)
        create.assert_called_once_with(target)

    def test_install_handoff_schedules_completion_before_closing_old_launcher(self):
        executable = Path("Program Files") / shortcuts.APP_NAME / shortcuts.EXE_NAME
        root = Mock()
        events = []
        root.destroy.side_effect = lambda: events.append("old launcher closes")
        with patch.object(gui.tk, "Tk", return_value=root):
            with patch.object(
                gui.shortcuts, "prepare_installation", return_value=(executable, True),
            ), patch.object(
                gui.shortcuts,
                "schedule_install_completion",
                side_effect=lambda *args, **kwargs: events.append("schedule completion"),
            ), patch.object(gui.subprocess, "Popen") as launch, patch.object(
                gui.messagebox, "showinfo",
            ) as showinfo, patch.object(
                gui.messagebox, "showerror",
            ), patch.object(gui, "StodoLauncher") as launcher:
                gui.run("1.0.4")

        self.assertEqual(events, ["schedule completion", "old launcher closes"])
        launch.assert_not_called()
        showinfo.assert_not_called()
        root.withdraw.assert_called_once_with()
        root.destroy.assert_called_once_with()
        launcher.assert_not_called()

    def test_uninstall_cancel_keeps_launcher_open(self):
        launcher = gui.StodoLauncher.__new__(gui.StodoLauncher)
        launcher.game_running = False
        launcher.game_status_error = False
        launcher.launching = False
        launcher.process = None
        launcher.root = Mock()
        launcher.play_button = Mock()
        launcher.uninstall_button = Mock()
        launcher.update_checking = False
        launcher.pending_update = None
        launcher.fabric_versions = ["fabric"]
        with patch.object(gui, "_minecraft_is_running", return_value=False), patch.object(
            gui.messagebox, "askyesno", return_value=False,
        ) as confirm, patch.object(
            gui.shortcuts, "schedule_uninstall",
        ) as schedule, patch.object(launcher, "_close") as close:
            launcher._uninstall_launcher()

        confirm.assert_called_once()
        schedule.assert_not_called()
        close.assert_not_called()

    def test_uninstall_confirmation_schedules_cleanup_and_closes_launcher(self):
        launcher = gui.StodoLauncher.__new__(gui.StodoLauncher)
        launcher.game_running = False
        launcher.game_status_error = False
        launcher.launching = False
        launcher.process = None
        launcher.root = Mock()
        launcher.play_button = Mock()
        launcher.uninstall_button = Mock()
        launcher.update_checking = False
        launcher.pending_update = None
        launcher.fabric_versions = ["fabric"]
        with patch.object(
            gui, "_minecraft_is_running", side_effect=[False, False],
        ), patch.object(
            gui.messagebox, "askyesno", return_value=True,
        ) as confirm, patch.object(
            gui.shortcuts, "schedule_uninstall",
        ) as schedule, patch.object(launcher, "_close") as close:
            launcher._uninstall_launcher()

        confirm.assert_called_once()
        schedule.assert_called_once_with()
        close.assert_called_once_with()

    def test_uninstall_is_blocked_while_game_launches(self):
        launcher = gui.StodoLauncher.__new__(gui.StodoLauncher)
        launcher.launching = True
        launcher.process = None
        launcher.root = Mock()
        with patch.object(gui.messagebox, "showwarning") as warning, patch.object(
            gui.messagebox, "askyesno",
        ) as confirm, patch.object(
            gui.shortcuts, "schedule_uninstall",
        ) as schedule:
            launcher._uninstall_launcher()

        warning.assert_called_once()
        confirm.assert_not_called()
        schedule.assert_not_called()

    def test_uninstall_is_blocked_while_minecraft_is_running(self):
        launcher = gui.StodoLauncher.__new__(gui.StodoLauncher)
        launcher.game_running = False
        launcher.game_status_error = False
        launcher.launching = False
        launcher.process = None
        launcher.root = Mock()
        launcher.play_button = Mock()
        launcher.uninstall_button = Mock()
        launcher.update_checking = False
        launcher.pending_update = None
        launcher.fabric_versions = ["fabric"]
        launcher.status_label = Mock()
        launcher.status_dot = Mock()
        launcher._set_status = Mock()
        with patch.object(gui, "_minecraft_is_running", return_value=True), patch.object(
            gui.messagebox, "showwarning",
        ) as warning, patch.object(
            gui.messagebox, "askyesno",
        ) as confirm, patch.object(
            gui.shortcuts, "schedule_uninstall",
        ) as schedule:
            launcher._uninstall_launcher()

        warning.assert_called_once()
        confirm.assert_not_called()
        schedule.assert_not_called()
        launcher.uninstall_button.configure.assert_called_with(
            state="disabled", cursor="arrow",
        )

    def test_normal_startup_shows_the_launcher_window(self):
        root = Mock()
        with patch.object(gui.tk, "Tk", return_value=root), patch.object(
            gui.shortcuts, "prepare_installation", return_value=None,
        ), patch.object(gui, "StodoLauncher") as launcher:
            gui.run("1.0.4")

        root.withdraw.assert_called_once_with()
        root.deiconify.assert_called_once_with()
        root.mainloop.assert_called_once_with()
        launcher.assert_called_once_with(root, "1.0.4")


if __name__ == "__main__":
    unittest.main()
