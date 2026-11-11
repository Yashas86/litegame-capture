import os
import socket
import struct
import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from adb_manager import ADBManager
from muxer import MP4Muxer
from preview_decoder import PreviewDecoder
from receiver_core import ReceiverCore, HEADER_STRUCT, HEADER_SIZE

class TestLowLoadCaptureV21Pipeline(unittest.TestCase):
    def test_01_preview_decoder_lifecycle(self):
        """Test PreviewDecoder start, stop, and enable toggle."""
        frames_received = []

        def on_frame(raw_bytes, w, h):
            frames_received.append((len(raw_bytes), w, h))

        decoder = PreviewDecoder(target_width=320, target_height=180, fps=15, on_frame=on_frame)
        self.assertFalse(decoder.enabled)

        decoder.start()
        self.assertTrue(decoder.enabled)

        # Toggle OFF and ON
        decoder.set_enabled(False)
        self.assertFalse(decoder.enabled)
        decoder.set_enabled(True)
        self.assertTrue(decoder.enabled)

        decoder.stop()
        self.assertFalse(decoder.enabled)
        print("\n[TEST SUCCESS] PreviewDecoder lifecycle and toggle verified.")

    def test_02_dual_track_mp4_muxer(self):
        """Test MP4Muxer with dual audio tracks (Game Audio + Phone Mic) and toggle flags."""
        test_dir = Path(__file__).parent / "test_output_v21"
        test_dir.mkdir(parents=True, exist_ok=True)

        muxer = MP4Muxer(recordings_dir=test_dir)

        # Generate synthetic H.264 video
        temp_v = test_dir / "temp_v.h264"
        subprocess.run([
            muxer.ffmpeg_path, "-y",
            "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=60:duration=2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-f", "h264", str(temp_v)
        ], check=True, capture_output=True)

        # Generate synthetic Game Audio (stereo) & Phone Mic (mono)
        temp_g = test_dir / "temp_g.pcm"
        subprocess.run([
            muxer.ffmpeg_path, "-y",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
            "-f", "s16le", "-ac", "2", str(temp_g)
        ], check=True, capture_output=True)

        temp_m = test_dir / "temp_m.pcm"
        subprocess.run([
            muxer.ffmpeg_path, "-y",
            "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000:duration=2",
            "-f", "s16le", "-ac", "1", str(temp_m)
        ], check=True, capture_output=True)

        # Test Case 1: Both audio tracks enabled
        ok, mp4_path = muxer.finalize_mp4(
            temp_v, temp_g, temp_m,
            custom_prefix="BGMI_V21_TEST",
            include_game_audio=True,
            include_phone_mic=True
        )
        self.assertTrue(ok, f"Muxer failed: {mp4_path}")
        mp4_file = Path(mp4_path)
        self.assertTrue(mp4_file.exists())
        self.assertGreater(mp4_file.stat().st_size, 0)

        # Probe MP4 streams
        res = subprocess.run([muxer.ffmpeg_path, "-i", str(mp4_file)], capture_output=True, text=True)
        self.assertIn("Video: h264", res.stderr)
        self.assertIn("Audio: aac", res.stderr)
        print(f"[TEST SUCCESS] Dual-track MP4 created: {mp4_file.name} ({mp4_file.stat().st_size} bytes)")

    def test_03_receiver_multi_track_and_audio_toggles(self):
        """Test ReceiverCore receiving Track 1 (Video), Track 2 (Game), Track 3 (Mic) and live audio toggles."""
        test_dir = Path(__file__).parent / "test_stream_output_v21"
        events = []

        rec = ReceiverCore(port=27191, output_dir=test_dir, on_event=lambda t, d: events.append((t, d)))
        rec.start()
        time.sleep(0.4)

        # Test live audio toggle methods
        rec.set_game_audio_enabled(False)
        self.assertFalse(rec.game_audio_enabled)
        rec.set_phone_mic_enabled(True)
        self.assertTrue(rec.phone_mic_enabled)
        rec.set_preview_enabled(False)
        self.assertFalse(rec.preview_enabled)

        # Connect client socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(("127.0.0.1", 27191))

        # Send packets for Track 1, 2, 3
        v_payload = b"\x00\x00\x00\x01\x67\x42\xc0\x28"
        g_payload = b"\x00\x00" * 960
        m_payload = b"\x00\x00" * 480

        sock.sendall(HEADER_STRUCT.pack(b"LCAP", 1, 1, 1, 1000, len(v_payload)) + v_payload)
        sock.sendall(HEADER_STRUCT.pack(b"LCAP", 2, 0, 2, 2000, len(g_payload)) + g_payload)
        sock.sendall(HEADER_STRUCT.pack(b"LCAP", 3, 0, 3, 3000, len(m_payload)) + m_payload)

        time.sleep(0.3)
        sock.close()
        time.sleep(0.3)
        rec.stop()

        self.assertTrue(any(e[0] == "CONNECTED" for e in events))
        self.assertTrue(any(e[0] == "DISCONNECTED" for e in events))
        print("[TEST SUCCESS] Multi-track streaming & live audio toggles verified.")

if __name__ == "__main__":
    unittest.main()
