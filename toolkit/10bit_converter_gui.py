#!/usr/bin/env python3
"""
10bit_converter_gui.py
A modern Mac GUI for upconverting 8-bit AI-generated video to 10-bit
(debanding + dithering + true 10-bit encode). Built on customtkinter.

SETUP (one-time, handled for you by Start_Here.command):
    pip3 install customtkinter
    # ffmpeg is bundled in bin/<arch>/ — no install needed

RUN:
    python3 10bit_converter_gui.py

WHAT IT DOES:
    - Debands gradients (the actual cause of flat/blocky-looking 8-bit AI video)
    - Adds subtle dither noise so gradients read as smooth
    - Encodes to a true 10-bit codec (HEVC Main10 or ProRes 4444)

FEATURES:
    - Modern themed UI (light/dark aware), batch queue with per-file status + %
    - "Now running" card with live frame / fps / speed / ETA parsed from ffmpeg
    - Deband strength: Low / Medium / High / Custom
    - Cancel button: stops instantly and discards the partial output
    - Settings (persisted): output folder, suffix, skip/overwrite, HEVC CRF +
      preset, deband range/blur, dither amount, custom threshold
    - Bundled ffmpeg (bin/<arch>) preferred over any system install
"""

import json
import os
import platform
import shutil
import subprocess
import threading

import customtkinter as ctk
from tkinter import filedialog, messagebox

VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm", ".mpg", ".mpeg", ".ts")

# Named deband strength -> per-plane threshold. "Custom" pulls from settings.
STRENGTH_THR = {"Low": "0.01", "Medium": "0.02", "High": "0.05", "Custom": None}

X265_PRESETS = ["ultrafast", "superfast", "veryfast", "faster", "fast",
                "medium", "slow", "slower", "veryslow"]

SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             ".10bit_converter_settings.json")

DEFAULT_SETTINGS = {
    "dest_mode": "same", "dest_dir": "", "suffix": "_10bit", "on_exists": "skip",
    "crf": 18, "preset": "slow", "deband_range": 16, "deband_blur": True,
    "dither": 2, "thr_custom": "0.03", "appearance": "System",
}

# Status -> (label text, colour as (light, dark))
STATUS_COLOR = {
    "Queued":    ("#8a8a8a", "#9a9a9a"),
    "Running":   ("#1f6feb", "#4c9aff"),
    "Done":      ("#1a7f37", "#3fb950"),
    "Failed":    ("#b42318", "#ff6b6b"),
    "Skipped":   ("#8a8a8a", "#9a9a9a"),
    "Cancelled": ("#8a8a8a", "#9a9a9a"),
}


# ---------------------------------------------------------------- ffmpeg / helpers
def ensure_bundled_ffmpeg():
    """Prefer the bundled ffmpeg/ffprobe (bin/<arch>) over any system install.
    Clears Gatekeeper quarantine on first run. No-op if the bundle is absent."""
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin", platform.machine())
    if os.path.isfile(os.path.join(d, "ffmpeg")) and os.path.isfile(os.path.join(d, "ffprobe")):
        try:
            subprocess.run(["xattr", "-dr", "com.apple.quarantine", d], capture_output=True)
        except Exception:
            pass
        os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")


def load_settings():
    s = dict(DEFAULT_SETTINGS)
    try:
        with open(SETTINGS_PATH) as f:
            s.update({k: v for k, v in json.load(f).items() if k in DEFAULT_SETTINGS})
    except Exception:
        pass
    return s


def save_settings(s):
    try:
        with open(SETTINGS_PATH, "w") as f:
            json.dump(s, f, indent=2)
    except Exception:
        pass


def build_filters(thr, pix_fmt, deband_range=16, deband_blur=True, dither=2):
    blur = 1 if deband_blur else 0
    return (f"deband=1thr={thr}:2thr={thr}:3thr={thr}:range={deband_range}:blur={blur},"
            f"noise=alls={dither}:allf=t+u,format={pix_fmt}")


def probe_duration(path):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path], capture_output=True, text=True, check=True).stdout.strip()
        return float(out)
    except Exception:
        return 0.0


