import socket
import struct
import threading
import time
from pathlib import Path
try:
    from muxer import MP4Muxer
    from preview_decoder import PreviewDecoder
    from gstreamer_pipeline import INDEX_STRUCT
    from utils import get_default_recordings_dir
except ImportError:
    from windows.muxer import MP4Muxer
    from windows.preview_decoder import PreviewDecoder
    from windows.gstreamer_pipeline import INDEX_STRUCT
    from windows.utils import get_default_recordings_dir

HEADER_STRUCT = struct.Struct("!4sBBHQI")
HEADER_SIZE = HEADER_STRUCT.size  # 20 bytes
FLAG_CODEC_CONFIG = 2
FLAG_EOS = 4


def _parse_sps_resolution(nal_bytes: bytes):
    """Best-effort H.264 SPS parser to extract display height label (e.g. 720p, 1080p) without external libs."""
    try:
        # Strip Annex-B start code
        pos = 0
        while pos < len(nal_bytes) - 4:
            if nal_bytes[pos:pos + 4] == b"\x00\x00\x00\x01":
                nal_start = pos + 4
            elif nal_bytes[pos:pos + 3] == b"\x00\x00\x01":
                nal_start = pos + 3
            else:
                pos += 1
                continue

            if (nal_bytes[nal_start] & 0x1F) == 7:
                # Remove emulation prevention bytes (0x00 0x00 0x03 -> 0x00 0x00)
                raw = bytearray()
                i = nal_start + 1
                end = len(nal_bytes)
                while i < end:
                    if i + 2 < end and nal_bytes[i] == 0 and nal_bytes[i + 1] == 0 and nal_bytes[i + 2] == 3:
                        raw.extend((0, 0))
                        i += 3
                    elif i + 2 < end and nal_bytes[i] == 0 and nal_bytes[i + 1] == 0 and nal_bytes[i + 2] == 1:
                        break
                    else:
                        raw.append(nal_bytes[i])
                        i += 1
                return _decode_sps_dims(bytes(raw))
            pos = nal_start + 1
    except Exception:
        pass
    return None


def _decode_sps_dims(rbsp: bytes):
    bit_pos = 0

    def read_bits(n):
        nonlocal bit_pos
        val = 0
        for _ in range(n):
            byte_idx = bit_pos >> 3
            if byte_idx >= len(rbsp):
                raise ValueError("EOF")
            bit = (rbsp[byte_idx] >> (7 - (bit_pos & 7))) & 1
            val = (val << 1) | bit
            bit_pos += 1
        return val

    def read_ue():
        zeros = 0
        while read_bits(1) == 0:
            zeros += 1
            if zeros > 31:
                raise ValueError("UE overflow")
        return (1 << zeros) - 1 + read_bits(zeros)

    def read_se():
        v = read_ue()
        return (v + 1) // 2 if (v & 1) else -(v // 2)

    profile_idc = read_bits(8)
    read_bits(16)  # constraint flags + level_idc
    read_ue()  # seq_parameter_set_id
    if profile_idc in (100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134):
        chroma_format_idc = read_ue()
        if chroma_format_idc == 3:
            read_bits(1)
        read_ue()
        read_ue()
        read_bits(1)
        if read_bits(1):
            count = 8 if chroma_format_idc != 3 else 12
            for idx in range(count):
                if read_bits(1):
                    size = 16 if idx < 6 else 64
                    last_scale = 8
                    next_scale = 8
                    for _ in range(size):
                        if next_scale != 0:
                            next_scale = (last_scale + read_se() + 256) % 256
                        last_scale = next_scale or last_scale
    read_ue()  # log2_max_frame_num_minus4
    poc_type = read_ue()
    if poc_type == 0:
        read_ue()
    elif poc_type == 1:
        read_bits(1)
        read_se()
        read_se()
        for _ in range(read_ue()):
            read_se()
    read_ue()  # max_num_ref_frames
    read_bits(1)  # gaps_in_frame_num_allowed
    pic_width_mbs = read_ue() + 1
    pic_height_map_units = read_ue() + 1
    frame_mbs_only = read_bits(1)
    if not frame_mbs_only:
        read_bits(1)
    read_bits(1)
    crop_left = crop_right = crop_top = crop_bottom = 0
    if read_bits(1):
        crop_left = read_ue()
        crop_right = read_ue()
        crop_top = read_ue()
        crop_bottom = read_ue()

    width = pic_width_mbs * 16 - (crop_left + crop_right) * 2
    height = (2 - frame_mbs_only) * pic_height_map_units * 16 - (crop_top + crop_bottom) * 2
    if width > 0 and height > 0:
        return width, height
    return None


