# Stodo Mc Launcher

This README describes the files and shortcuts the launcher creates on Windows.
Paths that depend on the current Windows account are shown with environment
variables.

## Installed launcher

The packaged `StodoMcLauncher.exe` installs itself on first run to:

```text
%ProgramW6432%\Stodo Mc Launcher\StodoMcLauncher.exe
```

If `%ProgramW6432%` is unavailable, the installer uses `%ProgramFiles%`. On a
typical 64-bit Windows installation, the full path is:

```text
C:\Program Files\Stodo Mc Launcher\StodoMcLauncher.exe
```

The installed executable is the launcher path used by the shortcuts. On first
installation, a helper waits for the original process to exit, removes the
original executable, shows the completion popup, and starts the installed copy
after the popup is dismissed.
When running from the repository, the build output is instead
`build\StodoMcLauncher.exe`; a source-code run does not install or create
shortcuts.

## Shortcuts

The installer creates or updates these shortcuts:

| Shortcut | Windows folder |
| --- | --- |
| `Stodo Mc Launcher.lnk` | Desktop folder (`[Environment]::GetFolderPath("DesktopDirectory")`) |
| `Stodo Mc Launcher.lnk` | Start Menu Programs folder (`[Environment]::GetFolderPath("Programs")`) |

Windows may redirect these known folders (for example, to OneDrive), so their
actual paths can differ from the usual Desktop and Start Menu locations.

## Files under `%LOCALAPPDATA%`

When the packaged executable runs, the launcher creates this working folder:

```text
%LOCALAPPDATA%\StodoMcLauncher\
```

It contains:

| Path | Purpose |
| --- | --- |
| `src\js\login.js` | Bundled Microsoft/Minecraft sign-in script copied from the executable |
| `src\js\package.json` | Node.js package manifest copied from the executable |
| `src\js\package-lock.json` | Node.js package lock copied from the executable |
| `src\js\node_modules\` | `prismarine-auth` and its npm dependencies, installed on first use |
| `auth-cache\` | Authentication cache created by `prismarine-auth` |

The authentication cache may contain sensitive sign-in data. Do not share its
contents. The packaged launcher fetches `server.json` from GitHub at runtime;
when running from source, the launcher reads the repository's `server.json`
when that file is present.

If Node.js is missing, the launcher attempts to install Node.js LTS with
`winget`. Node.js is managed separately and may be installed under Program
Files or another location chosen by `winget`.

## Other runtime files

The following are outside `%LOCALAPPDATA%`:

| Path | Purpose and lifecycle |
| --- | --- |
| `%TEMP%\stodo-launcher-*.status` | Temporary launcher/backend status file; removed when the launcher closes or the backend exits |
| `%TEMP%\stodo-launcher-update-*.ps1` | Temporary update helper; removed by the helper after the update |
| `%TEMP%\_MEI<random>\` | PyInstaller's one-file extraction directory for bundled runtime files; normally removed when the packaged launcher exits |
| `.StodoMcLauncher.update-*.exe` beside the installed executable | Temporary downloaded update; moved over the installed executable when applied |
| `.StodoMcLauncher.exe.<random>.tmp` beside the installed executable | Temporary file used while copying the launcher during installation; removed after the copy |
| `%APPDATA%\.minecraft\` | Default Minecraft data directory. Minecraft/Fabric versions, libraries, assets, and the Java runtime are managed here through `minecraft-launcher-lib`. |
| `%APPDATA%\.minecraft\mods\` | Temporarily holds the server's downloaded mods while the game runs |
| `%APPDATA%\.minecraft\mods_backup_stodo\` | Temporary backup of the user's existing mods, including `manifest.json`; removed after restoration succeeds |

The Minecraft directory can differ if the system's Minecraft location is
customized. If the launcher is interrupted while swapping mods, the backup
folder is retained so the next run can attempt to restore the user's mods.
Node.js/npm may also maintain their own installation files and cache outside
the launcher's working folder (commonly `%LOCALAPPDATA%\npm-cache`).

## Running from source

When started with Python instead of the packaged executable, the launcher uses
the repository as its working application folder rather than
`%LOCALAPPDATA%\StodoMcLauncher`. The Node.js files are used under
`src\js\`, and the authentication cache is created at the repository root as
`auth-cache\`.
