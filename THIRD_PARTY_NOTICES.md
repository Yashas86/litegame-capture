# Third-Party Licenses & Redistribution Notices

LiteGame Capture source code is licensed under the MIT License (see `LICENSE`).
Third-party runtime dependencies are NOT committed to the source Git repository;
when obtained via `windows/setup_dependencies.ps1` or packaged into binary releases,
the following licenses apply:

---

## 1. GStreamer (1.x Runtime)
- **Website:** https://gstreamer.freedesktop.org/
- **License:** GNU Lesser General Public License v2.1 or later (**LGPL-2.1-or-later**) for GStreamer core (`libgstreamer`), `gst-plugins-base` (`app`, `audioconvert`, `audioresample`), `gst-plugins-good` (`isomp4`/`mp4mux`, `audiomixer`), and `gst-plugins-bad` (`videoparsersbad`/`h264parse`).
- **VisualOn AAC (`voaacenc`):** Apache License 2.0 (plugin wrapper LGPL-2.1+).
- **Redistribution Note:** If you distribute standalone binary packages containing GStreamer DLLs, you must include the LGPL-2.1 license notice and inform users how to obtain or replace the dynamically linked LGPL libraries. If your GStreamer installation includes optional GPL plugins (such as `gstx264`), remove unused GPL plugin DLLs before distributing under MIT/LGPL, or comply with GPL terms.

## 2. FFmpeg (Optional / Preview & Fallback Decoder)
- **Website:** https://ffmpeg.org/
- **License:** **LGPL-2.1+** or **GPLv3** depending on the build selected.
  - Note: Standard `gyan.dev` Essentials builds (`--enable-gpl --enable-version3 --enable-libx264`) are **GPLv3**.
  - **Redistribution Note:** LiteGame Capture interacts with FFmpeg via separate CLI subprocess (`ffmpeg.exe`). However, if you bundle a GPLv3 `ffmpeg.exe` binary inside a downloadable Windows release ZIP, you must include the GPLv3 license text and corresponding FFmpeg source offer/URL (e.g., https://www.gyan.dev/ffmpeg/builds/) — or substitute an LGPL-only FFmpeg build.

## 3. Android SDK Platform-Tools (`adb`)
- **Website:** https://developer.android.com/tools/releases/platform-tools
- **License:** Android Software Development Kit License Agreement (Google).
- **Redistribution Note:** Official prebuilt Google `platform-tools` binaries are governed by Google's Android SDK terms and should be downloaded by end users or setup scripts (`setup_dependencies.ps1`) rather than committed into third-party Git repositories.

## 4. Python & Android Libraries
- **Pillow:** HPND (Historical Permission Notice and Disclaimer)
- **imageio-ffmpeg:** BSD-2-Clause
- **PyInstaller:** GPL-2.0-or-later with Bootloader Exception (allows MIT-licensed executables)
- **AndroidX (`activity-ktx`, `core-ktx`):** Apache License 2.0
