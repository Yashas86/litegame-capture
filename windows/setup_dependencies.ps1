# Helper script for source users to download required Windows runtime dependencies
# without committing large binaries into Git.
$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot
$repoRoot = (Resolve-Path "..").Path

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host " LiteGame Capture - Dependency Setup      " -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

# 1. Python dependencies
Write-Host "[1/3] Installing Python packages..." -ForegroundColor Yellow
py -3.12 -m pip install -r requirements.txt

# 2. FFmpeg binary (via imageio-ffmpeg or system)
$ffmpegTarget = Join-Path $PSScriptRoot "ffmpeg.exe"
if (-not (Test-Path $ffmpegTarget)) {
    Write-Host "[2/3] Provisioning FFmpeg executable..." -ForegroundColor Yellow
    py -3.12 -c "import shutil, imageio_ffmpeg; shutil.copy(imageio_ffmpeg.get_ffmpeg_exe(), '$($ffmpegTarget.Replace('\', '/'))')"
} else {
    Write-Host "[2/3] FFmpeg already present." -ForegroundColor Green
}

# 3. Android SDK Platform-Tools (adb.exe)
$adbTarget = Join-Path $repoRoot "platform-tools\adb.exe"
if (-not (Test-Path $adbTarget)) {
    Write-Host "[3/3] Downloading official Android SDK Platform-Tools..." -ForegroundColor Yellow
    $zipPath = Join-Path $env:TEMP "platform-tools-latest-windows.zip"
    Invoke-WebRequest -Uri "https://dl.google.com/android/repository/platform-tools-latest-windows.zip" -OutFile $zipPath
    Expand-Archive -Path $zipPath -DestinationPath $repoRoot -Force
    Remove-Item $zipPath -Force
} else {
    Write-Host "[3/3] Android Platform-Tools (ADB) already present." -ForegroundColor Green
}

$gstLocal = Join-Path $PSScriptRoot "gstreamer_runtime\bin\gstreamer-1.0-0.dll"
$gstSys = "C:\gstreamer\1.0\msvc_x86_64\bin\gstreamer-1.0-0.dll"
if ((Test-Path $gstLocal) -or (Test-Path $gstSys)) {
    Write-Host "[OK] GStreamer runtime detected." -ForegroundColor Green
} else {
    Write-Host "[NOTE] GStreamer runtime not found. Install GStreamer 1.0 MSVC 64-bit runtime from https://gstreamer.freedesktop.org/download/ for native appsrc MP4 muxing (FFmpeg fallback still works)." -ForegroundColor Yellow
}

Write-Host "Setup complete. Launch the app with: py -3.12 windows\gui.py" -ForegroundColor Green
