import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from gstreamer_pipeline import GStreamerPipeline
from muxer import MP4Muxer

class TestGStreamerPipelineIntegration(unittest.TestCase):
    def test_01_gstreamer_runtime_discovery(self):
        """Verify GStreamer 1.26.11 binary discovery."""
        test_dir = Path(__file__).parent / "test_gst_out_v2"
        test_dir.mkdir(parents=True, exist_ok=True)

        output_mp4 = test_dir / "GST_DISCOVERY_TEST.mp4"
        pipe = GStreamerPipeline(output_mp4_path=output_mp4)

        gst_bin = pipe.gst_launch_bin
        self.assertTrue(Path(gst_bin).exists(), f"GStreamer binary missing: {gst_bin}")

        res = subprocess.run([gst_bin, "--version"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        self.assertIn("1.26", res.stdout)
        print(f"\n[TEST SUCCESS] GStreamer Runtime discovered: {res.stdout.splitlines()[0]}")

    def test_02_mp4_muxer_engine(self):
        """Verify MP4Muxer creates playable MP4 with GStreamer / FFmpeg engine."""
        test_dir = Path(__file__).parent / "test_gst_out_v2"
        test_dir.mkdir(parents=True, exist_ok=True)

        muxer = MP4Muxer(recordings_dir=test_dir)

        # 1. Generate 4 seconds of synthetic 60 FPS H.264 video
        temp_v = test_dir / "synth_v.h264"
        subprocess.run([
            muxer.ffmpeg_path, "-y",
            "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=60:duration=4",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-f", "h264", str(temp_v)
        ], check=True, capture_output=True)

        # 2. Generate 4 seconds of synthetic 48kHz Game Stereo PCM & Phone Mic Mono PCM
        temp_g = test_dir / "synth_g.pcm"
        subprocess.run([
            muxer.ffmpeg_path, "-y",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=4",
            "-f", "s16le", "-ac", "2", str(temp_g)
        ], check=True, capture_output=True)

        temp_m = test_dir / "synth_m.pcm"
        subprocess.run([
            muxer.ffmpeg_path, "-y",
            "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000:duration=4",
            "-f", "s16le", "-ac", "1", str(temp_m)
        ], check=True, capture_output=True)

        # Execute MP4 finalization
        ok, mp4_path_str = muxer.finalize_mp4(
            temp_v, temp_g, temp_m,
            custom_prefix="BGMI_GST_TEST",
            include_game_audio=True,
            include_phone_mic=True
        )
        self.assertTrue(ok, f"MP4 Muxer failed: {mp4_path_str}")

        mp4_file = Path(mp4_path_str)
        self.assertTrue(mp4_file.exists())
        self.assertGreater(mp4_file.stat().st_size, 0)

        # Probe MP4 streams using FFmpeg
        probe_res = subprocess.run([muxer.ffmpeg_path, "-i", str(mp4_file)], capture_output=True, text=True)
        probe = probe_res.stderr

        self.assertIn("Video: h264", probe)
        self.assertIn("Audio: aac", probe)

        print(f"[TEST SUCCESS] Playable MP4 finalized: {mp4_file.name} ({mp4_file.stat().st_size} bytes)")

        # Cleanup
        try:
            if mp4_file.exists(): os.remove(mp4_file)
            if temp_v.exists(): os.remove(temp_v)
            if temp_g.exists(): os.remove(temp_g)
            if temp_m.exists(): os.remove(temp_m)
            os.rmdir(test_dir)
        except Exception:
            pass

if __name__ == "__main__":
    unittest.main()
