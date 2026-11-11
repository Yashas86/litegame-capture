# LowLoadCapture v2.1 - Android-to-Windows Hardware USB Game Recorder

**LowLoadCapture v2.1** is a high-performance Android-to-Windows game recording system with a **Live Hardware PC Video Preview** and **Independent Audio Toggles** (Game Audio ON/OFF and Phone Mic ON/OFF).

Designed for minimum additional load on your Android phone while playing games like BGMI.

---

## What's New in v2.1

- **Live PC Video Preview (`preview_decoder.py`)**: Real-time H.264 video decoding happens **100% on Windows PC** using hardware acceleration (`-hwaccel auto`). The preview is downscaled to 960x540 30 FPS for smooth, low-latency display in the GUI while file recording remains 1080p60.
- **Preview ON/OFF Toggle**: Easily toggle Preview ON or OFF in the Windows GUI. Turning Preview OFF stops all decoding & rendering loops, reducing PC CPU/GPU usage to 0%. Turning Preview ON/OFF **NEVER interrupts video recording**.
- **Independent Live Audio Toggles**:
  - **Game Audio ON / OFF**
  - **Phone Mic ON / OFF**
  - Audio toggling happens live on Windows during active recording without stopping screen capture or communicating back to Android.
- **Dual-Track LCAP v2 Protocol**: Android streams `Track 1` (Video H.264), `Track 2` (Game Audio 48kHz stereo PCM), and `Track 3` (Phone Mic 48kHz mono PCM). Android performs zero PCM software mixing, keeping phone CPU overhead at absolute minimum.
- **0% PC Video Re-encoding**: Video packets (`.h264`) continue to be saved directly to disk (`-c:v copy`). Final MP4 creation remains instant with 0% CPU video re-encoding load.

---

## Project Structure

```
LowLoadCapture_v2.1/
├── android/                   # Android App (Kotlin)
│   ├── app/src/main/java/com/example/lowloadcapture/
│   │   ├── MainActivity.kt    # Dark UI, status & timer
│   │   ├── CaptureService.kt  # MediaProjection FGS & dual-track streaming
│   │   ├── EncoderChecker.kt  # Hardware 1080p60 encoder detector
│   │   └── AudioMixer.kt      # PCM audio utilities
│   └── build.gradle.kts
├── windows/                   # Windows Desktop Suite (Python 3.12)
│   ├── gui.py                 # Live Preview Canvas & Control Dashboard
│   ├── preview_decoder.py     # FFmpeg Hardware-Accelerated Live Decoder
│   ├── receiver_core.py       # LCAP v2 multi-track TCP receiver
│   ├── muxer.py               # Zero-video-reencode MP4 Muxer (FFmpeg)
│   ├── adb_manager.py         # Automated ADB reverse & USB manager
│   ├── build_windows.ps1      # PyInstaller packaging script
│   └── requirements.txt
├── dist/LowLoadCapture/       # Standalone Windows EXE Output
├── recordings/                # Finalized MP4 Output Directory
├── platform-tools/            # Bundled ADB executables
└── README.md
```

---

## Daily Recording Workflow

1. **Connect Phone**: Connect your Android phone to your PC via USB cable.
2. **Launch Windows Application**: Launch `python windows/gui.py` (or run `dist/LowLoadCapture/LowLoadCapture.exe`).
   - The GUI will display **USB Device: Connected** and automatically configure `adb reverse tcp:27183 tcp:27183`.
3. **Open Android App**: Launch **LowLoadCapture** on your phone.
4. **Start Recording**: Tap **START RECORDING** in the Android app and approve the screen capture prompt.
5. **Live Video Preview & Toggles**:
   - Observe live gameplay feed in the Windows app.
   - Click **Preview: ON/OFF** to enable/disable preview rendering.
   - Click **Game Audio: ON/OFF** or **Phone Mic: ON/OFF** to toggle audio recording live.
6. **Stop Recording**: Tap **STOP RECORDING** in the app when finished.
7. **Playable MP4 Ready**: The Windows app will automatically finalize the recording and save the MP4 in:
   ```
   LowLoadCapture_v1/recordings/BGMI_YYYY-MM-DD_HH-MM-SS.mp4
   ```
