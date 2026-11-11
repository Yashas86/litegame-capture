import ctypes
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk
from pathlib import Path
from PIL import Image, ImageTk

from adb_manager import ADBManager
from receiver_core import ReceiverCore
from utils import check_runtime_dependencies, get_default_recordings_dir, redact_path

# Clean Dark Slate Theme
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

BTN_START_BG = "#16A34A"
BTN_START_ACTIVE = "#15803D"
BTN_STOP_BG = "#DC2626"
BTN_STOP_ACTIVE = "#B91C1C"
BTN_ON_BG = "#166534"
BTN_ON_ACTIVE = "#15803D"
BTN_OFF_BG = "#1E293B"
BTN_OFF_ACTIVE = "#334155"


def _enable_dpi_awareness():
    if sys.platform == "win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


class LiteGameCaptureGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("LiteGame Capture")
        self.root.geometry("1040x700")
        self.root.minsize(880, 600)
        self.root.configure(bg=BG_APP)

        self.recordings_dir = get_default_recordings_dir()

        self.receiver = None
        self.adb_mgr = None
        self.current_photo_image = None
        self._preview_image_item = None
        self._placeholder_items = []
        self._last_canvas_size = (0, 0)
        self._last_render_ts = 0.0

        self._latest_frame_data = None
        self._render_scheduled = False
        self._frame_lock = threading.Lock()
        self._showing_live_frame = False
        self._diag_expanded = False
        self._device_connected = False
        self._placeholder_text = (
            "Preview is OFF\n\n"
            "Connect your phone via USB and start recording.\n"
            "Turn Preview ON anytime without affecting gameplay."
        )

        self._setup_styles()
        self._build_ui()
        self._run_dependency_check()

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
        main_frame = tk.Frame(self.root, bg=BG_APP, padx=18, pady=14)
        main_frame.pack(fill="both", expand=True)

        # 1. HEADER
        header_frame = tk.Frame(main_frame, bg=BG_APP)
        header_frame.pack(fill="x", pady=(0, 12))

        header_left = tk.Frame(header_frame, bg=BG_APP)
        header_left.pack(side="left", fill="x", expand=True)

        title_row = tk.Frame(header_left, bg=BG_APP)
        title_row.pack(anchor="w")

        tk.Label(
            title_row,
            text="LG",
            font=("Segoe UI", 11, "bold"),
            bg="#0F2942",
            fg=ACCENT_BLUE,
            padx=8,
            pady=3,
        ).pack(side="left", padx=(0, 10))

        tk.Label(
            title_row,
            text="LiteGame Capture",
            font=("Segoe UI", 18, "bold"),
            bg=BG_APP,
            fg=TEXT_PRIMARY,
        ).pack(side="left")

        tk.Label(
            header_left,
            text="Lightweight Game Recording. Less Impact. More Gameplay.",
            font=("Segoe UI", 9),
            bg=BG_APP,
            fg=TEXT_SECONDARY,
        ).pack(anchor="w", pady=(3, 0))

        self.conn_pill = tk.Label(
            header_frame,
            text="●  Disconnected",
            font=("Segoe UI", 9, "bold"),
            bg="#172030",
            fg=TEXT_MUTED,
            padx=12,
            pady=6,
            highlightbackground=BORDER_CARD,
            highlightthickness=1,
        )
        self.conn_pill.pack(side="right", anchor="center")

        # 2. MAIN AREA
        body_frame = tk.Frame(main_frame, bg=BG_APP)
        body_frame.pack(fill="both", expand=True, pady=(0, 10))

        # --- LEFT: OPTIONAL LIVE PREVIEW ---
        preview_card, preview_inner = self._make_card(body_frame, padx=12, pady=10)
        preview_card.pack(side="left", fill="both", expand=True, padx=(0, 14))

        preview_hdr = tk.Frame(preview_inner, bg=BG_CARD)
        preview_hdr.pack(fill="x", pady=(0, 8))

        tk.Label(
            preview_hdr,
            text="LIVE PREVIEW",
            font=("Segoe UI", 9, "bold"),
            bg=BG_CARD,
            fg=ACCENT_BLUE,
        ).pack(side="left")

        self.preview_badge = tk.Label(
            preview_hdr,
            text="OFF",
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

        # --- RIGHT: SIMPLE CONTROLS & STATUS ---
        right_panel = tk.Frame(body_frame, bg=BG_APP, width=320)
        right_panel.pack(side="right", fill="y")
        right_panel.pack_propagate(False)

        # Device Status Card
        card_dev, dev_inner = self._make_card(right_panel, padx=14, pady=10)
        card_dev.pack(fill="x", pady=(0, 10))
        tk.Label(
            dev_inner, text="DEVICE STATUS", font=("Segoe UI", 9, "bold"), bg=BG_CARD, fg=ACCENT_BLUE
        ).pack(anchor="w")
        self.device_val_lbl = tk.Label(
            dev_inner, text="No Device Connected", font=("Segoe UI", 10, "bold"), bg=BG_CARD, fg=ACCENT_AMBER
        )
        self.device_val_lbl.pack(anchor="w", pady=(4, 0))
        self.adb_val_lbl = tk.Label(
            dev_inner,
            text="ADB: Connect phone with USB Debugging",
            font=("Segoe UI", 9),
            bg=BG_CARD,
            fg=TEXT_SECONDARY,
            wraplength=286,
            justify="left",
        )
        self.adb_val_lbl.pack(anchor="w", pady=(2, 0))

        # Recording Status Card
        card_rec, rec_inner = self._make_card(right_panel, padx=14, pady=10)
        card_rec.pack(fill="x", pady=(0, 10))

        rec_hdr = tk.Frame(rec_inner, bg=BG_CARD)
        rec_hdr.pack(fill="x")
        tk.Label(
            rec_hdr, text="RECORDING", font=("Segoe UI", 9, "bold"), bg=BG_CARD, fg=ACCENT_BLUE
        ).pack(side="left")
        self.rec_status_lbl = tk.Label(
            rec_hdr, text="Ready", font=("Segoe UI", 9, "bold"), bg=BG_CARD, fg=ACCENT_GREEN
        )
        self.rec_status_lbl.pack(side="right")

        self.timer_val_lbl = tk.Label(
            rec_inner, text="00:00:00", font=("Consolas", 22, "bold"), bg=BG_CARD, fg=TEXT_PRIMARY
        )
        self.timer_val_lbl.pack(anchor="w", pady=(4, 2))

        self.res_fps_lbl = tk.Label(
            rec_inner, text="Video: Ready (1080p / 720p VFR)", font=("Segoe UI", 9), bg=BG_CARD, fg=TEXT_SECONDARY
        )
        self.res_fps_lbl.pack(anchor="w")

        self.total_data_lbl = tk.Label(
            rec_inner, text="File Size: 0.0 MB", font=("Segoe UI", 9), bg=BG_CARD, fg=TEXT_SECONDARY
        )
        self.total_data_lbl.pack(anchor="w", pady=(2, 0))

        # Controls Card
        ctrl_card, ctrl_inner = self._make_card(right_panel, padx=14, pady=10)
        ctrl_card.pack(fill="x", pady=(0, 10))
        tk.Label(
            ctrl_inner, text="CONTROLS", font=("Segoe UI", 9, "bold"), bg=BG_CARD, fg=ACCENT_BLUE
        ).pack(anchor="w", pady=(0, 6))

        rec_btn_row = tk.Frame(ctrl_inner, bg=BG_CARD)
        rec_btn_row.pack(fill="x", pady=(0, 8))

        self.btn_start_rec = tk.Button(
            rec_btn_row,
            text="Start Recording",
            font=("Segoe UI", 9, "bold"),
            bg=BTN_START_BG,
            fg=TEXT_PRIMARY,
            activebackground=BTN_START_ACTIVE,
            activeforeground=TEXT_PRIMARY,
            relief="flat",
            bd=0,
            pady=8,
            cursor="hand2",
            command=self._start_recording,
        )
        self.btn_start_rec.pack(side="left", fill="x", expand=True, padx=(0, 4))

        self.btn_stop_rec = tk.Button(
            rec_btn_row,
            text="Stop Recording",
            font=("Segoe UI", 9, "bold"),
            bg=BTN_STOP_BG,
            fg=TEXT_PRIMARY,
            activebackground=BTN_STOP_ACTIVE,
            activeforeground=TEXT_PRIMARY,
            relief="flat",
            bd=0,
            pady=8,
            cursor="hand2",
            state="disabled",
            command=self._stop_recording,
        )
        self.btn_stop_rec.pack(side="right", fill="x", expand=True, padx=(4, 0))

        self.btn_toggle_preview = self._make_toggle_button(
            ctrl_inner, "Preview: OFF", BTN_OFF_BG, BTN_OFF_ACTIVE, self._toggle_preview
        )
        self.btn_toggle_preview.pack(fill="x", pady=3)

        self.btn_toggle_game_audio = self._make_toggle_button(
            ctrl_inner, "Game Audio: ON", BTN_ON_BG, BTN_ON_ACTIVE, self._toggle_game_audio
        )
        self.btn_toggle_game_audio.pack(fill="x", pady=3)

        self.btn_toggle_phone_mic = self._make_toggle_button(
            ctrl_inner, "Phone Mic: ON", BTN_ON_BG, BTN_ON_ACTIVE, self._toggle_phone_mic
        )
        self.btn_toggle_phone_mic.pack(fill="x", pady=3)

        self.btn_open_folder = self._make_toggle_button(
            right_panel,
            "Open Recordings Folder",
            BTN_OFF_BG,
            BTN_OFF_ACTIVE,
            self._open_recordings_folder,
        )
        self.btn_open_folder.config(pady=9, highlightbackground=BORDER_CARD, highlightthickness=1)
        self.btn_open_folder.pack(fill="x")

        # 3. INLINE STATUS BANNER (Non-modal, replaces popup dialogs)
        self.banner_frame = tk.Frame(
            main_frame,
            bg="#111C2D",
            highlightbackground=BORDER_CARD,
            highlightthickness=1,
            padx=12,
            pady=6,
        )
        self.banner_frame.pack(fill="x", pady=(0, 8))

        self.banner_lbl = tk.Label(
            self.banner_frame,
            text=f"Recordings save to {redact_path(self.recordings_dir)}",
            font=("Segoe UI", 9),
            bg="#111C2D",
            fg=TEXT_SECONDARY,
            anchor="w",
        )
        self.banner_lbl.pack(side="left", fill="x", expand=True)

        self.banner_action_btn = tk.Button(
            self.banner_frame,
            text="Open Folder",
            font=("Segoe UI", 8, "bold"),
            bg=BTN_OFF_BG,
            fg=ACCENT_BLUE,
            activebackground=BTN_OFF_ACTIVE,
            activeforeground=TEXT_PRIMARY,
            relief="flat",
            padx=10,
            pady=2,
            cursor="hand2",
            command=self._open_recordings_folder,
        )
        self.banner_action_btn.pack(side="right")

        # 4. COLLAPSIBLE ADVANCED DIAGNOSTICS
        diag_bar = tk.Frame(main_frame, bg=BG_APP)
        diag_bar.pack(fill="x")

        self.diag_toggle_btn = tk.Button(
            diag_bar,
            text="▸ Advanced Diagnostics",
            font=("Segoe UI", 9, "bold"),
            bg=BG_APP,
            fg=TEXT_SECONDARY,
            activebackground=BG_CARD,
            activeforeground=TEXT_PRIMARY,
            relief="flat",
            anchor="w",
            cursor="hand2",
            command=self._toggle_diagnostics,
        )
        self.diag_toggle_btn.pack(side="left")

        self.diag_container = tk.Frame(
            main_frame,
            bg=BG_CARD,
            highlightbackground=BORDER_CARD,
            highlightthickness=1,
            padx=10,
            pady=8,
        )
        # Collapsed by default for a simple, clean beginner UI

        diag_stats_row = tk.Frame(self.diag_container, bg=BG_CARD)
        diag_stats_row.pack(fill="x", pady=(0, 6))

        self.bitrate_val_lbl = tk.Label(
            diag_stats_row, text="Bitrate: 0.0 Mbps", font=("Consolas", 9), bg=BG_CARD, fg=TEXT_SECONDARY
        )
        self.bitrate_val_lbl.pack(side="left", padx=(0, 16))

        self.frames_val_lbl = tk.Label(
            diag_stats_row, text="Frames: 0", font=("Consolas", 9), bg=BG_CARD, fg=TEXT_SECONDARY
        )
        self.frames_val_lbl.pack(side="left", padx=(0, 16))

        self.deps_val_lbl = tk.Label(
            diag_stats_row, text="Dependencies: Checking...", font=("Consolas", 9), bg=BG_CARD, fg=TEXT_SECONDARY
        )
        self.deps_val_lbl.pack(side="left")

        clear_btn = tk.Button(
            diag_stats_row,
            text="Clear Log",
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

        log_body = tk.Frame(self.diag_container, bg=BG_WELL)
        log_body.pack(fill="x")

        scrollbar = ttk.Scrollbar(log_body, orient="vertical")
        scrollbar.pack(side="right", fill="y")

        self.log_text = tk.Text(
            log_body,
            bg=BG_WELL,
            fg="#34D399",
            font=("Consolas", 9),
            height=5,
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

        self._log("LiteGame Capture ready.")
        self._log(f"Recordings folder: {redact_path(self.recordings_dir)}")

    def _make_toggle_button(self, parent, text, bg, active_bg, command):
        return tk.Button(
            parent,
            text=text,
            font=("Segoe UI", 9, "bold"),
            bg=bg,
            fg=TEXT_PRIMARY,
            activebackground=active_bg,
            activeforeground=TEXT_PRIMARY,
            relief="flat",
            bd=0,
            padx=12,
            pady=7,
            cursor="hand2",
            command=command,
        )

    def _run_dependency_check(self):
        deps = check_runtime_dependencies()
        parts = [
            f"ADB={'OK' if deps['adb'] else 'Missing'}",
            f"GStreamer={'OK' if deps['gstreamer'] else 'Fallback'}",
            f"FFmpeg={'OK' if deps['ffmpeg'] else 'Missing'}",
        ]
        self.deps_val_lbl.config(text=" | ".join(parts))
        if deps["missing"]:
            msg = "Missing dependency: " + ", ".join(deps["missing"]) + " (run windows\\setup_dependencies.ps1)"
            self._set_banner(msg, ACCENT_AMBER)
            self._log(msg)

    def _set_banner(self, text, color=TEXT_SECONDARY):
        self.banner_lbl.config(text=text, fg=color)

    def _toggle_diagnostics(self):
        self._diag_expanded = not self._diag_expanded
        if self._diag_expanded:
            self.diag_toggle_btn.config(text="▾ Advanced Diagnostics")
            self.diag_container.pack(fill="x", pady=(4, 0))
        else:
            self.diag_toggle_btn.config(text="▸ Advanced Diagnostics")
            self.diag_container.pack_forget()

    def _on_canvas_resize(self, _event):
        if not self._showing_live_frame:
            self._show_preview_placeholder(self._placeholder_text)

    def _show_preview_placeholder(self, text):
        self._showing_live_frame = False
        self._placeholder_text = text
        self._preview_image_item = None
        self.preview_canvas.delete("all")
        w = max(self.preview_canvas.winfo_width(), 540)
        h = max(self.preview_canvas.winfo_height(), 340)
        self.preview_canvas.create_rectangle(0, 0, w, h, fill=BG_WELL, outline="")

        box_w = min(440, int(w * 0.8))
        box_h = min(140, int(h * 0.45))
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
            font=("Segoe UI", 10),
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
        if "ERROR" in upper or "FAILED" in upper or "MISSING" in upper:
            tag = "error"
        elif "SAVED" in upper or "SUCCESS" in upper or "READY" in upper:
            tag = "success"
        elif "NOTICE" in upper or "MUTED" in upper or "DISCONNECTED" in upper:
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

    def _start_recording(self):
        if not self._device_connected:
            self._set_banner(
                "Connect an Android phone with USB Debugging enabled, then tap Start Recording.",
                ACCENT_AMBER,
            )
            return
        ok, msg = self.adb_mgr.launch_capture_app()
        self._set_banner(msg, ACCENT_BLUE if ok else ACCENT_AMBER)
        self._log(msg)

    def _stop_recording(self):
        sent = self.receiver.request_stop_recording()
        if not sent and self.adb_mgr:
            self.adb_mgr.stop_capture_app()
        self.rec_status_lbl.config(text="Stopping...", fg=ACCENT_AMBER)
        self.btn_stop_rec.config(state="disabled")
        self._set_banner("Stopping recording and saving MP4...", ACCENT_AMBER)

    def _toggle_preview(self):
        new_state = not self.receiver.preview_enabled
        self.receiver.set_preview_enabled(new_state)
        if new_state:
            self.btn_toggle_preview.config(text="Preview: ON", bg=BTN_ON_BG, activebackground=BTN_ON_ACTIVE)
            self.preview_badge.config(text="ON (HW)", fg=ACCENT_GREEN)
            self._log("Live preview enabled")
        else:
            self._latest_frame_data = None
            self.btn_toggle_preview.config(text="Preview: OFF", bg=BTN_OFF_BG, activebackground=BTN_OFF_ACTIVE)
            self.preview_badge.config(text="OFF", fg=TEXT_MUTED)
            self._show_preview_placeholder(
                "Preview is OFF\n\n(Recording runs independently with minimal PC load)"
            )
            self._log("Live preview disabled")

    def _toggle_game_audio(self):
        new_state = not self.receiver.game_audio_enabled
        self.receiver.set_game_audio_enabled(new_state)
        if new_state:
            self.btn_toggle_game_audio.config(text="Game Audio: ON", bg=BTN_ON_BG, activebackground=BTN_ON_ACTIVE)
            self._log("Game audio enabled")
        else:
            self.btn_toggle_game_audio.config(text="Game Audio: OFF", bg=BTN_OFF_BG, activebackground=BTN_OFF_ACTIVE)
            self._log("Game audio muted")

    def _toggle_phone_mic(self):
        new_state = not self.receiver.phone_mic_enabled
        self.receiver.set_phone_mic_enabled(new_state)
        if new_state:
            self.btn_toggle_phone_mic.config(text="Phone Mic: ON", bg=BTN_ON_BG, activebackground=BTN_ON_ACTIVE)
            self._log("Phone mic enabled")
        else:
            self.btn_toggle_phone_mic.config(text="Phone Mic: OFF", bg=BTN_OFF_BG, activebackground=BTN_OFF_ACTIVE)
            self._log("Phone mic muted")

    def _on_adb_status(self, connected, device, message):
        self.root.after(0, self._update_adb_ui, connected, device, message)

    def _update_adb_ui(self, connected, _device, message):
        self._device_connected = bool(connected)
        if connected:
            self.conn_pill.config(text="●  Connected", bg="#0F2E1E", fg=ACCENT_GREEN)
            self.device_val_lbl.config(text="Android Device Connected", fg=ACCENT_GREEN)
            self.adb_val_lbl.config(text=message, fg=ACCENT_BLUE)
            self._log(f"USB status: Android Device Connected ({message})")
        else:
            self.conn_pill.config(text="●  Disconnected", bg="#172030", fg=TEXT_MUTED)
            self.device_val_lbl.config(text="No Device Connected", fg=ACCENT_AMBER)
            self.adb_val_lbl.config(text=message, fg=TEXT_SECONDARY)
            self._log(f"USB status: {message}")

    def _on_preview_frame(self, raw_bytes, width, height):
        with self._frame_lock:
            self._latest_frame_data = (raw_bytes, width, height)
            if not self._render_scheduled:
                self._render_scheduled = True
                self.root.after(1, self._render_latest_frame)

    def _render_latest_frame(self):
        with self._frame_lock:
            self._render_scheduled = False
            frame_data = self._latest_frame_data
            self._latest_frame_data = None
        if not self.receiver or not self.receiver.preview_enabled or not frame_data:
            return
        now = time.monotonic()
        if now - self._last_render_ts < 0.028:
            with self._frame_lock:
                if self._latest_frame_data is None:
                    self._latest_frame_data = frame_data
                if not self._render_scheduled:
                    self._render_scheduled = True
                    self.root.after(15, self._render_latest_frame)
            return
        self._last_render_ts = now

        try:
            raw_bytes, width, height = frame_data
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

            self.current_photo_image = ImageTk.PhotoImage(img)
            if not self._showing_live_frame or self._preview_image_item is None:
                self.preview_canvas.delete("all")
                self._preview_image_item = self.preview_canvas.create_image(
                    ox, oy, image=self.current_photo_image, anchor="nw"
                )
                self._showing_live_frame = True
            else:
                # Update existing canvas item in-place to eliminate frame flicker
                self.preview_canvas.itemconfig(self._preview_image_item, image=self.current_photo_image)
                self.preview_canvas.coords(self._preview_image_item, ox, oy)
        except Exception:
            pass

    def _on_receiver_event(self, event_type, data):
        self.root.after(0, self._update_receiver_ui, event_type, data)

    def _update_receiver_ui(self, event_type, data):
        if event_type == "STATUS":
            self._log(data)
        elif event_type == "CONNECTED":
            self._log(data)
            self.rec_status_lbl.config(text="Recording", fg=ACCENT_RED)
            self.timer_val_lbl.config(text="00:00:00", fg=ACCENT_RED)
            self.res_fps_lbl.config(text="Video: Detecting...")
            self.total_data_lbl.config(text="File Size: 0.0 MB")
            self.btn_start_rec.config(state="disabled")
            self.btn_stop_rec.config(state="normal")
            self._set_banner("Recording active via USB...", ACCENT_GREEN)
        elif event_type == "STATS":
            sec = data["elapsed_sec"]
            h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
            res = data.get("resolution", "1080p")
            fps = data.get("fps", 0.0)
            self.timer_val_lbl.config(text=f"{h:02d}:{m:02d}:{s:02d}")
            self.res_fps_lbl.config(text=f"Video: {res} • {fps} fps")
            self.total_data_lbl.config(text=f"File Size: {data['total_mb']} MB")
            self.bitrate_val_lbl.config(text=f"Bitrate: {data['bitrate_mbps']} Mbps")
            self.frames_val_lbl.config(text=f"Frames: {data['video_frames']}")
        elif event_type == "DISCONNECTED":
            self._latest_frame_data = None
            self._log(data)
            self.rec_status_lbl.config(text="Saving MP4...", fg=ACCENT_AMBER)
            self.bitrate_val_lbl.config(text="Bitrate: 0.0 Mbps")
            self.btn_stop_rec.config(state="disabled")
            self._show_preview_placeholder("Stream finished.\n\nFinalizing MP4 recording...")
            self._set_banner("Finalizing MP4 file...", ACCENT_AMBER)
        elif event_type == "MP4_READY":
            safe_name = Path(data).name
            safe_dir = redact_path(Path(data).parent)
            self._log(f"Saved MP4: {safe_dir}\\{safe_name}")
            self.rec_status_lbl.config(text="Saved", fg=ACCENT_GREEN)
            self.timer_val_lbl.config(fg=TEXT_PRIMARY)
            self.btn_start_rec.config(state="normal")
            self.btn_stop_rec.config(state="disabled")
            self._show_preview_placeholder(self._placeholder_text if not self.receiver.preview_enabled else "Preview Ready")
            self._set_banner(f"Recording saved: {safe_name} (in {safe_dir})", ACCENT_GREEN)
        elif event_type == "MP4_ERROR":
            self._log(f"ERROR: {data}")
            self.rec_status_lbl.config(text="Error", fg=ACCENT_RED)
            self.timer_val_lbl.config(fg=TEXT_PRIMARY)
            self.btn_start_rec.config(state="normal")
            self.btn_stop_rec.config(state="disabled")
            self._set_banner(f"Save failed: {data}", ACCENT_RED)
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
    app = LiteGameCaptureGUI(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()


if __name__ == "__main__":
    main()
