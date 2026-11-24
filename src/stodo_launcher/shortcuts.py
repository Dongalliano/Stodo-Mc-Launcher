import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


APP_NAME = "Stodo Mc Launcher"
EXE_NAME = "StodoMcLauncher.exe"


class ShortcutError(RuntimeError):
    """Raised when installation or shortcut creation fails."""


_CREATE_SHORTCUTS_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$target = $env:STODO_LAUNCHER_SHORTCUT_TARGET
$workingDirectory = Split-Path -Parent $target
$desktop = [Environment]::GetFolderPath('DesktopDirectory')
$programs = [Environment]::GetFolderPath('Programs')
New-Item -ItemType Directory -Force -Path $desktop, $programs | Out-Null
$shell = New-Object -ComObject WScript.Shell
foreach ($shortcutPath in @(
    (Join-Path $desktop 'Stodo Mc Launcher.lnk'),
    (Join-Path $programs 'Stodo Mc Launcher.lnk')
)) {
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $target
    $shortcut.WorkingDirectory = $workingDirectory
    $shortcut.IconLocation = "$target,0"
    $shortcut.Save()
}
"""

_ELEVATED_COPY_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$arguments = '--install-copy "' + $env:STODO_LAUNCHER_INSTALL_TARGET + '"'
$process = Start-Process -FilePath $env:STODO_LAUNCHER_INSTALL_SOURCE `
    -ArgumentList $arguments -Verb RunAs -Wait -PassThru
exit $process.ExitCode
"""

_INSTALL_COMPLETION_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$source = $env:STODO_LAUNCHER_SOURCE_TO_DELETE
$target = $env:STODO_LAUNCHER_INSTALL_TARGET
$processId = [int]$env:STODO_LAUNCHER_SOURCE_PROCESS_ID
while (Get-Process -Id $processId -ErrorAction SilentlyContinue) {
    Start-Sleep -Milliseconds 250
}
$removalStatus = 'Die ursprüngliche Launcher-Datei wurde gelöscht.'
try {
    if (Test-Path -LiteralPath $source) {
        try {
            Remove-Item -LiteralPath $source -Force -ErrorAction Stop
        }
        catch {
            $sourceLiteral = "'" + $source.Replace("'", "''") + "'"
            $deleteCommand = '$ErrorActionPreference = ''Stop''; Remove-Item -LiteralPath ' +
                $sourceLiteral + ' -Force -ErrorAction Stop'
            $encodedCommand = [Convert]::ToBase64String(
                [Text.Encoding]::Unicode.GetBytes($deleteCommand)
            )
            $powershell = Join-Path $PSHOME 'powershell.exe'
            $process = Start-Process -FilePath $powershell `
                -ArgumentList "-NoProfile -NonInteractive -EncodedCommand $encodedCommand" `
                -Verb RunAs -Wait -PassThru -WindowStyle Hidden
            if ($process.ExitCode -ne 0) {
                throw 'Die Löschung mit Administratorrechten ist fehlgeschlagen.'
            }
        }
    }
}
catch {
    $removalStatus = "Die ursprüngliche Launcher-Datei konnte nicht gelöscht werden:`n" +
        $_.Exception.Message
}

Add-Type -AssemblyName System.Windows.Forms
$message = "Stodo Mc Launcher wurde hier installiert:`n" +
    (Split-Path -Parent $target) +
    "`n`nVerknüpfungen wurden auf dem Desktop und im Startmenü erstellt.`n" +
    $removalStatus
[System.Windows.Forms.MessageBox]::Show(
    $message,
    'Installation abgeschlossen',
    [System.Windows.Forms.MessageBoxButtons]::OK,
    [System.Windows.Forms.MessageBoxIcon]::Information
) | Out-Null

