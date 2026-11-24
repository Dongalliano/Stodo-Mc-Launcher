"""Installs Fabric mods from Modrinth for a given Minecraft version.

Each entry is a Modrinth URL or plain slug, optionally followed by an exact version:
    https://modrinth.com/mod/sodium
    https://modrinth.com/mod/lithium 0.14.3+mc26.3
    modmenu

Required dependencies (e.g. Fabric API) are installed automatically unless
they are already listed explicitly.
Only needs the Python standard library.
"""

import hashlib
import json
import re
import urllib.parse
import urllib.request
from collections import deque
from pathlib import Path

LOADER = "fabric"
API = "https://api.modrinth.com/v2"
USER_AGENT = "StodoMcLauncher-mod-installer/1.0"

URL_RE = re.compile(r"^https?://(?:www\.)?modrinth\.com/(?:mod|plugin)/([^/\s?#]+)", re.IGNORECASE)
SLUG_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


def parse_entries(lines):
    """Return a list of (slug, pinned_version_or_None). '#' lines are ignored."""
    entries = []
    seen = set()

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        parts = line.split(None, 1)
        target = parts[0]
        pin = parts[1].strip() if len(parts) > 1 else None

        m = URL_RE.match(target)
        if m:
            slug = m.group(1)
        elif SLUG_RE.match(target):
            slug = target
        else:
            raise ValueError(f"Ungültiger Mod-Eintrag: {line}")

        slug = slug.lower()
        if slug in seen:
            continue
        seen.add(slug)
        entries.append((slug, pin))

    return entries


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

    # Use only the file name, never a path from the API response
    filename = Path(f["filename"]).name
    if not filename.lower().endswith(".jar"):
        raise RuntimeError(f"Unerwarteter Dateityp: {filename}")

    target = out_dir / filename
    expected = f["hashes"].get("sha512")

    if target.exists() and expected and \
            hashlib.sha512(target.read_bytes()).hexdigest() == expected:
        return

    req = urllib.request.Request(f["url"], headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = resp.read()

    if expected and hashlib.sha512(data).hexdigest() != expected:
        raise RuntimeError(f"Die Prüfsumme von {filename} stimmt nicht überein.")

    target.write_bytes(data)


def install_mods(mod_lines, mc_version, out_dir, with_deps=True):
    """Install the mods into out_dir. Returns the list of mods that failed."""
    entries = parse_entries(mod_lines)
    if not entries:
        return []

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    dependencies = []
    listed_project_ids = set()
    done = set()
    failed = []

    for project, pin in entries:
        if project in done:
            continue
        done.add(project)

        try:
            version = find_version(project, mc_version, pin)
            if version is None:
                failed.append(project)
                continue

            download(version, out_dir)

            project_id = version.get("project_id")
            if project_id:
                listed_project_ids.add(project_id)
                done.add(project_id)

            if with_deps:
                dependencies.extend(version.get("dependencies", []))
        except Exception:
            failed.append(project)

    queue = deque(
        (dep["project_id"], None)
        for dep in dependencies
        if dep["dependency_type"] == "required"
        and dep.get("project_id")
        and dep["project_id"] not in listed_project_ids
    )

    while queue:
        project, pin = queue.popleft()
        if project in done:
            continue
        done.add(project)

        try:
            version = find_version(project, mc_version, pin)
            if version is None:
                failed.append(project)
                continue

            download(version, out_dir)

            if with_deps:
                for dep in version.get("dependencies", []):
                    if dep["dependency_type"] == "required" and dep.get("project_id"):
                        if (
                            dep["project_id"] not in done
                            and dep["project_id"] not in listed_project_ids
                        ):
                            queue.append((dep["project_id"], None))

        except Exception:
            failed.append(project)

    return failed