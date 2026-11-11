import subprocess
import threading
import time
try:
    from utils import find_adb_executable, get_subprocess_kwargs
except ImportError:
    from windows.utils import find_adb_executable, get_subprocess_kwargs


class ADBManager:
    def __init__(self, port=27183, on_status_change=None):
        self.port = port
        self.on_status_change = on_status_change
        self.adb_path = find_adb_executable()
        self.connected_device = None
        self._device_serial = None  # Kept private; never displayed in UI or logs
        self.adb_state = "disconnected"
        self.running = False
        self._thread = None

    def start(self):
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self.running = False

    def _run_adb(self, args, timeout=5):
        if not self.adb_path:
            self.adb_path = find_adb_executable()
        if not self.adb_path:
            return None
        cmd = [self.adb_path]
        if self._device_serial and args[0] != "devices":
            cmd.extend(["-s", self._device_serial])
        cmd.extend(args)
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **get_subprocess_kwargs())

    def setup_reverse(self):
        if not self.adb_path:
            return False, "ADB executable not found"
        try:
            res = self._run_adb(["reverse", f"tcp:{self.port}", f"tcp:{self.port}"])
            if res and res.returncode == 0:
                return True, f"ADB reverse active (port {self.port})"
            err = res.stderr.strip() if res and res.stderr else "Unable to configure ADB reverse"
            return False, f"ADB reverse failed: {err}"
        except Exception as e:
            return False, f"ADB execution failed: {str(e)}"

    def launch_capture_app(self, package="com.litegame.capture", activity=".MainActivity"):
        """Brings up LiteGame Capture on the phone and requests the MediaProjection prompt."""
        if not self._device_serial:
            return False, "No Android device connected via USB"
        try:
            res = self._run_adb(
                ["shell", "am", "start", "-n", f"{package}/{activity}", "--ez", "auto_start", "true"],
                timeout=5,
            )
            if res and res.returncode == 0:
                return True, "Prompt sent to phone. Tap 'Start now' on Android to begin."
            err = res.stderr.strip() if res and res.stderr else "App launch failed"
            return False, f"Install LiteGame Capture APK on phone ({err})"
        except Exception as e:
            return False, f"Could not launch Android app: {str(e)}"

    def stop_capture_app(self, package="com.litegame.capture", activity=".MainActivity"):
        """Fallback ADB stop command if TCP control channel is unavailable."""
        if not self._device_serial:
            return False
        try:
            res = self._run_adb(
                ["shell", "am", "start", "-n", f"{package}/{activity}", "--ez", "auto_stop", "true"],
                timeout=4,
            )
            return bool(res and res.returncode == 0)
        except Exception:
            return False

    def get_device_states(self):
        if not self.adb_path:
            self.adb_path = find_adb_executable()
        if not self.adb_path:
            return [], "missing_adb"
        try:
            res = subprocess.run(
                [self.adb_path, "devices"],
                capture_output=True,
                text=True,
                timeout=5,
                **get_subprocess_kwargs(),
            )
            if res.returncode != 0:
                return [], "adb_error"

            ready = []
            unauthorized = []
            offline = []
            for line in res.stdout.strip().splitlines()[1:]:
                parts = line.strip().split()
                if len(parts) < 2:
                    continue
                serial, state = parts[0], parts[1]
                if state == "device":
                    ready.append(serial)
                elif state == "unauthorized":
                    unauthorized.append(serial)
                elif state == "offline":
                    offline.append(serial)

            if ready:
                return ready, "device"
            if unauthorized:
                return [], "unauthorized"
            if offline:
                return [], "offline"
            return [], "disconnected"
        except Exception:
            return [], "disconnected"

    def get_connected_devices(self):
        ready, _ = self.get_device_states()
        return ["Android Device"] if ready else []

    def _poll_loop(self):
        prev_status_key = None
        while self.running:
            ready_serials, state = self.get_device_states()
            self._device_serial = ready_serials[0] if ready_serials else None
            self.adb_state = state

            if state == "device":
                self.connected_device = "Android Device"
                status_key = ("connected", len(ready_serials))
                if status_key != prev_status_key:
                    prev_status_key = status_key
                    ok, msg = self.setup_reverse()
                    if self.on_status_change:
                        self.on_status_change(True, "Android Device", msg)
            else:
                self.connected_device = None
                status_key = (state, 0)
                if status_key != prev_status_key:
                    prev_status_key = status_key
                    human_msg = {
                        "unauthorized": "Authorize USB Debugging prompt on phone",
                        "offline": "Device offline — reconnect USB cable",
                        "missing_adb": "ADB not found — run setup_dependencies.ps1",
                        "adb_error": "ADB daemon error — reconnecting",
                    }.get(state, "Connect Android phone with USB Debugging enabled")
                    if self.on_status_change:
                        self.on_status_change(False, None, human_msg)

            time.sleep(2.0)
