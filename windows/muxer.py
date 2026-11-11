import os
import subprocess
from datetime import datetime
from pathlib import Path
try:
    from utils import find_ffmpeg_executable, get_default_recordings_dir, get_subprocess_kwargs
    from gstreamer_pipeline import GStreamerPipeline
except ImportError:
    from windows.utils import find_ffmpeg_executable, get_default_recordings_dir, get_subprocess_kwargs
    from windows.gstreamer_pipeline import GStreamerPipeline


class MP4Muxer:
    def __init__(self, recordings_dir=None):
        if recordings_dir is None:
            self.recordings_dir = get_default_recordings_dir()
        else:
            self.recordings_dir = Path(recordings_dir)
        self.recordings_dir.mkdir(parents=True, exist_ok=True)
        self.ffmpeg_path = find_ffmpeg_executable()

    def finalize_mp4(
        self,
        video_path,
        game_audio_path=None,
        phone_mic_path=None,
        custom_prefix="LiteGame",
        include_game_audio=True,
        include_phone_mic=True,
        audio_delay_ms=0,
        packet_index_path=None,
        session_end_pts_us=None,
        on_log=None,
    ):
        """
        Muxes H.264 elementary video stream (zero video re-encoding)
        and active PCM audio streams (-c:a aac) into a single standard MP4 file
        using the native GStreamer appsrc media pipeline.
        """
        now_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        output_filename = f"{custom_prefix}_{now_str}.mp4"
        output_path = self.recordings_dir / output_filename

        video_path = Path(video_path)
        if not video_path.exists() or video_path.stat().st_size == 0:
            return False, f"Video stream file is missing or empty: {video_path}"

        # 1. Primary: Native GStreamer appsrc Media Pipeline (preserves exact VFR & A/V PTS)
        gst_err = None
        try:
            gst_pipe = GStreamerPipeline(output_mp4_path=output_path, on_status=on_log, on_error=on_log)
            ok, msg = gst_pipe.mux_from_index(
                video_path=video_path,
                game_audio_path=game_audio_path,
                phone_mic_path=phone_mic_path,
                packet_index_path=packet_index_path,
                include_game_audio=include_game_audio,
                include_phone_mic=include_phone_mic,
                audio_delay_ms=audio_delay_ms,
                session_end_pts_us=session_end_pts_us,
            )

            if ok and output_path.exists() and output_path.stat().st_size > 0:
                self._cleanup_files(video_path, game_audio_path, phone_mic_path, packet_index_path)
                return True, str(output_path)
            gst_err = msg
        except Exception as exc:
            gst_err = str(exc)

        if gst_err and on_log:
            on_log(f"GStreamer pipeline fallback notice: {gst_err}")

        # 2. Fallback to FFmpeg engine if GStreamer pipeline is unavailable
        has_game_audio = (
            include_game_audio
            and game_audio_path
            and Path(game_audio_path).exists()
            and Path(game_audio_path).stat().st_size > 0
        )
        has_phone_mic = (
            include_phone_mic
            and phone_mic_path
            and Path(phone_mic_path).exists()
            and Path(phone_mic_path).stat().st_size > 0
        )

        cmd = [
            self.ffmpeg_path,
            "-y",
            "-f", "h264",
            "-r", "60",
            "-i", str(video_path),
        ]

        delay_filter = f",adelay={audio_delay_ms}|{audio_delay_ms}" if audio_delay_ms > 0 else ""

        if has_game_audio and has_phone_mic:
            filter_str = f"[1:a][2:a]amerge=inputs=2,pan=stereo|c0<c0+c2|c1<c1+c2{delay_filter}[aout]"
            cmd.extend([
                "-f", "s16le", "-ar", "48000", "-ac", "2", "-i", str(game_audio_path),
                "-f", "s16le", "-ar", "48000", "-ac", "1", "-i", str(phone_mic_path),
                "-filter_complex", filter_str,
                "-map", "0:v:0",
                "-map", "[aout]",
                "-c:v", "copy",
                "-c:a", "aac",
                "-b:a", "192k",
                "-movflags", "+faststart",
                str(output_path),
            ])
        elif has_game_audio:
            filter_str = f"adelay={audio_delay_ms}|{audio_delay_ms}" if audio_delay_ms > 0 else ""
            audio_args = ["-af", filter_str] if filter_str else []
            cmd.extend([
                "-f", "s16le", "-ar", "48000", "-ac", "2", "-i", str(game_audio_path),
                "-map", "0:v:0",
                "-map", "1:a:0",
                *audio_args,
                "-c:v", "copy",
                "-c:a", "aac",
                "-b:a", "192k",
                "-movflags", "+faststart",
                str(output_path),
            ])
        elif has_phone_mic:
            filter_str = f"adelay={audio_delay_ms}|{audio_delay_ms}" if audio_delay_ms > 0 else ""
            audio_args = ["-af", filter_str] if filter_str else []
            cmd.extend([
                "-f", "s16le", "-ar", "48000", "-ac", "1", "-i", str(phone_mic_path),
                "-map", "0:v:0",
                "-map", "1:a:0",
                *audio_args,
                "-c:v", "copy",
                "-c:a", "aac",
                "-b:a", "192k",
                "-movflags", "+faststart",
                str(output_path),
            ])
        else:
            cmd.extend([
                "-c:v", "copy",
                "-movflags", "+faststart",
                str(output_path),
            ])

        try:
            timeout_sec = max(120, int(video_path.stat().st_size / (1024 * 1024)) + 120)
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec, **get_subprocess_kwargs())
            if res.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
                self._cleanup_files(video_path, game_audio_path, phone_mic_path, packet_index_path)
                return True, str(output_path)
            else:
                return False, f"Muxing error: {res.stderr.strip() if res.stderr else 'Process failed with exit code ' + str(res.returncode)}"
        except Exception as e:
            return False, f"Muxing exception: {str(e)}"

    @staticmethod
    def _cleanup_files(*paths):
        for p in paths:
            if p:
                try:
                    fp = Path(p)
                    if fp.exists():
                        os.remove(fp)
                except Exception:
                    pass
