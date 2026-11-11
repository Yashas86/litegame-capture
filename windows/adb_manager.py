import os
import shutil
import subprocess
import threading
import time
from utils import get_subprocess_kwargs

class ADBManager:
    def __init__(self, port=27183, on_status_change=None):
        self.port = port
        self.on_status_change = on_status_change
        self.adb_path = self._find_adb()
        self.connected_device = None
        self.running = False
        self._thread = None

    def _find_adb(self):
        # 1. Check local platform-tools directory
        local_adb = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "platform-tools", "adb.exe"))
        if os.path.exists(local_adb):
            return local_adb

        # 2. Check current working directory platform-tools
        cwd_adb = os.path.abspath(os.path.join(os.getcwd(), "platform-tools", "adb.exe"))
        if os.path.exists(cwd_adb):
            return cwd_adb

        # 3. Check system PATH
        system_adb = shutil.which("adb")
        if system_adb:
            return system_adb

        return "adb"

    def start(self):
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self.running = False

    def setup_reverse(self):
        if not self.adb_path:
            return False, "ADB executable not found"
        try:
            cmd = [self.adb_path, "reverse", f"tcp:{self.port}", f"tcp:{self.port}"]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5, **get_subprocess_kwargs())
            if res.returncode == 0:
                return True, f"ADB reverse tcp:{self.port} set up successfully"
            else:
                return False, f"ADB reverse error: {res.stderr.strip()}"
        except Exception as e:
            return False, f"ADB execution failed: {str(e)}"

    def get_connected_devices(self):
        if not self.adb_path:
            return []
        try:
            cmd = [self.adb_path, "devices"]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5, **get_subprocess_kwargs())
            if res.returncode != 0:
                return []

            devices = []
            for line in res.stdout.strip().splitlines()[1:]:
                parts = line.strip().split()
                if len(parts) >= 2 and parts[1] == "device":
                    devices.append(parts[0])
            return devices
        except Exception:
            return []

    def _poll_loop(self):
        prev_device = None
        while self.running:
            devices = self.get_connected_devices()
            current_device = devices[0] if devices else None

            if current_device != prev_device:
                self.connected_device = current_device
                prev_device = current_device
                if current_device:
                    ok, msg = self.setup_reverse()
                    if self.on_status_change:
                        self.on_status_change(True, current_device, msg)
                else:
                    if self.on_status_change:
                        self.on_status_change(False, None, "No USB device connected")

            time.sleep(2.0)
