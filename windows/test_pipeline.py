import os
import socket
import struct
import subprocess
import sys
import time
import unittest
from pathlib import Path

# Add windows dir to sys.path
sys.path.insert(0, str(Path(__file__).parent))

from adb_manager import ADBManager
from muxer import MP4Muxer
from receiver_core import ReceiverCore, HEADER_STRUCT, HEADER_SIZE

class TestLowLoadCapturePipeline(unittest.TestCase):
    def test_01_header_struct(self):
        """Test LCAP v2 header binary packing and unpacking."""
        magic = b"LCAP"
        track = 1
        flags = 2
        seq = 123
        pts = 9876543210
        size = 1024

        packed = HEADER_STRUCT.pack(magic, track, flags, seq, pts, size)
        self.assertEqual(len(packed), HEADER_SIZE)

        u_magic, u_track, u_flags, u_seq, u_pts, u_size = HEADER_STRUCT.unpack(packed)
        self.assertEqual(u_magic, magic)
        self.assertEqual(u_track, track)
        self.assertEqual(u_flags, flags)
        self.assertEqual(u_seq, seq)
        self.assertEqual(u_pts, pts)
        self.assertEqual(u_size, size)

    def test_02_mp4_muxer(self):
        """Test MP4Muxer creating a playable MP4 file from raw H.264 and PCM audio."""
        test_dir = Path(__file__).parent / "test_output"
        test_dir.mkdir(parents=True, exist_ok=True)

        muxer = MP4Muxer(recordings_dir=test_dir)

        # Generate synthetic H.264 elementary video file using FFmpeg
        temp_h264 = test_dir / "temp_synth.h264"
        cmd_gen_v = [
            muxer.ffmpeg_path, "-y",
            "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=60:duration=2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-f", "h264", str(temp_h264)
        ]
        subprocess.run(cmd_gen_v, check=True, capture_output=True)

        # Generate synthetic raw 48kHz stereo PCM audio file using FFmpeg
        temp_pcm = test_dir / "temp_synth.pcm"
        cmd_gen_a = [
            muxer.ffmpeg_path, "-y",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
            "-f", "s16le", str(temp_pcm)
        ]
        subprocess.run(cmd_gen_a, check=True, capture_output=True)

        # Execute MP4 Muxer
        ok, mp4_path_str = muxer.finalize_mp4(temp_h264, temp_pcm, custom_prefix="TEST_BGMI")
        self.assertTrue(ok, f"Muxer failed: {mp4_path_str}")

        mp4_file = Path(mp4_path_str)
        self.assertTrue(mp4_file.exists())
        self.assertGreater(mp4_file.stat().st_size, 0)

        # Inspect resulting MP4 streams using FFmpeg
        cmd_probe = [muxer.ffmpeg_path, "-i", str(mp4_file)]
        probe_res = subprocess.run(cmd_probe, capture_output=True, text=True)
        probe_output = probe_res.stderr

        self.assertIn("Video: h264", probe_output)
        self.assertIn("Audio: aac", probe_output)
        print(f"\n[TEST SUCCESS] Finalized MP4 verified: {mp4_file.name} ({mp4_file.stat().st_size} bytes)")

    def test_03_receiver_socket_stream(self):
        """Test ReceiverCore receiving LCAP packets over TCP socket."""
        test_dir = Path(__file__).parent / "test_stream_output"
        events = []

        def on_event(ev_type, data):
            events.append((ev_type, data))

        rec = ReceiverCore(port=27189, output_dir=test_dir, on_event=on_event)
        rec.start()
        time.sleep(0.5)

        # Connect client socket to test transmission
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(("127.0.0.1", 27189))

        # Send 1 Video Packet and 1 Audio Packet
        v_payload = b"\x00\x00\x00\x01\x67\x42\xc0\x28" # Dummy H.264 NAL
        a_payload = b"\x00\x00" * 960 # Dummy PCM 48kHz frame

        # Write Video Packet (Track 1)
        v_hdr = HEADER_STRUCT.pack(b"LCAP", 1, 1, 1, 1000, len(v_payload))
        sock.sendall(v_hdr + v_payload)

        # Write Audio Packet (Track 2)
        a_hdr = HEADER_STRUCT.pack(b"LCAP", 2, 0, 2, 2000, len(a_payload))
        sock.sendall(a_hdr + a_payload)

        sock.close()
        time.sleep(0.3)
        rec.stop()

        self.assertTrue(any(e[0] == "CONNECTED" for e in events))
        self.assertTrue(any(e[0] == "DISCONNECTED" for e in events))
        print("[TEST SUCCESS] Socket streaming & packet framing verified.")

if __name__ == "__main__":
    unittest.main()
