import argparse
import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import TypeGuard

import minecraft_launcher_lib as mll
from minecraft_launcher_lib.types import MinecraftOptions

from .mod_installer import install_mods

# ---------- Where things live ----------

FROZEN = getattr(sys, "frozen", False)   # True when running as the built .exe

if FROZEN:
    # Resources are bundled in the exe; everything we create goes to LOCALAPPDATA
    RES_DIR = Path(getattr(sys, "_MEIPASS"))
    APP_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "StodoMcLauncher"
else:
    RES_DIR = Path(__file__).resolve().parents[2]
    APP_DIR = RES_DIR

APP_DIR.mkdir(parents=True, exist_ok=True)

SRC_PATH = APP_DIR / "src"
JS_PATH = SRC_PATH / "js"
LOGIN_JS = JS_PATH / "login.js"
STATUS_FILE_ENV = "STODO_LAUNCHER_STATUS_FILE"
GAME_MUTEX_NAME = "Local\\StodoMcLauncherGame"


# ---------- No-console mode ----------

def _has_console():
    if os.name != "nt":
        return sys.stdout is not None
    return bool(ctypes.windll.kernel32.GetConsoleWindow())


HEADLESS = sys.stdout is None or sys.stderr is None or not _has_console()
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if HEADLESS else 0

