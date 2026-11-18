# Builds the single-file GUI launcher into the "build" folder.
# Usage:  powershell -ExecutionPolicy Bypass -File .\run_build.ps1          (build only)
#         powershell -ExecutionPolicy Bypass -File .\run_build.ps1 -Zip     (build + StodoMcLauncher.zip)
param([switch]$Zip)

$ErrorActionPreference = "Stop"

$Root     = $PSScriptRoot
$BuildDir = Join-Path $Root "build"
$ExeName  = "StodoMcLauncher"
$EntryScript = Join-Path $Root "main.py"
$SourceDir = Join-Path $Root "src"
$JsDir    = Join-Path $Root "src\js"
$BackgroundImage = Join-Path $Root "background.jpg"
$PackageDir = Join-Path $SourceDir "stodo_launcher"
$ZipPath  = Join-Path $Root "$ExeName.zip"
$IconPng  = Join-Path $Root "launcher_icon.png"
$IconIcoSrc = Join-Path $Root "launcher_icon.ico"   # optional: a ready-made .ico is used as it is

# PyInstaller's temporary files go to a temp folder, so "build" only ever contains finished files
$WorkDir  = Join-Path ([System.IO.Path]::GetTempPath()) ("stodo-build-" + [guid]::NewGuid().ToString("N"))

function Run-Py {
    & py @args
    if ($LASTEXITCODE -ne 0) { throw "Befehl fehlgeschlagen: py $($args -join ' ')" }
}

# ---- Checks ----
if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw "Der Python-Launcher 'py' wurde nicht gefunden. Installiere Python von python.org und aktiviere 'Add to PATH'."
}

$required = @(
    $EntryScript,
    (Join-Path $PackageDir "__init__.py"),
    (Join-Path $PackageDir "backend.py"),
    (Join-Path $PackageDir "gui.py"),
    (Join-Path $PackageDir "mod_installer.py"),
    (Join-Path $PackageDir "updater.py"),
    $BackgroundImage,
    (Join-Path $JsDir "login.js"),
    (Join-Path $JsDir "package.json"),
    (Join-Path $JsDir "package-lock.json")
)
foreach ($f in $required) {
    if (-not (Test-Path $f)) { throw "Datei fehlt: $f" }
}

if (Get-Process -Name $ExeName -ErrorAction SilentlyContinue) {
    throw "$ExeName.exe läuft bereits. Schließe den Launcher und starte den Build erneut."
}

# ---- Dependencies ----
Write-Host "Installing build dependencies..."
Run-Py -m pip install --quiet --upgrade pyinstaller minecraft-launcher-lib pillow pywin32-ctypes

# ---- Start with an empty build folder ----
New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null
Get-ChildItem -Path $BuildDir -Force | Remove-Item -Recurse -Force
Remove-Item -Force $ZipPath -ErrorAction SilentlyContinue

