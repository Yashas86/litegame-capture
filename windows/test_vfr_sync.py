import csv
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from gstreamer_pipeline import GStreamerPipeline
from muxer import MP4Muxer
from receiver_core import HEADER_STRUCT, ReceiverCore


def _find_ffprobe():
    found = shutil.which("ffprobe")
    if found:
        return found
    winget_root = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    if winget_root.exists():
        for cand in winget_root.rglob("ffprobe.exe"):
            return str(cand)
    return None


class TestVFRAndAVSync(unittest.TestCase):
    def _probe_mp4(self, mp4_path: Path):
        ffprobe = _find_ffprobe()
        if ffprobe:
            res = subprocess.run(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-show_format",
                    "-show_streams",
                    "-of",
                    "json",
                    str(mp4_path),
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            return json.loads(res.stdout), ffprobe
        return None, None

    def test_01_real_device_lcap_replay_over_socket(self):
        """Replay real-device LCAP capture (recordings/) through ReceiverCore -> MP4Muxer -> GStreamer."""
        rec_dir = Path(__file__).parent.parent / "recordings"
        packets_csv = rec_dir / "packets.csv"
        if not packets_csv.exists():
            self.skipTest("Real device recordings/packets.csv not present")

        v_bytes = (rec_dir / "video.h264").read_bytes()
        g_bytes = (rec_dir / "game_audio_s16le_48k_stereo.pcm").read_bytes()
        m_bytes = (rec_dir / "phone_mic_s16le_48k_mono.pcm").read_bytes()
        with packets_csv.open("r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

        # Normalize real capture's older un-zeroed Track 1 video PTS using arrival time offset
        t1_media0_pts = next(int(r["pts_us"]) for r in rows if int(r["track"]) == 1 and int(r["flags"]) != 2)
        t1_media0_arr = next(int(r["arrival_monotonic_ns"]) for r in rows if int(r["track"]) == 1 and int(r["flags"]) != 2)
        t2_0_arr = next(int(r["arrival_monotonic_ns"]) for r in rows if int(r["track"]) == 2)
        v_offset_us = max(0, (t1_media0_arr - t2_0_arr) // 1000)
        t2_0_pts = next(int(r["pts_us"]) for r in rows if int(r["track"]) == 2)

        test_out = Path(__file__).parent / "test_vfr_real_out"
        if test_out.exists():
            shutil.rmtree(test_out, ignore_errors=True)
        test_out.mkdir(parents=True, exist_ok=True)

        events = []
        rec = ReceiverCore(port=27193, output_dir=test_out, on_event=lambda t, d: events.append((t, d)))
        rec.start()
        time.sleep(0.3)

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect(("127.0.0.1", 27193))
            seq = 0
            for r in rows:
                tr = int(r["track"])
                fl = int(r["flags"])
                pts = int(r["pts_us"])
                off = int(r["offset_bytes"])
                sz = int(r["payload_bytes"])
                seq = (seq + 1) & 0xFFFF
                if tr == 1:
                    pay = v_bytes[off:off + sz]
                    send_pts = 0 if fl == 2 else max(0, (pts - t1_media0_pts) + v_offset_us)
                elif tr == 2:
                    pay = g_bytes[off:off + sz]
                    send_pts = max(0, pts - t2_0_pts)
                else:
                    pay = m_bytes[off:off + sz]
                    send_pts = max(0, pts - t2_0_pts)
                sock.sendall(HEADER_STRUCT.pack(b"LCAP", tr, fl, seq, send_pts, len(pay)) + pay)
            sock.close()

            deadline = time.time() + 30
            while time.time() < deadline:
                if any(e[0] in ("MP4_READY", "MP4_ERROR") for e in events):
                    break
                time.sleep(0.1)
        finally:
            rec.stop()

        ready = [e[1] for e in events if e[0] == "MP4_READY"]
        errs = [e[1] for e in events if e[0] == "MP4_ERROR"]
        self.assertTrue(ready, f"Expected MP4_READY, got errors={errs}, events={events}")

        mp4_path = Path(ready[0])
        self.assertTrue(mp4_path.exists())
        probe_json, ffprobe_bin = self._probe_mp4(mp4_path)
        if probe_json:
            v_stream = next(s for s in probe_json["streams"] if s["codec_type"] == "video")
            a_stream = next(s for s in probe_json["streams"] if s["codec_type"] == "audio")
            v_dur = float(v_stream["duration"])
            a_dur = float(a_stream["duration"])
            self.assertEqual(v_stream["codec_name"], "h264")
            self.assertEqual(a_stream["codec_name"], "aac")
            self.assertEqual(int(a_stream["channels"]), 2)
            self.assertEqual(int(a_stream["sample_rate"]), 48000)
            self.assertAlmostEqual(v_dur, 34.71, delta=0.25)
            self.assertAlmostEqual(a_dur, v_dur, delta=0.10)
            print(
                f"\n[FFPROBE REAL REPLAY ({ffprobe_bin})] video_dur={v_dur:.3f}s, "
                f"audio_dur={a_dur:.3f}s, avg_fps={v_stream['avg_frame_rate']}, "
                f"frames={v_stream.get('nb_frames')}"
            )

        shutil.rmtree(test_out, ignore_errors=True)

    def test_02_synthetic_vfr_with_static_pause_and_offsets(self):
        """Verify 10s VFR stream (25fps + 3s pause + 25fps) does NOT fast-forward and syncs audio."""
        test_out = Path(__file__).parent / "test_vfr_synth_out"
        if test_out.exists():
            shutil.rmtree(test_out, ignore_errors=True)
        test_out.mkdir(parents=True, exist_ok=True)

        muxer = MP4Muxer(recordings_dir=test_out)

        # Generate 150 H.264 frames (representing 3s @ 25fps + 4s static hold + 3s @ 25fps = 10s real time)
        temp_v = test_out / "vfr_test.h264"
        subprocess.run(
            [
                muxer.ffmpeg_path,
                "-y",
                "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=25:duration=6",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "25", "-bf", "0",
                "-f", "h264", str(temp_v),
            ],
            check=True,
            capture_output=True,
        )

        # Generate 10s Game Audio (48kHz stereo) and 9.8s Phone Mic (starting at +200ms)
        temp_g = test_out / "vfr_g.pcm"
        subprocess.run(
            [
                muxer.ffmpeg_path, "-y",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=10",
                "-f", "s16le", "-ac", "2", str(temp_g),
            ],
            check=True,
            capture_output=True,
        )
        temp_m = test_out / "vfr_m.pcm"
        subprocess.run(
            [
                muxer.ffmpeg_path, "-y",
                "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000:duration=9.8",
                "-f", "s16le", "-ac", "1", str(temp_m),
            ],
            check=True,
            capture_output=True,
        )

        # Split Annex-B AUs and stream over LCAP socket with 4-second pause in the middle
        from gstreamer_pipeline import _split_annexb_access_units

        units = _split_annexb_access_units(temp_v.read_bytes())
        g_raw = temp_g.read_bytes()
        m_raw = temp_m.read_bytes()

        events = []
        rec = ReceiverCore(port=27194, output_dir=test_out, on_event=lambda t, d: events.append((t, d)))
        rec.start()
        time.sleep(0.3)

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect(("127.0.0.1", 27194))
            seq = 0
            # Interleave video AUs (0..3s, gap to 7s, 7..10s) and 40ms audio packets
            v_schedule = []
            for idx, (au, fl) in enumerate(units):
                if idx < 75:
                    pts_us = idx * 40_000  # 0.00s .. 2.96s
                else:
                    pts_us = 7_000_000 + (idx - 75) * 40_000  # 7.00s .. 9.96s
                v_schedule.append((pts_us, 1, fl, au))

            a_schedule = []
            g_chunk = 48000 * 4 // 25  # 40ms = 7680 bytes
            for i, off in enumerate(range(0, len(g_raw), g_chunk)):
                a_schedule.append((i * 40_000, 2, 0, g_raw[off:off + g_chunk]))
            m_chunk = 48000 * 2 // 25  # 40ms = 3840 bytes
            for i, off in enumerate(range(0, len(m_raw), m_chunk)):
                a_schedule.append((200_000 + i * 40_000, 3, 0, m_raw[off:off + m_chunk]))

            all_pkts = sorted(v_schedule + a_schedule, key=lambda x: x[0])
            for pts_us, tr, fl, pay in all_pkts:
                seq = (seq + 1) & 0xFFFF
                sock.sendall(HEADER_STRUCT.pack(b"LCAP", tr, fl, seq, pts_us, len(pay)) + pay)
            # Send EOS marker at 10.000s
            seq = (seq + 1) & 0xFFFF
            sock.sendall(HEADER_STRUCT.pack(b"LCAP", 1, 4, seq, 10_000_000, 0))
            sock.close()

            deadline = time.time() + 30
            while time.time() < deadline:
                if any(e[0] in ("MP4_READY", "MP4_ERROR") for e in events):
                    break
                time.sleep(0.1)
        finally:
            rec.stop()

        ready = [e[1] for e in events if e[0] == "MP4_READY"]
        self.assertTrue(ready, f"Expected MP4_READY, got events={events}")

        mp4_path = Path(ready[0])
        probe_json, ffprobe_bin = self._probe_mp4(mp4_path)
        if probe_json:
            v_stream = next(s for s in probe_json["streams"] if s["codec_type"] == "video")
            a_stream = next(s for s in probe_json["streams"] if s["codec_type"] == "audio")
            v_dur = float(v_stream["duration"])
            a_dur = float(a_stream["duration"])
            self.assertAlmostEqual(v_dur, 10.0, delta=0.15)
            self.assertAlmostEqual(a_dur, 10.0, delta=0.15)
            self.assertAlmostEqual(v_dur, a_dur, delta=0.10)
            self.assertEqual(int(v_stream["nb_frames"]), len(units))
            print(
                f"\n[FFPROBE VFR SYNTH ({ffprobe_bin})] video_dur={v_dur:.3f}s, "
                f"audio_dur={a_dur:.3f}s, avg_fps={v_stream['avg_frame_rate']}, "
                f"frames={v_stream['nb_frames']}"
            )
        else:
            probe_res = subprocess.run([muxer.ffmpeg_path, "-i", str(mp4_path)], capture_output=True, text=True)
            self.assertIn("Duration: 00:00:10.", probe_res.stderr)

        shutil.rmtree(test_out, ignore_errors=True)

    def test_03_live_audio_mute_and_single_track(self):
        """Verify live Game Audio mute toggle mid-session and Phone Mic only session."""
        test_out = Path(__file__).parent / "test_vfr_mute_out"
        if test_out.exists():
            shutil.rmtree(test_out, ignore_errors=True)
        test_out.mkdir(parents=True, exist_ok=True)

        muxer = MP4Muxer(recordings_dir=test_out)
        temp_v = test_out / "mute_v.h264"
        subprocess.run(
            [
                muxer.ffmpeg_path, "-y",
                "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30:duration=4",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-bf", "0",
                "-f", "h264", str(temp_v),
            ],
            check=True,
            capture_output=True,
        )
        from gstreamer_pipeline import _split_annexb_access_units
        units = _split_annexb_access_units(temp_v.read_bytes())

        events = []
        rec = ReceiverCore(port=27195, output_dir=test_out, on_event=lambda t, d: events.append((t, d)))
        rec.set_game_audio_enabled(False)  # Start with Game Audio OFF
        rec.set_phone_mic_enabled(True)    # Only Phone Mic ON
        rec.start()
        time.sleep(0.3)

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect(("127.0.0.1", 27195))
            seq = 0
            m_chunk = b"\x01\x00" * 1920  # 40ms mono 48kHz
            g_chunk = b"\x02\x00" * 3840  # 40ms stereo 48kHz
            for idx in range(100):
                t_us = idx * 40_000
                if idx < len(units):
                    au, fl = units[idx]
                    seq = (seq + 1) & 0xFFFF
                    sock.sendall(HEADER_STRUCT.pack(b"LCAP", 1, fl, seq, t_us, len(au)) + au)
                seq = (seq + 1) & 0xFFFF
                sock.sendall(HEADER_STRUCT.pack(b"LCAP", 2, 0, seq, t_us, len(g_chunk)) + g_chunk)
                seq = (seq + 1) & 0xFFFF
                sock.sendall(HEADER_STRUCT.pack(b"LCAP", 3, 0, seq, t_us, len(m_chunk)) + m_chunk)
                if idx == 50:
                    rec.set_phone_mic_enabled(False)  # Mute Phone Mic mid-session, then finish muted!
                    time.sleep(0.05)
            seq = (seq + 1) & 0xFFFF
            sock.sendall(HEADER_STRUCT.pack(b"LCAP", 1, 4, seq, 4_000_000, 0))
            sock.close()

            deadline = time.time() + 30
            while time.time() < deadline:
                if any(e[0] in ("MP4_READY", "MP4_ERROR") for e in events):
                    break
                time.sleep(0.1)
        finally:
            rec.stop()

        ready = [e[1] for e in events if e[0] == "MP4_READY"]
        self.assertTrue(ready, f"Expected MP4_READY, got events={events}")
        mp4_path = Path(ready[0])
        probe_json, _ = self._probe_mp4(mp4_path)
        if probe_json:
            v_stream = next(s for s in probe_json["streams"] if s["codec_type"] == "video")
            a_stream = next(s for s in probe_json["streams"] if s["codec_type"] == "audio")
            self.assertAlmostEqual(float(v_stream["duration"]), 4.0, delta=0.15)
            self.assertAlmostEqual(float(a_stream["duration"]), 4.0, delta=0.15)
            self.assertEqual(int(a_stream["channels"]), 2)

        shutil.rmtree(test_out, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
