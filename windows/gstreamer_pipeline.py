import ctypes
import os
import shutil
import struct
import sys
import threading
import time
from pathlib import Path

INDEX_STRUCT = struct.Struct("<BBQQI")  # track:u8, flags:u8, pts_us:u64, offset:u64, size:u32
INDEX_RECORD_SIZE = INDEX_STRUCT.size  # 22 bytes

GST_CLOCK_TIME_NONE = 0xFFFFFFFFFFFFFFFF
GST_STATE_NULL = 1
GST_STATE_PLAYING = 4
GST_STATE_CHANGE_FAILURE = 0
GST_MESSAGE_EOS = 1 << 0
GST_MESSAGE_ERROR = 1 << 1
GST_PAD_PROBE_TYPE_BUFFER = 1 << 4
GST_PAD_PROBE_OK = 1

_GST_LOCK = threading.Lock()
_GST_RUNTIME = None


def _find_gstreamer_root():
    candidates = [
        Path(__file__).parent / "gstreamer_runtime",
        Path(__file__).parent.parent / "windows" / "gstreamer_runtime",
    ]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.insert(0, Path(meipass) / "gstreamer_runtime")
    if getattr(sys, "frozen", False):
        candidates.insert(0, Path(sys.executable).parent / "gstreamer_runtime")
    candidates.append(Path("C:/gstreamer/1.0/msvc_x86_64"))

    for cand in candidates:
        if (cand / "bin" / "gstreamer-1.0-0.dll").exists():
            return cand.resolve()
    return None


class _GstRuntime:
    """Thread-safe singleton wrapper around the bundled GStreamer 1.26 C libraries."""

    def __init__(self, gst_root: Path):
        self.gst_root = gst_root
        gst_bin = str(gst_root / "bin")
        gst_plugins = str(gst_root / "lib" / "gstreamer-1.0")

        if hasattr(os, "add_dll_directory"):
            self._dll_dir = os.add_dll_directory(gst_bin)
        os.environ["PATH"] = gst_bin + os.pathsep + os.environ.get("PATH", "")
        os.environ["GST_PLUGIN_PATH"] = gst_plugins
        os.environ["GST_PLUGIN_SYSTEM_PATH"] = gst_plugins

        self.gst = ctypes.CDLL(os.path.join(gst_bin, "gstreamer-1.0-0.dll"))
        self.gstapp = ctypes.CDLL(os.path.join(gst_bin, "gstapp-1.0-0.dll"))
        self.glib = ctypes.CDLL(os.path.join(gst_bin, "glib-2.0-0.dll"))

        self._bind_signatures()
        self.gst.gst_init(None, None)

        self.pts_slot, self.dts_slot, self.dur_slot = self._detect_buffer_slots()
        self.msg_type_offset = 64
        self.probe_cb_type = ctypes.CFUNCTYPE(
            ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p
        )

    def _bind_signatures(self):
        g = self.gst
        a = self.gstapp
        gl = self.glib

        g.gst_init.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        g.gst_parse_launch.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)]
        g.gst_parse_launch.restype = ctypes.c_void_p
        g.gst_bin_get_by_name.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        g.gst_bin_get_by_name.restype = ctypes.c_void_p
        g.gst_element_get_static_pad.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        g.gst_element_get_static_pad.restype = ctypes.c_void_p
        g.gst_pad_add_probe.argtypes = [
            ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p
        ]
        g.gst_pad_add_probe.restype = ctypes.c_uint
        g.gst_element_set_state.argtypes = [ctypes.c_void_p, ctypes.c_int]
        g.gst_element_set_state.restype = ctypes.c_int
        g.gst_element_get_bus.argtypes = [ctypes.c_void_p]
        g.gst_element_get_bus.restype = ctypes.c_void_p
        g.gst_bus_timed_pop_filtered.argtypes = [ctypes.c_void_p, ctypes.c_uint64, ctypes.c_uint64]
        g.gst_bus_timed_pop_filtered.restype = ctypes.c_void_p
        g.gst_message_parse_error.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p)
        ]
        g.gst_mini_object_make_writable.argtypes = [ctypes.c_void_p]
        g.gst_mini_object_make_writable.restype = ctypes.c_void_p
        g.gst_mini_object_unref.argtypes = [ctypes.c_void_p]
        g.gst_object_unref.argtypes = [ctypes.c_void_p]

        g.gst_buffer_new_allocate.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p]
        g.gst_buffer_new_allocate.restype = ctypes.c_void_p
        g.gst_buffer_fill.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t]
        g.gst_buffer_fill.restype = ctypes.c_size_t

        a.gst_app_src_push_buffer.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        a.gst_app_src_push_buffer.restype = ctypes.c_int
        a.gst_app_src_end_of_stream.argtypes = [ctypes.c_void_p]
        a.gst_app_src_end_of_stream.restype = ctypes.c_int

        gl.g_error_free.argtypes = [ctypes.c_void_p]
        gl.g_free.argtypes = [ctypes.c_void_p]

    def _detect_buffer_slots(self):
        buf = self.gst.gst_buffer_new_allocate(None, 8, None)
        try:
            arr = (ctypes.c_uint64 * 20).from_address(buf)
            for idx in range(8, 16):
                if (
                    arr[idx] == GST_CLOCK_TIME_NONE
                    and arr[idx + 1] == GST_CLOCK_TIME_NONE
                    and arr[idx + 2] == GST_CLOCK_TIME_NONE
                ):
                    return idx, idx + 1, idx + 2
            raise RuntimeError("Unexpected GstBuffer memory layout")
        finally:
            self.gst.gst_mini_object_unref(buf)

    def make_buffer(self, data: bytes, pts_ns: int, dts_ns: int, dur_ns: int):
        buf = self.gst.gst_buffer_new_allocate(None, len(data), None)
        if not buf:
            raise MemoryError("gst_buffer_new_allocate failed")
        if data:
            self.gst.gst_buffer_fill(buf, 0, data, len(data))
        arr = (ctypes.c_uint64 * 16).from_address(buf)
        arr[self.pts_slot] = pts_ns
        arr[self.dts_slot] = dts_ns
        arr[self.dur_slot] = dur_ns
        return buf