try {
    New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null

    # ---- Icon: launcher_icon.png (or launcher_icon.ico) in the project folder ----
    $Icon = $null
    if (Test-Path $IconIcoSrc) {
        $Icon = $IconIcoSrc
        Write-Host "Using icon: launcher_icon.ico"
    }
    elseif (Test-Path $IconPng) {
        Write-Host "Converting launcher_icon.png to an icon..."
        $Icon = Join-Path $WorkDir "launcher_icon.ico"
        $converter = Join-Path $WorkDir "make_icon.py"
        @'
import sys
from PIL import Image

src, dst = sys.argv[1], sys.argv[2]
im = Image.open(src).convert("RGBA")

# Make the picture square (transparent padding) so it is not stretched
side = max(im.size)
canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
canvas.paste(im, ((side - im.width) // 2, (side - im.height) // 2))
canvas = canvas.resize((256, 256), Image.LANCZOS)

canvas.save(dst, format="ICO", sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
'@ | Set-Content -Path $converter -Encoding ASCII
        Run-Py $converter $IconPng $Icon

        if (-not (Test-Path $Icon)) { throw "Die Symbol-Konvertierung ist fehlgeschlagen: $Icon wurde nicht erstellt." }
        Write-Host "Icon created: $Icon ($([math]::Round((Get-Item $Icon).Length / 1KB, 1)) KB)"
    }
    else {
        Write-Host "No launcher_icon.png found, building without a custom icon." -ForegroundColor Yellow
    }

    # ---- Shared PyInstaller options ----
    $commonPyArgs = @(
        "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onefile",
        "--distpath", $BuildDir,
        "--specpath", $WorkDir,
        "--paths", $SourceDir,
        "--add-data", "$(Join-Path $JsDir 'login.js');src/js",
        "--add-data", "$(Join-Path $JsDir 'package.json');src/js",
        "--add-data", "$(Join-Path $JsDir 'package-lock.json');src/js",
        "--add-data", "$BackgroundImage;.",
        "--hidden-import", "stodo_launcher.backend",
        "--hidden-import", "stodo_launcher.mod_installer",
        "--collect-all", "minecraft_launcher_lib"
    )
    if ($Icon) {
        $commonPyArgs += @("--icon", $Icon, "--add-data", "$Icon;.")
    }
    if (Test-Path $IconPng) {
        $commonPyArgs += @("--add-data", "$IconPng;.")
    }

    # ---- Build single-file GUI launcher ----
    $pyArgs = $commonPyArgs + @(
        "--workpath", $WorkDir,
        "--noconsole",
        "--name", $ExeName,
        $EntryScript
    )
    Write-Host "Building $ExeName.exe from $(Split-Path $EntryScript -Leaf)..."
    Run-Py @pyArgs

    $exe = Join-Path $BuildDir "$ExeName.exe"
    if (-not (Test-Path $exe)) { throw "Der Build wurde beendet, aber $exe wurde nicht erstellt." }

    # ---- Check that the icon is really inside the exe (reads the file itself, not Explorer's cache) ----
    if ($Icon) {
        try {
            Add-Type -AssemblyName System.Drawing
            $extracted = [System.Drawing.Icon]::ExtractAssociatedIcon($exe)
            $bmp = $extracted.ToBitmap()
            $px  = $bmp.GetPixel(3, 3)
            $bmp.Dispose()
            Write-Host "Icon inside the exe: corner pixel R=$($px.R) G=$($px.G) B=$($px.B) A=$($px.A)"
            if ($px.A -gt 200 -and $px.G -gt $px.R -and $px.G -gt $px.B) {
                Write-Host "OK: the custom icon is embedded in the exe." -ForegroundColor Green
            }
            else {
                Write-Host "WARNUNG: Die EXE enthält möglicherweise noch das PyInstaller-Standardsymbol." -ForegroundColor Yellow
                Write-Host "(Diese Prüfung erwartet ein grünes Symbol; bei anderen Farben kannst du die Warnung ignorieren.)" -ForegroundColor Yellow
            }
        }
        catch {
            Write-Host "Das Symbol konnte nicht automatisch überprüft werden (nicht kritisch)." -ForegroundColor Yellow
        }

        # ---- Refresh the Windows icon cache so Explorer shows the new icon ----
        try {
            (Get-Item $exe).LastWriteTime = Get-Date
            & ie4uinit.exe -ClearIconCache 2>$null
            & ie4uinit.exe -show 2>$null
            Write-Host "Icon cache refreshed. If Explorer still shows the old icon, restart Explorer or sign out and in." -ForegroundColor Cyan
        }
        catch {
            Write-Host "Der Symbolcache konnte nicht automatisch aktualisiert werden (nicht kritisch)." -ForegroundColor Yellow
        }
    }

    # ---- Optional zip (stored next to the build folder, not inside it) ----
    if ($Zip) {
        Compress-Archive -Path (Join-Path $BuildDir "*") -DestinationPath $ZipPath
        Write-Host "Created: $ZipPath"
    }

    Write-Host ""
    Get-ChildItem $BuildDir | Format-Table Name, @{ n = "MB"; e = { [math]::Round($_.Length / 1MB, 2) } } -AutoSize
    Write-Host "Done. Distribute $ExeName.exe from: $BuildDir" -ForegroundColor Green
}
finally {
    # Always remove the temporary files (PyInstaller work files and the converted icon)
    Remove-Item -Recurse -Force $WorkDir -ErrorAction SilentlyContinue
}