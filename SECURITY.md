# Security & Privacy Policy

## Local-Only Architecture
LiteGame Capture operates exclusively over a local USB ADB tunnel (`127.0.0.1:27183`).
- No telemetry, analytics, or crash reports are collected or transmitted.
- No cloud accounts or internet access are required for recording.
- Recordings are saved locally in `%USERPROFILE%\Videos\LiteGameCapture`.
- Android device serial numbers are redacted in UI and activity logs.

## Reporting a Vulnerability
If you discover a security or privacy issue (such as unintended network exposure, credential leaks, or unsafe subprocess execution):
1. Please do **not** open a public issue with exploit details or sensitive logs.
2. Report it privately to the repository maintainers with reproduction steps and affected versions.
3. We will review and patch verified issues before public disclosure.