def _get_gst_runtime() -> _GstRuntime:
    global _GST_RUNTIME
    with _GST_LOCK:
        if _GST_RUNTIME is None:
            gst_root = _find_gstreamer_root()
            if gst_root is None:
                raise FileNotFoundError("GStreamer runtime DLLs not found")
            _GST_RUNTIME = _GstRuntime(gst_root)
        return _GST_RUNTIME


def _group_nals_into_access_units(nals):
    units = []
    pending_config = bytearray()
    for nal, nal_type in nals:
        if nal_type in (6, 7, 8, 9):
            pending_config.extend(nal)
        else:
            if pending_config:
                units.append((bytes(pending_config) + nal, 1 if nal_type == 5 else 0))
                pending_config.clear()
            else:
                units.append((nal, 1 if nal_type == 5 else 0))
    if pending_config and units:
        last_bytes, last_fl = units[-1]
        units[-1] = (last_bytes + bytes(pending_config), last_fl)
    return units


def _split_annexb_access_units(data: bytes):
    """
    Splits a raw H.264 stream (Annex-B or 4-byte length-prefixed AVCC) into
    Annex-B per-frame access units (combining SPS/PPS/AUD/SEI with the next slice).
    """
    n = len(data)
    if n < 4:
        return [(data, 0)] if data else []

    # Detect 4-byte length-prefixed AVCC stream (e.g., x264enc -> h264parse -> filesink)
    if not (data[:4] == b"\x00\x00\x00\x01" or data[:3] == b"\x00\x00\x01"):
        pos = 0
        avcc_nals = []
        valid_avcc = True
        while pos + 4 <= n:
            nal_len = int.from_bytes(data[pos:pos + 4], "big")
            if nal_len <= 0 or pos + 4 + nal_len > n:
                valid_avcc = False
                break
            nal_body = data[pos + 4:pos + 4 + nal_len]
            avcc_nals.append((b"\x00\x00\x00\x01" + nal_body, nal_body[0] & 0x1F))
            pos += 4 + nal_len
        if valid_avcc and pos == n and avcc_nals:
            return _group_nals_into_access_units(avcc_nals)

    offsets = []
    i = 0
    while i < n - 3:
        if data[i] == 0 and data[i + 1] == 0:
            if data[i + 2] == 1:
                offsets.append((i, 3))
                i += 3
                continue
            if i < n - 4 and data[i + 2] == 0 and data[i + 3] == 1:
                offsets.append((i, 4))
                i += 4
                continue
        i += 1

    if not offsets:
        return [(data, 0)] if data else []

    annexb_nals = [
        (data[pos:(offsets[idx + 1][0] if idx + 1 < len(offsets) else n)], data[pos + sc_len] & 0x1F)
        for idx, (pos, sc_len) in enumerate(offsets)
    ]
    return _group_nals_into_access_units(annexb_nals)


