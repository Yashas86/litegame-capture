# Windows receiver

Run `py -m pip install -r requirements.txt`.

Start receiver:
`py receiver.py --output ..\recordings`

With PC microphone:
`py receiver.py --output ..\recordings --pc-mic`

List input devices:
`py receiver.py --list-mics`

The receiver binds only to 127.0.0.1. Use `adb reverse tcp:27183 tcp:27183` while the phone is connected and authorized.
