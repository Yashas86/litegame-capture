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
    kwargs = {
        "env": get_clean_env()
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x08000000 # CREATE_NO_WINDOW
    return kwargs

def find_ffmpeg_executable():
    # 1. Check local directory
    local_1 = Path(__file__).parent / "ffmpeg.exe"
    if local_1.exists():
        return str(local_1.resolve())

    # 2. Check parent/dist folder
    local_2 = Path(__file__).parent.parent / "ffmpeg.exe"
    if local_2.exists():
        return str(local_2.resolve())

    # 3. Check system PATH
    sys_ffmpeg = shutil.which("ffmpeg")
    if sys_ffmpeg:
        return sys_ffmpeg

    # 4. Fallback to imageio_ffmpeg
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if os.path.exists(exe):
            return exe
    except Exception:
        pass

    return "ffmpeg"
