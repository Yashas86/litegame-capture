# LiteGame Capture

**Lightweight Game Recording. Less Impact. More Gameplay.**

LiteGame Capture is an open-source, low-overhead Android-to-Windows USB game recording suite built for **BGMI** and Android gaming — especially on low-end and mid-range phones.

All video preview decoding, audio mixing, AAC encoding, and MP4 muxing run on your Windows PC over USB (`adb reverse`), keeping your phone focused on running your game smoothly.

---

## Key Features

- **Low Android CPU/GPU/RAM Overhead:** Uses Android `MediaProjection` + `VirtualDisplay` + hardware `MediaCodec` H.264 surface encoding with buffer pooling — no OpenGL preview rendering on the phone, no software encoding, and zero PC-side H.264 re-encoding.
- **Selectable Quality Profiles:**
  - **Low Impact:** `720p @ 30 FPS` (`4 Mbps`) — ideal for low-end devices
  - **Balanced:** `1080p @ 30 FPS` (`6 Mbps`) — crisp 1080p for mid-range devices
  - **High Quality:** `1080p @ 60 FPS` (`8 Mbps`) — 60 FPS target when hardware-supported
- **True VFR Playback Speed & A/V Sync:** Preserves exact per-frame Android presentation timestamps (`pts_us`) through a GStreamer `appsrc` + `h264parse` + `audiomixer` + `mp4mux` pipeline, eliminating fast-forwarding and audio/video drift.
- **Independent Dual Audio:** Captures internal game audio (`AudioPlaybackCapture`, 48 kHz stereo) and phone microphone (`AudioRecord`, 48 kHz mono) separately, with live ON/OFF mute toggles on Windows.
- **Optional Low-Latency PC Preview:** OFF by default for minimal PC load; toggle ON/OFF anytime during recording without interrupting the saved MP4.
- **Privacy-First & Local-Only:** Works completely offline over USB (`127.0.0.1:27183`). No cloud accounts, no analytics, redacted device status, and recordings saved locally to `%USERPROFILE%\Videos\LiteGameCapture`.

---

## Architecture

```text
+-----------------------------------+          USB Cable (ADB Reverse TCP:27183)          +---------------------------------------+
|          Android Device           |  ================================================>  |              Windows PC               |
|                                   |                                                     |                                       |
|  VirtualDisplay -> MediaCodec     |   Track 1: H.264 NAL + monotonic pts_us             |  ReceiverCore (LCAP v2 + .idx index)  |
|  AudioPlaybackCapture (48kHz 2ch) |   Track 2: Game PCM + AudioTimestamp pts_us         |    |                                  |
|  AudioRecord MIC (48kHz 1ch)      |   Track 3: Mic PCM  + AudioTimestamp pts_us         |    +-> Optional PreviewDecoder (HW)   |
|                                   |  <==== Control Channel ("STOP") ================    |    +-> GStreamer appsrc -> mp4mux     |
+-----------------------------------+                                                     |          (Zero H.264 re-encode)       |
                                                                                          +---------------------------------------+
                                                                                                             |
                                                                                                             v
                                                                                           %USERPROFILE%\Videos\LiteGameCapture\
```

---

## System & Android Requirements

### Android Device
- **OS:** Android 10 (API 29) or newer (required for `AudioPlaybackCapture`)
- **Developer Options:** **USB Debugging** enabled
- **Hardware:** Hardware H.264 (`video/avc`) `MediaCodec` surface encoder (auto-detected on startup)
- **Cable:** Reliable USB data cable (charge-only cables will not work)

### Windows PC
- **OS:** Windows 10 or Windows 11 (64-bit)
- **For prebuilt release:** Standalone `LiteGameCapture.exe` bundle (no Python installation required)
- **For running/building from source:**
  - Python **3.12** (64-bit)
  - GStreamer 1.0 MSVC 64-bit runtime (1.26+ recommended)
  - FFmpeg executable (`ffmpeg.exe`)
  - Android SDK Platform-Tools (`adb.exe`)

---

## Quick Installation (End Users)

1. **Windows:** Download and extract the latest **LiteGame Capture Windows Release** ZIP, then run `LiteGameCapture.exe`.
2. **Android:** Install `LiteGameCapture.apk` on your phone (enable "Install unknown apps" if prompted) and grant **Microphone** + **Notifications** permissions when opening the app.
3. **Connect via USB:**
   - Enable **Developer Options** on your phone (`Settings > About phone > Tap Build number 7 times`).
   - Enable **USB Debugging** (`Settings > Developer options > USB debugging`).
   - Connect your phone to your PC with a USB cable and tap **Allow** on the *"Allow USB debugging?"* prompt.
   - Verify the Windows app shows **Android Device Connected** (`ADB Ready`).

