import os
import socket
import struct
import threading
import time
from pathlib import Path
try:
    from muxer import MP4Muxer
    from preview_decoder import PreviewDecoder
    from gstreamer_pipeline import GStreamerPipeline, INDEX_STRUCT
except ImportError:
    from windows.muxer import MP4Muxer
    from windows.preview_decoder import PreviewDecoder
    from windows.gstreamer_pipeline import GStreamerPipeline, INDEX_STRUCT

HEADER_STRUCT = struct.Struct("!4sBBHQI")
HEADER_SIZE = HEADER_STRUCT.size  # 20 bytes
FLAG_CODEC_CONFIG = 2
FLAG_EOS = 4


class ReceiverCore:
    def __init__(self, port=27183, output_dir=None, on_event=None, on_preview_frame=None):
        self.port = port
        self.output_dir = Path(output_dir) if output_dir else Path(__file__).parent.parent / "recordings"
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

        # Toggles
        self.preview_enabled = False  # Preview OFF by default for 0 PC/GPU load
        self.game_audio_enabled = True
        self.phone_mic_enabled = True

        # Stats & Diagnostic Timestamps
        self.total_bytes = 0
        self.video_frames = 0
        self.game_audio_bytes = 0
        self.phone_mic_bytes = 0
        self.start_time = 0
        self.current_bitrate_mbps = 0.0

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
        self.preview_decoder.stop()
        if self.server_socket:
            try:
                self.server_socket.close()
            except Exception:
                pass

    def set_preview_enabled(self, enabled: bool):
        with self._preview_lock:
            self.preview_enabled = enabled
            if enabled:
                if self.is_recording:
                    if not self.preview_decoder.running:
                        self.preview_decoder.start()
                    else:
                        self.preview_decoder.set_enabled(True)
                    for cfg in self._codec_config_packets:
                        self.preview_decoder.push_packet(cfg)
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
        self._notify("LOG", f"Preview Hardware Decoder: {err_msg}")

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
            self._notify("STATUS", f"Receiver waiting on 127.0.0.1:{self.port}...")
        except Exception as e:
            self._notify("ERROR", f"Failed to bind socket: {str(e)}")
            self.running = False
            return

        while self.running:
            try:
                self.server_socket.settimeout(2.0)
                try:
                    conn, addr = self.server_socket.accept()
                except socket.timeout:
                    continue

                self._handle_client(conn, addr)
            except Exception as e:
                if self.running:
                    self._notify("LOG", f"Server loop info: {str(e)}")

        self._notify("STATUS", "Receiver stopped.")

    def _handle_client(self, conn, addr):
        self._notify("CONNECTED", f"Android connected from {addr[0]}:{addr[1]}")
        self.is_recording = True
        self.total_bytes = 0
        self.video_frames = 0
        self.game_audio_bytes = 0
        self.phone_mic_bytes = 0
        self.start_time = time.time()

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
                    self._notify("LOG", f"Protocol magic mismatch: {magic!r}")
                    break

                payload = self._read_exact(conn, payload_size)
                if payload is None:
                    break

                # 1. Video Stream (Track 1)
                if track == 1:
                    if flags == FLAG_EOS:
                        session_end_pts_us = pts_us
                        continue

                    if (flags & FLAG_CODEC_CONFIG) != 0:
                        with self._preview_lock:
                            if len(self._codec_config_packets) < 8:
                                self._codec_config_packets.append(payload)
                    else:
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
                            self.preview_decoder.push_packet(payload)

                # 2. Game Audio Stream (Track 2)
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

                # 3. Phone Mic Stream (Track 3)
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
                    self._notify(
                        "STATS",
                        {
                            "elapsed_sec": elapsed,
                            "bitrate_mbps": round(self.current_bitrate_mbps, 2),
                            "total_mb": round(self.total_bytes / (1024 * 1024), 1),
                            "video_frames": self.video_frames,
                            "fps": round(fps_now, 1),
                        },
                    )

        except Exception as e:
            self._notify("LOG", f"Client stream ended: {str(e)}")
        finally:
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
            self._notify("DISCONNECTED", "Stream stopped. Processing timestamps & muxing MP4...")

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
                f"STREAM DIAGNOSTICS: Video Frames={self.video_frames} (~{avg_fps:.1f} FPS), "
                f"Span={video_dur_sec:.2f}s (PTS {self.first_video_pts} -> {self.last_video_pts})",
            )
            self._notify(
                "LOG",
                f"STREAM DIAGNOSTICS: GameAudio={game_dur_sec:.2f}s, PhoneMic={mic_dur_sec:.2f}s, "
                f"FirstAudioOffset={audio_delay_ms} ms",
            )

            success, result_msg = self.muxer.finalize_mp4(
                video_file_path,
                game_audio_path,
                phone_mic_path,
                custom_prefix="BGMI",
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
                self._notify("MP4_ERROR", f"MP4 Muxing Failed: {result_msg}")
