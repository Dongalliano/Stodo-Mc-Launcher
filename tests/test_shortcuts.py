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

    def test_source_removal_waits_for_the_current_process_to_exit(self):
        source = Path("download") / shortcuts.EXE_NAME
        installed = Path("Program Files") / shortcuts.APP_NAME / shortcuts.EXE_NAME
        with patch.object(shortcuts.shutil, "which", return_value="powershell.exe"), patch.object(
            shortcuts.subprocess, "Popen",
        ) as popen:
            shortcuts.schedule_source_removal(source, installed)

        command = popen.call_args.args[0]
        options = popen.call_args.kwargs
        self.assertIn("Get-Process -Id", command[-1])
        self.assertIn("Remove-Item -LiteralPath", command[-1])
        self.assertIn("-Verb RunAs", command[-1])
        self.assertIn("-EncodedCommand", command[-1])
        self.assertEqual(
            options["env"]["STODO_LAUNCHER_SOURCE_TO_DELETE"],
            str(source.resolve()),
        )
        self.assertEqual(
            options["env"]["STODO_LAUNCHER_SOURCE_PROCESS_ID"],
            str(shortcuts.os.getpid()),
        )

    def test_source_removal_never_deletes_the_installed_executable(self):
        installed = Path("Program Files") / shortcuts.APP_NAME / shortcuts.EXE_NAME
        with patch.object(shortcuts.subprocess, "Popen") as popen:
            shortcuts.schedule_source_removal(installed, installed)

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

    def test_success_popup_follows_installation_and_launcher_restart(self):
        executable = Path("Program Files") / shortcuts.APP_NAME / shortcuts.EXE_NAME
        root = Mock()
        events = []
        with patch.object(gui.tk, "Tk", return_value=root), patch.object(
            gui.shortcuts, "prepare_installation", return_value=(executable, True),
        ), patch.object(
            gui.subprocess, "Popen", side_effect=lambda *args, **kwargs: events.append("launch"),
        ), patch.object(
            gui.shortcuts,
            "schedule_source_removal",
            side_effect=lambda *args, **kwargs: events.append("delete"),
        ), patch.object(
            gui.messagebox,
            "showinfo",
            side_effect=lambda *args, **kwargs: events.append("popup"),
        ), patch.object(gui, "StodoLauncher") as launcher:
            showinfo = gui.messagebox.showinfo
            gui.run("1.0.4")

        self.assertEqual(events, ["launch", "delete", "popup"])
        self.assertIn("Installation abgeschlossen", showinfo.call_args.args[0])
        self.assertIn("Verknüpfungen", showinfo.call_args.args[1])
        self.assertIn("wird nach dem Schließen gelöscht", showinfo.call_args.args[1])
        root.withdraw.assert_called_once_with()
        root.destroy.assert_called_once_with()
        launcher.assert_not_called()

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
