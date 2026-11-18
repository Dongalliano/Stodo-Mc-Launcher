#!/usr/bin/env python3
"""
Installs a specific Minecraft version of Fabric mods from Modrinth.
The mods are read from a text file.

Mod list format (e.g. "Client Mods.txt"): one mod per line.
Lines starting with '#' (headers/comments) and empty lines are ignored,
so the file may also contain nothing but links.

    # Performance #                         <- ignored
    https://modrinth.com/mod/sodium
    https://modrinth.com/mod/lithium 0.14.3+mc26.3   <- optional exact version pin
    modmenu                                 <- a plain slug works too

Usage:
    python install_mods.py                  (asks for Minecraft version and mod file)
    python install_mods.py "Client Mods.txt"
    python install_mods.py "Client Mods.txt" -v 26.3 -o ./custom_folder
    python install_mods.py "Client Mods.txt" --no-deps

Required dependencies of each mod (e.g. Fabric API) are installed automatically.
By default the mods are saved in a folder named after the input file
(e.g. "Client Mods.txt" -> ./Client Mods/). Use -o to choose another folder.

Only needs the Python standard library (3.8+).
"""

import argparse
import hashlib
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_MC_VERSION = "26.3"
DEFAULT_MOD_FILE = "mods.txt"
LOADER = "fabric"

API = "https://api.modrinth.com/v2"
USER_AGENT = "fabric-mod-installer/1.1 (personal script)"

URL_RE = re.compile(r"modrinth\.com/(?:mod|plugin)/([^/\s?#]+)", re.IGNORECASE)
SLUG_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


# ----------------------------------------------------------------------------
# Mod list parsing
# ----------------------------------------------------------------------------
def parse_mod_file(path):
    """Return a list of (slug, pinned_version_or_None). '#' lines are ignored."""
    entries = []
    seen = set()

    for raw in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line:
            continue

        # headers / comments such as "# Visual #"
        if line.startswith("#"):
            continue

        parts = line.split(None, 1)
        target = parts[0]
        pin = parts[1].strip() if len(parts) > 1 else None

        m = URL_RE.search(target)
        if m:
            slug = m.group(1)
        elif SLUG_RE.match(target):
            slug = target
        else:
            print(f"[WARN] Ignoring unrecognised line: {line}")
            continue

        slug = slug.lower()
        if slug in seen:
            continue
        seen.add(slug)
        entries.append((slug, pin))

    return entries


# ----------------------------------------------------------------------------
# Modrinth helpers
# ----------------------------------------------------------------------------
def api_get(path, params=None):
    url = f"{API}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def find_version(project, mc_version, pinned=None):
    versions = api_get(
        f"/project/{project}/version",
        {
            "loaders": json.dumps([LOADER]),
            "game_versions": json.dumps([mc_version]),
        },
    )
    if not versions:
        return None

    if pinned:
        return next((v for v in versions if v["version_number"] == pinned), None)

    # Prefer release > beta > alpha, newest first within each type
    for vtype in ("release", "beta", "alpha"):
        candidates = [v for v in versions if v["version_type"] == vtype]
        if candidates:
            candidates.sort(key=lambda v: v["date_published"], reverse=True)
            return candidates[0]
    return None


def download(version, out_dir):
    files = version["files"]
    f = next((x for x in files if x.get("primary")), files[0])
    target = out_dir / f["filename"]
    expected = f["hashes"].get("sha512")

    if target.exists() and expected and \
            hashlib.sha512(target.read_bytes()).hexdigest() == expected:
        return target, False  # already present and valid

    req = urllib.request.Request(f["url"], headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = resp.read()

    if expected and hashlib.sha512(data).hexdigest() != expected:
        raise RuntimeError(f"Hash mismatch for {f['filename']}")

    target.write_bytes(data)
    return target, True


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def ask(question, default):
    answer = input(f"{question} [{default}]: ").strip()
    return answer or default


def install_mods():
    parser = argparse.ArgumentParser(description="Install Fabric mods from Modrinth")
    parser.add_argument("modfile", nargs="?", default=None,
                        help="Text file with mod URLs (asked interactively if omitted)")
    parser.add_argument("-v", "--version", default=None,
                        help="Minecraft version (asked interactively if omitted)")
    parser.add_argument("-o", "--output", default=None,
                        help="Output folder (default: folder named after the mod file)")
    parser.add_argument("--no-deps", action="store_true",
                        help="Do not install required dependencies")
    args = parser.parse_args()

    if args.version is None:
        args.version = ask("Minecraft version", DEFAULT_MC_VERSION)
    if args.modfile is None:
        txt_files = sorted(p.name for p in Path.cwd().glob("*.txt"))
        if txt_files:
            print("Text files in this folder: " + ", ".join(txt_files))
        args.modfile = ask("Mod list file", DEFAULT_MOD_FILE).strip('"\'')

    if not Path(args.modfile).is_file():
        sys.exit(f"Mod list not found: {args.modfile}")

    entries = parse_mod_file(args.modfile)
    if not entries:
        sys.exit("No mods found in the file.")

    out_dir = Path(args.output) if args.output else Path(Path(args.modfile).stem)
    out_dir.mkdir(parents=True, exist_ok=True)

    queue = [(slug, pin, False) for slug, pin in entries]
    done = set()
    failed = []

    print(f"Minecraft {args.version} | loader: {LOADER} | "
          f"{len(entries)} mods from '{args.modfile}' -> {out_dir.resolve()}\n")

    while queue:
        project, pin, is_dep = queue.pop(0)
        if project in done:
            continue
        done.add(project)

        try:
            version = find_version(project, args.version, pin)
            if version is None:
                what = f"version {pin}" if pin else f"a {LOADER} build"
                print(f"[FAIL] {project}: no {what} for Minecraft {args.version}")
                failed.append(project)
                continue

            path, fresh = download(version, out_dir)
            status = "OK  " if fresh else "SKIP"
            label = f"{project} (dependency)" if is_dep else project
            print(f"[{status}] {label:<36} "
                  f"{version['version_number']}  ->  {path.name}")

            if not args.no_deps:
                for dep in version.get("dependencies", []):
                    if dep["dependency_type"] == "required" and dep.get("project_id"):
                        if dep["project_id"] not in done:
                            queue.append((dep["project_id"], None, True))

        except urllib.error.HTTPError as e:
            print(f"[FAIL] {project}: HTTP {e.code}")
            failed.append(project)
        except Exception as e:  # noqa: BLE001
            print(f"[FAIL] {project}: {e}")
            failed.append(project)

    print()
    if failed:
        print("Not installed: " + ", ".join(failed))
    else:
        print("All mods installed.")
