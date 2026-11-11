import os
import shutil
import sys
from pathlib import Path


def get_clean_env():
    env = os.environ.copy()
    if "PATH" in env:
        paths = env["PATH"].split(os.pathsep)
        # Strip PyInstaller internal folders from PATH for subprocess execution
        clean_paths = [p for p in paths if "_internal" not in p]
        env["PATH"] = os.pathsep.join(clean_paths)
    return env


def get_subprocess_kwargs():
    kwargs = {"env": get_clean_env()}
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    return kwargs


def get_default_recordings_dir() -> Path:
    """
    Returns an application-managed user Videos directory without exposing personal repo paths.
    Default: %USERPROFILE%\\Videos\\LiteGameCapture
    """
    home = Path.home()
    videos = home / "Videos"
    target = (videos if videos.exists() else home) / "LiteGameCapture"
    try:
        target.mkdir(parents=True, exist_ok=True)
        return target
    except Exception:
        fallback = Path(__file__).parent.parent / "recordings"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def redact_path(path) -> str:
    """Redacts the Windows username / user home prefix for privacy-safe logs and UI."""
    try:
        p = Path(path).resolve()
        home = Path.home().resolve()
        rel = p.relative_to(home)
        return f"%USERPROFILE%\\{rel}" if sys.platform == "win32" else f"~/{rel.as_posix()}"
    except Exception:
        return Path(path).name or "LiteGameCapture"


def find_ffmpeg_executable():
    candidates = [
        Path(__file__).parent / "ffmpeg.exe",
        Path(__file__).parent.parent / "ffmpeg.exe",
    ]
    if getattr(sys, "frozen", False):
        candidates.insert(0, Path(sys.executable).parent / "ffmpeg.exe")
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.insert(0, Path(meipass) / "ffmpeg.exe")

    for cand in candidates:
        if cand.exists():
            return str(cand.resolve())

    sys_ffmpeg = shutil.which("ffmpeg")
    if sys_ffmpeg:
        return sys_ffmpeg

    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if os.path.exists(exe):
            return exe
    except Exception:
        pass

    return "ffmpeg"


def find_adb_executable():
    candidates = [
        Path(__file__).resolve().parent.parent / "platform-tools" / "adb.exe",
        Path.cwd() / "platform-tools" / "adb.exe",
    ]
    if getattr(sys, "frozen", False):
        candidates.insert(0, Path(sys.executable).parent / "platform-tools" / "adb.exe")

    for cand in candidates:
        if cand.exists():
            return str(cand.resolve())

    sys_adb = shutil.which("adb")
    if sys_adb:
        return sys_adb
    return None


def check_runtime_dependencies():
    """Checks ADB, GStreamer, and FFmpeg availability for user-friendly startup guidance."""
    adb_exe = find_adb_executable()
    ffmpeg_exe = find_ffmpeg_executable()
    ffmpeg_ok = Path(ffmpeg_exe).exists() or shutil.which(ffmpeg_exe) is not None

    gst_ok = False
    gst_candidates = [
        Path(__file__).parent / "gstreamer_runtime" / "bin" / "gstreamer-1.0-0.dll",
        Path(__file__).parent.parent / "windows" / "gstreamer_runtime" / "bin" / "gstreamer-1.0-0.dll",
        Path("C:/gstreamer/1.0/msvc_x86_64/bin/gstreamer-1.0-0.dll"),
    ]
    if getattr(sys, "frozen", False):
        gst_candidates.insert(0, Path(sys.executable).parent / "gstreamer_runtime" / "bin" / "gstreamer-1.0-0.dll")
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        gst_candidates.insert(0, Path(meipass) / "gstreamer_runtime" / "bin" / "gstreamer-1.0-0.dll")

    for cand in gst_candidates:
        if cand.exists():
            gst_ok = True
            break

    missing = []
    if not adb_exe:
        missing.append("ADB (platform-tools)")
    if not gst_ok and not ffmpeg_ok:
        missing.append("GStreamer or FFmpeg")
    elif not gst_ok:
        missing.append("GStreamer runtime (using FFmpeg fallback)")

    return {
        "adb": adb_exe is not None,
        "gstreamer": gst_ok,
        "ffmpeg": ffmpeg_ok,
        "missing": missing,
    }
