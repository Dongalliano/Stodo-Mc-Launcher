# build.ps1 - builds StodoMcLauncher.exe into the "build" folder next to this script
$ErrorActionPreference = "Stop"

$Root       = $PSScriptRoot
$BuildDir   = Join-Path $Root "build"
$WorkDir    = Join-Path $BuildDir "_work"      # temporary PyInstaller files, deleted afterwards
$ExeName    = "StodoMcLauncher"
$JsDir      = Join-Path $Root "src\js"
$PyDir      = Join-Path $Root "src\py"

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
    (Join-Path $JsDir "login.js"),
    (Join-Path $JsDir "package.json"),
    (Join-Path $JsDir "package-lock.json"),
    (Join-Path $PyDir "install_mods.py")
)
foreach ($f in $required) {
    if (-not (Test-Path $f)) { throw "Missing file: $f" }
}

if (Get-Process -Name $ExeName -ErrorAction SilentlyContinue) {
    throw "$ExeName.exe is running. Close it first, then build again."
}

# ---- Dependencies ----
Write-Host "Installing build dependencies..."
Run-Py -m pip install --quiet pyinstaller minecraft-launcher-lib

# ---- Clean old output ----
New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null
Remove-Item -Recurse -Force $WorkDir -ErrorAction SilentlyContinue
Remove-Item -Force (Join-Path $BuildDir "$ExeName.exe") -ErrorAction SilentlyContinue

# ---- Build ----
Write-Host "Building $ExeName.exe from $(Split-Path $MainScript -Leaf)..."
$pyArgs = @(
    "-m", "PyInstaller",
    "--noconfirm", "--onefile", "--noconsole",
    "--name", $ExeName,
    "--distpath", $BuildDir,
    "--workpath", $WorkDir,
    "--specpath", $WorkDir,
    "--paths", $PyDir,
    "--add-data", "$(Join-Path $JsDir 'login.js');src/js",
    "--add-data", "$(Join-Path $JsDir 'package.json');src/js",
    "--add-data", "$(Join-Path $JsDir 'package-lock.json');src/js",
    "--collect-all", "minecraft_launcher_lib",
    $MainScript
)
Run-Py @pyArgs

# ---- Clean up temporary files ----
Remove-Item -Recurse -Force $WorkDir -ErrorAction SilentlyContinue

$exe = Join-Path $BuildDir "$ExeName.exe"
if (-not (Test-Path $exe)) { throw "Build finished but $exe was not created." }

$sizeMb = [math]::Round((Get-Item $exe).Length / 1MB, 1)
Write-Host ""
Write-Host "Done: $exe ($sizeMb MB)" -ForegroundColor Green