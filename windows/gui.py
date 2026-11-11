import ctypes
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path
from PIL import Image, ImageTk

from adb_manager import ADBManager
from receiver_core import ReceiverCore

# Modern Dark Slate Palette
BG_APP = "#0B0F17"
BG_CARD = "#131A26"
BG_WELL = "#070A10"
BORDER_CARD = "#232F43"
BORDER_SUBTLE = "#1E293B"
TEXT_PRIMARY = "#F8FAFC"
TEXT_SECONDARY = "#94A3B8"
TEXT_MUTED = "#64748B"
ACCENT_BLUE = "#38BDF8"
ACCENT_GREEN = "#22C55E"
ACCENT_RED = "#EF4444"
ACCENT_AMBER = "#F59E0B"

BTN_ON_BG = "#166534"
BTN_ON_ACTIVE = "#15803D"
BTN_OFF_BG = "#1E293B"
BTN_OFF_ACTIVE = "#334155"
BTN_NEUTRAL_BG = "#1E293B"
BTN_NEUTRAL_ACTIVE = "#334155"


def _enable_dpi_awareness():
    if sys.platform == "win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


class LowLoadCaptureGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("LowLoadCapture v2.1 - Android-to-Windows Game Recorder")
        self.root.geometry("1080x760")
        self.root.minsize(920, 640)
        self.root.configure(bg=BG_APP)

        # Config & State
        self.recordings_dir = Path(__file__).parent.parent / "recordings"
        self.recordings_dir.mkdir(parents=True, exist_ok=True)

        self.receiver = None
        self.adb_mgr = None
        self.current_photo_image = None

        self._latest_frame_data = None
        self._render_scheduled = False
        self._placeholder_text = (
            "LIVE PREVIEW STANDBY\n\n"
            "Connect Android via USB & tap START RECORDING on phone.\n"
            "Toggle Preview ON anytime without affecting recording."
        )
        self._showing_live_frame = False

        self._setup_styles()
        self._build_ui()

        # Initialize ADB and Receiver
        self.adb_mgr = ADBManager(port=27183, on_status_change=self._on_adb_status)
        self.receiver = ReceiverCore(
            port=27183,
            output_dir=self.recordings_dir,
            on_event=self._on_receiver_event,
            on_preview_frame=self._on_preview_frame,
        )

        self.adb_mgr.start()
        self.receiver.start()

    def _setup_styles(self):
        self.style = ttk.Style()
        self.style.theme_use("clam")
        self.style.configure(".", background=BG_APP, foreground=TEXT_PRIMARY)
        self.style.configure("TFrame", background=BG_APP)
        self.style.configure(
            "Vertical.TScrollbar",
            background=BG_CARD,
            troughcolor=BG_WELL,
            bordercolor=BG_CARD,
            arrowcolor=TEXT_SECONDARY,
        )

    def _make_card(self, parent, padx=14, pady=12):
        card = tk.Frame(
            parent,
            bg=BG_CARD,
            highlightbackground=BORDER_CARD,
            highlightthickness=1,
            bd=0,
        )
        inner = tk.Frame(card, bg=BG_CARD, padx=padx, pady=pady)
        inner.pack(fill="both", expand=True)
        return card, inner

    def _build_ui(self):
        main_frame = tk.Frame(self.root, bg=BG_APP, padx=18, pady=16)
        main_frame.pack(fill="both", expand=True)

        # 1. Header Bar
        header_frame = tk.Frame(main_frame, bg=BG_APP)
        header_frame.pack(fill="x", pady=(0, 14))

        header_left = tk.Frame(header_frame, bg=BG_APP)
        header_left.pack(side="left", fill="x", expand=True)

        title_row = tk.Frame(header_left, bg=BG_APP)
        title_row.pack(anchor="w")

        tk.Label(
            title_row,
            text="LowLoadCapture",
            font=("Segoe UI", 18, "bold"),
            bg=BG_APP,
            fg=TEXT_PRIMARY,
        ).pack(side="left")

        tk.Label(
            title_row,
            text="v2.1",
            font=("Segoe UI", 9, "bold"),
            bg="#0F2942",
            fg=ACCENT_BLUE,
            padx=8,
            pady=2,
        ).pack(side="left", padx=(10, 0), pady=(2, 0))

        tk.Label(
            header_left,
            text="1080p60 USB Hardware Game Recorder  •  Live PC Preview  •  GStreamer VFR Sync",
            font=("Segoe UI", 9),
            bg=BG_APP,
            fg=TEXT_SECONDARY,
        ).pack(anchor="w", pady=(2, 0))

        self.session_pill = tk.Label(
            header_frame,
            text="●  STANDBY",
            font=("Segoe UI", 9, "bold"),
            bg="#172030",
            fg=ACCENT_AMBER,
            padx=12,
            pady=6,
            highlightbackground=BORDER_CARD,
            highlightthickness=1,
        )
        self.session_pill.pack(side="right", anchor="center")

        # 2. Main Body Split: Left = Live Video Preview, Right = Dashboard & Controls
        body_frame = tk.Frame(main_frame, bg=BG_APP)
        body_frame.pack(fill="both", expand=True, pady=(0, 12))

        # --- LEFT PANEL: LIVE VIDEO PREVIEW ---
        preview_card, preview_inner = self._make_card(body_frame, padx=12, pady=10)
        preview_card.pack(side="left", fill="both", expand=True, padx=(0, 14))

        preview_hdr = tk.Frame(preview_inner, bg=BG_CARD)
        preview_hdr.pack(fill="x", pady=(0, 8))

        tk.Label(
            preview_hdr,
            text="LIVE DESKTOP PREVIEW",
            font=("Segoe UI", 9, "bold"),
            bg=BG_CARD,
            fg=ACCENT_BLUE,
        ).pack(side="left")

        self.preview_badge = tk.Label(
            preview_hdr,
            text="DECODER IDLE (0% LOAD)",
            font=("Segoe UI", 8, "bold"),
            bg=BG_APP,
            fg=TEXT_MUTED,
            padx=8,
            pady=2,
        )
        self.preview_badge.pack(side="right")

        self.preview_canvas = tk.Canvas(
            preview_inner,
            bg=BG_WELL,
            highlightthickness=1,
            highlightbackground=BORDER_SUBTLE,
        )
        self.preview_canvas.pack(fill="both", expand=True)
        self.preview_canvas.bind("<Configure>", self._on_canvas_resize)

        self._show_preview_placeholder(self._placeholder_text)

        # --- RIGHT PANEL: DASHBOARD & TOGGLE CONTROLS ---
        right_panel = tk.Frame(body_frame, bg=BG_APP, width=330)
        right_panel.pack(side="right", fill="y")
        right_panel.pack_propagate(False)

        # USB Device Status Card
        card_device, dev_inner = self._make_card(right_panel, padx=14, pady=12)
        card_device.pack(fill="x", pady=(0, 10))
        tk.Label(
            dev_inner, text="USB DEVICE STATUS", font=("Segoe UI", 9, "bold"), bg=BG_CARD, fg=ACCENT_BLUE
        ).pack(anchor="w")
        self.device_val_lbl = tk.Label(
            dev_inner, text="Searching...", font=("Segoe UI", 11, "bold"), bg=BG_CARD, fg=ACCENT_AMBER
        )
        self.device_val_lbl.pack(anchor="w", pady=(6, 0))
        self.adb_val_lbl = tk.Label(
            dev_inner, text="ADB Reverse: Checking...", font=("Segoe UI", 9), bg=BG_CARD, fg=TEXT_SECONDARY
        )
        self.adb_val_lbl.pack(anchor="w", pady=(2, 0))

        # Recording Stats Card
        card_rec, rec_inner = self._make_card(right_panel, padx=14, pady=12)
        card_rec.pack(fill="x", pady=(0, 10))
        tk.Label(
            rec_inner, text="RECORDING TELEMETRY", font=("Segoe UI", 9, "bold"), bg=BG_CARD, fg=ACCENT_BLUE
        ).pack(anchor="w")
        self.timer_val_lbl = tk.Label(
            rec_inner, text="00:00:00", font=("Consolas", 22, "bold"), bg=BG_CARD, fg=TEXT_PRIMARY
        )
        self.timer_val_lbl.pack(anchor="w", pady=(4, 2))
        self.bitrate_val_lbl = tk.Label(
            rec_inner, text="Bitrate: 0.0 Mbps", font=("Segoe UI", 9), bg=BG_CARD, fg=TEXT_SECONDARY
        )
        self.bitrate_val_lbl.pack(anchor="w")
        self.total_data_lbl = tk.Label(
            rec_inner, text="Total: 0.0 MB (0 frames)", font=("Segoe UI", 9), bg=BG_CARD, fg=TEXT_SECONDARY
        )
        self.total_data_lbl.pack(anchor="w", pady=(2, 0))

        # Live Control Toggles Card
        toggles_card, toggles_inner = self._make_card(right_panel, padx=14, pady=12)
        toggles_card.pack(fill="x", pady=(0, 10))
        tk.Label(
            toggles_inner, text="LIVE CONTROL TOGGLES", font=("Segoe UI", 9, "bold"), bg=BG_CARD, fg=ACCENT_BLUE
        ).pack(anchor="w", pady=(0, 8))

        self.btn_toggle_preview = self._make_toggle_button(
            toggles_inner, "●  Preview: OFF", BTN_OFF_BG, BTN_OFF_ACTIVE, self._toggle_preview
        )
        self.btn_toggle_preview.pack(fill="x", pady=4)

        self.btn_toggle_game_audio = self._make_toggle_button(
            toggles_inner, "●  Game Audio: ON", BTN_ON_BG, BTN_ON_ACTIVE, self._toggle_game_audio
        )
        self.btn_toggle_game_audio.pack(fill="x", pady=4)

        self.btn_toggle_phone_mic = self._make_toggle_button(
            toggles_inner, "●  Phone Mic: ON", BTN_ON_BG, BTN_ON_ACTIVE, self._toggle_phone_mic
        )
        self.btn_toggle_phone_mic.pack(fill="x", pady=4)

        # Output Folder Button
        self.btn_open_folder = self._make_toggle_button(
            right_panel,
            "Open Recordings Folder",
            BTN_NEUTRAL_BG,
            BTN_NEUTRAL_ACTIVE,
            self._open_recordings_folder,
        )
        self.btn_open_folder.config(pady=10, highlightbackground=BORDER_CARD, highlightthickness=1)
        self.btn_open_folder.pack(fill="x", pady=(2, 0))

        # 3. Activity Log Console
        log_card, log_inner = self._make_card(main_frame, padx=12, pady=10)
        log_card.pack(fill="x", side="bottom")

        log_hdr = tk.Frame(log_inner, bg=BG_CARD)
        log_hdr.pack(fill="x", pady=(0, 6))

        tk.Label(
            log_hdr, text="ACTIVITY LOG", font=("Segoe UI", 9, "bold"), bg=BG_CARD, fg=ACCENT_BLUE
        ).pack(side="left")

        clear_btn = tk.Button(
            log_hdr,
            text="Clear",
            font=("Segoe UI", 8),
            bg=BG_APP,
            fg=TEXT_SECONDARY,
            activebackground=BTN_OFF_ACTIVE,
            activeforeground=TEXT_PRIMARY,
            relief="flat",
            padx=8,
            pady=1,
            cursor="hand2",
            command=self._clear_log,
        )
        clear_btn.pack(side="right")

        log_body = tk.Frame(log_inner, bg=BG_WELL)
        log_body.pack(fill="x")

        scrollbar = ttk.Scrollbar(log_body, orient="vertical")
        scrollbar.pack(side="right", fill="y")

        self.log_text = tk.Text(
            log_body,
            bg=BG_WELL,
            fg="#34D399",
            insertbackground=TEXT_PRIMARY,
            font=("Consolas", 9),
            height=6,
            relief="flat",
            padx=8,
            pady=6,
            wrap="word",
            yscrollcommand=scrollbar.set,
        )
        self.log_text.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self.log_text.yview)

        self.log_text.tag_configure("error", foreground="#F87171")
        self.log_text.tag_configure("success", foreground="#4ADE80")
        self.log_text.tag_configure("warn", foreground="#FBBF24")
        self.log_text.tag_configure("info", foreground="#94A3B8")

        self._log("LowLoadCapture v2.1 initialized (GStreamer appsrc VFR muxer active).")
        self._log(f"Recording output directory: {self.recordings_dir.resolve()}")

    def _make_toggle_button(self, parent, text, bg, active_bg, command):
        return tk.Button(
            parent,
            text=text,
            font=("Segoe UI", 10, "bold"),
            bg=bg,
            fg=TEXT_PRIMARY,
            activebackground=active_bg,
            activeforeground=TEXT_PRIMARY,
            relief="flat",
            bd=0,
            padx=12,
            pady=8,
            cursor="hand2",
            command=command,
        )

    def _on_canvas_resize(self, _event):
        if not self._showing_live_frame:
            self._show_preview_placeholder(self._placeholder_text)

    def _show_preview_placeholder(self, text):
        self._showing_live_frame = False
        self._placeholder_text = text
        self.preview_canvas.delete("all")
        w = max(self.preview_canvas.winfo_width(), 600)
        h = max(self.preview_canvas.winfo_height(), 360)
        self.preview_canvas.create_rectangle(0, 0, w, h, fill=BG_WELL, outline="")

        box_w = min(460, int(w * 0.78))
        box_h = min(150, int(h * 0.45))
        x0 = (w - box_w) // 2
        y0 = (h - box_h) // 2
        self.preview_canvas.create_rectangle(
            x0, y0, x0 + box_w, y0 + box_h, fill=BG_CARD, outline=BORDER_CARD, width=1
        )
        self.preview_canvas.create_text(
            w // 2,
            h // 2,
            text=text,
            fill=TEXT_SECONDARY,
            font=("Segoe UI", 10, "bold"),
            justify="center",
            width=box_w - 32,
        )

    def _clear_log(self):
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.config(state="disabled")

    def _log(self, msg):
        upper = msg.upper()
        tag = None
        if "ERROR" in upper or "FAILED" in upper:
            tag = "error"
        elif "SUCCESS" in upper or "MP4 CREATED" in upper:
            tag = "success"
        elif "NOTICE" in upper or "DISCONNECTED" in upper or "MUTED" in upper:
            tag = "warn"

        self.log_text.config(state="normal")
        self.log_text.insert("end", f"[{time.strftime('%H:%M:%S')}] {msg}\n", tag)
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def _open_recordings_folder(self):
        path = str(self.recordings_dir.resolve())
        if sys.platform == "win32":
            os.startfile(path)
        else:
            subprocess.run(["xdg-open", path])

    def _toggle_preview(self):
        new_state = not self.receiver.preview_enabled
        self.receiver.set_preview_enabled(new_state)
        if new_state:
            self.btn_toggle_preview.config(
                text="●  Preview: ON", bg=BTN_ON_BG, activebackground=BTN_ON_ACTIVE
            )
            self.preview_badge.config(text="HARDWARE DECODING ACTIVE", fg=ACCENT_GREEN)
            self._log("Live Video Preview ENABLED")
        else:
            self.btn_toggle_preview.config(
                text="●  Preview: OFF", bg=BTN_OFF_BG, activebackground=BTN_OFF_ACTIVE
            )
            self.preview_badge.config(text="DECODER IDLE (0% LOAD)", fg=TEXT_MUTED)
            self._show_preview_placeholder(
                "LIVE PREVIEW DISABLED\n\n(Recording active in background with 0% preview CPU/GPU load)"
            )
            self._log("Live Video Preview DISABLED (Saving PC CPU/GPU)")

    def _toggle_game_audio(self):
        new_state = not self.receiver.game_audio_enabled
        self.receiver.set_game_audio_enabled(new_state)
        if new_state:
            self.btn_toggle_game_audio.config(
                text="●  Game Audio: ON", bg=BTN_ON_BG, activebackground=BTN_ON_ACTIVE
            )
            self._log("Game Audio Recording ENABLED")
        else:
            self.btn_toggle_game_audio.config(
                text="●  Game Audio: OFF", bg=BTN_OFF_BG, activebackground=BTN_OFF_ACTIVE
            )
            self._log("Game Audio Recording MUTED")

    def _toggle_phone_mic(self):
        new_state = not self.receiver.phone_mic_enabled
        self.receiver.set_phone_mic_enabled(new_state)
        if new_state:
            self.btn_toggle_phone_mic.config(
                text="●  Phone Mic: ON", bg=BTN_ON_BG, activebackground=BTN_ON_ACTIVE
            )
            self._log("Phone Mic Recording ENABLED")
        else:
            self.btn_toggle_phone_mic.config(
                text="●  Phone Mic: OFF", bg=BTN_OFF_BG, activebackground=BTN_OFF_ACTIVE
            )
            self._log("Phone Mic Recording MUTED")

    def _on_adb_status(self, connected, device, message):
        self.root.after(0, self._update_adb_ui, connected, device, message)

    def _update_adb_ui(self, connected, device, message):
        if connected:
            self.device_val_lbl.config(text=f"Connected ({device})", fg=ACCENT_GREEN)
            self.adb_val_lbl.config(text="ADB Reverse: tcp:27183 Active", fg=ACCENT_BLUE)
            self._log(f"USB Device connected: {device} ({message})")
        else:
            self.device_val_lbl.config(text="No USB Device", fg=ACCENT_RED)
            self.adb_val_lbl.config(text="Waiting for USB connection...", fg=TEXT_SECONDARY)
            self._log("USB Device disconnected.")

    def _on_preview_frame(self, raw_bytes, width, height):
        self._latest_frame_data = (raw_bytes, width, height)
        if not self._render_scheduled:
            self._render_scheduled = True
            self.root.after(25, self._render_latest_frame)

    def _render_latest_frame(self):
        self._render_scheduled = False
        if not self.receiver or not self.receiver.preview_enabled or not self._latest_frame_data:
            return
        try:
            raw_bytes, width, height = self._latest_frame_data
            img = Image.frombytes("RGB", (width, height), raw_bytes)

            cw = self.preview_canvas.winfo_width()
            ch = self.preview_canvas.winfo_height()
            if cw > 10 and ch > 10:
                scale = min(cw / width, ch / height)
                new_w = max(1, int(width * scale))
                new_h = max(1, int(height * scale))
                img = img.resize((new_w, new_h), Image.Resampling.BILINEAR)
                ox = (cw - new_w) // 2
                oy = (ch - new_h) // 2
            else:
                ox, oy = 0, 0

            self._showing_live_frame = True
            self.current_photo_image = ImageTk.PhotoImage(img)
            self.preview_canvas.delete("all")
            self.preview_canvas.create_image(ox, oy, image=self.current_photo_image, anchor="nw")
        except Exception:
            pass

    def _on_receiver_event(self, event_type, data):
        self.root.after(0, self._update_receiver_ui, event_type, data)

    def _update_receiver_ui(self, event_type, data):
        if event_type == "STATUS":
            self._log(f"STATUS: {data}")
        elif event_type == "CONNECTED":
            self._log(f"STREAM START: {data}")
            self.session_pill.config(text="●  RECORDING LIVE", bg="#3B1219", fg=ACCENT_RED)
            self.timer_val_lbl.config(text="00:00:00", fg=ACCENT_RED)
            self.bitrate_val_lbl.config(text="Bitrate: 0.0 Mbps")
            self.total_data_lbl.config(text="Total: 0.0 MB (0 frames)")
        elif event_type == "STATS":
            sec = data["elapsed_sec"]
            h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
            fps_str = f" • {data['fps']} fps" if "fps" in data else ""
            self.timer_val_lbl.config(text=f"{h:02d}:{m:02d}:{s:02d}")
            self.bitrate_val_lbl.config(text=f"Bitrate: {data['bitrate_mbps']} Mbps")
            self.total_data_lbl.config(
                text=f"Total: {data['total_mb']} MB ({data['video_frames']} frames{fps_str})"
            )
        elif event_type == "DISCONNECTED":
            self._log(f"STREAM END: {data}")
            self.session_pill.config(text="●  FINALIZING MP4...", bg="#2E2212", fg=ACCENT_AMBER)
            self.bitrate_val_lbl.config(text="Bitrate: 0.0 Mbps")
            self._show_preview_placeholder("STREAM DISCONNECTED\n\nFinalizing MP4 Recording...")
        elif event_type == "MP4_READY":
            self._log(f"SUCCESS: MP4 Created -> {data}")
            self.session_pill.config(text="●  STANDBY", bg="#172030", fg=ACCENT_GREEN)
            self.timer_val_lbl.config(fg=TEXT_PRIMARY)
            self._show_preview_placeholder(
                "RECORDING SAVED\n\n(Tap START RECORDING on phone to capture again)"
            )
            messagebox.showinfo("Recording Saved", f"Playable MP4 file finalized successfully:\n\n{data}")
        elif event_type == "MP4_ERROR":
            self._log(f"ERROR: {data}")
            self.session_pill.config(text="●  MUX ERROR", bg="#3B1219", fg=ACCENT_RED)
            self.timer_val_lbl.config(fg=TEXT_PRIMARY)
            messagebox.showerror("MP4 Muxing Error", data)
        elif event_type == "LOG":
            self._log(data)

    def on_closing(self):
        if self.adb_mgr:
            self.adb_mgr.stop()
        if self.receiver:
            self.receiver.stop()
        self.root.destroy()


def main():
    _enable_dpi_awareness()
    root = tk.Tk()
    app = LowLoadCaptureGUI(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()


if __name__ == "__main__":
    main()
