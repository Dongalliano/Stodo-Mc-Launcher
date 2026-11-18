import json
import os
import re
import shutil
import struct
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path


SERVER_JSON_URL = (
    "https://raw.githubusercontent.com/"
    "Dongalliano/Stodo-Mc-Launcher/main/server.json"
)
EXECUTABLE_URL = (
    "https://raw.githubusercontent.com/"
    "Dongalliano/Stodo-Mc-Launcher/main/build/StodoMcLauncher.exe"
)
MAX_EXECUTABLE_SIZE = 256 * 1024 * 1024


class UpdateError(RuntimeError):
    pass


def _version_parts(version):
    if not isinstance(version, str) or not re.fullmatch(r"\d+(?:\.\d+)*", version):
        raise UpdateError("Die Launcher-Version auf dem Update-Server ist ungültig.")
    return tuple(int(part) for part in version.split("."))


def is_newer_version(remote_version, current_version):
    remote = _version_parts(remote_version)
    current = _version_parts(current_version)
    width = max(len(remote), len(current))
    remote += (0,) * (width - len(remote))
    current += (0,) * (width - len(current))
    return remote > current


def get_latest_version():
    request = urllib.request.Request(
        SERVER_JSON_URL,
        headers={"User-Agent": "StodoMcLauncher", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.loads(response.read(1024 * 1024).decode("utf-8"))
    except (OSError, urllib.error.URLError, TimeoutError, ValueError) as error:
        raise UpdateError("Die Launcher-Version konnte nicht geprüft werden.") from error

    if not isinstance(data, dict):
        raise UpdateError("Die Versionsdatei des Launchers ist ungültig.")
    version = data.get("version")
    _version_parts(version)
    return version


def _is_windows_executable(path):
    size = path.stat().st_size
    if size < 64 or size > MAX_EXECUTABLE_SIZE:
        return False
    with path.open("rb") as executable:
        if executable.read(2) != b"MZ":
            return False
        executable.seek(0x3C)
        offset_data = executable.read(4)
        if len(offset_data) != 4:
            return False
        pe_offset = struct.unpack("<I", offset_data)[0]
        if pe_offset > size - 4:
            return False
        executable.seek(pe_offset)
        return executable.read(4) == b"PE\0\0"


def download_update(executable_path):
    target = Path(executable_path)
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.stem}.update-",
            suffix=".exe",
            dir=target.parent,
        )
    except OSError as error:
        raise UpdateError("Im Launcher-Ordner kann kein Update gespeichert werden.") from error

    temporary_path = Path(temporary_name)
    download_complete = False
    try:
        request = urllib.request.Request(
            EXECUTABLE_URL,
            headers={"User-Agent": "StodoMcLauncher", "Accept": "application/octet-stream"},
        )
        with os.fdopen(descriptor, "wb") as destination:
            descriptor = -1
            with urllib.request.urlopen(request, timeout=60) as response:
                content_length = response.headers.get("Content-Length")
                if content_length and int(content_length) > MAX_EXECUTABLE_SIZE:
                    raise UpdateError("Die Launcher-Datei ist zu groß.")

                downloaded = 0
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    downloaded += len(chunk)
                    if downloaded > MAX_EXECUTABLE_SIZE:
                        raise UpdateError("Die Launcher-Datei ist zu groß.")
                    destination.write(chunk)

        if not _is_windows_executable(temporary_path):
            raise UpdateError("Die heruntergeladene Launcher-Datei ist ungültig.")
        download_complete = True
        return temporary_path
    except (OSError, urllib.error.URLError, TimeoutError, ValueError) as error:
        raise UpdateError("Das Launcher-Update konnte nicht heruntergeladen werden.") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if not download_complete and temporary_path.exists():
            temporary_path.unlink()


def prepare_update(current_version, executable_path, report_status):
    latest_version = get_latest_version()
    if not is_newer_version(latest_version, current_version):
        return None

    report_status(f"Lade Launcher-Version {latest_version} herunter ...")
    staged_path = download_update(executable_path)
    return latest_version, staged_path


_UPDATE_SCRIPT = r"""
param([string]$StagedPath, [string]$TargetPath)
$ErrorActionPreference = 'Stop'
$mutex = [System.Threading.Mutex]::new($false, 'Local\StodoMcLauncherUpdater')
$hasLock = $false
try {
    $hasLock = $mutex.WaitOne(0)
    if (-not $hasLock) { exit 0 }
    $processName = [IO.Path]::GetFileNameWithoutExtension($TargetPath)
    while (Get-Process | Where-Object { $_.ProcessName -eq $processName }) {
        Start-Sleep -Milliseconds 250
    }
    try {
        Move-Item -LiteralPath $StagedPath -Destination $TargetPath -Force
        Start-Process -FilePath $TargetPath -WorkingDirectory (Split-Path -Parent $TargetPath)
    }
    catch {
        Add-Type -AssemblyName System.Windows.Forms
        [System.Windows.Forms.MessageBox]::Show(
            'Das Launcher-Update konnte nicht installiert werden. Bitte prüfe die Schreibrechte des Launcher-Ordners.',
            'Stodo Mc Launcher'
        ) | Out-Null
        if (Test-Path -LiteralPath $TargetPath) {
            Start-Process -FilePath $TargetPath -WorkingDirectory (Split-Path -Parent $TargetPath)
        }
    }
}
finally {
    if ($hasLock) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
    Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
}
"""


def schedule_replacement(staged_path, executable_path):
    script_descriptor = None
    script_path = None
    launched = False
    try:
        script_descriptor, script_name = tempfile.mkstemp(
            prefix="stodo-launcher-update-",
            suffix=".ps1",
        )
        script_path = Path(script_name)
        with os.fdopen(script_descriptor, "w", encoding="utf-8-sig") as script:
            script_descriptor = None
            script.write(_UPDATE_SCRIPT)

        powershell = shutil.which("powershell.exe")
        if not powershell:
            raise UpdateError("PowerShell wurde nicht gefunden; das Update kann nicht installiert werden.")
        subprocess.Popen(
            [
                powershell,
                "-NoProfile",
                "-NonInteractive",
                "-WindowStyle",
                "Hidden",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script_path),
                str(staged_path),
                str(executable_path),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        launched = True
    except OSError as error:
        raise UpdateError("Der Launcher konnte das Update nicht vorbereiten.") from error
    finally:
        if script_descriptor is not None:
            os.close(script_descriptor)
        if not launched and script_path is not None and script_path.exists():
            script_path.unlink()