if HEADLESS:
    _null_stream = open(os.devnull, "w", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = _null_stream


def run(cmd, **kwargs):
    """subprocess.run that also works without a console window."""
    if HEADLESS:
        kwargs.setdefault("stdin", subprocess.DEVNULL)
        kwargs.setdefault("stdout", sys.stdout)
        kwargs.setdefault("stderr", sys.stderr)
        kwargs.setdefault("creationflags", NO_WINDOW)
    return subprocess.run(cmd, **kwargs)


def notify_status(text):
    status_file = os.environ.get(STATUS_FILE_ENV)
    if not status_file:
        return
    path = Path(status_file)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(text, encoding="utf-8")
    os.replace(temporary_path, path)


def _create_game_mutex():
    if os.name != "nt":
        return None

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CreateMutexW.argtypes = [
        ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p,
    ]
    ctypes.set_last_error(0)
    handle = kernel32.CreateMutexW(None, False, GAME_MUTEX_NAME)
    error = ctypes.get_last_error()
    if not handle:
        raise ctypes.WinError(error)
    if error == 183:
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        kernel32.CloseHandle(handle)
        raise SystemExit("Minecraft läuft bereits.")
    return handle


def _close_game_mutex(handle):
    if handle:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        if not kernel32.CloseHandle(handle):
            raise ctypes.WinError(ctypes.get_last_error())


def start_game(cmd):
    if HEADLESS:
        return subprocess.Popen(
            cmd,
            cwd=MC_DIR,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=NO_WINDOW,
        )
    return subprocess.Popen(
        cmd,
        cwd=MC_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


_console_hidden = False


def _console_root():
    """Handle of the launcher's own console window (None if there is none)."""
    if os.name != "nt" or HEADLESS:
        return None
    kernel32 = ctypes.windll.kernel32
    user32 = ctypes.windll.user32
    kernel32.GetConsoleWindow.restype = ctypes.c_void_p
    user32.GetAncestor.restype = ctypes.c_void_p
    user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    hwnd = kernel32.GetConsoleWindow()
    if not hwnd:
        return None
    # In Windows Terminal the console window is a hidden helper,
    # so use the real top-level window that owns it
    return user32.GetAncestor(hwnd, 3) or hwnd   # GA_ROOTOWNER


def hide_console():
    """Hide the launcher's console window completely (no window, no taskbar button)."""
    global _console_hidden
    try:
        root = _console_root()
        if root:
            user32 = ctypes.windll.user32
            user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
            user32.ShowWindow(root, 0)   # SW_HIDE
            _console_hidden = True
    except Exception:
        pass


def show_console():
    """Bring the console window back (used when something goes wrong after it was hidden)."""
    global _console_hidden
    if not _console_hidden:
        return
    try:
        root = _console_root()
        if root:
            user32 = ctypes.windll.user32
            user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
            user32.ShowWindow(root, 9)   # SW_RESTORE
    except Exception:
        pass
    _console_hidden = False


def show_error(msg):
    try:
        ctypes.windll.user32.MessageBoxW(0, str(msg), "Stodo Mc Launcher", 0x10)
    except Exception:
        pass


def wait_for_enter():
    """Keep the console open until the user presses Enter."""
    try:
        print("\nZum Schließen bitte die Eingabetaste drücken...", flush=True)
        input()
    except (EOFError, OSError, RuntimeError):
        pass


def report_error(message):
    """Show an error in a dialog or in the console."""
    show_console()   # brings the window back if it was hidden
    if HEADLESS:
        show_error(message)
        return
    print(f"\nFEHLER: {message}", file=sys.stderr, flush=True)
    wait_for_enter()


class GermanArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        if message:
            raise SystemExit("Ungültige Startparameter. Bitte starte den Launcher erneut.")
        raise SystemExit("Ungültige Startparameter.")


_mutex = None


def acquire_single_instance():
    """Only one launcher at a time, otherwise two mod swaps would collide."""
    global _mutex
    if os.name != "nt":
        return True
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _mutex = kernel32.CreateMutexW(None, False, "Local\\StodoMcLauncher")
    return ctypes.get_last_error() != 183   # 183 = ERROR_ALREADY_EXISTS


def sync_resources():
    """In the exe: copy login.js and the package files out of it so Node can use them."""
    if not FROZEN:
        return
    JS_PATH.mkdir(parents=True, exist_ok=True)
    for name in ("login.js", "package.json", "package-lock.json"):
        src = RES_DIR / "src" / "js" / name
        if src.exists():
            shutil.copy2(src, JS_PATH / name)


# ---- Configuration ----
SERVER_JSON_URL = "https://raw.githubusercontent.com/Dongalliano/Stodo-Mc-Launcher/main/server.json"
# -----------------------

MC_DIR = Path(mll.utils.get_minecraft_directory())

MODS_DIR = MC_DIR / "mods"
BACKUP_DIR = MC_DIR / "mods_backup_stodo"   # temporary home of your own mods
BACKUP_MODS = BACKUP_DIR / "mods"
MANIFEST = BACKUP_DIR / "manifest.json"


# ---------- server.json (read straight from GitHub) ----------

def load_config() -> dict[str, object]:
    local_config = RES_DIR / "server.json"
    if not FROZEN and local_config.is_file():
        try:
            data = json.loads(local_config.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            sys.exit("Die lokale Serverkonfiguration konnte nicht geladen werden.")
    else:
        try:
            req = urllib.request.Request(SERVER_JSON_URL, headers={"User-Agent": "StodoMcLauncher"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.load(resp)
        except Exception:
            sys.exit("Die Serverkonfiguration konnte nicht geladen werden. Prüfe deine Internetverbindung.")
    if not isinstance(data, dict):
        sys.exit("Die Serverkonfiguration hat ein ungültiges Format.")
    return data


def valid_address(addr: object) -> TypeGuard[str]:
    # hostname or IPv4, optional :port. Rejects anything that could be read as a launch option.
    return isinstance(addr, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*(:\d{1,5})?", addr) is not None


def get_server(config: dict[str, object]) -> str:
    ip = config.get("ip")
    if not valid_address(ip):
        sys.exit("Die Serverkonfiguration enthält keine gültige Serveradresse.")
    return ip


def get_mods(config: dict[str, object]) -> list[str]:
    mods = config.get("mods", [])
    if not isinstance(mods, list):
        sys.exit("Die Mod-Liste in der Serverkonfiguration ist ungültig.")
    return [m for m in mods if isinstance(m, str)]


# ---------- Mod swap (stash your own mods, restore them afterwards) ----------

def _delete(path):
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def stash_mods():
    """Move everything in .minecraft/mods to the backup folder."""
    MODS_DIR.mkdir(parents=True, exist_ok=True)
    originals = [p.name for p in MODS_DIR.iterdir()]

    # The list is written BEFORE anything is moved, so an interrupted run can be recovered
    BACKUP_MODS.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps({"originals": originals}), encoding="utf-8")

    for name in originals:
        shutil.move(str(MODS_DIR / name), str(BACKUP_MODS / name))


def restore_mods():
    """Delete the mods installed by the launcher and move your own mods back.
    Returns True if everything is back in place."""
    if not MANIFEST.exists():
        return True

    try:
        originals = set(json.loads(MANIFEST.read_text(encoding="utf-8")).get("originals", []))
    except Exception:
        return False

    ok = True
    MODS_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Delete what the launcher installed.
    #    An item that is one of your originals and is NOT in the backup was never moved
    #    (interrupted run), so it is kept.
    for item in list(MODS_DIR.iterdir()):
        if item.name in originals and not (BACKUP_MODS / item.name).exists():
            continue
        try:
            _delete(item)
        except OSError:
            ok = False

    # 2. Move your own mods back
    for name in originals:
        src = BACKUP_MODS / name
        if not src.exists():
            continue
        try:
            shutil.move(str(src), str(MODS_DIR / name))
        except OSError:
            ok = False

    if ok:
        shutil.rmtree(BACKUP_DIR, ignore_errors=True)
    return ok


# ---------- Node.js ----------

def find_node():
    exe = shutil.which("node")
    if exe:
        return Path(exe)
    # Right after an install, the current process doesn't know the new PATH yet
    for var in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = os.environ.get(var)
        if base:
            p = Path(base) / "nodejs" / "node.exe"
            if p.exists():
                return p
    return None


def ensure_node():
    node = find_node()
    if node:
        return node

    if not shutil.which("winget"):
        sys.exit("winget ist nicht verfügbar. Installiere Node.js (LTS) manuell von https://nodejs.org und starte den Launcher erneut.")

    notify_status("Node.js wird installiert ...")
    run([
        "winget", "install", "OpenJS.NodeJS.LTS",
        "--silent", "--accept-package-agreements", "--accept-source-agreements",
    ])

    node = find_node()
    if not node:
        sys.exit("Die Node.js-Installation wurde nicht abgeschlossen. Installiere Node.js manuell von https://nodejs.org und starte den Launcher erneut.")
    return node


def node_env(node):
    env = os.environ.copy()
    env["PATH"] = str(node.parent) + os.pathsep + env.get("PATH", "")
    return env


def ensure_packages(node, env):
    if (JS_PATH / "node_modules" / "prismarine-auth").exists():
        notify_status("Anmeldemodul ist installiert")
        return
    notify_status("Anmeldemodul wird installiert ...")
    npm = node.parent / "npm.cmd"
    npm_cmd = str(npm) if npm.exists() else (shutil.which("npm.cmd") or "npm.cmd")
    result = run([npm_cmd, "install", "prismarine-auth"], cwd=JS_PATH, env=env)
    if result.returncode != 0:
        sys.exit("Das Anmeldemodul konnte nicht installiert werden.")
    notify_status("Anmeldemodul wurde installiert")


# ---------- Fabric ----------

def find_newest_fabric(selected_id=None):
    """Return the id of the most recently installed Fabric version."""
    candidates = []
    for v in mll.utils.get_installed_versions(MC_DIR):
        vid = v["id"]
        if vid.startswith("fabric-loader-"):
            json_file = MC_DIR / "versions" / vid / f"{vid}.json"
            if json_file.exists():
                candidates.append((json_file.stat().st_mtime, vid))
    if not candidates:
        sys.exit(
            "Es wurde keine Fabric-Version gefunden.\n"
            "Installiere Fabric und starte das Profil einmal im Minecraft Launcher."
        )
    if selected_id is not None:
        if selected_id not in {version_id for _, version_id in candidates}:
            sys.exit(f"Die ausgewählte Fabric-Version ist nicht installiert: {selected_id}")
        return selected_id
    candidates.sort()
    return candidates[-1][1]


def get_game_version(fabric_id):
    """Minecraft version a Fabric profile is built for, e.g. 26.3."""
    json_file = MC_DIR / "versions" / fabric_id / f"{fabric_id}.json"
    try:
        parent = json.loads(json_file.read_text(encoding="utf-8")).get("inheritsFrom")
        if parent:
            return parent
    except Exception:
        pass
    m = re.match(r"fabric-loader-\d+(?:\.\d+)*-(.+)$", fabric_id)
    if m:
        return m.group(1)
    sys.exit(f"Die Minecraft-Version des Fabric-Profils {fabric_id} konnte nicht ermittelt werden.")


# ---------- Login ----------

def get_login(node, env):
    result = run(
        [str(node), str(LOGIN_JS)],
        stdout=subprocess.PIPE, text=True, cwd=JS_PATH, env=env,
    )
    if result.returncode != 0:
        sys.exit("Die Anmeldung ist fehlgeschlagen. Prüfe dein Microsoft-Konto und versuche es erneut.")

    lines = [l for l in result.stdout.splitlines() if l.strip()]
    try:
        return json.loads(lines[-1])
    except (IndexError, json.JSONDecodeError):
        # Don't print the raw output, it may contain the token
        sys.exit("Das Ergebnis der Anmeldung konnte nicht verarbeitet werden.")


# ---------- Main ----------

def main(fabric_version=None, disabled_mod_groups=None):
    if not acquire_single_instance():
        sys.exit("Der Launcher ist bereits geöffnet.")

    notify_status("Serverkonfiguration wird geladen ...")
    sync_resources()
    if not LOGIN_JS.exists():
        sys.exit("Die Anmeldedateien des Launchers fehlen.")

    # A previous run may have been killed before it could put your mods back
    if MANIFEST.exists():
        if not restore_mods():
            sys.exit("Deine Mods konnten nicht wiederhergestellt werden. Schließe Minecraft und starte den Launcher erneut.")

    config = load_config()
    server = get_server(config)
    mods = get_mods(config)

    notify_status("Prüfe Node.js ...")
    node = ensure_node()
    notify_status("Node.js ist installiert")
    env = node_env(node)
    ensure_packages(node, env)

    notify_status("Fabric-Profil wird vorbereitet ...")
    fabric_id = find_newest_fabric(fabric_version)
    mc_version = get_game_version(fabric_id)

    notify_status("Microsoft-Anmeldung wird vorbereitet ...")
    login = get_login(node, env)

    # Makes sure the base game, libraries and assets are present
    notify_status("Minecraft-Dateien werden vorbereitet ...")
    mll.install.install_minecraft_version(fabric_id, MC_DIR)

    options: MinecraftOptions = {
        "username": login["name"],
        "uuid": login["id"],
        "token": login["token"],
        "quickPlayMultiplayer": server,
        "gameDirectory": str(MC_DIR),
    }

    # Use the Java runtime Mojang specifies for this version
    info = mll.runtime.get_version_runtime_information(fabric_id, MC_DIR)
    if info:
        mll.runtime.install_jvm_runtime(info["name"], MC_DIR)
        executable_path = mll.runtime.get_executable_path(info["name"], MC_DIR)
        if executable_path:
            options["executablePath"] = executable_path

    cmd = mll.command.get_minecraft_command(fabric_id, MC_DIR, options)

    swapped = False
    game_mutex = None
    try:
        if mods:
            # 1. Move your own mods away, 2. install the server mods
            stash_mods()
            swapped = True

            notify_status("Mods werden installiert ...")
            failed = install_mods(
                mods,
                mc_version,
                MODS_DIR,
                disabled_groups=disabled_mod_groups,
            )
            if failed:
                sys.exit("Mods konnten nicht installiert werden: " + ", ".join(failed))

        notify_status("Minecraft wird gestartet ...")
        game_mutex = _create_game_mutex()
        hide_console()
        game_process = start_game(cmd)
        notify_status("Minecraft läuft")
        game_process.wait()
    finally:
        try:
            # 3. Delete the installed mods, 4. move your own mods back
            if swapped:
                if not restore_mods():
                    sys.exit(
                        "Einige Mods konnten nicht wiederhergestellt werden. "
                        "Deine Sicherung liegt hier: " + str(BACKUP_MODS)
                    )
        finally:
            _close_game_mutex(game_mutex)


def cli(show_errors=True):
    try:
        parser = GermanArgumentParser()
        parser.add_argument("--fabric-version", help="Installiertes Fabric-Profil zum Starten")
        parser.add_argument(
            "--disabled-mod-groups",
            nargs="*",
            default=[],
            help="Optionale Mod-Gruppen, die beim Start ausgelassen werden",
        )
        arguments = parser.parse_args()
        main(arguments.fabric_version, set(arguments.disabled_mod_groups))
        return 0
    except SystemExit as e:
        if e.code not in (None, 0):
            if show_errors:
                message = e.code if isinstance(e.code, str) else "Der Launcher wurde mit einem Fehler beendet."
                report_error(message)
            return e.code if isinstance(e.code, int) else 1
        return 0
    except Exception:
        if show_errors:
            report_error(
                "Ein unerwarteter Fehler ist aufgetreten. Prüfe deine "
                "Internetverbindung und die Minecraft-Installation."
            )
        return 1
    finally:
        status_file = os.environ.get(STATUS_FILE_ENV)
        if status_file:
            try:
                Path(status_file).unlink()
            except FileNotFoundError:
                pass