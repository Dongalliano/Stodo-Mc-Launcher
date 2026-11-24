"""Installs Fabric mods from Modrinth for a given Minecraft version.

Each entry is a Modrinth URL or plain slug, optionally pinned by a
``/version/<version>`` URL path or trailing version:
    https://modrinth.com/mod/sodium
    https://modrinth.com/mod/lithium/version/0.14.3+mc26.3
    modmenu

Required dependencies are installed automatically unless explicitly listed.
Only needs the Python standard library.
"""

import hashlib
import json
import re
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import dataclass
from pathlib import Path

LOADER = "fabric"
API = "https://api.modrinth.com/v2"
USER_AGENT = "StodoMcLauncher-mod-installer/1.0"

URL_RE = re.compile(
    r"^https?://(?:www\.)?modrinth\.com/(?:mod|plugin)/"
    r"([^/\s?#]+)(?:/version/([^/\s?#]+))?(?:[?#].*)?$",
    re.IGNORECASE,
)
SLUG_RE = re.compile(r"^[A-Za-z0-9_\-]+$")
OPTIONAL_PREFIX_RE = re.compile(r"^([A-Z]+)(\d*)$")


@dataclass(frozen=True)
class ModEntry:
    project: str
    pinned_version: str | None
    optional_group: str | None
    addon_for: str | None


def parse_entries(lines):
    """Parse mod URLs, optional version pins, and optional/add-on group tags."""
    entries = []
    seen = set()

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        optional_group = None
        addon_for = None
        prefix_parts = line.split(None, 1)
        if len(prefix_parts) == 2:
            prefix = OPTIONAL_PREFIX_RE.fullmatch(prefix_parts[0])
            candidate_url = prefix_parts[1].split(None, 1)[0]
            if prefix and URL_RE.match(candidate_url):
                group, addon_number = prefix.groups()
                if addon_number:
                    addon_for = group
                else:
                    optional_group = group
                line = prefix_parts[1]

        parts = line.split(None, 1)
        target = parts[0]
        trailing_pin = parts[1].strip() if len(parts) > 1 else None

        m = URL_RE.match(target)
        if m:
            slug = m.group(1)
            url_pin = urllib.parse.unquote(m.group(2)) if m.group(2) else None
            if url_pin and trailing_pin and url_pin != trailing_pin:
                raise ValueError(f"Ungültiger Mod-Eintrag: {line}")
            pin = url_pin or trailing_pin
        elif SLUG_RE.match(target):
            slug = target
            pin = trailing_pin
        else:
            raise ValueError(f"Ungültiger Mod-Eintrag: {line}")

        slug = slug.lower()
        if slug in seen:
            continue
        seen.add(slug)
        entries.append(ModEntry(slug, pin, optional_group, addon_for))

    return entries


def filter_mod_entries(entries, disabled_groups=None):
    disabled_groups = set(disabled_groups or ())
    return [
        entry for entry in entries
        if entry.optional_group not in disabled_groups
        and entry.addon_for not in disabled_groups
    ]


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
        return next(
            (
                version for version in versions
                if version["version_number"] == pinned or version.get("id") == pinned
            ),
            None,
        )

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


def install_mods(
    mod_lines,
    mc_version,
    out_dir,
    with_deps=True,
    disabled_groups=None,
):
    """Install the mods into out_dir. Returns the list of mods that failed."""
    entries = filter_mod_entries(parse_entries(mod_lines), disabled_groups)
    if not entries:
        return []

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    dependencies = []
    listed_project_ids = set()
    done = set()
    failed = []

    for entry in entries:
        project = entry.project
        if project in done:
            continue
        done.add(project)

        try:
            version = find_version(project, mc_version, entry.pinned_version)
            if version is None:
                project_id = api_get(f"/project/{project}").get("id")
                if project_id:
                    listed_project_ids.add(project_id)
                failed.append(project)
                continue

            project_id = version.get("project_id")
            if project_id:
                listed_project_ids.add(project_id)

            download(version, out_dir)

            if with_deps:
                dependencies.extend(version.get("dependencies", []))

        except Exception:
            failed.append(project)

    queue = deque(
        dep["project_id"]
        for dep in dependencies
        if dep["dependency_type"] == "required"
        and dep.get("project_id")
        and dep["project_id"] not in listed_project_ids
    )

    while queue:
        project = queue.popleft()
        if project in done:
            continue
        done.add(project)

        try:
            version = find_version(project, mc_version)
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
                            queue.append(dep["project_id"])

        except Exception:
            failed.append(project)

    return failed