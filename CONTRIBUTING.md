# Contributing to LiteGame Capture

Thank you for helping improve **LiteGame Capture**!

## Guiding Principles
1. **Low-end Android performance comes first.** Avoid adding per-frame heap allocations, software pixel conversions, OpenGL renders, or excessive logging inside Android capture loops.
2. **Preserve VFR timestamps.** Never hardcode fixed frame rates when muxing variable-frame-rate `MediaProjection` streams, and never duplicate frames artificially.
3. **Protect user privacy.** Never commit personal recordings, `.mp4`/`.h264`/`.pcm` files, device serial numbers, or local user paths.

## Development Workflow
1. Fork the repository and create a feature branch from `main`.
2. Run the Windows test suite before submitting a pull request:
   ```powershell
   cd windows
   py -3.12 -m unittest test_pipeline.py test_pipeline_v21.py test_gstreamer_pipeline.py test_10min_sim.py test_vfr_sync.py -v
   ```
3. Build and test the Android APK:
   ```powershell
   cd android
   .\gradlew.bat assembleDebug
   ```
4. Submit a clear pull request describing the change, affected files, and test results.
