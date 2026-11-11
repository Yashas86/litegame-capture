# Build standalone LowLoadCapture v2.1 Windows Executable using PyInstaller & Python 3.12
$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host " Building LowLoadCapture v2.1 Executable  " -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

# 1. Install dependencies with Python 3.12
Write-Host "[1/4] Installing Python dependencies using Python 3.12..." -ForegroundColor Yellow
py -3.12 -m pip install -r requirements.txt

# Ensure static ffmpeg.exe is present in windows/
$ffmpegSrc = Join-Path $PSScriptRoot "ffmpeg.exe"
if (-not (Test-Path $ffmpegSrc)) {
    py -3.12 -c "import shutil, imageio_ffmpeg; shutil.copy(imageio_ffmpeg.get_ffmpeg_exe(), '$($ffmpegSrc.Replace('\', '/'))')"
}

# 2. Run PyInstaller with Python 3.12
Write-Host "[2/4] Running PyInstaller under Python 3.12..." -ForegroundColor Yellow
py -3.12 -m PyInstaller --noconfirm --clean --onedir --windowed `
    --name "LowLoadCapture" `
    --hidden-import "encodings" `
    --hidden-import "PIL._tkinter_finder" `
    --add-data "ffmpeg.exe;." `
    --add-data "gstreamer_runtime;gstreamer_runtime" `
    --collect-all "imageio_ffmpeg" `
    --collect-all "PIL" `
    gui.py

# 3. Copy Platform Tools if present
$distDir = Join-Path $PSScriptRoot "dist\LowLoadCapture"
$platformToolsSource = Join-Path $PSScriptRoot "..\platform-tools"
$platformToolsDest = Join-Path $distDir "platform-tools"

if (Test-Path $platformToolsSource) {
    Write-Host "[3/4] Bundling platform-tools into build output..." -ForegroundColor Yellow
    Copy-Item -Path $platformToolsSource -Destination $platformToolsDest -Recurse -Force
}

# 4. Copy GStreamer Runtime into dist output
$gstSource = Join-Path $PSScriptRoot "gstreamer_runtime"
$gstDest = Join-Path $distDir "gstreamer_runtime"

if (Test-Path $gstSource) {
    Write-Host "[4/4] Bundling gstreamer_runtime into build output..." -ForegroundColor Yellow
    Copy-Item -Path $gstSource -Destination $gstDest -Recurse -Force
}

# Also ensure ffmpeg.exe is copied directly into dist root
if (Test-Path $ffmpegSrc) {
    Copy-Item -Path $ffmpegSrc -Destination (Join-Path $distDir "ffmpeg.exe") -Force
}

Write-Host "==========================================" -ForegroundColor Green
Write-Host " BUILD SUCCESSFUL! " -ForegroundColor Green
Write-Host " Executable created at: $distDir\LowLoadCapture.exe" -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Green
