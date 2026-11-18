import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

import minecraft_launcher_lib as mll

# ---- Configuration ----
SERVER_JSON_URL = "https://raw.githubusercontent.com/Dongalliano/Stodo-Mc-Launcher/main/server.json"
# -----------------------

SCRIPT_PATH = Path(__file__).parent
SRC_PATH = SCRIPT_PATH / "src"
JS_PATH = SRC_PATH / "js"
PY_PATH = SRC_PATH / "py"
LOGIN_JS = JS_PATH / "login.js"
MC_DIR = Path(mll.utils.get_minecraft_directory())

MODS_DIR = MC_DIR / "mods"
BACKUP_DIR = MC_DIR / "mods_backup_stodo"   # temporary home of your own mods
BACKUP_MODS = BACKUP_DIR / "mods"
MANIFEST = BACKUP_DIR / "manifest.json"

sys.path.insert(0, str(PY_PATH))
from install_mods import install_mods  # noqa: E402

# ---------- server.json (read straight from GitHub) ----------

def load_config():
    # Read server.json straight from GitHub (in memory, nothing is saved)
    try:
        req = urllib.request.Request(SERVER_JSON_URL, headers={"User-Agent": "StodoMcLauncher"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.load(resp)
    except Exception as e:
        sys.exit(f"Could not read server.json from GitHub: {e}")
    if not isinstance(data, dict):
        sys.exit("server.json on GitHub has an unexpected format.")
    return data


def valid_address(addr):
    # hostname or IPv4, optional :port. Rejects anything that could be read as a launch option.
    return isinstance(addr, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*(:\d{1,5})?", addr) is not None


def get_server(config):
    ip = config.get("ip")
    if not valid_address(ip):
        sys.exit("server.json on GitHub has no valid 'ip' entry.")
    print(f"Server from GitHub: {ip}")
    return ip


def get_mods(config):
    mods = config.get("mods", [])
    if not isinstance(mods, list):
        print("server.json: 'mods' is not a list, skipping mods.")
        return []
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
    if originals:
        print(f"Moved {len(originals)} of your own mods to: {BACKUP_MODS}")


def restore_mods():
    """Delete the mods installed by the launcher and move your own mods back.
    Returns True if everything is back in place."""
    if not MANIFEST.exists():
        return True

    try:
        originals = set(json.loads(MANIFEST.read_text(encoding="utf-8")).get("originals", []))
    except Exception:
        print(f"The mod backup list is unreadable. Nothing was touched, your mods are in: {BACKUP_MODS}")
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
        except OSError as e:
            print(f"Could not delete {item.name}: {e}")
            ok = False

    # 2. Move your own mods back
    for name in originals:
        src = BACKUP_MODS / name
        if not src.exists():
            continue
        try:
            shutil.move(str(src), str(MODS_DIR / name))
        except OSError as e:
            print(f"Could not restore {name}: {e}")
            ok = False

    if ok:
        shutil.rmtree(BACKUP_DIR, ignore_errors=True)
        print("Your own mods are back in place.")
    else:
        print(f"Some mods could not be restored. Close Minecraft and start the launcher again, or find them in: {BACKUP_MODS}")
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

    print("Node.js not found, installing it with winget...")
    if not shutil.which("winget"):
        sys.exit("winget is not available. Install Node.js (LTS) manually from https://nodejs.org and run this again.")

    subprocess.run([
        "winget", "install", "OpenJS.NodeJS.LTS",
        "--silent", "--accept-package-agreements", "--accept-source-agreements",
    ])

    node = find_node()
    if not node:
        sys.exit("Node.js installation did not finish. Install it manually from https://nodejs.org and run this again.")
    print("Node.js installed.")
    return node


def node_env(node):
    env = os.environ.copy()
    env["PATH"] = str(node.parent) + os.pathsep + env.get("PATH", "")
    return env


def ensure_packages(node, env):
    if (JS_PATH / "node_modules" / "prismarine-auth").exists():
        return
    print("Installing prismarine-auth...")
    npm = node.parent / "npm.cmd"
    npm_cmd = str(npm) if npm.exists() else (shutil.which("npm.cmd") or "npm.cmd")
    result = subprocess.run([npm_cmd, "install", "prismarine-auth"], cwd=JS_PATH, env=env)
    if result.returncode != 0:
        sys.exit("Installing prismarine-auth failed, see the message above.")


# ---------- Fabric ----------

def find_newest_fabric():
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
            "No Fabric version found in " + str(MC_DIR) + ".\n"
            "Install Fabric with the Fabric installer "
            "and start it once from the Minecraft Launcher."
        )
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
    sys.exit(f"Could not work out the Minecraft version of {fabric_id}.")


# ---------- Login ----------

def get_login(node, env):
    # stderr is not captured, so the sign-in code is shown in the console
    result = subprocess.run(
        [str(node), str(LOGIN_JS)],
        stdout=subprocess.PIPE, text=True, cwd=JS_PATH, env=env,
    )
    if result.returncode != 0:
        sys.exit("Login failed, see the message above.")

    lines = [l for l in result.stdout.splitlines() if l.strip()]
    try:
        return json.loads(lines[-1])
    except (IndexError, json.JSONDecodeError):
        # Don't print the raw output, it may contain the token
        sys.exit("Could not read the login result.")


# ---------- Main ----------

def main():
    if not LOGIN_JS.exists():
        sys.exit(f"{LOGIN_JS} not found.")

    # A previous run may have been killed before it could put your mods back
    if MANIFEST.exists():
        print("Found an unfinished mod swap from an earlier run, restoring your mods first...")
        if not restore_mods():
            sys.exit("Could not restore your mods. Close Minecraft and run the launcher again.")

    config = load_config()
    server = get_server(config)
    mods = get_mods(config)

    node = ensure_node()
    env = node_env(node)
    ensure_packages(node, env)

    version = find_newest_fabric()
    print(f"Using Fabric version: {version}")

    login = get_login(node, env)
    print(f"Signed in as {login['name']}")

    # Makes sure the base game, libraries and assets are present
    try:
        mll.install.install_minecraft_version(version, MC_DIR)
    except Exception as e:
        print("Install check skipped:", e)

    options = {
        "username": login["name"],
        "uuid": login["id"],
        "token": login["token"],
        "quickPlayMultiplayer": server,
        "gameDirectory": str(MC_DIR),
    }

    # Use the Java runtime Mojang specifies for this version
    try:
        info = mll.runtime.get_version_runtime_information(version, MC_DIR)
        if info:
            mll.runtime.install_jvm_runtime(info["name"], MC_DIR)
            options["executablePath"] = mll.runtime.get_executable_path(info["name"], MC_DIR)
    except Exception as e:
        print("Using system Java:", e)

    cmd = mll.command.get_minecraft_command(version, MC_DIR, options)

    swapped = False
    try:
        if mods:
            # 1. Move your own mods away, 2. install the server mods
            stash_mods()
            swapped = True

            mc_version = get_game_version(version)
            print(f"Installing mods for Minecraft {mc_version}...")
            failed = install_mods(mods, mc_version, MODS_DIR)
            if failed:
                print("Warning: some mods could not be installed, starting anyway.")

        print("Starting Minecraft...")
        subprocess.run(cmd, cwd=MC_DIR)   # returns when the game is closed
    finally:
        # 3. Delete the installed mods, 4. move your own mods back
        if swapped:
            print("Minecraft closed, restoring your mods...")
            restore_mods()


if __name__ == "__main__":
    main()