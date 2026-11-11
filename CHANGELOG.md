# Changelog

## [1.0.0] - LiteGame Capture (2026-11-11)
Transformed and rebranded from LowLoadCapture v2.1 into **LiteGame Capture** for public open-source release.

### Added
- Selectable Android recording profiles: **Low Impact (720p30)**, **Balanced (1080p30)**, and **High Quality (1080p60)** with auto-fallback for low-end encoders.
- Remote `Start Recording` and `Stop Recording` controls from the Windows GUI (over ADB + TCP control channel), plus a Stop button in the Android foreground notification.
- Clean dark-mode Windows UI with inline status notifications (no modal pop-up dialogs) and a collapsible **Advanced Diagnostics** drawer.
- Privacy protections: `%USERPROFILE%\Videos\LiteGameCapture` output directory, redacted device status (`Android Device Connected`), and redacted path logging.
- Startup runtime dependency checker (`ADB`, `GStreamer`, `FFmpeg`) and `windows/setup_dependencies.ps1`.
- Repository documentation: `README.md`, `LICENSE` (MIT), `THIRD_PARTY_NOTICES.md`, `CONTRIBUTING.md`, `SECURITY.md`, and `.gitignore`.

### Fixed
- **Saved MP4 fast-forwarding & A/V desync:** Replaced broken `filesrc ! framerate=60/1` / FFmpeg `-r 60` muxing with an in-process GStreamer `appsrc` pipeline driven by a binary per-packet timestamp index (`packets_*.idx`), preserving true VFR timing with zero H.264 re-encoding.
- **Audio duration mismatch:** Routed single and dual audio tracks unconditionally through `audiomixer start-time-selection=zero` constrained to 48 kHz stereo AAC, and added an `h264parse:src` pad probe to preserve the final video frame's true duration.
- **Android capture optimization:** Added buffer/packet pooling in `CaptureService.kt`, `MediaCodec` `presentationTimeUs` monotonic clock normalization, `AudioRecord.getTimestamp` anchoring, `KEY_OPERATING_RATE` + 2 s keyframe interval, and thread priority tuning (`THREAD_PRIORITY_DISPLAY` / `THREAD_PRIORITY_AUDIO`).
- **Live PC preview delay & flicker:** Switched `PreviewDecoder` to `-fflags nobuffer -flags low_delay -probesize 32768 -analyzeduration 0 -fps_mode passthrough`, unbuffered pipe reads, keyframe-aware queue dropping, and in-place Tkinter canvas image updates (eliminating `delete("all")` black flashes).
