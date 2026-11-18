import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import minecraft_launcher_lib as mll

# ---- Configuration ----
SERVER = "donutsmp.net"
# -----------------------

SCRIPT_PATH = Path(__file__).parent
SRC_PATH = SCRIPT_PATH / "src"
JS_PATH = SRC_PATH / "js"
LOGIN_JS = JS_PATH / "login.js"
MC_DIR = mll.utils.get_minecraft_directory()


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
            json_file = Path(MC_DIR) / "versions" / vid / f"{vid}.json"
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
        "quickPlayMultiplayer": SERVER,
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
        
    print("Installing Mods")

    cmd = mll.command.get_minecraft_command(version, MC_DIR, options)
    print("Starting Minecraft...")
    subprocess.run(cmd, cwd=MC_DIR)


if __name__ == "__main__":
    main()