class ReceiverCore:
    def __init__(self, port=27183, output_dir=None, on_event=None, on_preview_frame=None):
        self.port = port
        self.output_dir = Path(output_dir) if output_dir else get_default_recordings_dir()
        self.on_event = on_event
        self.on_preview_frame = on_preview_frame

        self.muxer = MP4Muxer(recordings_dir=self.output_dir)
        self.preview_decoder = PreviewDecoder(
            target_width=960,
            target_height=540,
            fps=30,
            on_frame=self._on_decoder_frame,
            on_error=self._on_decoder_error,
        )

        self.running = False
        self.is_recording = False
        self.server_socket = None
        self.listen_thread = None
        self._conn_lock = threading.Lock()
        self._active_conn = None

        # Toggles (Preview OFF by default for minimal overhead)
        self.preview_enabled = False
        self.game_audio_enabled = True
        self.phone_mic_enabled = True

        # Stats & Diagnostic Timestamps
        self.total_bytes = 0
        self.video_frames = 0
        self.game_audio_bytes = 0
        self.phone_mic_bytes = 0
        self.start_time = 0
        self.current_bitrate_mbps = 0.0
        self.video_resolution = None

        self.first_video_pts = None
        self.last_video_pts = None
        self.first_audio_pts = None
        self.last_audio_pts = None
        self.first_game_audio_pts = None
        self.last_game_audio_pts = None
        self.first_phone_mic_pts = None
        self.last_phone_mic_pts = None

        self._preview_lock = threading.Lock()
        self._codec_config_packets = []

    def start(self):
        if self.running:
            return
        self.running = True
        self.listen_thread = threading.Thread(target=self._server_loop, daemon=True)
        self.listen_thread.start()

    def stop(self):
        self.running = False
        self.request_stop_recording()
        self.preview_decoder.stop()
        if self.server_socket:
            try:
                self.server_socket.close()
            except Exception:
                pass

    def request_stop_recording(self):
        """Signals the connected Android CaptureService to end the active recording cleanly."""
        with self._conn_lock:
            conn = self._active_conn
        if not conn:
            return False
        try:
            conn.sendall(b"STOP")
            return True
        except Exception:
            return False

    def set_preview_enabled(self, enabled: bool):
        with self._preview_lock:
            self.preview_enabled = enabled
            if enabled:
                if self.is_recording:
                    self.preview_decoder.set_codec_config(self._codec_config_packets)
                    if not self.preview_decoder.running:
                        self.preview_decoder.start()
                    else:
                        self.preview_decoder.set_enabled(True)
            else:
                self.preview_decoder.set_enabled(False)
        self._notify(
            "TOGGLE_CHANGE",
            {"preview": enabled, "game_audio": self.game_audio_enabled, "phone_mic": self.phone_mic_enabled},
        )

    def set_game_audio_enabled(self, enabled: bool):
        self.game_audio_enabled = enabled
        self._notify(
            "TOGGLE_CHANGE",
            {"preview": self.preview_enabled, "game_audio": enabled, "phone_mic": self.phone_mic_enabled},
        )

    def set_phone_mic_enabled(self, enabled: bool):
        self.phone_mic_enabled = enabled
        self._notify(
            "TOGGLE_CHANGE",
            {"preview": self.preview_enabled, "game_audio": self.game_audio_enabled, "phone_mic": enabled},
        )

    def _on_decoder_frame(self, raw_frame_bytes, width, height):
        if self.on_preview_frame and self.preview_enabled:
            try:
                self.on_preview_frame(raw_frame_bytes, width, height)
            except Exception:
                pass

    def _on_decoder_error(self, err_msg):
        self._notify("LOG", f"Preview decoder: {err_msg}")

    def _notify(self, event_type, data):
        if self.on_event:
            try:
                self.on_event(event_type, data)
            except Exception:
                pass

    def _read_exact(self, conn, size):
        if size == 0:
            return b""
        chunks = bytearray()
        while len(chunks) < size:
            try:
                part = conn.recv(size - len(chunks))
                if not part:
                    return None
                chunks.extend(part)
            except Exception:
                return None
        return bytes(chunks)

    def _server_loop(self):
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.server_socket.bind(("127.0.0.1", self.port))
            self.server_socket.listen(1)
            self._notify("STATUS", "Receiver listening on local USB ADB tunnel")
        except Exception as e:
            self._notify("ERROR", f"Failed to bind local receiver socket: {str(e)}")
            self.running = False
            return

        while self.running:
            try:
                self.server_socket.settimeout(2.0)
                try:
                    conn, _addr = self.server_socket.accept()
                except socket.timeout:
                    continue

                self._handle_client(conn)
            except Exception as e:
                if self.running:
                    self._notify("LOG", f"Receiver loop notice: {str(e)}")

        self._notify("STATUS", "Receiver stopped.")

    def _handle_client(self, conn):
        with self._conn_lock:
            self._active_conn = conn
        self._notify("CONNECTED", "Phone stream connected (USB ADB)")
        self.is_recording = True
        self.total_bytes = 0
        self.video_frames = 0
        self.game_audio_bytes = 0
        self.phone_mic_bytes = 0
        self.start_time = time.time()
        self.video_resolution = None

        self.first_video_pts = None
        self.last_video_pts = None
        self.first_audio_pts = None
        self.last_audio_pts = None
        self.first_game_audio_pts = None
        self.last_game_audio_pts = None
        self.first_phone_mic_pts = None
        self.last_phone_mic_pts = None
        session_end_pts_us = None

        has_any_game_audio = False
        has_any_phone_mic = False

        with self._preview_lock:
            self._codec_config_packets = []
            self.preview_decoder.set_codec_config([])
            if self.preview_enabled:
                self.preview_decoder.start()

        temp_dir = self.output_dir / ".temp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        session_id = int(time.time() * 1000)

        video_file_path = temp_dir / f"video_{session_id}.h264"
        game_audio_path = temp_dir / f"game_audio_{session_id}.pcm"
        phone_mic_path = temp_dir / f"phone_mic_{session_id}.pcm"
        index_file_path = temp_dir / f"packets_{session_id}.idx"

        v_file = open(video_file_path, "wb")
        g_file = open(game_audio_path, "wb")
        p_file = open(phone_mic_path, "wb")
        idx_file = open(index_file_path, "wb")

        v_offset = 0
        g_offset = 0
        p_offset = 0

        last_stats_time = time.time()
        bytes_since_last_stats = 0
        frames_since_last_stats = 0

        try:
            conn.settimeout(10.0)
            while self.running:
                header_bytes = self._read_exact(conn, HEADER_SIZE)
                if not header_bytes:
                    break

                magic, track, flags, seq, pts_us, payload_size = HEADER_STRUCT.unpack(header_bytes)
                if magic != b"LCAP":
                    self._notify("LOG", f"Protocol header mismatch: {magic!r}")
                    break

                payload = self._read_exact(conn, payload_size)
                if payload is None:
                    break

                if track == 1:
                    if flags == FLAG_EOS:
                        session_end_pts_us = pts_us
                        continue

                    if (flags & FLAG_CODEC_CONFIG) != 0:
                        if self.video_resolution is None:
                            self.video_resolution = _parse_sps_resolution(payload)
                        with self._preview_lock:
                            if payload not in self._codec_config_packets and len(self._codec_config_packets) < 8:
                                self._codec_config_packets.append(payload)
                                self.preview_decoder.set_codec_config(self._codec_config_packets)
                    else:
                        if self.video_resolution is None and (flags & 1) != 0:
                            self.video_resolution = _parse_sps_resolution(payload)
                        if self.first_video_pts is None:
                            self.first_video_pts = pts_us
                        self.last_video_pts = pts_us
                        self.video_frames += 1
                        frames_since_last_stats += 1

                    if payload_size > 0:
                        v_file.write(payload)
                        idx_file.write(INDEX_STRUCT.pack(1, flags, pts_us, v_offset, payload_size))
                        v_offset += payload_size
                        if self.preview_enabled:
                            self.preview_decoder.push_packet(payload, flags)

                elif track == 2 and payload_size > 0:
                    if self.first_audio_pts is None:
                        self.first_audio_pts = pts_us
                    self.last_audio_pts = pts_us
                    if self.first_game_audio_pts is None:
                        self.first_game_audio_pts = pts_us
                    self.last_game_audio_pts = pts_us

                    if self.game_audio_enabled:
                        g_file.write(payload)
                        has_any_game_audio = True
                    else:
                        g_file.write(b"\x00" * payload_size)
                    idx_file.write(INDEX_STRUCT.pack(2, flags, pts_us, g_offset, payload_size))
                    g_offset += payload_size
                    self.game_audio_bytes += payload_size

                elif track == 3 and payload_size > 0:
                    if self.first_audio_pts is None:
                        self.first_audio_pts = pts_us
                    self.last_audio_pts = pts_us
                    if self.first_phone_mic_pts is None:
                        self.first_phone_mic_pts = pts_us
                    self.last_phone_mic_pts = pts_us

                    if self.phone_mic_enabled:
                        p_file.write(payload)
                        has_any_phone_mic = True
                    else:
                        p_file.write(b"\x00" * payload_size)
                    idx_file.write(INDEX_STRUCT.pack(3, flags, pts_us, p_offset, payload_size))
                    p_offset += payload_size
                    self.phone_mic_bytes += payload_size

                self.total_bytes += HEADER_SIZE + payload_size
                bytes_since_last_stats += HEADER_SIZE + payload_size

                now = time.time()
                if now - last_stats_time >= 1.0:
                    dt = now - last_stats_time
                    self.current_bitrate_mbps = (bytes_since_last_stats * 8.0) / (dt * 1_000_000.0)
                    fps_now = frames_since_last_stats / dt
                    bytes_since_last_stats = 0
                    frames_since_last_stats = 0
                    last_stats_time = now

                    elapsed = int(now - self.start_time)
                    res_label = (
                        f"{self.video_resolution[1]}p" if self.video_resolution else "1080p"
                    )
                    self._notify(
                        "STATS",
                        {
                            "elapsed_sec": elapsed,
                            "bitrate_mbps": round(self.current_bitrate_mbps, 2),
                            "total_mb": round(self.total_bytes / (1024 * 1024), 1),
                            "video_frames": self.video_frames,
                            "fps": round(fps_now, 1),
                            "resolution": res_label,
                        },
                    )

        except Exception as e:
            self._notify("LOG", f"Stream session ended: {str(e)}")
        finally:
            with self._conn_lock:
                self._active_conn = None

            for fh in (v_file, g_file, p_file, idx_file):
                try:
                    fh.flush()
                    fh.close()
                except Exception:
                    pass

            self.preview_decoder.stop()

            try:
                conn.close()
            except Exception:
                pass

            self.is_recording = False
            self._notify("DISCONNECTED", "Stream finished. Saving MP4...")

            video_dur_sec = 0.0
            if self.first_video_pts is not None and self.last_video_pts is not None:
                video_dur_sec = max(0.0, (self.last_video_pts - self.first_video_pts) / 1_000_000.0)

            game_dur_sec = 0.0
            if self.first_game_audio_pts is not None and self.last_game_audio_pts is not None:
                game_dur_sec = max(0.0, (self.last_game_audio_pts - self.first_game_audio_pts) / 1_000_000.0)

            mic_dur_sec = 0.0
            if self.first_phone_mic_pts is not None and self.last_phone_mic_pts is not None:
                mic_dur_sec = max(0.0, (self.last_phone_mic_pts - self.first_phone_mic_pts) / 1_000_000.0)

            audio_delay_ms = 0
            if self.first_video_pts is not None and self.first_audio_pts is not None:
                diff_us = self.first_audio_pts - self.first_video_pts
                if diff_us > 0:
                    audio_delay_ms = int(diff_us / 1000)

            avg_fps = (self.video_frames / video_dur_sec) if video_dur_sec > 0 else 0.0
            self._notify(
                "LOG",
                f"Diagnostics: Video Frames={self.video_frames} (~{avg_fps:.1f} FPS), "
                f"Span={video_dur_sec:.2f}s",
            )
            self._notify(
                "LOG",
                f"Diagnostics: GameAudio={game_dur_sec:.2f}s, PhoneMic={mic_dur_sec:.2f}s, "
                f"StartOffset={audio_delay_ms} ms",
            )

            success, result_msg = self.muxer.finalize_mp4(
                video_file_path,
                game_audio_path,
                phone_mic_path,
                custom_prefix="LiteGame",
                include_game_audio=has_any_game_audio,
                include_phone_mic=has_any_phone_mic,
                audio_delay_ms=audio_delay_ms,
                packet_index_path=index_file_path,
                session_end_pts_us=session_end_pts_us,
                on_log=lambda msg: self._notify("LOG", msg),
            )
            if success:
                self._notify("MP4_READY", result_msg)
            else:
                self._notify("MP4_ERROR", f"MP4 save failed: {result_msg}")