class GStreamerPipeline:
    """
    Native GStreamer media pipeline manager for Windows.
    Ingests timestamped H.264 video, 48kHz stereo Game Audio PCM, and 48kHz mono Phone Mic PCM
    via appsrc with exact nanosecond PTS/DTS/duration, mixes audio with audiomixer, encodes AAC
    with voaacenc, and pass-through muxes H.264 via h264parse into MP4 using mp4mux.
    """

    def __init__(self, output_mp4_path, on_status=None, on_error=None):
        self.output_mp4_path = Path(output_mp4_path).resolve()
        self.output_mp4_path.parent.mkdir(parents=True, exist_ok=True)
        self.on_status = on_status
        self.on_error = on_error

        self.gst_launch_bin = self._find_gst_launch()
        self.proc = None
        self.running = False

        ts = int(time.time() * 1000)
        self.temp_v_file = self.output_mp4_path.parent / f".gst_v_{ts}.h264"
        self.temp_g_file = self.output_mp4_path.parent / f".gst_g_{ts}.pcm"
        self.temp_m_file = self.output_mp4_path.parent / f".gst_m_{ts}.pcm"
        self.temp_idx_file = self.output_mp4_path.parent / f".gst_idx_{ts}.bin"

        self.v_out = None
        self.g_out = None
        self.m_out = None
        self.idx_out = None
        self._v_off = 0
        self._g_off = 0
        self._m_off = 0
        self._has_explicit_pts = False

    def _find_gst_launch(self):
        root = _find_gstreamer_root()
        if root is not None:
            exe = root / "bin" / "gst-launch-1.0.exe"
            if exe.exists():
                return str(exe)

        local_1 = Path(__file__).parent / "gstreamer_runtime" / "bin" / "gst-launch-1.0.exe"
        if local_1.exists():
            return str(local_1.resolve())

        local_2 = Path(__file__).parent.parent / "windows" / "gstreamer_runtime" / "bin" / "gst-launch-1.0.exe"
        if local_2.exists():
            return str(local_2.resolve())

        sys_gst = Path("C:/gstreamer/1.0/msvc_x86_64/bin/gst-launch-1.0.exe")
        if sys_gst.exists():
            return str(sys_gst.resolve())

        path_gst = shutil.which("gst-launch-1.0")
        if path_gst:
            return path_gst

        return "gst-launch-1.0"

    def start(self):
        if self.running:
            return
        self.running = True
        self._v_off = 0
        self._g_off = 0
        self._m_off = 0
        self._has_explicit_pts = False

        self.v_out = open(self.temp_v_file, "wb")
        self.g_out = open(self.temp_g_file, "wb")
        self.m_out = open(self.temp_m_file, "wb")
        self.idx_out = open(self.temp_idx_file, "wb")

        if self.on_status:
            self.on_status("Native GStreamer pipeline session active. Ingesting streams...")

    def push_video(self, payload: bytes, pts_us: int = None, flags: int = 0):
        if self.running and self.v_out and payload:
            try:
                self.v_out.write(payload)
                if pts_us is not None:
                    self._has_explicit_pts = True
                    self.idx_out.write(INDEX_STRUCT.pack(1, flags, pts_us, self._v_off, len(payload)))
                self._v_off += len(payload)
            except Exception:
                pass

    def push_game_audio(self, payload: bytes, pts_us: int = None):
        if self.running and self.g_out and payload:
            try:
                self.g_out.write(payload)
                if pts_us is not None:
                    self._has_explicit_pts = True
                    self.idx_out.write(INDEX_STRUCT.pack(2, 0, pts_us, self._g_off, len(payload)))
                self._g_off += len(payload)
            except Exception:
                pass

    def push_phone_mic(self, payload: bytes, pts_us: int = None):
        if self.running and self.m_out and payload:
            try:
                self.m_out.write(payload)
                if pts_us is not None:
                    self._has_explicit_pts = True
                    self.idx_out.write(INDEX_STRUCT.pack(3, 0, pts_us, self._m_off, len(payload)))
                self._m_off += len(payload)
            except Exception:
                pass

    def stop(self, include_game_audio=True, include_phone_mic=True, audio_delay_ms=0, session_end_pts_us=None):
        if not self.running:
            return False, "Pipeline not running"
        self.running = False

        for fh in (self.v_out, self.g_out, self.m_out, self.idx_out):
            if fh:
                try:
                    fh.flush()
                    fh.close()
                except Exception:
                    pass
        self.v_out = self.g_out = self.m_out = self.idx_out = None

        try:
            idx_path = self.temp_idx_file if self._has_explicit_pts else None
            return self.mux_from_index(
                video_path=self.temp_v_file,
                game_audio_path=self.temp_g_file,
                phone_mic_path=self.temp_m_file,
                packet_index_path=idx_path,
                include_game_audio=include_game_audio,
                include_phone_mic=include_phone_mic,
                audio_delay_ms=audio_delay_ms,
                session_end_pts_us=session_end_pts_us,
            )
        finally:
            self._cleanup_temp()

    def mux_from_index(
        self,
        video_path,
        game_audio_path=None,
        phone_mic_path=None,
        packet_index_path=None,
        include_game_audio=True,
        include_phone_mic=True,
        audio_delay_ms=0,
        session_end_pts_us=None,
    ):
        """
        Muxes video and optional audio streams into MP4 via GStreamer appsrc -> h264parse -> mp4mux
        using exact per-packet nanosecond timestamps (zero H.264 re-encoding).
        """
        if self.on_status:
            self.on_status("Finalizing MP4 recording using GStreamer appsrc media engine...")

        video_path = Path(video_path)
        game_audio_path = Path(game_audio_path) if game_audio_path else None
        phone_mic_path = Path(phone_mic_path) if phone_mic_path else None
        packet_index_path = Path(packet_index_path) if packet_index_path else None

        if not video_path.exists() or video_path.stat().st_size == 0:
            return False, "Video stream is empty or missing"

        has_g = bool(
            include_game_audio and game_audio_path and game_audio_path.exists() and game_audio_path.stat().st_size > 0
        )
        has_m = bool(
            include_phone_mic and phone_mic_path and phone_mic_path.exists() and phone_mic_path.stat().st_size > 0
        )

        try:
            if packet_index_path and packet_index_path.exists() and packet_index_path.stat().st_size >= INDEX_RECORD_SIZE:
                v_plan, g_plan, m_plan, last_v_pts_ns, last_v_dur_ns, tail_silence = self._build_indexed_plan(
                    video_path, game_audio_path, phone_mic_path, packet_index_path,
                    has_g, has_m, audio_delay_ms, session_end_pts_us
                )
            else:
                v_plan, g_plan, m_plan, last_v_pts_ns, last_v_dur_ns, tail_silence = self._build_legacy_plan(
                    video_path, game_audio_path, phone_mic_path,
                    has_g, has_m, audio_delay_ms
                )
        except Exception as exc:
            return False, f"Failed to build timestamp mux plan: {exc}"

        if not v_plan:
            return False, "No valid video frames found to mux"

        has_g = bool(g_plan)
        has_m = bool(m_plan)

        try:
            runtime = _get_gst_runtime()
            return self._run_appsrc_pipeline(
                runtime=runtime,
                video_path=video_path,
                game_audio_path=game_audio_path,
                phone_mic_path=phone_mic_path,
                v_plan=v_plan,
                g_plan=g_plan,
                m_plan=m_plan,
                last_v_pts_ns=last_v_pts_ns,
                last_v_dur_ns=last_v_dur_ns,
                tail_silence=tail_silence,
                has_g=has_g,
                has_m=has_m,
            )
        except Exception as exc:
            if self.on_error:
                self.on_error(str(exc))
            return False, f"GStreamer exception: {exc}"

    def _build_indexed_plan(
        self,
        video_path: Path,
        game_audio_path: Path,
        phone_mic_path: Path,
        packet_index_path: Path,
        has_g: bool,
        has_m: bool,
        audio_delay_ms: int,
        session_end_pts_us: int,
    ):
        v_raw, g_raw, m_raw = [], [], []
        idx_bytes = packet_index_path.read_bytes()
        usable = len(idx_bytes) - (len(idx_bytes) % INDEX_RECORD_SIZE)
        for pos in range(0, usable, INDEX_RECORD_SIZE):
            track, flags, pts_us, offset, size = INDEX_STRUCT.unpack_from(idx_bytes, pos)
            if size == 0:
                continue
            if track == 1:
                v_raw.append((pts_us, flags, offset, size))
            elif track == 2 and has_g:
                g_raw.append((pts_us, flags, offset, size))
            elif track == 3 and has_m:
                m_raw.append((pts_us, flags, offset, size))

        if not v_raw:
            return [], [], [], 0, 16_666_667, None

        v_media_pts0 = next((pts for pts, fl, _, _ in v_raw if (fl & 2) == 0), None)
        if v_media_pts0 is None:
            return [], [], [], 0, 16_666_667, None

        audio_firsts = []
        if g_raw:
            audio_firsts.append(g_raw[0][0])
        if m_raw:
            audio_firsts.append(m_raw[0][0])
        min_audio_pts0 = min(audio_firsts) if audio_firsts else None

        # Safety alignment if an external/legacy source recorded video on an unnormalized monotonic epoch
        v_shift_us = 0
        if min_audio_pts0 is not None and abs(v_media_pts0 - min_audio_pts0) > 60_000_000:
            v_shift_us = min_audio_pts0 - v_media_pts0

        v_shifted_media0 = v_media_pts0 + v_shift_us
        candidates = [v_shifted_media0]
        if min_audio_pts0 is not None:
            candidates.append(min_audio_pts0)
        base_pts_us = min(candidates)

        # Normalize video packets: keep codec config at first media frame PTS; enforce strict monotonic PTS
        first_v_norm_us = max(0, v_shifted_media0 - base_pts_us)
        v_plan = []  # (mode='file', pts_ns, dts_ns, dur_ns, offset, size, is_config)
        v_media_entries = []
        prev_v_us = -1

        for pts_us, fl, offset, size in v_raw:
            is_cfg = (fl & 2) != 0
            if is_cfg:
                v_plan.append(("file", first_v_norm_us * 1000, first_v_norm_us * 1000, 0, offset, size, True))
            else:
                norm_us = max(0, (pts_us + v_shift_us) - base_pts_us)
                if prev_v_us >= 0 and norm_us <= prev_v_us:
                    norm_us = prev_v_us + 1
                prev_v_us = norm_us
                idx_in_plan = len(v_plan)
                v_plan.append(["file", norm_us * 1000, norm_us * 1000, 16_666_667, offset, size, False])
                v_media_entries.append((idx_in_plan, norm_us))

        if not v_media_entries:
            return [], [], [], 0, 16_666_667, None

        # Compute video frame durations from successive PTS deltas
        deltas_us = []
        for k in range(len(v_media_entries) - 1):
            plan_idx, cur_us = v_media_entries[k]
            _, nxt_us = v_media_entries[k + 1]
            dt_us = max(1000, nxt_us - cur_us)
            deltas_us.append(dt_us)
            v_plan[plan_idx][3] = dt_us * 1000

        median_v_dur_us = sorted(deltas_us)[len(deltas_us) // 2] if deltas_us else 16_667
        median_v_dur_us = max(16_667, min(median_v_dur_us, 100_000))

        # Normalize audio packets (preserving start offsets and detecting any gaps cleanly)
        def build_audio_plan(raw_list, bytes_per_sec, file_limit):
            plan = []
            prev_end_ns = 0
            for pts_us, _, offset, size in raw_list:
                if offset >= file_limit:
                    continue
                actual_size = min(size, file_limit - offset)
                frame_align = 4 if bytes_per_sec == 192000 else 2
                actual_size -= actual_size % frame_align
                if actual_size <= 0:
                    continue
                pts_ns = max(0, (pts_us - base_pts_us) * 1000)
                if prev_end_ns > 0 and 0 < (prev_end_ns - pts_ns) < 5_000_000:
                    pts_ns = prev_end_ns
                dur_ns = (actual_size * 1_000_000_000) // bytes_per_sec
                plan.append(("file", pts_ns, GST_CLOCK_TIME_NONE, dur_ns, offset, actual_size))
                prev_end_ns = max(prev_end_ns, pts_ns + dur_ns)
            return plan, prev_end_ns

        g_size = game_audio_path.stat().st_size if (has_g and game_audio_path) else 0
        m_size = phone_mic_path.stat().st_size if (has_m and phone_mic_path) else 0
        g_plan, g_end_ns = build_audio_plan(g_raw, 48000 * 4, g_size) if has_g else ([], 0)
        m_plan, m_end_ns = build_audio_plan(m_raw, 48000 * 2, m_size) if has_m else ([], 0)

        last_v_idx, last_v_us = v_media_entries[-1]
        last_v_pts_ns = last_v_us * 1000
        max_audio_end_ns = max(g_end_ns, m_end_ns)

        # Harmonize end duration between video and audio so neither track terminates early
        target_end_ns = last_v_pts_ns + median_v_dur_us * 1000
        if session_end_pts_us is not None:
            sess_ns = max(0, (session_end_pts_us - base_pts_us) * 1000)
            if sess_ns > target_end_ns and (max_audio_end_ns == 0 or sess_ns <= max_audio_end_ns + 500_000_000):
                target_end_ns = sess_ns
        if max_audio_end_ns > target_end_ns:
            target_end_ns = max_audio_end_ns

        last_v_dur_ns = max(16_666_667, target_end_ns - last_v_pts_ns)
        v_plan[last_v_idx][3] = last_v_dur_ns

        # If video ended slightly after audio (up to 2s), pad audio tail with silence so durations match
        tail_silence = None
        if max_audio_end_ns > 0 and target_end_ns > max_audio_end_ns:
            gap_ns = target_end_ns - max_audio_end_ns
            if 20_000_000 <= gap_ns <= 2_000_000_000:
                if g_plan:
                    tail_silence = ("g", max_audio_end_ns, gap_ns)
                elif m_plan:
                    tail_silence = ("m", max_audio_end_ns, gap_ns)

        return [tuple(x) for x in v_plan], g_plan, m_plan, last_v_pts_ns, last_v_dur_ns, tail_silence

    def _build_legacy_plan(
        self,
        video_path: Path,
        game_audio_path: Path,
        phone_mic_path: Path,
        has_g: bool,
        has_m: bool,
        audio_delay_ms: int,
    ):
        raw_v = video_path.read_bytes()
        units = _split_annexb_access_units(raw_v)
        frame_dur_ns = 16_666_667  # 60 FPS fallback only when no per-frame timestamps exist
        v_plan = []
        for idx, (au_bytes, _) in enumerate(units):
            pts_ns = idx * frame_dur_ns
            v_plan.append(("mem", pts_ns, pts_ns, frame_dur_ns, au_bytes, len(au_bytes), False))

        last_v_pts_ns = (len(v_plan) - 1) * frame_dur_ns if v_plan else 0
        last_v_dur_ns = frame_dur_ns
        delay_ns = max(0, int(audio_delay_ms)) * 1_000_000

        def build_pcm_chunks(path: Path, bytes_per_sec: int, align: int):
            total = path.stat().st_size
            total -= total % align
            chunk_bytes = (bytes_per_sec // 25)  # 40ms chunks
            chunk_bytes -= chunk_bytes % align
            plan = []
            off = 0
            cur_pts_ns = delay_ns
            while off < total:
                sz = min(chunk_bytes, total - off)
                dur_ns = (sz * 1_000_000_000) // bytes_per_sec
                plan.append(("file", cur_pts_ns, GST_CLOCK_TIME_NONE, dur_ns, off, sz))
                off += sz
                cur_pts_ns += dur_ns
            return plan, cur_pts_ns

        g_plan, g_end = build_pcm_chunks(game_audio_path, 48000 * 4, 4) if has_g else ([], 0)
        m_plan, m_end = build_pcm_chunks(phone_mic_path, 48000 * 2, 2) if has_m else ([], 0)

        max_a_end = max(g_end, m_end)
        if v_plan and max_a_end > last_v_pts_ns + frame_dur_ns:
            last_v_dur_ns = max_a_end - last_v_pts_ns
            entry = list(v_plan[-1])
            entry[3] = last_v_dur_ns
            v_plan[-1] = tuple(entry)

        return v_plan, g_plan, m_plan, last_v_pts_ns, last_v_dur_ns, None

    def _run_appsrc_pipeline(
        self,
        runtime: _GstRuntime,
        video_path: Path,
        game_audio_path: Path,
        phone_mic_path: Path,
        v_plan,
        g_plan,
        m_plan,
        last_v_pts_ns: int,
        last_v_dur_ns: int,
        tail_silence,
        has_g: bool,
        has_m: bool,
    ):
        gst = runtime.gst
        gstapp = runtime.gstapp
        output_str = str(self.output_mp4_path).replace("\\", "/")

        parts = [
            f'mp4mux name=mux faststart=true ! filesink location="{output_str}"',
            'appsrc name=vsrc format=time is-live=false block=true max-bytes=8388608 '
            'caps="video/x-h264,stream-format=byte-stream" ! '
            'h264parse name=hp ! '
            'queue max-size-buffers=0 max-size-time=0 max-size-bytes=16777216 ! mux.video_0',
        ]

        if has_g or has_m:
            if has_g:
                parts.append(
                    'appsrc name=gsrc format=time is-live=false block=true max-bytes=4194304 '
                    'caps="audio/x-raw,format=S16LE,rate=48000,channels=2,layout=interleaved" ! '
                    'audioconvert ! audioresample ! mix.'
                )
            if has_m:
                parts.append(
                    'appsrc name=msrc format=time is-live=false block=true max-bytes=4194304 '
                    'caps="audio/x-raw,format=S16LE,rate=48000,channels=1,layout=interleaved" ! '
                    'audioconvert ! audioresample ! mix.'
                )
            parts.append(
                'audiomixer name=mix start-time-selection=zero ! '
                'audioconvert ! audio/x-raw,format=S16LE,rate=48000,channels=2,layout=interleaved ! '
                'voaacenc bitrate=192000 ! aacparse ! '
                'queue max-size-buffers=0 max-size-time=0 max-size-bytes=8388608 ! mux.audio_0'
            )

        pipe_desc = " ".join(parts)
        gerr = ctypes.c_void_p()
        pipeline = gst.gst_parse_launch(pipe_desc.encode("utf-8"), ctypes.byref(gerr))
        if not pipeline:
            err_msg = "Failed to construct GStreamer pipeline"
            if gerr.value:
                runtime.glib.g_error_free(gerr)
            return False, err_msg

        refs = []
        bus = None
        vsrc = gsrc = msrc = hp = hp_src = None
        probe_id = 0
        probe_cb_ref = None
        abort_event = threading.Event()

        try:
            vsrc = gst.gst_bin_get_by_name(pipeline, b"vsrc")
            hp = gst.gst_bin_get_by_name(pipeline, b"hp")
            bus = gst.gst_element_get_bus(pipeline)
            if has_g:
                gsrc = gst.gst_bin_get_by_name(pipeline, b"gsrc")
            if has_m:
                msrc = gst.gst_bin_get_by_name(pipeline, b"msrc")

            # Ensure the final video sample preserves its real duration (avoiding stts=0 at EOS)
            if hp and last_v_dur_ns > 0:
                hp_src = gst.gst_element_get_static_pad(hp, b"src")
                if hp_src:
                    pts_slot = runtime.pts_slot
                    dur_slot = runtime.dur_slot

                    def on_hp_src(_pad_ptr, info_ptr, _user_data):
                        u64s = (ctypes.c_uint64 * 4).from_address(info_ptr)
                        buf = u64s[1]
                        if buf:
                            barr = (ctypes.c_uint64 * 16).from_address(buf)
                            if barr[pts_slot] == last_v_pts_ns:
                                wbuf = gst.gst_mini_object_make_writable(buf)
                                u64s[1] = wbuf
                                warr = (ctypes.c_uint64 * 16).from_address(wbuf)
                                warr[dur_slot] = last_v_dur_ns
                        return GST_PAD_PROBE_OK

                    probe_cb_ref = runtime.probe_cb_type(on_hp_src)
                    probe_id = gst.gst_pad_add_probe(
                        hp_src, GST_PAD_PROBE_TYPE_BUFFER, probe_cb_ref, None, None
                    )

            state_ret = gst.gst_element_set_state(pipeline, GST_STATE_PLAYING)
            if state_ret == GST_STATE_CHANGE_FAILURE:
                return False, "GStreamer pipeline failed to transition to PLAYING"

            push_errors = []

            def push_video_worker():
                try:
                    with open(video_path, "rb") as vfh:
                        for mode, pts_ns, dts_ns, dur_ns, src_data, size, _ in v_plan:
                            if abort_event.is_set():
                                break
                            if mode == "file":
                                vfh.seek(src_data)
                                payload = vfh.read(size)
                            else:
                                payload = src_data
                            if not payload:
                                continue
                            buf = runtime.make_buffer(payload, pts_ns, dts_ns, dur_ns)
                            flow = gstapp.gst_app_src_push_buffer(vsrc, buf)
                            if flow != 0:
                                push_errors.append(f"vsrc flow={flow}")
                                break
                except Exception as e:
                    push_errors.append(f"vsrc exception: {e}")
                finally:
                    gstapp.gst_app_src_end_of_stream(vsrc)

            def push_audio_worker(src_elem, path: Path, plan, track_tag: str, bytes_per_sec: int, align: int):
                try:
                    with open(path, "rb") as afh:
                        for _, pts_ns, dts_ns, dur_ns, offset, size in plan:
                            if abort_event.is_set():
                                break
                            afh.seek(offset)
                            payload = afh.read(size)
                            if not payload:
                                continue
                            buf = runtime.make_buffer(payload, pts_ns, dts_ns, dur_ns)
                            flow = gstapp.gst_app_src_push_buffer(src_elem, buf)
                            if flow != 0:
                                push_errors.append(f"{track_tag} flow={flow}")
                                break
                        if (
                            not abort_event.is_set()
                            and tail_silence is not None
                            and tail_silence[0] == track_tag
                        ):
                            _, tail_pts_ns, gap_ns = tail_silence
                            tail_bytes = int((gap_ns * bytes_per_sec) // 1_000_000_000)
                            tail_bytes -= tail_bytes % align
                            if tail_bytes > 0:
                                tail_dur_ns = (tail_bytes * 1_000_000_000) // bytes_per_sec
                                buf = runtime.make_buffer(b"\x00" * tail_bytes, tail_pts_ns, GST_CLOCK_TIME_NONE, tail_dur_ns)
                                gstapp.gst_app_src_push_buffer(src_elem, buf)
                except Exception as e:
                    push_errors.append(f"{track_tag} exception: {e}")
                finally:
                    gstapp.gst_app_src_end_of_stream(src_elem)

            workers = [threading.Thread(target=push_video_worker, daemon=True)]
            if has_g and gsrc:
                workers.append(
                    threading.Thread(
                        target=push_audio_worker,
                        args=(gsrc, game_audio_path, g_plan, "g", 48000 * 4, 4),
                        daemon=True,
                    )
                )
            if has_m and msrc:
                workers.append(
                    threading.Thread(
                        target=push_audio_worker,
                        args=(msrc, phone_mic_path, m_plan, "m", 48000 * 2, 2),
                        daemon=True,
                    )
                )

            for w in workers:
                w.start()

            est_sec = max(120, int((last_v_pts_ns / 1e9) * 2) + 60)
            deadline = time.monotonic() + est_sec
            eos_ok = False
            err_detail = None

            while time.monotonic() < deadline:
                msg = gst.gst_bus_timed_pop_filtered(
                    bus, 1_000_000_000, GST_MESSAGE_EOS | GST_MESSAGE_ERROR
                )
                if not msg:
                    continue
                try:
                    msg_type = ctypes.c_uint32.from_address(msg + runtime.msg_type_offset).value
                    if msg_type & GST_MESSAGE_EOS:
                        eos_ok = True
                        break
                    if msg_type & GST_MESSAGE_ERROR:
                        err_ptr = ctypes.c_void_p()
                        dbg_ptr = ctypes.c_void_p()
                        gst.gst_message_parse_error(msg, ctypes.byref(err_ptr), ctypes.byref(dbg_ptr))
                        dbg_txt = ""
                        if dbg_ptr.value:
                            dbg_txt = ctypes.string_at(dbg_ptr.value).decode("utf-8", errors="replace")
                            runtime.glib.g_free(dbg_ptr.value)
                        if err_ptr.value:
                            runtime.glib.g_error_free(err_ptr.value)
                        err_detail = dbg_txt or "GStreamer bus error"
                        break
                finally:
                    gst.gst_mini_object_unref(msg)

            abort_event.set()
            if err_detail or not eos_ok:
                gst.gst_element_set_state(pipeline, GST_STATE_NULL)
            for w in workers:
                w.join(timeout=5.0)

            if err_detail:
                return False, f"GStreamer pipeline error: {err_detail}"
            if not eos_ok:
                return False, f"GStreamer pipeline timed out after {est_sec}s"
            if push_errors:
                return False, f"GStreamer appsrc push failed: {'; '.join(push_errors)}"
            if not self.output_mp4_path.exists() or self.output_mp4_path.stat().st_size == 0:
                return False, "GStreamer produced empty MP4 output"
            return True, str(self.output_mp4_path)
        finally:
            abort_event.set()
            gst.gst_element_set_state(pipeline, GST_STATE_NULL)
            for obj in (hp_src, hp, vsrc, gsrc, msrc, bus, pipeline):
                if obj:
                    gst.gst_object_unref(obj)
            # Keep probe_cb_ref alive until pipeline reaches NULL state
            _ = probe_cb_ref, probe_id, refs

    def _cleanup_temp(self):
        for p in (self.temp_v_file, self.temp_g_file, self.temp_m_file, self.temp_idx_file):
            try:
                if p and p.exists():
                    os.remove(p)
            except Exception:
                pass