try {
    Start-Process -FilePath $target -WorkingDirectory (Split-Path -Parent $target)
}
catch {
    [System.Windows.Forms.MessageBox]::Show(
        "Der installierte Launcher konnte nicht gestartet werden:`n" + $_.Exception.Message,
        'Stodo Mc Launcher',
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Error
    ) | Out-Null
}
"""


def _run_powershell(script: str, environment: dict[str, str]) -> None:
    powershell = shutil.which("powershell.exe")
    if not powershell:
        raise ShortcutError(
            "Windows PowerShell wurde nicht gefunden. Die Installation kann nicht fortgesetzt werden."
        )

    try:
        result = subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            env=environment,
        )
    except OSError as error:
        raise ShortcutError(f"Die Installation konnte nicht fortgesetzt werden: {error}") from error

    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        message = "Die Installation konnte nicht abgeschlossen werden."
        if detail:
            message += f"\n{detail}"
        raise ShortcutError(message)


def installation_path() -> Path:
    program_files = os.environ.get("ProgramW6432") or os.environ.get("ProgramFiles")
    if not program_files:
        raise ShortcutError("Der Windows-Programmordner konnte nicht gefunden werden.")
    return Path(program_files) / APP_NAME / EXE_NAME


def _same_path(first: Path, second: Path) -> bool:
    return os.path.normcase(str(first.resolve())) == os.path.normcase(str(second.resolve()))


def _copy_executable(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        shutil.copy2(source, temporary_path)
        os.replace(temporary_path, target)
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def install_copy_cli(arguments: list[str]) -> int:
    try:
        target = installation_path()
        if len(arguments) != 1 or not _same_path(Path(arguments[0]), target):
            raise ShortcutError("Der angeforderte Installationspfad ist ungültig.")
        _copy_executable(Path(sys.executable), target)
    except (OSError, ShortcutError) as error:
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            0,
            str(error),
            APP_NAME,
            0x10,
        )
        return 1
    return 0


def create_shortcuts(target: Path) -> None:
    environment = os.environ.copy()
    environment["STODO_LAUNCHER_SHORTCUT_TARGET"] = str(target.resolve())
    _run_powershell(_CREATE_SHORTCUTS_SCRIPT, environment)


def _install_with_elevation(target: Path) -> None:
    environment = os.environ.copy()
    environment["STODO_LAUNCHER_INSTALL_SOURCE"] = str(Path(sys.executable).resolve())
    environment["STODO_LAUNCHER_INSTALL_TARGET"] = str(target.resolve())
    _run_powershell(_ELEVATED_COPY_SCRIPT, environment)


def schedule_install_completion(source: Path, installed_executable: Path) -> None:
    if _same_path(source, installed_executable):
        return

    powershell = shutil.which("powershell.exe")
    if not powershell:
        raise ShortcutError(
            "Windows PowerShell wurde nicht gefunden. Die ursprüngliche Launcher-Datei bleibt erhalten."
        )

    environment = os.environ.copy()
    environment["STODO_LAUNCHER_SOURCE_TO_DELETE"] = str(source.resolve())
    environment["STODO_LAUNCHER_INSTALL_TARGET"] = str(installed_executable.resolve())
    environment["STODO_LAUNCHER_SOURCE_PROCESS_ID"] = str(os.getpid())
    try:
        subprocess.Popen(
            [
                powershell,
                "-NoProfile",
                "-NonInteractive",
                "-WindowStyle",
                "Hidden",
                "-Command",
                _INSTALL_COMPLETION_SCRIPT,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            env=environment,
        )
    except OSError as error:
        raise ShortcutError(
            f"Der Installationsabschluss konnte nicht gestartet werden: {error}"
        ) from error


def prepare_installation() -> tuple[Path, bool] | None:
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return None

    target = installation_path()
    current_executable = Path(sys.executable)
    if _same_path(current_executable, target):
        create_shortcuts(target)
        return None

    newly_installed = not target.is_file()
    if newly_installed:
        _install_with_elevation(target)
    create_shortcuts(target)
    return target, newly_installed
