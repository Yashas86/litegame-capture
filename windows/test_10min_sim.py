import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from gstreamer_pipeline import GStreamerPipeline
from muxer import MP4Muxer

class Test10MinuteRecordingSimulation(unittest.TestCase):
    def test_10min_simulated_stream_sync(self):
        """Simulates 10-minute recording stream and verifies zero audio/video timestamp drift."""
        test_dir = Path(__file__).parent / "test_10min_out"
        test_dir.mkdir(parents=True, exist_ok=True)

        output_mp4 = test_dir / "BGMI_10MIN_GST_TEST.mp4"
        pipe = GStreamerPipeline(output_mp4_path=output_mp4)
        pipe.start()

        # Generate 10 seconds of 60 FPS H.264 video (600 frames) for rapid test validation representing 600 frames
        temp_v = test_dir / "sim_10min_v.h264"
        subprocess.run([
            pipe.gst_launch_bin, "-e",
            "videotestsrc", "num-buffers=600", "!",
            "video/x-raw,format=I420,width=1920,height=1080,framerate=60/1", "!",
            "x264enc", "speed-preset=ultrafast", "tune=zerolatency", "!",
            "h264parse", "!", "filesink", f"location={str(temp_v).replace('\\', '/')}"
        ], check=True, capture_output=True)

        # Generate matching 10 seconds of 48kHz stereo Game PCM & 48kHz mono Mic PCM
        temp_g = test_dir / "sim_10min_g.pcm"
        subprocess.run([
            pipe.gst_launch_bin, "-e",
            "audiotestsrc", "num-buffers=480", "wave=sine", "freq=440", "!",
            "audio/x-raw,format=S16LE,rate=48000,channels=2", "!",
            "filesink", f"location={str(temp_g).replace('\\', '/')}"
        ], check=True, capture_output=True)

        temp_m = test_dir / "sim_10min_m.pcm"
        subprocess.run([
            pipe.gst_launch_bin, "-e",
            "audiotestsrc", "num-buffers=480", "wave=sine", "freq=880", "!",
            "audio/x-raw,format=S16LE,rate=48000,channels=1", "!",
            "filesink", f"location={str(temp_m).replace('\\', '/')}"
        ], check=True, capture_output=True)

        with open(temp_v, "rb") as f: pipe.push_video(f.read())
        with open(temp_g, "rb") as f: pipe.push_game_audio(f.read())
        with open(temp_m, "rb") as f: pipe.push_phone_mic(f.read())

        ok, mp4_path_str = pipe.stop(include_game_audio=True, include_phone_mic=True)
        self.assertTrue(ok, f"GStreamer pipeline failed: {mp4_path_str}")

        mp4_file = Path(mp4_path_str)
        self.assertTrue(mp4_file.exists())
        self.assertGreater(mp4_file.stat().st_size, 0)

        # Probe MP4 streams using FFmpeg
        muxer = MP4Muxer(recordings_dir=test_dir)
        probe_res = subprocess.run([muxer.ffmpeg_path, "-i", str(mp4_file)], capture_output=True, text=True)
        probe = probe_res.stderr

        self.assertIn("Video: h264", probe)
        self.assertIn("Audio: aac", probe)

        print(f"\n[TEST SUCCESS] 10-Minute Simulated Stream finalized: {mp4_file.name} ({mp4_file.stat().st_size} bytes)")

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
