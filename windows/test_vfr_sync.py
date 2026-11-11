import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from gstreamer_pipeline import _split_annexb_access_units
from muxer import MP4Muxer
from preview_decoder import PreviewDecoder
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
        self.assertIsNotNone(ffprobe, "ffprobe executable must be available for MP4 verification")
        res = subprocess.run(
            [
                ffprobe,
                "-v", "error",
                "-show_format",
                "-show_streams",
                "-of", "json",
                str(mp4_path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(res.stdout), ffprobe

    def test_01_vfr_with_static_pause_and_audio_offsets(self):
        """Verify 10s VFR stream (25fps + 4s static hold + 25fps) preserves real duration & A/V sync."""
        test_out = Path(__file__).parent / "test_vfr_synth_out"
        shutil.rmtree(test_out, ignore_errors=True)
        test_out.mkdir(parents=True, exist_ok=True)

        try:
            muxer = MP4Muxer(recordings_dir=test_out)
            temp_v = test_out / "vfr_test.h264"
            subprocess.run(
                [
                    muxer.ffmpeg_path, "-y",
                    "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=25:duration=6",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "25", "-bf", "0",
                    "-f", "h264", str(temp_v),
                ],
                check=True,
                capture_output=True,
            )
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

            units = _split_annexb_access_units(temp_v.read_bytes())
            g_raw = temp_g.read_bytes()
            m_raw = temp_m.read_bytes()

            events = []
            rec = ReceiverCore(port=27193, output_dir=test_out, on_event=lambda t, d: events.append((t, d)))
            rec.start()
            time.sleep(0.3)

            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.connect(("127.0.0.1", 27193))
                seq = 0
                v_schedule = []
                for idx, (au, fl) in enumerate(units):
                    pts_us = idx * 40_000 if idx < 75 else 7_000_000 + (idx - 75) * 40_000
                    v_schedule.append((pts_us, 1, fl, au))

                a_schedule = []
                g_chunk = 48000 * 4 // 25
                for i, off in enumerate(range(0, len(g_raw), g_chunk)):
                    a_schedule.append((i * 40_000, 2, 0, g_raw[off:off + g_chunk]))
                m_chunk = 48000 * 2 // 25
                for i, off in enumerate(range(0, len(m_raw), m_chunk)):
                    a_schedule.append((200_000 + i * 40_000, 3, 0, m_raw[off:off + m_chunk]))

                for pts_us, tr, fl, pay in sorted(v_schedule + a_schedule, key=lambda x: x[0]):
                    seq = (seq + 1) & 0xFFFF
                    sock.sendall(HEADER_STRUCT.pack(b"LCAP", tr, fl, seq, pts_us, len(pay)) + pay)

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
            v_stream = next(s for s in probe_json["streams"] if s["codec_type"] == "video")
            a_stream = next(s for s in probe_json["streams"] if s["codec_type"] == "audio")
            v_dur = float(v_stream["duration"])
            a_dur = float(a_stream["duration"])
            self.assertEqual(v_stream["codec_name"], "h264")
            self.assertEqual(a_stream["codec_name"], "aac")
            self.assertEqual(int(a_stream["channels"]), 2)
            self.assertAlmostEqual(v_dur, 10.0, delta=0.15)
            self.assertAlmostEqual(a_dur, 10.0, delta=0.15)
            self.assertAlmostEqual(v_dur, a_dur, delta=0.10)
            self.assertEqual(int(v_stream["nb_frames"]), len(units))
            print(
                f"\n[FFPROBE VFR ({Path(ffprobe_bin).name})] video_dur={v_dur:.3f}s, "
                f"audio_dur={a_dur:.3f}s, avg_fps={v_stream['avg_frame_rate']}, frames={v_stream['nb_frames']}"
            )
        finally:
            shutil.rmtree(test_out, ignore_errors=True)

    def test_02_live_audio_mute_and_remote_stop(self):
        """Verify live Game Audio mute toggle, mid-session mic mute, and TCP remote STOP command."""
        test_out = Path(__file__).parent / "test_vfr_mute_out"
        shutil.rmtree(test_out, ignore_errors=True)
        test_out.mkdir(parents=True, exist_ok=True)

        try:
            muxer = MP4Muxer(recordings_dir=test_out)
            temp_v = test_out / "mute_v.h264"
            subprocess.run(
                [
                    muxer.ffmpeg_path, "-y",
                    "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30:duration=4",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-bf", "0",
                    "-f", "h264", str(temp_v),
                ],
                check=True,
                capture_output=True,
            )
            units = _split_annexb_access_units(temp_v.read_bytes())

            events = []
            rec = ReceiverCore(port=27195, output_dir=test_out, on_event=lambda t, d: events.append((t, d)))
            rec.set_game_audio_enabled(False)
            rec.set_phone_mic_enabled(True)
            rec.start()
            time.sleep(0.3)

            stop_received = threading.Event()
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.connect(("127.0.0.1", 27195))

                def read_ctrl():
                    try:
                        data = sock.recv(4)
                        if data == b"STOP":
                            stop_received.set()
                    except Exception:
                        pass

                threading.Thread(target=read_ctrl, daemon=True).start()

                seq = 0
                m_chunk = b"\x01\x00" * 1920
                g_chunk = b"\x02\x00" * 3840
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
                        rec.set_phone_mic_enabled(False)
                        time.sleep(0.02)

                self.assertTrue(rec.request_stop_recording())
                self.assertTrue(stop_received.wait(timeout=2.0), "Remote STOP command not received over socket")

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
            probe_json, _ = self._probe_mp4(Path(ready[0]))
            v_stream = next(s for s in probe_json["streams"] if s["codec_type"] == "video")
            a_stream = next(s for s in probe_json["streams"] if s["codec_type"] == "audio")
            self.assertAlmostEqual(float(v_stream["duration"]), 4.0, delta=0.15)
            self.assertAlmostEqual(float(a_stream["duration"]), 4.0, delta=0.15)
            self.assertEqual(int(a_stream["channels"]), 2)
        finally:
            shutil.rmtree(test_out, ignore_errors=True)

    def test_03_low_latency_preview_decoder(self):
        """Verify PreviewDecoder produces decoded RGB frames rapidly with zero buffering stall."""
        test_out = Path(__file__).parent / "test_preview_out"
        shutil.rmtree(test_out, ignore_errors=True)
        test_out.mkdir(parents=True, exist_ok=True)
        try:
            muxer = MP4Muxer(recordings_dir=test_out)
            temp_v = test_out / "prev.h264"
            subprocess.run(
                [
                    muxer.ffmpeg_path, "-y",
                    "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=60:duration=1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-x264-params", "sliced-threads=0", "-bf", "0",
                    "-f", "h264", str(temp_v),
                ],
                check=True,
                capture_output=True,
            )
            units = _split_annexb_access_units(temp_v.read_bytes())
            frames = []
            first_frame_event = threading.Event()

            def on_frame(raw, w, h):
                frames.append((len(raw), w, h))
                first_frame_event.set()

            dec = PreviewDecoder(target_width=640, target_height=360, fps=30, on_frame=on_frame)
            dec.start()
            t0 = time.monotonic()
            for au, fl in units[:15]:
                dec.push_packet(au, fl)
                time.sleep(0.016)
            got = first_frame_event.wait(timeout=2.5)
            latency = time.monotonic() - t0
            dec.stop()
            self.assertTrue(got, "PreviewDecoder failed to output first frame within 2.5s")
            self.assertEqual(frames[0], (640 * 360 * 3, 640, 360))
            print(f"\n[PREVIEW LATENCY] First frame decoded in {latency*1000:.0f} ms, total frames={len(frames)}")
        finally:
            shutil.rmtree(test_out, ignore_errors=True)

    @staticmethod
    def _extract_video_elst_entries(mp4_bytes: bytes):
        """Parse MP4 boxes to return list of (segment_duration, media_time) in the video trak's elst."""
        def iter_boxes(start: int, end: int):
            pos = start
            while pos + 8 <= end:
                sz, typ = struct.unpack_from(">I4s", mp4_bytes, pos)
                hdr = 8
                if sz == 1:
                    sz = struct.unpack_from(">Q", mp4_bytes, pos + 8)[0]
                    hdr = 16
                elif sz == 0:
                    sz = end - pos
                if sz < hdr or pos + sz > end:
                    break
                yield typ, pos + hdr, pos + sz
                pos += sz

        for m_typ, m_s, m_e in iter_boxes(0, len(mp4_bytes)):
            if m_typ != b"moov":
                continue
            for t_typ, t_s, t_e in iter_boxes(m_s, m_e):
                if t_typ != b"trak":
                    continue
                is_video = False
                elst_entries = []
                for c_typ, c_s, c_e in iter_boxes(t_s, t_e):
                    if c_typ == b"mdia":
                        for md_typ, md_s, md_e in iter_boxes(c_s, c_e):
                            if md_typ == b"hdlr" and md_s + 12 <= md_e:
                                if mp4_bytes[md_s + 8:md_s + 12] == b"vide":
                                    is_video = True
                    elif c_typ == b"edts":
                        for e_typ, e_s, e_e in iter_boxes(c_s, c_e):
                            if e_typ == b"elst" and e_s + 8 <= e_e:
                                ver = mp4_bytes[e_s]
                                cnt = struct.unpack_from(">I", mp4_bytes, e_s + 4)[0]
                                off = e_s + 8
                                for _ in range(cnt):
                                    if ver == 1:
                                        seg_dur, med_t, _ = struct.unpack_from(">QqI", mp4_bytes, off)
                                        off += 20
                                    else:
                                        seg_dur, med_t, _ = struct.unpack_from(">IiI", mp4_bytes, off)
                                        off += 12
                                    elst_entries.append((seg_dur, med_t))
                if is_video:
                    return elst_entries
        return []

    def test_04_audio_before_video_startup_no_elst_black_gap(self):
        """Audio starting 2s before first video IDR + pre-IDR delta + duplicate CSD must produce t=0 keyframe without black gap."""
        test_out = Path(__file__).parent / "test_startup_black_out"
        shutil.rmtree(test_out, ignore_errors=True)
        test_out.mkdir(parents=True, exist_ok=True)

        try:
            muxer = MP4Muxer(recordings_dir=test_out)
            temp_v = test_out / "startup_v.h264"
            subprocess.run(
                [
                    muxer.ffmpeg_path, "-y",
                    "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=25:duration=4",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "25", "-bf", "0",
                    "-f", "h264", str(temp_v),
                ],
                check=True,
                capture_output=True,
            )
            temp_g = test_out / "startup_g.pcm"
            subprocess.run(
                [
                    muxer.ffmpeg_path, "-y",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=6",
                    "-f", "s16le", "-ac", "2", str(temp_g),
                ],
                check=True,
                capture_output=True,
            )

            units = _split_annexb_access_units(temp_v.read_bytes())
            self.assertGreaterEqual(len(units), 100)
            g_raw = temp_g.read_bytes()

            # Split SPS/PPS prefix from IDR unit[0] so we can simulate duplicate FLAG_CODEC_CONFIG packets
            au0, fl0 = units[0]
            idr_off = au0.find(b"\x00\x00\x00\x01\x65")
            if idr_off < 0:
                idr_off = au0.find(b"\x00\x00\x01\x65")
            self.assertGreater(idr_off, 0, "Expected SPS/PPS before IDR in unit[0]")
            csd_bytes = au0[:idr_off]
            idr_only = au0[idr_off:]

            events = []
            rec = ReceiverCore(port=27197, output_dir=test_out, on_event=lambda t, d: events.append((t, d)))
            rec.start()
            time.sleep(0.3)

            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.connect(("127.0.0.1", 27197))
                seq = 0
                packets = []

                # Game audio runs 0.00s .. 6.00s
                g_chunk = 48000 * 4 // 25  # 40ms
                for i, off in enumerate(range(0, len(g_raw), g_chunk)):
                    packets.append((i * 40_000, 0, 2, 0, g_raw[off:off + g_chunk]))

                # Simulate duplicate CODEC_CONFIG packets at 0ms & 100ms, orphan pre-IDR P-frame at 1.5s,
                # and first real IDR arriving 2.00s after session start (2.00s .. 6.00s)
                packets.append((0, 0, 1, 2, csd_bytes))
                packets.append((100_000, 1, 1, 2, csd_bytes))
                packets.append((1_500_000, 2, 1, 0, units[1][0]))  # orphan P-frame before first IDR
                packets.append((2_000_000, 3, 1, 1, idr_only))
                for idx in range(1, len(units)):
                    au, fl = units[idx]
                    packets.append((2_000_000 + idx * 40_000, 3 + idx, 1, fl, au))

                for pts_us, _, tr, fl, pay in sorted(packets, key=lambda x: (x[0], x[1])):
                    seq = (seq + 1) & 0xFFFF
                    sock.sendall(HEADER_STRUCT.pack(b"LCAP", tr, fl, seq, pts_us, len(pay)) + pay)

                seq = (seq + 1) & 0xFFFF
                sock.sendall(HEADER_STRUCT.pack(b"LCAP", 1, 4, seq, 6_000_000, 0))
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

            probe_json, ffprobe = self._probe_mp4(mp4_path)
            v_stream = next(s for s in probe_json["streams"] if s["codec_type"] == "video")
            a_stream = next(s for s in probe_json["streams"] if s["codec_type"] == "audio")
            self.assertAlmostEqual(float(v_stream["duration"]), 4.0, delta=0.15)
            self.assertAlmostEqual(float(a_stream["duration"]), 4.0, delta=0.15)

            pkt_res = subprocess.run(
                [
                    ffprobe, "-v", "error", "-select_streams", "v:0",
                    "-show_packets", "-show_entries", "packet=pts_time,duration_time,flags",
                    "-read_intervals", "%+2", "-of", "json", str(mp4_path),
                ],
                capture_output=True, text=True, check=True,
            )
            pkts = json.loads(pkt_res.stdout)["packets"]
            self.assertGreater(len(pkts), 5)
            self.assertLess(abs(float(pkts[0]["pts_time"])), 0.005, f"First packet delayed: {pkts[0]}")
            self.assertTrue(str(pkts[0]["flags"]).startswith("K"), f"First packet not keyframe: {pkts[0]}")

            elst = self._extract_video_elst_entries(mp4_path.read_bytes())
            self.assertTrue(elst, "Expected non-empty video elst")
            for seg_dur, med_t in elst:
                self.assertGreaterEqual(med_t, 0, f"Found empty black edit in video elst: {elst}")

            blk = subprocess.run(
                [
                    muxer.ffmpeg_path, "-v", "info", "-i", str(mp4_path),
                    "-vf", "blackdetect=d=0.05:pic_th=0.80:pix_th=0.15",
                    "-f", "null", "-",
                ],
                capture_output=True, text=True, check=True,
            )
            black_hits = re.findall(r"black_start:([0-9.]+)\s+black_end:([0-9.]+)\s+black_duration:([0-9.]+)", blk.stderr)
            self.assertEqual(black_hits, [], f"Unexpected black frames in generated MP4: {black_hits}")
        finally:
            shutil.rmtree(test_out, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
