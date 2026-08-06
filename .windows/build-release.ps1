[CmdletBinding()]
param(
    [string]$Version = "0.1.0",
    [switch]$IncludeGpu,
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Toolkit = Join-Path $Root "toolkit"
$Venv = Join-Path $Root ".venv-windows"
$Python = Join-Path $Venv "Scripts\python.exe"
$FfmpegDir = Join-Path $PSScriptRoot "bin\win-x64"
$GpuDir = Join-Path $FfmpegDir "libplacebo"
$BuildRoot = Join-Path $Root "build\pyinstaller-windows"
$RuntimeBinDir = Join-Path $BuildRoot "win-x64"
$DistRoot = Join-Path $Root "dist"
$AppDir = Join-Path $DistRoot "10-bit Converter"

foreach ($required in @(
    (Join-Path $FfmpegDir "ffmpeg.exe"),
    (Join-Path $FfmpegDir "ffprobe.exe"),
    (Join-Path $Toolkit "index.html"),
    (Join-Path $Toolkit "JZB.png"),
    (Join-Path $PSScriptRoot "JZB.ico")
)) {
    if (-not (Test-Path $required)) {
        throw "Windows release input is missing: $required`nSee .windows\README.md before building."
    }
}
if ($IncludeGpu) {
    foreach ($required in @((Join-Path $GpuDir "ffmpeg.exe"), (Join-Path $GpuDir "ffprobe.exe"))) {
        if (-not (Test-Path $required)) {
            throw "GPU release input is missing: $required`nA GPU build needs a self-contained libplacebo/Vulkan FFmpeg bundle under .windows\bin\win-x64\libplacebo\."
        }
    }
}

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw "Python Launcher (py.exe) is required. Install 64-bit Python 3.11, then rerun this script."
}
if (-not (Test-Path $Python)) {
    & py -3.11 -m venv $Venv
}

& $Python -m pip install --upgrade pip
& $Python -m pip install -r (Join-Path $PSScriptRoot "requirements.txt")

New-Item -ItemType Directory -Force $BuildRoot | Out-Null
Remove-Item -Recurse -Force $AppDir -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force $RuntimeBinDir -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $RuntimeBinDir | Out-Null
# Package the ordinary FFmpeg pair (and its sibling runtime DLLs) directly.
# Keep the GPU engine out of this directory so it has its own install/runtime
# path below.
Get-ChildItem -Force $FfmpegDir | Where-Object { $_.Name -ne "libplacebo" } |
    ForEach-Object { Copy-Item -Recurse -Force $_.FullName $RuntimeBinDir }

$PyInstallerArgs = @(
    "--noconfirm", "--clean", "--windowed",
    "--name", "10-bit Converter",
    "--icon", (Join-Path $PSScriptRoot "JZB.ico"),
    "--paths", $Toolkit,
    "--add-data", "$Toolkit\index.html;.",
    "--add-data", "$Toolkit\JZB.png;.",
    "--add-data", "$Toolkit\ui;ui",
    "--add-data", "$RuntimeBinDir;bin\win-x64",
    "--collect-all", "webview",
    "--collect-submodules", "engines",
    "--hidden-import", "webview.platforms.edgechromium",
    "--hidden-import", "tkinter",
    "--hidden-import", "tkinter.filedialog",
    "--distpath", $DistRoot,
    "--workpath", $BuildRoot,
    "--specpath", $BuildRoot,
    (Join-Path $Toolkit "server.py")
)

if ($IncludeGpu) {
    # server.py copies this optional engine to LocalAppData before it builds an
    # absolute Vulkan ICD manifest. The distributed installer contains folders,
    # not a user-facing zip archive.
    $PyInstallerArgs += @("--add-data", "$GpuDir;bin\win-x64\libplacebo")
}

& $Python -m PyInstaller @PyInstallerArgs

$SmokeArgs = @((Join-Path $PSScriptRoot "windows_release_smoke_test.py"))
if ($IncludeGpu) { $SmokeArgs += "--require-gpu" }
$SmokeArgs += $AppDir
& $Python @SmokeArgs

if (-not $SkipInstaller) {
    $Iscc = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if (-not $Iscc) {
        throw "The portable app was built and verified at '$AppDir', but Inno Setup 6 is required to create the shareable installer. Install it, then rerun this command."
    }
    & $Iscc.Source "/DMyAppVersion=$Version" (Join-Path $PSScriptRoot "10-bit-converter.iss")
}

Write-Host "Windows x64 release is ready in: $DistRoot"
