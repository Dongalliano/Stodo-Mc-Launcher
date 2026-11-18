# Builds the single-file GUI launcher into the "build" folder.
# Usage:  powershell -ExecutionPolicy Bypass -File .\run_build.ps1          (build only)
#         powershell -ExecutionPolicy Bypass -File .\run_build.ps1 -Zip     (build + StodoMcLauncher.zip)
param([switch]$Zip)

$ErrorActionPreference = "Stop"

$Root     = $PSScriptRoot
$BuildDir = Join-Path $Root "build"
$ExeName  = "StodoMcLauncher"
$GuiScript = Join-Path $Root "launcher_gui.py"
$JsDir    = Join-Path $Root "src\js"
$PyDir    = Join-Path $Root "src\py"
$ZipPath  = Join-Path $Root "$ExeName.zip"
$IconPng  = Join-Path $Root "launcher_icon.png"
$IconIcoSrc = Join-Path $Root "launcher_icon.ico"   # optional: a ready-made .ico is used as it is

# PyInstaller's temporary files go to a temp folder, so "build" only ever contains finished files
$WorkDir  = Join-Path ([System.IO.Path]::GetTempPath()) ("stodo-build-" + [guid]::NewGuid().ToString("N"))

# Use main.pyw if it exists, otherwise main.py
$MainScript = Join-Path $Root "main.pyw"
if (-not (Test-Path $MainScript)) { $MainScript = Join-Path $Root "main.py" }

function Run-Py {
    & py @args
    if ($LASTEXITCODE -ne 0) { throw "Command failed: py $($args -join ' ')" }
}

# ---- Checks ----
if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw "Python launcher 'py' not found. Install Python from python.org and tick 'Add to PATH'."
}

$required = @(
    $MainScript,
    $GuiScript,
    (Join-Path $JsDir "login.js"),
    (Join-Path $JsDir "package.json"),
    (Join-Path $JsDir "package-lock.json"),
    (Join-Path $PyDir "install_mods.py")
)
foreach ($f in $required) {
    if (-not (Test-Path $f)) { throw "Missing file: $f" }
}

# The exe needs the exe-ready version of the script (it looks for its files inside the exe)
if (-not (Select-String -Path $MainScript -Pattern "FROZEN" -SimpleMatch -Quiet)) {
    throw "$(Split-Path $MainScript -Leaf) is not the exe-ready version (no FROZEN handling). Update the script first."
}

if (Get-Process -Name $ExeName -ErrorAction SilentlyContinue) {
    throw "$ExeName.exe is running. Close it first, then build again."
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

        if (-not (Test-Path $Icon)) { throw "Icon conversion failed: $Icon was not created." }
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
        "--paths", $PyDir,
        "--add-data", "$(Join-Path $JsDir 'login.js');src/js",
        "--add-data", "$(Join-Path $JsDir 'package.json');src/js",
        "--add-data", "$(Join-Path $JsDir 'package-lock.json');src/js",
        "--add-data", "$MainScript;.",
        "--hidden-import", "install_mods",
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
        $GuiScript
    )
    Write-Host "Building $ExeName.exe from $(Split-Path $GuiScript -Leaf)..."
    Run-Py @pyArgs

    $exe = Join-Path $BuildDir "$ExeName.exe"
    if (-not (Test-Path $exe)) { throw "Build finished but $exe was not created." }

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
                Write-Host "WARNING: the exe may still contain the default PyInstaller icon." -ForegroundColor Yellow
                Write-Host "(This check assumes a green icon; ignore it if your icon is not green.)" -ForegroundColor Yellow
            }
        }
        catch {
            Write-Host "Could not verify the icon automatically (not critical)." -ForegroundColor Yellow
        }

        # ---- Refresh the Windows icon cache so Explorer shows the new icon ----
        try {
            (Get-Item $exe).LastWriteTime = Get-Date
            & ie4uinit.exe -ClearIconCache 2>$null
            & ie4uinit.exe -show 2>$null
            Write-Host "Icon cache refreshed. If Explorer still shows the old icon, restart Explorer or sign out and in." -ForegroundColor Cyan
        }
        catch {
            Write-Host "Could not refresh the icon cache automatically (not critical)." -ForegroundColor Yellow
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