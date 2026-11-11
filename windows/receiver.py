import argparse
import csv
import queue
import socket
import struct
import threading
import time
import wave
from pathlib import Path

HEADER = struct.Struct("!4sBBHQI")
PORT = 27183
TRACK_FILES = {
    1: "video.h264",
    2: "game_audio_s16le_48k_stereo.pcm",
    3: "phone_mic_s16le_48k_mono.pcm",
}

def read_exact(sock, size):
    chunks = bytearray()
    while len(chunks) < size:
        part = sock.recv(size - len(chunks))
        if not part:
            return None
        chunks.extend(part)
    return bytes(chunks)

def list_mics():
    try:
        import sounddevice as sd
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0:
                print(f'{i}: {d["name"]} (inputs={d["max_input_channels"]})')
    except Exception as exc:
        print("Could not list microphones:", exc)

def start_pc_mic(output_path, device=None):
    import sounddevice as sd
    import numpy as np

    q = queue.Queue(maxsize=100)
    stop = threading.Event()
    wav_file = wave.open(str(output_path), "wb")
    wav_file.setnchannels(1)
    wav_file.setsampwidth(2)
    wav_file.setframerate(48000)

    def callback(indata, frames, timing, status):
        if status:
            print("PC mic:", status)
        try:
            q.put_nowait(indata.copy())
        except queue.Full:
            print("PC mic buffer full; dropping mic block")

    stream = sd.InputStream(
        samplerate=48000, channels=1, dtype="int16",
        blocksize=960, callback=callback, device=device
    )
    stream.start()

    def writer():
        while not stop.is_set() or not q.empty():
            try:
                block = q.get(timeout=0.2)
                wav_file.writeframesraw(block.tobytes())
            except queue.Empty:
                pass
        wav_file.close()

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()

    def finish():
        stop.set()
        stream.stop()
        stream.close()
        thread.join(timeout=5)

    return finish

def receive(args):
    out = Path(args.output).resolve()
    out.mkdir(parents=True, exist_ok=True)
    streams = {k: open(out / name, "wb") for k, name in TRACK_FILES.items()}
    offsets = {k: 0 for k in TRACK_FILES}
    csv_file = open(out / "packets.csv", "w", newline="", encoding="utf-8")
    writer = csv.writer(csv_file)
    writer.writerow(["track", "pts_us", "flags", "offset_bytes", "payload_bytes", "arrival_monotonic_ns"])

    mic_finish = None
    if args.pc_mic:
        try:
            mic_finish = start_pc_mic(out / "pc_mic.wav", args.mic_device)
            print("PC microphone recording started.")
        except Exception as exc:
            print("PC microphone unavailable:", exc)

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", args.port))
    server.listen(1)
    print(f"Waiting on 127.0.0.1:{args.port}; run adb reverse tcp:{args.port} tcp:{args.port}")

    try:
        conn, addr = server.accept()
        print("Android connected:", addr)
        with conn:
            while True:
                raw = read_exact(conn, HEADER.size)
                if raw is None:
                    break
                magic, track, flags, reserved, pts, size = HEADER.unpack(raw)
                if magic != b"LCAP" or track not in streams or size > args.max_packet:
                    raise ValueError(f"Invalid packet: magic={magic!r}, track={track}, size={size}")
                payload = read_exact(conn, size)
                if payload is None:
                    break
                streams[track].write(payload)
                writer.writerow([track, pts, flags, offsets[track], size, time.monotonic_ns()])
                offsets[track] += size
                if args.flush_every and offsets[track] % args.flush_every < size:
                    streams[track].flush()
        print("USB stream disconnected.")
    finally:
        for f in streams.values():
            f.flush()
            f.close()
        csv_file.close()
        server.close()
        if mic_finish:
            mic_finish()
        print("Saved files to:", out)

def main():
    p = argparse.ArgumentParser(description="LowLoadCapture USB receiver")
    p.add_argument("--port", type=int, default=PORT)
    p.add_argument("--output", default="recordings")
    p.add_argument("--pc-mic", action="store_true")
    p.add_argument("--mic-device", type=int, default=None)
    p.add_argument("--list-mics", action="store_true")
    p.add_argument("--max-packet", type=int, default=8_000_000)
    p.add_argument("--flush-every", type=int, default=4_000_000)
    args = p.parse_args()
    if args.list_mics:
        list_mics()
    else:
        receive(args)

if __name__ == "__main__":
    main()
