import ctypes
import queue
import subprocess
import threading
import time
try:
    from gstreamer_pipeline import GST_STATE_CHANGE_FAILURE, GST_STATE_NULL, GST_STATE_PLAYING, _get_gst_runtime
    from utils import find_ffmpeg_executable, get_subprocess_kwargs
except ImportError:
    from windows.gstreamer_pipeline import GST_STATE_CHANGE_FAILURE, GST_STATE_NULL, GST_STATE_PLAYING, _get_gst_runtime
    from windows.utils import find_ffmpeg_executable, get_subprocess_kwargs


class PreviewDecoder:
    """
    Independent low-latency H.264 preview decoder for Windows.
    Primary engine: In-process GStreamer pipeline (d3d11h264dec hardware -> avdec_h264/openh264dec fallback)
    with appsink (drop=true, max-buffers=2, sync=false) for ~50ms preview latency and zero pop-up/flicker.
    Fallback engine: FFmpeg subprocess if GStreamer runtime is unavailable.
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
        self.packet_queue = queue.Queue(maxsize=12)
        self._lock = threading.Lock()
        self._waiting_for_keyframe = False
        self._codec_config = []

        self._pipe_active = False
        self._gst_rt = None
        self._gst_pipe = None
        self._gst_src = None
        self._gst_sink = None

        self.proc = None
        self.write_thread = None
        self.read_thread = None

    def start(self):
        with self._lock:
            if self.running:
                return
            self.running = True
            self.enabled = True
            self._waiting_for_keyframe = False
            self._clear_queue()
            self._start_pipeline()

    def stop(self):
        with self._lock:
            self.enabled = False
            self.running = False
            self._waiting_for_keyframe = False
            self._codec_config = []
            self._clear_queue()
            self._stop_pipeline()

    def set_enabled(self, enabled: bool):
        with self._lock:
            self.enabled = enabled
            if not self.running:
                return
            if enabled and not self._pipe_active:
                self._waiting_for_keyframe = False
                self._clear_queue()
                self._start_pipeline()
            elif not enabled and self._pipe_active:
                self._clear_queue()
                self._stop_pipeline()

    def set_codec_config(self, config_packets):
        with self._lock:
            self._codec_config = list(config_packets)

    def _clear_queue(self):
        try:
            with self.packet_queue.mutex:
                self.packet_queue.queue.clear()
        except Exception:
            pass

    @staticmethod
    def _is_keyframe_or_config(data: bytes) -> bool:
        if len(data) < 5:
            return False
        i = 0
        n = len(data)
        while i < n - 4:
            if data[i] == 0 and data[i + 1] == 0:
                if data[i + 2] == 1:
                    if (data[i + 3] & 0x1F) in (5, 7, 8):
                        return True
                    i += 3
                    continue
                if i < n - 4 and data[i + 2] == 0 and data[i + 3] == 1:
                    if (data[i + 4] & 0x1F) in (5, 7, 8):
                        return True
                    i += 4
                    continue
            i += 1
        return False

    def push_packet(self, data: bytes, flags: int = 0):
        if not self.enabled or not self.running or not self._pipe_active or not data:
            return
        is_cfg = (flags & 2) != 0
        is_key = (flags & 1) != 0 or self._is_keyframe_or_config(data)

        if self._waiting_for_keyframe and not (is_cfg or is_key):
            return
        if is_key or is_cfg:
            self._waiting_for_keyframe = False

        try:
            self.packet_queue.put(data, block=False)
        except queue.Full:
            # Drop stale delta backlog cleanly and resync on next keyframe
            self._clear_queue()
            if is_key or is_cfg:
                try:
                    self.packet_queue.put(data, block=False)
                except Exception:
                    pass
            else:
                self._waiting_for_keyframe = True

    def _start_pipeline(self):
        if self._pipe_active:
            return

        if self._try_start_gstreamer():
            return

        self._start_ffmpeg_fallback()

    def _try_start_gstreamer(self) -> bool:
        try:
            rt = _get_gst_runtime()
        except Exception:
            return False

        gst = rt.gst
        for decoder in ("d3d11h264dec", "avdec_h264", "openh264dec"):
            desc = (
                f'appsrc name=psrc format=time is-live=true do-timestamp=true '
                f'caps="video/x-h264,stream-format=byte-stream" ! '
                f'h264parse ! {decoder} ! videoconvert ! videoscale ! '
                f'video/x-raw,format=RGB,width={self.target_width},height={self.target_height} ! '
                f'appsink name=psink max-buffers=2 drop=true sync=false'
            )
            gerr = ctypes.c_void_p()
            pipe = gst.gst_parse_launch(desc.encode("utf-8"), ctypes.byref(gerr))
            if not pipe:
                if gerr.value:
                    rt.glib.g_error_free(gerr)
                continue

            src = gst.gst_bin_get_by_name(pipe, b"psrc")
            sink = gst.gst_bin_get_by_name(pipe, b"psink")
            if not src or not sink or gst.gst_element_set_state(pipe, GST_STATE_PLAYING) == GST_STATE_CHANGE_FAILURE:
                gst.gst_element_set_state(pipe, GST_STATE_NULL)
                for obj in (src, sink, pipe):
                    if obj:
                        gst.gst_object_unref(obj)
                continue

            self._gst_rt = rt
            self._gst_pipe = pipe
            self._gst_src = src
            self._gst_sink = sink
            self._pipe_active = True

            for cfg in self._codec_config:
                try:
                    self.packet_queue.put(cfg, block=False)
                except Exception:
                    break

            self.write_thread = threading.Thread(target=self._gst_writer_loop, daemon=True)
            self.read_thread = threading.Thread(target=self._gst_reader_loop, daemon=True)
            self.write_thread.start()
            self.read_thread.start()
            return True

        return False

    def _start_ffmpeg_fallback(self):
        cmd = [
            self.ffmpeg_path,
            "-hide_banner",
            "-loglevel", "quiet",
            "-probesize", "16384",
            "-analyzeduration", "0",
            "-f", "h264",
            "-i", "pipe:0",
            "-vf", f"scale={self.target_width}:{self.target_height}",
            "-fps_mode", "passthrough",
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
                bufsize=0,
                **get_subprocess_kwargs(),
            )
            self._pipe_active = True
        except Exception as e:
            if self.on_error:
                self.on_error(f"Preview decoder failed to start: {str(e)}")
            self.proc = None
            return

        for cfg in self._codec_config:
            try:
                self.packet_queue.put(cfg, block=False)
            except Exception:
                break

        self.write_thread = threading.Thread(target=self._ffmpeg_writer_loop, daemon=True)
        self.read_thread = threading.Thread(target=self._ffmpeg_reader_loop, daemon=True)
        self.write_thread.start()
        self.read_thread.start()

    def _stop_pipeline(self):
        self._pipe_active = False

        # Shutdown GStreamer pipeline if active
        pipe = self._gst_pipe
        src = self._gst_src
        sink = self._gst_sink
        rt = self._gst_rt
        self._gst_pipe = None
        self._gst_src = None
        self._gst_sink = None
        self._gst_rt = None

        if pipe and rt:
            try:
                rt.gst.gst_element_set_state(pipe, GST_STATE_NULL)
            except Exception:
                pass
            if self.write_thread and self.write_thread.is_alive():
                self.write_thread.join(timeout=0.5)
            if self.read_thread and self.read_thread.is_alive():
                self.read_thread.join(timeout=0.5)
            for obj in (src, sink, pipe):
                if obj:
                    try:
                        rt.gst.gst_object_unref(obj)
                    except Exception:
                        pass

        # Shutdown FFmpeg fallback process if active
        proc = self.proc
        self.proc = None
        if proc:
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                if stream:
                    try:
                        stream.close()
                    except Exception:
                        pass
            try:
                proc.terminate()
                proc.wait(timeout=1.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def _gst_writer_loop(self):
        rt = self._gst_rt
        src = self._gst_src
        if not rt or not src:
            return
        pts_ns = 0
        while self._pipe_active and self.enabled and self.running:
            try:
                pkt = self.packet_queue.get(timeout=0.05)
                buf = rt.make_buffer(pkt, pts_ns, pts_ns, 16_666_667)
                pts_ns += 16_666_667
                flow = rt.gstapp.gst_app_src_push_buffer(src, buf)
                if flow != 0:
                    break
            except queue.Empty:
                continue
            except Exception:
                break

    def _gst_reader_loop(self):
        rt = self._gst_rt
        sink = self._gst_sink
        if not rt or not sink:
            return
        expected_bytes = self.target_width * self.target_height * 3
        while self._pipe_active and self.enabled and self.running:
            try:
                samp = rt.gstapp.gst_app_sink_try_pull_sample(sink, 50_000_000)
                if not samp:
                    continue
                try:
                    buf = rt.gst.gst_sample_get_buffer(samp)
                    if buf:
                        sz = rt.gst.gst_buffer_get_size(buf)
                        if sz >= expected_bytes:
                            raw_buf = bytearray(expected_bytes)
                            c_arr = (ctypes.c_char * expected_bytes).from_buffer(raw_buf)
                            rt.gst.gst_buffer_extract(buf, 0, c_arr, expected_bytes)
                            if self.on_frame and self.enabled and self._pipe_active:
                                self.on_frame(bytes(raw_buf), self.target_width, self.target_height)
                finally:
                    rt.gst.gst_mini_object_unref(samp)
            except Exception:
                break

    def _ffmpeg_writer_loop(self):
        while self._pipe_active and self.enabled and self.running:
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

    @staticmethod
    def _read_exact(stream, size: int):
        buf = bytearray()
        while len(buf) < size:
            chunk = stream.read(size - len(buf))
            if not chunk:
                return None
            buf.extend(chunk)
        return bytes(buf)

    def _ffmpeg_reader_loop(self):
        frame_bytes = self.target_width * self.target_height * 3
        while self._pipe_active and self.enabled and self.running:
            proc = self.proc
            if not proc or not proc.stdout:
                break
            try:
                raw_frame = self._read_exact(proc.stdout, frame_bytes)
                if not raw_frame:
                    if proc.poll() is not None:
                        break
                    time.sleep(0.005)
                    continue

                if self.on_frame and self.enabled and self._pipe_active:
                    self.on_frame(raw_frame, self.target_width, self.target_height)
            except Exception:
                break