---

## How to Record Gameplay

1. Open **LiteGame Capture** on Windows.
2. Open **LiteGame Capture** on your Android phone (or click **Start Recording** in the Windows app to bring up the capture prompt on your phone).
3. Choose a profile on the phone (**Low Impact 720p30**, **Balanced 1080p30**, or **High Quality 1080p60**) and tap **START RECORDING**.
4. Approve Android's *"Start recording or casting?"* prompt, then switch to **BGMI**.
5. *(Optional)* On Windows, toggle **Preview**, **Game Audio**, or **Phone Mic** live at any time.
6. When finished, click **Stop Recording** in the Windows app, tap **Stop** in the Android notification, or return to the Android app and tap **STOP RECORDING**.
7. Your finalized `.mp4` is saved automatically to:
   ```text
   %USERPROFILE%\Videos\LiteGameCapture\
   ```

---

## Running & Building from Source

### 1. Clone the Repository
```powershell
git clone https://github.com/<your-org>/litegame-capture.git
cd litegame-capture
```

### 2. Windows Setup & Run from Source
Run the dependency helper script (installs Python packages, provisions `ffmpeg.exe`, and downloads official Android `platform-tools`):
```powershell
powershell -ExecutionPolicy Bypass -File windows\setup_dependencies.ps1
```
Install [GStreamer 1.0 MSVC 64-bit runtime](https://gstreamer.freedesktop.org/download/) (`C:\gstreamer\1.0\msvc_x86_64` or place in `windows\gstreamer_runtime\`).

Launch from source:
```powershell
py -3.12 windows\gui.py
```

### 3. Build Windows Standalone Executable
```powershell
powershell -ExecutionPolicy Bypass -File windows\build_windows.ps1
```
Output executable: `windows\dist\LiteGameCapture\LiteGameCapture.exe`.

### 4. Build Android APK
Requirements: JDK 17 (or Android Studio JBR) and Android SDK (API 35). Set `ANDROID_HOME` (or `android\local.properties` with `sdk.dir=...`):
```powershell
cd android
.\gradlew.bat assembleDebug
```
Output APK: `android\app\build\outputs\apk\debug\app-debug.apk`.

### 5. Run Automated Windows Tests
```powershell
cd windows
py -3.12 -m unittest test_pipeline.py test_pipeline_v21.py test_gstreamer_pipeline.py test_10min_sim.py test_vfr_sync.py -v
```

---

## Troubleshooting

- **"No Device Connected" / "Authorize USB Debugging prompt on phone":**
  - Ensure your USB cable supports data transfer.
  - Unlock your phone screen and tap **Allow** on the RSA fingerprint dialog.
  - Try switching USB mode on the phone to **File Transfer (MTP)**.
- **"Missing dependency" banner on Windows:**
  - Run `windows\setup_dependencies.ps1` if running from source, or verify `platform-tools\adb.exe` and `ffmpeg.exe` exist alongside `LiteGameCapture.exe`.
- **No internal game audio recorded:**
  - Android `AudioPlaybackCapture` requires Android 10+ and respects apps that explicitly set `android:allowAudioPlaybackCapture="false"` in their manifest.
- **Recorded average FPS is below 60 FPS:**
  - LiteGame Capture records variable-frame-rate (`VFR`) video matching frames actually rendered by the phone and never duplicates frames artificially. Static screens (menus/lobbies) emit fewer frames to save battery and CPU. On low-end devices, switch to **Balanced (1080p30)** or **Low Impact (720p30)** for more consistent frame pacing.

---

## Known Limitations

- Requires a wired USB connection with USB Debugging enabled (wireless ADB is not tuned for high-bitrate 1080p60).
- Apps that opt out of `AudioPlaybackCapture` cannot have their internal audio captured without root.
- Live preview requires FFmpeg hardware/software decode on Windows and adds small PC decoding overhead when toggled ON (kept OFF by default).

---

## Privacy

- **100% Local Operation:** All video/audio streams travel over local `adb reverse` (`127.0.0.1:27183`). Nothing is uploaded to external servers.
- **No Telemetry:** Zero analytics, tracking, or account logins.
- **Privacy-Safe UI & Logs:** Device serial numbers and local Windows usernames are redacted in the UI and diagnostics log.

---

## License

LiteGame Capture source code is released under the **MIT License** — see [LICENSE](LICENSE).
Third-party components (GStreamer LGPL-2.1+, FFmpeg GPLv3/LGPL, Android SDK Platform-Tools) carry their own licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) before redistributing binary releases.