def make_output_path(in_path, is_prores, dest_dir, suffix):
    base = os.path.splitext(os.path.basename(in_path))[0]
    ext = "mov" if is_prores else "mp4"
    directory = dest_dir if dest_dir else os.path.dirname(in_path)
    return os.path.join(directory, f"{base}{suffix}.{ext}")


def fmt_time(seconds):
    if seconds is None or seconds < 0 or seconds != seconds:
        return "--:--"
    seconds = int(seconds)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


# ---------------------------------------------------------------- main app
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.settings = load_settings()
        ctk.set_appearance_mode(self.settings.get("appearance", "System"))
        ctk.set_default_color_theme("blue")

        self.title("8-bit → 10-bit Converter")
        self.geometry("720x760")
        self.minsize(660, 700)

        self.queue = []          # ordered list of paths
        self.rows = {}           # path -> dict(frame,name,status,pct)
        self.mode = ctk.StringVar(value="HEVC")
        self.strength = ctk.StringVar(value="Medium")
        self._cancel = threading.Event()

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)   # queue expands

        self._build_header()
        self._build_toolbar()
        self._build_queue()
        self._build_options()
        self._build_now_running()
        self._build_actions()
        self._build_statusbar()
        self._refresh_empty_state()

    # ---- sections ----
    def _build_header(self):
        head = ctk.CTkFrame(self, corner_radius=0, fg_color=("#2d7dd2", "#1b3a5c"))
        head.grid(row=0, column=0, sticky="ew")
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text="🎬  8-bit → 10-bit Converter",
                     font=ctk.CTkFont(size=22, weight="bold"),
                     text_color="white").grid(row=0, column=0, sticky="w", padx=20, pady=(16, 0))
        ctk.CTkLabel(head, text="Debands gradients and re-encodes to true 10-bit  •  batch-capable",
                     font=ctk.CTkFont(size=13), text_color=("#e6eefc", "#c9d7ea")).grid(
            row=1, column=0, sticky="w", padx=20, pady=(0, 16))

    def _build_toolbar(self):
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew", padx=16, pady=(14, 6))
        for i in range(5):
            bar.grid_columnconfigure(i, weight=0)
        bar.grid_columnconfigure(4, weight=1)
        ctk.CTkButton(bar, text="＋ Add files", width=110, command=self.add_files).grid(
            row=0, column=0, padx=(0, 6))
        ctk.CTkButton(bar, text="📁 Add folder", width=120, command=self.add_folder).grid(
            row=0, column=1, padx=6)
        ctk.CTkButton(bar, text="Remove", width=80, fg_color="transparent", border_width=1,
                      command=self.remove_selected).grid(row=0, column=2, padx=6)
        ctk.CTkButton(bar, text="Clear", width=70, fg_color="transparent", border_width=1,
                      command=self.clear_queue).grid(row=0, column=3, padx=6)
        ctk.CTkButton(bar, text="⚙  Settings", width=110, fg_color="transparent", border_width=1,
                      command=self.open_settings).grid(row=0, column=4, sticky="e")

    def _build_queue(self):
        self.queue_frame = ctk.CTkScrollableFrame(self, label_text="Queue")
        self.queue_frame.grid(row=2, column=0, sticky="nsew", padx=16, pady=4)
        self.queue_frame.grid_columnconfigure(0, weight=1)
        self.empty_label = ctk.CTkLabel(
            self.queue_frame, text="Add files or a whole folder to begin.\n"
            "Each file is converted to  NAME_10bit  next to the original (or a folder you choose).",
            text_color=("#8a8a8a", "#9a9a9a"), font=ctk.CTkFont(size=13))

    def _build_options(self):
        opt = ctk.CTkFrame(self, fg_color="transparent")
        opt.grid(row=3, column=0, sticky="ew", padx=16, pady=(8, 2))
        opt.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(opt, text="Format").grid(row=0, column=0, sticky="w", padx=(2, 10))
        ctk.CTkSegmentedButton(opt, values=["HEVC", "ProRes 4444"],
                               variable=self.mode).grid(row=0, column=1, sticky="w", pady=4)
        ctk.CTkLabel(opt, text="Deband").grid(row=1, column=0, sticky="w", padx=(2, 10))
        ctk.CTkSegmentedButton(opt, values=["Low", "Medium", "High", "Custom"],
                               variable=self.strength).grid(row=1, column=1, sticky="w", pady=4)

    def _build_now_running(self):
        card = ctk.CTkFrame(self)
        card.grid(row=4, column=0, sticky="ew", padx=16, pady=(8, 4))
        card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(card, text="NOW RUNNING", font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=("#8a8a8a", "#9a9a9a")).grid(row=0, column=0, sticky="w",
                                                             padx=14, pady=(10, 0))
        self.now_file = ctk.CTkLabel(card, text="—", font=ctk.CTkFont(size=15, weight="bold"),
                                     anchor="w")
        self.now_file.grid(row=1, column=0, sticky="ew", padx=14)
        self.now_stats = ctk.CTkLabel(card, text="idle", text_color=("#666", "#aaa"), anchor="w")
        self.now_stats.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 4))
        self.progress = ctk.CTkProgressBar(card)
        self.progress.set(0)
        self.progress.grid(row=3, column=0, sticky="ew", padx=14, pady=(2, 14))

    def _build_actions(self):
        act = ctk.CTkFrame(self, fg_color="transparent")
        act.grid(row=5, column=0, pady=(4, 2))
        self.convert_btn = ctk.CTkButton(act, text="Convert", width=200, height=44,
                                         font=ctk.CTkFont(size=16, weight="bold"),
                                         state="disabled", command=self.start_convert)
        self.convert_btn.grid(row=0, column=0, padx=6)
        self.cancel_btn = ctk.CTkButton(act, text="Cancel", width=120, height=44,
                                        font=ctk.CTkFont(size=15, weight="bold"),
                                        fg_color="#d9534f", hover_color="#b52b27",
                                        state="disabled", command=self.cancel_convert)
        self.cancel_btn.grid(row=0, column=1, padx=6)

    def _build_statusbar(self):
        self.status = ctk.CTkLabel(self, text="Add files or a folder to begin.",
                                   text_color=("#555", "#bbb"), anchor="w")
        self.status.grid(row=6, column=0, sticky="ew", padx=18, pady=(2, 12))

    # ---- queue management ----
    def _refresh_empty_state(self):
        if self.queue:
            self.empty_label.grid_forget()
        else:
            self.empty_label.grid(row=0, column=0, pady=40)
        self.convert_btn.configure(state="normal" if self.queue else "disabled")

    def _add_paths(self, paths):
        added = 0
        for p in paths:
            if os.path.isdir(p):
                for f in sorted(os.listdir(p)):
                    fp = os.path.join(p, f)
                    if (os.path.isfile(fp) and f.lower().endswith(VIDEO_EXTS)
                            and "_10bit." not in f and fp not in self.queue):
                        self._insert_row(fp); added += 1
            elif (os.path.isfile(p) and p.lower().endswith(VIDEO_EXTS) and p not in self.queue):
                self._insert_row(p); added += 1
        self._refresh_empty_state()
        if added:
            self.status.configure(text=f"{len(self.queue)} file(s) queued.")

    def _insert_row(self, path):
        self.queue.append(path)
        row = ctk.CTkFrame(self.queue_frame, fg_color=("#f2f2f2", "#2b2b2b"), corner_radius=8)
        row.grid(row=len(self.queue), column=0, sticky="ew", pady=3, padx=2)
        row.grid_columnconfigure(1, weight=1)
        chk = ctk.CTkCheckBox(row, text="", width=24)
        chk.grid(row=0, column=0, padx=(10, 4), pady=8)
        name = ctk.CTkLabel(row, text=os.path.basename(path), anchor="w")
        name.grid(row=0, column=1, sticky="ew", padx=4)
        status = ctk.CTkLabel(row, text="Queued", width=90, text_color=STATUS_COLOR["Queued"])
        status.grid(row=0, column=2, padx=6)
        pct = ctk.CTkLabel(row, text="", width=52, anchor="e")
        pct.grid(row=0, column=3, padx=(4, 12))
        self.rows[path] = {"frame": row, "check": chk, "name": name,
                           "status": status, "pct": pct}

    def _set_row(self, path, status=None, pct=None):
        r = self.rows.get(path)
        if not r:
            return
        if status is not None:
            r["status"].configure(text=status, text_color=STATUS_COLOR.get(status, ("#888", "#888")))
        if pct is not None:
            r["pct"].configure(text=pct)

    def _selected_paths(self):
        return [p for p, r in self.rows.items() if r["check"].get()]

    def remove_selected(self):
        sel = self._selected_paths()
        if not sel:
            self.status.configure(text="Tick the checkbox on rows you want to remove.")
            return
        for p in sel:
            self.rows[p]["frame"].destroy()
            del self.rows[p]
            self.queue.remove(p)
        self._regrid_rows()
        self._refresh_empty_state()

    def clear_queue(self):
        for r in self.rows.values():
            r["frame"].destroy()
        self.rows.clear(); self.queue.clear()
        self._refresh_empty_state()
        self.status.configure(text="Queue cleared.")

    def _regrid_rows(self):
        for i, p in enumerate(self.queue, start=1):
            self.rows[p]["frame"].grid_configure(row=i)

    def add_files(self):
        paths = filedialog.askopenfilenames(
            title="Select video(s)",
            filetypes=[("Video files", " ".join(f"*{e}" for e in VIDEO_EXTS)), ("All files", "*.*")])
        if paths:
            self._add_paths(list(paths))

    def add_folder(self):
        d = filedialog.askdirectory(title="Select a folder of videos")
        if d:
            self._add_paths([d])

    # ---- settings ----
    def open_settings(self):
        SettingsDialog(self, self.settings, self._on_settings_saved)

    def _on_settings_saved(self, new_settings):
        self.settings = new_settings
        save_settings(self.settings)
        ctk.set_appearance_mode(self.settings.get("appearance", "System"))
        self.status.configure(text="Settings saved.")

    def _resolve_thr(self):
        thr = STRENGTH_THR.get(self.strength.get())
        return thr if thr is not None else str(self.settings.get("thr_custom", "0.03"))

    # ---- conversion ----
    def start_convert(self):
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            messagebox.showerror("ffmpeg not found",
                                 "Bundled ffmpeg missing and none installed.\n\n"
                                 "Install with:  brew install ffmpeg")
            return
        if not self.queue:
            return
        for p in self.queue:
            self._set_row(p, status="Queued", pct="")
        self._cancel.clear()
        self._set_controls(False)
        threading.Thread(target=self.run_batch, daemon=True).start()

    def cancel_convert(self):
        self._cancel.set()
        self.cancel_btn.configure(state="disabled")
        self.status.configure(text="Cancelling…")

    def _set_controls(self, enabled):
        self.convert_btn.configure(state="normal" if (enabled and self.queue) else "disabled")
        self.cancel_btn.configure(state="disabled" if enabled else "normal")

    def run_batch(self):
        thr = self._resolve_thr()
        is_prores = self.mode.get().startswith("ProRes")
        dest_dir = self.settings["dest_dir"] if self.settings["dest_mode"] == "custom" else ""
        suffix = self.settings["suffix"] or "_10bit"
        overwrite = self.settings["on_exists"] == "overwrite"
        pix_fmt = "yuv444p10le" if is_prores else "yuv420p10le"
        filters = build_filters(thr, pix_fmt, self.settings["deband_range"],
                                self.settings["deband_blur"], self.settings["dither"])

        items = list(self.queue)
        total = len(items)
        done = failed = skipped = 0
        last_output = None

        for idx, in_path in enumerate(items, start=1):
            if self._cancel.is_set():
                break
            out_path = make_output_path(in_path, is_prores, dest_dir, suffix)
            if dest_dir and not os.path.isdir(dest_dir):
                try:
                    os.makedirs(dest_dir, exist_ok=True)
                except OSError:
                    pass
            if os.path.exists(out_path) and not overwrite:
                skipped += 1
                self._ui(lambda p=in_path: self._set_row(p, status="Skipped", pct="—"))
                continue

            if is_prores:
                cmd = ["ffmpeg", "-y", "-nostats", "-i", in_path, "-vf", filters,
                       "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuv444p10le",
                       "-c:a", "pcm_s16le", "-progress", "pipe:1", out_path]
            else:
                cmd = ["ffmpeg", "-y", "-nostats", "-i", in_path, "-vf", filters,
                       "-c:v", "libx265", "-pix_fmt", "yuv420p10le",
                       "-crf", str(self.settings["crf"]), "-preset", str(self.settings["preset"]),
                       "-tag:v", "hvc1", "-c:a", "aac", "-b:a", "192k",
                       "-progress", "pipe:1", out_path]

            dur = probe_duration(in_path)
            name = os.path.basename(in_path)
            self._ui(lambda p=in_path: self._set_row(p, status="Running", pct="0%"))
            self._ui(lambda n=name, i=idx: (self.now_file.configure(text=f"[{i}/{total}]  {n}"),
                                            self.now_stats.configure(text="starting…"),
                                            self.progress.set(0),
                                            self.status.configure(text=f"Converting {n}…")))

            err_tail = self._run_ffmpeg(cmd, dur, in_path)

            if self._cancel.is_set():
                try:
                    if os.path.exists(out_path):
                        os.remove(out_path)
                except OSError:
                    pass
                self._ui(lambda p=in_path: self._set_row(p, status="Cancelled", pct="—"))
                break
            if err_tail is not None:
                failed += 1
                self._ui(lambda p=in_path: self._set_row(p, status="Failed", pct="—"))
                self._ui(lambda e=err_tail, n=name: messagebox.showerror("ffmpeg error", f"{n}\n\n{e}"))
                continue
            done += 1
            last_output = out_path
            self._ui(lambda p=in_path: self._set_row(p, status="Done", pct="100%"))

        cancelled = self._cancel.is_set()
        self._ui(lambda: (self.now_file.configure(text="—"),
                          self.now_stats.configure(text="idle"), self.progress.set(0)))

        def summary():
            parts = [f"{done} done"]
            if skipped: parts.append(f"{skipped} skipped")
            if failed: parts.append(f"{failed} failed")
            if cancelled: parts.append("cancelled")
            self.status.configure(text="Finished: " + ", ".join(parts) + ".")
            self._set_controls(True)
        self._ui(summary)

        if last_output and not cancelled:
            subprocess.run(["open", "-R", last_output])

    def _run_ffmpeg(self, cmd, dur, row_path=None):
        """Run ffmpeg with live progress. Returns None on success, else stderr tail."""
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, bufsize=1)

        # Watcher kills ffmpeg promptly on cancel. kill() not terminate(): under
        # SIGTERM libx265 finishes the encode first (10s+ lag); the partial file
        # is discarded on cancel, so a hard kill is correct and instant.
        def _watch():
            while proc.poll() is None:
                if self._cancel.wait(0.2):
                    proc.kill()
                    return
        threading.Thread(target=_watch, daemon=True).start()

        cur = {}
        for line in proc.stdout:
            if self._cancel.is_set():
                proc.kill()
                break
            line = line.strip()
            if "=" not in line:
                continue
            key, val = line.split("=", 1)
            cur[key] = val
            if key == "progress":
                self._emit_progress(cur, dur, row_path)
                cur = {}
        proc.wait()
        if proc.returncode not in (0, None) and not self._cancel.is_set():
            err = proc.stderr.read() if proc.stderr else ""
            return err[-800:] if err else f"ffmpeg exited with code {proc.returncode}"
        return None

    def _emit_progress(self, cur, dur, row_path):
        us = cur.get("out_time_us") or cur.get("out_time_ms")
        secs = int(us) / 1_000_000 if (us and us.isdigit()) else 0.0
        pct = min(100.0, secs / dur * 100) if dur > 0 else 0.0
        frame = cur.get("frame", "?"); fps = cur.get("fps", "?"); speed = cur.get("speed", "?")
        eta = None
        try:
            spd = float(str(speed).rstrip("x"))
            if spd > 0 and dur > 0:
                eta = max(0.0, (dur - secs) / spd)
        except ValueError:
            pass
        if cur.get("progress") == "end":
            pct = 100.0; eta = 0.0
        stat = f"{pct:.0f}%   •   frame {frame}   •   {fps} fps   •   {speed}   •   ETA {fmt_time(eta)}"

        def apply():
            self.progress.set(pct / 100.0)
            self.now_stats.configure(text=stat)
            if row_path is not None:
                self._set_row(row_path, pct=f"{pct:.0f}%")
        self._ui(apply)

    def _ui(self, fn):
        self.after(0, fn)


