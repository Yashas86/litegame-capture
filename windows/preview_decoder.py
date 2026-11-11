import queue
import subprocess
import threading
import time
try:
    from utils import find_ffmpeg_executable, get_subprocess_kwargs
except ImportError:
    from windows.utils import find_ffmpeg_executable, get_subprocess_kwargs


class PreviewDecoder:
    """
    Decodes incoming H.264 video packets on Windows using FFmpeg hardware acceleration (-hwaccel auto)
    downscaled to 960x540 at 30 FPS for smooth live preview in Tkinter GUI.
    """

    def __init__(self, target_width=960, target_height=540, fps=30, on_frame=None, on_error=None):
        self.target_width = target_width
        self.target_height = target_height
        self.fps = fps
        self.on_frame = on_frame
        self.on_error = on_error

        self.ffmpeg_path = find_ffmpeg_executable()
        self.enabled = False
        self.running = False
        self.packet_queue = queue.Queue(maxsize=120)
        self._lock = threading.Lock()

        self.proc = None
        self.write_thread = None
        self.read_thread = None

    def start(self):
        with self._lock:
            if self.running:
                return
            self.running = True
            self.enabled = True
            self._clear_queue()
            self._start_pipeline()

    def stop(self):
        with self._lock:
            self.enabled = False
            self.running = False
            self._clear_queue()
            self._stop_pipeline()

    def set_enabled(self, enabled: bool):
        with self._lock:
            self.enabled = enabled
            if not self.running:
                return
            if enabled and not self.proc:
                self._clear_queue()
                self._start_pipeline()
            elif not enabled and self.proc:
                self._clear_queue()
                self._stop_pipeline()

    def _clear_queue(self):
        try:
            with self.packet_queue.mutex:
                self.packet_queue.queue.clear()
        except Exception:
            pass

    def push_packet(self, data: bytes):
        if not self.enabled or not self.running or not self.proc or not data:
            return
        try:
            self.packet_queue.put(data, block=False)
        except queue.Full:
            # Clear stale packets to maintain lowest latency for live preview
            try:
                with self.packet_queue.mutex:
                    self.packet_queue.queue.clear()
                self.packet_queue.put(data, block=False)
            except Exception:
                pass

    def _start_pipeline(self):
        if self.proc:
            return

        cmd = [
            self.ffmpeg_path,
            "-hide_banner",
            "-loglevel", "quiet",
            "-hwaccel", "auto",
            "-f", "h264",
            "-i", "pipe:0",
            "-vf", f"scale={self.target_width}:{self.target_height}",
            "-r", str(self.fps),
            "-f", "rawvideo",
            "-pix_fmt", "rgb24",
            "pipe:1",
        ]

        try:
            self.proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=10 * 1024 * 1024,
                **get_subprocess_kwargs(),
            )
        except Exception as e:
            if self.on_error:
                self.on_error(f"Failed to launch FFmpeg hardware preview decoder: {str(e)}")
            self.proc = None
            return

        self.write_thread = threading.Thread(target=self._writer_loop, daemon=True)
        self.read_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self.write_thread.start()
        self.read_thread.start()

    def _stop_pipeline(self):
        proc = self.proc
        self.proc = None
        if proc:
            try:
                if proc.stdin:
                    proc.stdin.close()
            except Exception:
                pass
            try:
                if proc.stdout:
                    proc.stdout.close()
            except Exception:
                pass
            try:
                if proc.stderr:
                    proc.stderr.close()
            except Exception:
                pass
            try:
                proc.terminate()
                proc.wait(timeout=1.0)
            except Exception:
                pass

    def _writer_loop(self):
        while self.enabled and self.running:
            proc = self.proc
            if not proc:
                break
            try:
                pkt = self.packet_queue.get(timeout=0.1)
                if proc.stdin:
                    proc.stdin.write(pkt)
                    proc.stdin.flush()
            except queue.Empty:
                continue
            except Exception:
                break

    def _reader_loop(self):
        frame_bytes = self.target_width * self.target_height * 3  # RGB24
        while self.enabled and self.running:
            proc = self.proc
            if not proc or not proc.stdout:
                break
            try:
                raw_frame = proc.stdout.read(frame_bytes)
                if not raw_frame:
                    if proc.poll() is not None:
                        break
                    time.sleep(0.005)
                    continue
                if len(raw_frame) < frame_bytes:
                    break

                if self.on_frame:
                    self.on_frame(raw_frame, self.target_width, self.target_height)
            except Exception:
                break