# ---------------------------------------------------------------- settings dialog
class SettingsDialog(ctk.CTkToplevel):
    def __init__(self, parent, settings, on_save):
        super().__init__(parent)
        self.title("Settings")
        self.geometry("460x560")
        self.resizable(False, False)
        self.on_save = on_save
        s = dict(settings)

        self.dest_mode = ctk.StringVar(value=s["dest_mode"])
        self.dest_dir = ctk.StringVar(value=s["dest_dir"])
        self.suffix = ctk.StringVar(value=s["suffix"])
        self.on_exists = ctk.StringVar(value=s["on_exists"])
        self.crf = ctk.IntVar(value=int(s["crf"]))
        self.preset = ctk.StringVar(value=s["preset"])
        self.deband_range = ctk.IntVar(value=int(s["deband_range"]))
        self.deband_blur = ctk.BooleanVar(value=bool(s["deband_blur"]))
        self.dither = ctk.IntVar(value=int(s["dither"]))
        self.thr_custom = ctk.StringVar(value=str(s["thr_custom"]))
        self.appearance = ctk.StringVar(value=s.get("appearance", "System"))

        frm = ctk.CTkScrollableFrame(self, label_text="Settings")
        frm.pack(fill="both", expand=True, padx=12, pady=(12, 4))
        frm.grid_columnconfigure(1, weight=1)
        r = 0

        def header(t):
            nonlocal r
            ctk.CTkLabel(frm, text=t, font=ctk.CTkFont(size=13, weight="bold")).grid(
                row=r, column=0, columnspan=2, sticky="w", pady=(12, 2)); r += 1

        def label(t):
            nonlocal r
            ctk.CTkLabel(frm, text=t).grid(row=r, column=0, sticky="w", pady=4, padx=(2, 8))

        header("Output")
        label("Destination")
        ctk.CTkSegmentedButton(frm, values=["same", "custom"], variable=self.dest_mode,
                               command=lambda _=None: self._toggle_dest()).grid(
            row=r, column=1, sticky="w"); r += 1
        self.dest_entry = ctk.CTkEntry(frm, textvariable=self.dest_dir, placeholder_text="Choose a folder…")
        self.dest_entry.grid(row=r, column=0, columnspan=2, sticky="ew", pady=2)
        r += 1
        self.dest_btn = ctk.CTkButton(frm, text="Browse…", command=self._browse_dest)
        self.dest_btn.grid(row=r, column=1, sticky="e", pady=(0, 4)); r += 1
        label("Filename suffix")
        ctk.CTkEntry(frm, textvariable=self.suffix, width=140).grid(row=r, column=1, sticky="w"); r += 1
        label("If output exists")
        ctk.CTkSegmentedButton(frm, values=["skip", "overwrite"], variable=self.on_exists).grid(
            row=r, column=1, sticky="w"); r += 1

        header("Quality (HEVC only)")
        label("CRF (lower = better)")
        self.crf_label = ctk.CTkLabel(frm, text=str(self.crf.get()), width=28)
        crf_slider = ctk.CTkSlider(frm, from_=0, to=51, number_of_steps=51, variable=self.crf,
                                   command=lambda v: self.crf_label.configure(text=str(int(float(v)))))
        crf_slider.grid(row=r, column=1, sticky="ew", padx=(0, 34))
        self.crf_label.grid(row=r, column=1, sticky="e"); r += 1
        label("Encoder preset")
        ctk.CTkOptionMenu(frm, values=X265_PRESETS, variable=self.preset, width=140).grid(
            row=r, column=1, sticky="w"); r += 1

        header("Deband & dither")
        label("Deband range")
        ctk.CTkEntry(frm, textvariable=self.deband_range, width=80).grid(row=r, column=1, sticky="w"); r += 1
        ctk.CTkSwitch(frm, text="Deband blur", variable=self.deband_blur).grid(
            row=r, column=1, sticky="w", pady=4); r += 1
        label("Dither amount")
        ctk.CTkEntry(frm, textvariable=self.dither, width=80).grid(row=r, column=1, sticky="w"); r += 1
        label("Custom threshold")
        ctk.CTkEntry(frm, textvariable=self.thr_custom, width=80).grid(row=r, column=1, sticky="w"); r += 1

        header("Appearance")
        label("Theme")
        ctk.CTkOptionMenu(frm, values=["System", "Light", "Dark"], variable=self.appearance,
                          width=140).grid(row=r, column=1, sticky="w"); r += 1

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=12, pady=10)
        ctk.CTkButton(btns, text="Restore defaults", fg_color="transparent", border_width=1,
                      command=self._restore).pack(side="left")
        ctk.CTkButton(btns, text="Cancel", fg_color="transparent", border_width=1,
                      command=self.destroy).pack(side="right", padx=(6, 0))
        ctk.CTkButton(btns, text="Save", command=self._save).pack(side="right")

        self._toggle_dest()
        self.after(80, self._raise)

    def _raise(self):
        try:
            self.lift(); self.focus_force(); self.grab_set()
        except Exception:
            pass

    def _toggle_dest(self):
        state = "normal" if self.dest_mode.get() == "custom" else "disabled"
        self.dest_entry.configure(state=state)
        self.dest_btn.configure(state=state)

    def _browse_dest(self):
        d = filedialog.askdirectory(title="Choose output folder", parent=self)
        if d:
            self.dest_dir.set(d)

    def _restore(self):
        d = DEFAULT_SETTINGS
        self.dest_mode.set(d["dest_mode"]); self.dest_dir.set(d["dest_dir"])
        self.suffix.set(d["suffix"]); self.on_exists.set(d["on_exists"])
        self.crf.set(d["crf"]); self.crf_label.configure(text=str(d["crf"]))
        self.preset.set(d["preset"]); self.deband_range.set(d["deband_range"])
        self.deband_blur.set(d["deband_blur"]); self.dither.set(d["dither"])
        self.thr_custom.set(d["thr_custom"]); self.appearance.set(d["appearance"])
        self._toggle_dest()

    def _save(self):
        try:
            rng = int(self.deband_range.get()); dith = int(self.dither.get())
        except (ValueError, Exception):
            messagebox.showwarning("Check values", "Deband range and dither must be whole numbers.",
                                   parent=self); return
        new = {
            "dest_mode": self.dest_mode.get(),
            "dest_dir": self.dest_dir.get().strip(),
            "suffix": self.suffix.get().strip() or "_10bit",
            "on_exists": self.on_exists.get(),
            "crf": int(self.crf.get()),
            "preset": self.preset.get(),
            "deband_range": rng,
            "deband_blur": bool(self.deband_blur.get()),
            "dither": dith,
            "thr_custom": self.thr_custom.get().strip() or "0.03",
            "appearance": self.appearance.get(),
        }
        if new["dest_mode"] == "custom" and not new["dest_dir"]:
            messagebox.showwarning("Pick a folder",
                                   "Choose an output folder, or switch destination back to “same”.",
                                   parent=self); return
        self.on_save(new)
        self.destroy()


if __name__ == "__main__":
    ensure_bundled_ffmpeg()
    app = App()
    app.mainloop()
