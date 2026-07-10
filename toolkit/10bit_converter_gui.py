#!/usr/bin/env python3
"""
10bit_converter_gui.py
A simple Mac GUI for upconverting 8-bit AI-generated video to 10-bit
(debanding + dithering + true 10-bit encode).

SETUP (one-time):
    brew install ffmpeg python-tk

RUN:
    python3 10bit_converter_gui.py

WHAT IT DOES:
    - Debands gradients (the actual cause of flat/blocky-looking 8-bit AI video)
    - Adds subtle dither noise so gradients read as smooth
    - Encodes to a true 10-bit codec (HEVC Main10 or ProRes 4444)

FEATURES:
    - Batch queue as a real table: each file shows Queued / Running / Done /
      Failed / Skipped and its own progress %, with the active row highlighted
    - "Now running" panel with live frame / fps / speed / ETA parsed from ffmpeg
    - Real percentage progress bar (parsed from ffmpeg, not a fake spinner)
    - Deband strength: Low / Medium / High / Custom
    - Cancel button: stops instantly and discards the partial output
    - Settings dialog (persisted): output folder, filename suffix, skip/overwrite,
      HEVC CRF + preset, deband range/blur, dither amount, custom threshold
"""

import json
import os
import platform
import re
import shutil
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, ttk, messagebox

VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm", ".mpg", ".mpeg", ".ts")


def ensure_bundled_ffmpeg():
    """Prefer the bundled ffmpeg/ffprobe (bin/<arch>) over any system install.
    Clears Gatekeeper quarantine on first run. No-op if the bundle is absent
    (then the app falls back to system ffmpeg on PATH)."""
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin", platform.machine())
    if os.path.isfile(os.path.join(d, "ffmpeg")) and os.path.isfile(os.path.join(d, "ffprobe")):
        try:
            subprocess.run(["xattr", "-dr", "com.apple.quarantine", d],
                           capture_output=True)
        except Exception:
            pass
        os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")

# Deband threshold per named strength level (applied to all three planes).
STRENGTH_THR = {
    "Low (subtle, preserves detail)": "0.01",
    "Medium (balanced)": "0.02",
    "High (smooth skies/gradients)": "0.05",
    "Custom (from Settings)": None,          # resolved from settings["thr_custom"]
}

X265_PRESETS = ["ultrafast", "superfast", "veryfast", "faster", "fast",
                "medium", "slow", "slower", "veryslow"]

SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             ".10bit_converter_settings.json")

DEFAULT_SETTINGS = {
    "dest_mode": "same",     # "same" (next to source) or "custom"
    "dest_dir": "",          # used when dest_mode == "custom"
    "suffix": "_10bit",
    "on_exists": "skip",     # "skip" or "overwrite"
    "crf": 18,               # HEVC quality (lower = better/bigger)
    "preset": "slow",        # x265 preset
    "deband_range": 16,      # deband sampling radius
    "deband_blur": True,     # deband blur flag
    "dither": 2,             # noise strength (alls)
    "thr_custom": "0.03",    # threshold used by the "Custom" strength option
}


def load_settings() -> dict:
    s = dict(DEFAULT_SETTINGS)
    try:
        with open(SETTINGS_PATH) as f:
            s.update({k: v for k, v in json.load(f).items() if k in DEFAULT_SETTINGS})
    except Exception:
        pass
    return s


def save_settings(s: dict) -> None:
    try:
        with open(SETTINGS_PATH, "w") as f:
            json.dump(s, f, indent=2)
    except Exception:
        pass


def build_filters(thr, pix_fmt, deband_range=16, deband_blur=True, dither=2) -> str:
    """Build the deband + dither + 10-bit-format filter chain."""
    blur = 1 if deband_blur else 0
    return (
        f"deband=1thr={thr}:2thr={thr}:3thr={thr}:range={deband_range}:blur={blur},"
        f"noise=alls={dither}:allf=t+u,format={pix_fmt}"
    )


def probe_duration(path: str) -> float:
    """Total duration in seconds (0.0 if unknown)."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        return float(out)
    except Exception:
        return 0.0


def make_output_path(in_path, is_prores, dest_dir, suffix) -> str:
    """Where a converted file should be written, given settings."""
    base = os.path.splitext(os.path.basename(in_path))[0]
    ext = "mov" if is_prores else "mp4"
    directory = dest_dir if dest_dir else os.path.dirname(in_path)
    return os.path.join(directory, f"{base}{suffix}.{ext}")


def fmt_time(seconds: float) -> str:
    if seconds is None or seconds < 0 or seconds != seconds:  # NaN guard
        return "--:--"
    seconds = int(seconds)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


class ConverterApp:
    STATUS_QUEUED = "Queued"
    STATUS_RUNNING = "Running"
    STATUS_DONE = "Done"
    STATUS_FAILED = "Failed"
    STATUS_SKIPPED = "Skipped"
    STATUS_CANCELLED = "Cancelled"

    def __init__(self, root):
        self.root = root
        root.title("8-bit → 10-bit Converter")
        root.geometry("640x680")
        root.minsize(600, 640)

        self.settings = load_settings()
        self.queue = []                       # list of input file paths (row iids)
        self.mode = tk.StringVar(value="HEVC (smaller, delivery)")
        self.strength = tk.StringVar(value="Medium (balanced)")
        self.status = tk.StringVar(value="Add files or a folder to begin.")
        self._cancel = threading.Event()

        tk.Label(root, text="8-bit → 10-bit Video Converter",
                 font=("Helvetica", 16, "bold")).pack(pady=(14, 2))
        tk.Label(root, text="Debands gradients + upconverts to true 10-bit  •  batch-capable",
                 fg="#666").pack(pady=(0, 8))

        # --- Queue table (File / Status / %) ---
        table_frame = tk.Frame(root)
        table_frame.pack(fill="both", expand=True, padx=20)
        cols = ("file", "status", "pct")
        self.tree = ttk.Treeview(table_frame, columns=cols, show="headings", height=8,
                                 selectmode="extended")
        self.tree.heading("file", text="File")
        self.tree.heading("status", text="Status")
        self.tree.heading("pct", text="%")
        self.tree.column("file", width=360, anchor="w")
        self.tree.column("status", width=110, anchor="center")
        self.tree.column("pct", width=70, anchor="center")
        self.tree.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        sb.pack(side="right", fill="y")
        self.tree.config(yscrollcommand=sb.set)

        # Row colour coding by status
        self.tree.tag_configure(self.STATUS_RUNNING, background="#eaf3ff")
        self.tree.tag_configure(self.STATUS_DONE, background="#e9f7ec", foreground="#1a7f37")
        self.tree.tag_configure(self.STATUS_FAILED, background="#fdeaea", foreground="#b42318")
        self.tree.tag_configure(self.STATUS_SKIPPED, foreground="#888")
        self.tree.tag_configure(self.STATUS_CANCELLED, foreground="#888")

        # Optional native drag-and-drop if tkinterdnd2 is installed
        try:
            from tkinterdnd2 import DND_FILES  # noqa
            self.tree.drop_target_register(DND_FILES)
            self.tree.dnd_bind("<<Drop>>", self._on_drop)
        except Exception:
            pass

        # --- Queue buttons + Settings ---
        btns = tk.Frame(root)
        btns.pack(pady=8, fill="x", padx=20)
        tk.Button(btns, text="Add files…", command=self.add_files).pack(side="left", padx=3)
        tk.Button(btns, text="Add folder…", command=self.add_folder).pack(side="left", padx=3)
        tk.Button(btns, text="Remove", command=self.remove_selected).pack(side="left", padx=3)
        tk.Button(btns, text="Clear", command=self.clear_queue).pack(side="left", padx=3)
        tk.Button(btns, text="Settings…", command=self.open_settings).pack(side="right", padx=3)

        # --- Options (format + strength) ---
        opt = tk.Frame(root)
        opt.pack(pady=4)
        tk.Label(opt, text="Format:").grid(row=0, column=0, sticky="e", padx=(0, 6), pady=3)
        ttk.Combobox(opt, textvariable=self.mode, state="readonly", width=32,
                     values=["HEVC (smaller, delivery)",
                             "ProRes 4444 (grading, huge file)"]).grid(row=0, column=1, pady=3)
        tk.Label(opt, text="Deband strength:").grid(row=1, column=0, sticky="e", padx=(0, 6), pady=3)
        ttk.Combobox(opt, textvariable=self.strength, state="readonly", width=32,
                     values=list(STRENGTH_THR.keys())).grid(row=1, column=1, pady=3)

        # --- "Now running" panel ---
        run_box = tk.LabelFrame(root, text="Now running", fg="#333", padx=10, pady=6)
        run_box.pack(fill="x", padx=20, pady=(8, 2))
        self.now_file = tk.StringVar(value="—")
        self.now_stats = tk.StringVar(value="idle")
        tk.Label(run_box, textvariable=self.now_file, font=("Helvetica", 12, "bold"),
                 anchor="w").pack(fill="x")
        tk.Label(run_box, textvariable=self.now_stats, fg="#555", anchor="w").pack(fill="x")
        self.progress = ttk.Progressbar(run_box, mode="determinate", length=560, maximum=100)
        self.progress.pack(pady=(6, 2), fill="x")

        # --- Convert / Cancel buttons ---
        action = tk.Frame(root)
        action.pack(pady=10)
        self.convert_btn = tk.Button(action, text="Convert", font=("Helvetica", 13, "bold"),
                                     bg="#2d7dd2", fg="white", command=self.start_convert,
                                     state="disabled", width=16)
        self.convert_btn.pack(side="left", padx=4)
        self.cancel_btn = tk.Button(action, text="Cancel", font=("Helvetica", 13, "bold"),
                                    bg="#d9534f", fg="white", command=self.cancel_convert,
                                    state="disabled", width=10)
        self.cancel_btn.pack(side="left", padx=4)

        tk.Label(root, textvariable=self.status, fg="#444", wraplength=560).pack(pady=(0, 10))

    # ------------------------------------------------------------------ queue
    def _add_paths(self, paths):
        added = 0
        for p in paths:
            if os.path.isdir(p):
                for f in sorted(os.listdir(p)):
                    fp = os.path.join(p, f)
                    if (os.path.isfile(fp) and f.lower().endswith(VIDEO_EXTS)
                            and "_10bit." not in f and fp not in self.queue):
                        self._insert_row(fp); added += 1
            elif (os.path.isfile(p) and p.lower().endswith(VIDEO_EXTS)
                  and p not in self.queue):
                self._insert_row(p); added += 1
        self.convert_btn.config(state="normal" if self.queue else "disabled")
        if added:
            self.status.set(f"{len(self.queue)} file(s) queued.")

    def _insert_row(self, path):
        self.queue.append(path)
        self.tree.insert("", "end", iid=path,
                         values=(os.path.basename(path), self.STATUS_QUEUED, ""),
                         tags=(self.STATUS_QUEUED,))

    def _set_row(self, path, status=None, pct=None):
        if not self.tree.exists(path):
            return
        if status is not None:
            self.tree.set(path, "status", status)
            self.tree.item(path, tags=(status,))
        if pct is not None:
            self.tree.set(path, "pct", pct)

    def _refresh_list(self):
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        for p in self.queue:
            self._insert_row(p)
        self.convert_btn.config(state="normal" if self.queue else "disabled")

    def _on_drop(self, event):
        paths = [p.strip("{}") for p in re.findall(r"\{[^}]*\}|\S+", event.data)]
        self._add_paths(paths)

    def add_files(self):
        paths = filedialog.askopenfilenames(
            title="Select video(s)",
            filetypes=[("Video files", " ".join(f"*{e}" for e in VIDEO_EXTS)),
                       ("All files", "*.*")])
        if paths:
            self._add_paths(list(paths))

    def add_folder(self):
        d = filedialog.askdirectory(title="Select a folder of videos")
        if d:
            self._add_paths([d])

    def remove_selected(self):
        for iid in self.tree.selection():
            if iid in self.queue:
                self.queue.remove(iid)
            self.tree.delete(iid)
        self.convert_btn.config(state="normal" if self.queue else "disabled")

    def clear_queue(self):
        self.queue.clear()
        self._refresh_list()
        self.status.set("Queue cleared.")

    # --------------------------------------------------------------- settings
    def open_settings(self):
        SettingsDialog(self.root, self.settings, self._on_settings_saved)

    def _on_settings_saved(self, new_settings):
        self.settings = new_settings
        save_settings(self.settings)
        self.status.set("Settings saved.")

    def _resolve_thr(self):
        key = self.strength.get()
        thr = STRENGTH_THR.get(key)
        return thr if thr is not None else str(self.settings.get("thr_custom", "0.03"))

    # --------------------------------------------------------------- converting
    def start_convert(self):
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            messagebox.showerror("ffmpeg not found",
                                 "Install it in Terminal with:\n\nbrew install ffmpeg")
            return
        if not self.queue:
            return
        # Reset any prior run's row states for items still queued.
        for p in self.queue:
            self._set_row(p, status=self.STATUS_QUEUED, pct="")
        self._cancel.clear()
        self._set_controls(False)
        threading.Thread(target=self.run_batch, daemon=True).start()

    def cancel_convert(self):
        # Signal the worker; the watcher thread kills the running ffmpeg promptly.
        self._cancel.set()
        self.cancel_btn.config(state="disabled")
        self.status.set("Cancelling…")

    def _set_controls(self, enabled: bool):
        # enabled=True  -> idle: Convert usable (if queue), Cancel off
        # enabled=False -> converting: Convert off, Cancel on
        self.convert_btn.config(state="normal" if (enabled and self.queue) else "disabled")
        self.cancel_btn.config(state="disabled" if enabled else "normal")

    def run_batch(self):
        thr = self._resolve_thr()
        is_prores = self.mode.get().startswith("ProRes")
        dest_dir = self.settings["dest_dir"] if self.settings["dest_mode"] == "custom" else ""
        suffix = self.settings["suffix"] or "_10bit"
        overwrite = self.settings["on_exists"] == "overwrite"

        pix_fmt = "yuv444p10le" if is_prores else "yuv420p10le"
        filters = build_filters(thr, pix_fmt,
                                 self.settings["deband_range"],
                                 self.settings["deband_blur"],
                                 self.settings["dither"])

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
                self._ui(lambda p=in_path: self._set_row(p, status=self.STATUS_SKIPPED, pct="—"))
                continue

            if is_prores:
                cmd = ["ffmpeg", "-y", "-nostats", "-i", in_path, "-vf", filters,
                       "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuv444p10le",
                       "-c:a", "pcm_s16le", "-progress", "pipe:1", out_path]
            else:
                cmd = ["ffmpeg", "-y", "-nostats", "-i", in_path, "-vf", filters,
                       "-c:v", "libx265", "-pix_fmt", "yuv420p10le",
                       "-crf", str(self.settings["crf"]),
                       "-preset", str(self.settings["preset"]),
                       "-tag:v", "hvc1", "-c:a", "aac", "-b:a", "192k",
                       "-progress", "pipe:1", out_path]

            dur = probe_duration(in_path)
            name = os.path.basename(in_path)
            self._ui(lambda p=in_path: self._set_row(p, status=self.STATUS_RUNNING, pct="0%"))
            self._ui(lambda n=name, i=idx: (self.now_file.set(f"[{i}/{total}]  {n}"),
                                            self.now_stats.set("starting…"),
                                            self.progress.config(value=0),
                                            self.status.set(f"Converting {n}…")))

            err_tail = self._run_ffmpeg(cmd, dur, in_path)

            if self._cancel.is_set():
                try:
                    if os.path.exists(out_path):
                        os.remove(out_path)
                except OSError:
                    pass
                self._ui(lambda p=in_path: self._set_row(p, status=self.STATUS_CANCELLED, pct="—"))
                break

            if err_tail is not None:
                failed += 1
                self._ui(lambda p=in_path: self._set_row(p, status=self.STATUS_FAILED, pct="—"))
                self._ui(lambda e=err_tail, n=name:
                         messagebox.showerror("ffmpeg error", f"{n}\n\n{e}"))
                continue

            done += 1
            last_output = out_path
            self._ui(lambda p=in_path: self._set_row(p, status=self.STATUS_DONE, pct="100%"))

        # Wrap up
        cancelled = self._cancel.is_set()
        self._ui(lambda: self.now_file.set("—"))
        self._ui(lambda: self.now_stats.set("idle"))
        self._ui(lambda: self.progress.config(value=0))

        def summary():
            parts = [f"{done} done"]
            if skipped:
                parts.append(f"{skipped} skipped")
            if failed:
                parts.append(f"{failed} failed")
            if cancelled:
                parts.append("cancelled")
            self.status.set("Finished: " + ", ".join(parts) + ".")
            self._set_controls(True)
        self._ui(summary)

        if last_output and not cancelled:
            subprocess.run(["open", "-R", last_output])

    def _run_ffmpeg(self, cmd, dur, row_path=None):
        """Run ffmpeg, updating the progress bar + Now-running stats + row %.
        Returns None on success, or the tail of stderr on failure."""
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, bufsize=1)

        # Watcher: ffmpeg block-buffers stdout, so checking the cancel flag only
        # between progress lines can lag many seconds. This thread stops ffmpeg
        # promptly (within ~0.2s) when Cancel is pressed, regardless of output timing.
        # We use kill() (SIGKILL) not terminate() (SIGTERM): under SIGTERM, libx265
        # finishes the current encode before exiting (10s+ lag); the partial output
        # is discarded on cancel anyway, so a hard kill is both correct and instant.
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
            # A progress block ends with progress=continue|end — emit an update then.
            if key == "progress":
                self._emit_progress(cur, dur, row_path)
                cur = {}
        proc.wait()
        if proc.returncode not in (0, None) and not self._cancel.is_set():
            err = proc.stderr.read() if proc.stderr else ""
            return err[-800:] if err else f"ffmpeg exited with code {proc.returncode}"
        return None

    def _emit_progress(self, cur, dur, row_path):
        """Translate one ffmpeg -progress block into UI updates."""
        us = cur.get("out_time_us") or cur.get("out_time_ms")
        secs = int(us) / 1_000_000 if (us and us.isdigit()) else 0.0
        pct = min(100.0, secs / dur * 100) if dur > 0 else 0.0

        frame = cur.get("frame", "?")
        fps = cur.get("fps", "?")
        speed = cur.get("speed", "?")          # e.g. "1.3x"
        # ETA from speed + remaining time
        eta = None
        try:
            spd = float(str(speed).rstrip("x"))
            if spd > 0 and dur > 0:
                eta = max(0.0, (dur - secs) / spd)
        except ValueError:
            pass

        ended = cur.get("progress") == "end"
        if ended:
            pct = 100.0
            eta = 0.0

        stat_txt = (f"{pct:.0f}%   •   frame {frame}   •   {fps} fps   "
                    f"•   {speed}   •   ETA {fmt_time(eta)}")
        pct_txt = f"{pct:.0f}%"

        def apply():
            self.progress.config(value=pct)
            self.now_stats.set(stat_txt)
            if row_path is not None:
                self._set_row(row_path, pct=pct_txt)
        self._ui(apply)

    def _ui(self, fn):
        """Schedule a UI update on the main thread."""
        self.root.after(0, fn)


class SettingsDialog(tk.Toplevel):
    """Modal-ish settings editor. Calls on_save(new_settings_dict) when saved."""

    def __init__(self, parent, settings, on_save):
        super().__init__(parent)
        self.title("Settings")
        self.resizable(False, False)
        self.on_save = on_save
        s = dict(settings)

        self.dest_mode = tk.StringVar(value=s["dest_mode"])
        self.dest_dir = tk.StringVar(value=s["dest_dir"])
        self.suffix = tk.StringVar(value=s["suffix"])
        self.on_exists = tk.StringVar(value=s["on_exists"])
        self.crf = tk.IntVar(value=int(s["crf"]))
        self.preset = tk.StringVar(value=s["preset"])
        self.deband_range = tk.IntVar(value=int(s["deband_range"]))
        self.deband_blur = tk.BooleanVar(value=bool(s["deband_blur"]))
        self.dither = tk.IntVar(value=int(s["dither"]))
        self.thr_custom = tk.StringVar(value=str(s["thr_custom"]))

        pad = {"padx": 10, "pady": 4}
        r = 0

        def section(title):
            nonlocal r
            tk.Label(self, text=title, font=("Helvetica", 12, "bold")).grid(
                row=r, column=0, columnspan=3, sticky="w", padx=10, pady=(12, 2))
            r += 1

        # --- Output ---
        section("Output")
        tk.Label(self, text="Save to:").grid(row=r, column=0, sticky="e", **pad)
        tk.Radiobutton(self, text="Next to each source", variable=self.dest_mode,
                       value="same", command=self._toggle_dest).grid(row=r, column=1, sticky="w")
        r += 1
        tk.Radiobutton(self, text="A specific folder:", variable=self.dest_mode,
                       value="custom", command=self._toggle_dest).grid(row=r, column=1, sticky="w")
        r += 1
        self.dest_entry = tk.Entry(self, textvariable=self.dest_dir, width=34)
        self.dest_entry.grid(row=r, column=1, sticky="w", padx=10)
        self.dest_btn = tk.Button(self, text="Browse…", command=self._browse_dest)
        self.dest_btn.grid(row=r, column=2, sticky="w")
        r += 1
        tk.Label(self, text="Filename suffix:").grid(row=r, column=0, sticky="e", **pad)
        tk.Entry(self, textvariable=self.suffix, width=16).grid(row=r, column=1, sticky="w", padx=10)
        r += 1
        tk.Label(self, text="If output exists:").grid(row=r, column=0, sticky="e", **pad)
        ef = tk.Frame(self); ef.grid(row=r, column=1, sticky="w")
        tk.Radiobutton(ef, text="Skip", variable=self.on_exists, value="skip").pack(side="left")
        tk.Radiobutton(ef, text="Overwrite", variable=self.on_exists, value="overwrite").pack(side="left")
        r += 1

        # --- Quality (HEVC) ---
        section("Quality (HEVC only)")
        tk.Label(self, text="CRF (lower = better/bigger):").grid(row=r, column=0, sticky="e", **pad)
        tk.Spinbox(self, from_=0, to=51, textvariable=self.crf, width=6).grid(
            row=r, column=1, sticky="w", padx=10)
        r += 1
        tk.Label(self, text="Encoder preset:").grid(row=r, column=0, sticky="e", **pad)
        ttk.Combobox(self, textvariable=self.preset, state="readonly", width=12,
                     values=X265_PRESETS).grid(row=r, column=1, sticky="w", padx=10)
        r += 1

        # --- Deband / dither ---
        section("Deband & dither")
        tk.Label(self, text="Deband range:").grid(row=r, column=0, sticky="e", **pad)
        tk.Spinbox(self, from_=1, to=64, textvariable=self.deband_range, width=6).grid(
            row=r, column=1, sticky="w", padx=10)
        r += 1
        tk.Checkbutton(self, text="Deband blur", variable=self.deband_blur).grid(
            row=r, column=1, sticky="w", padx=10)
        r += 1
        tk.Label(self, text="Dither amount:").grid(row=r, column=0, sticky="e", **pad)
        tk.Spinbox(self, from_=0, to=20, textvariable=self.dither, width=6).grid(
            row=r, column=1, sticky="w", padx=10)
        r += 1
        tk.Label(self, text="Custom threshold:").grid(row=r, column=0, sticky="e", **pad)
        tk.Entry(self, textvariable=self.thr_custom, width=8).grid(row=r, column=1, sticky="w", padx=10)
        tk.Label(self, text="(used by the “Custom” strength option)", fg="#888").grid(
            row=r, column=2, sticky="w")
        r += 1

        # --- Buttons ---
        bf = tk.Frame(self)
        bf.grid(row=r, column=0, columnspan=3, pady=14)
        tk.Button(bf, text="Restore defaults", command=self._restore).pack(side="left", padx=6)
        tk.Button(bf, text="Cancel", command=self.destroy).pack(side="left", padx=6)
        tk.Button(bf, text="Save", font=("Helvetica", 11, "bold"),
                  command=self._save).pack(side="left", padx=6)

        self._toggle_dest()
        self.transient(parent)
        self.grab_set()

    def _toggle_dest(self):
        state = "normal" if self.dest_mode.get() == "custom" else "disabled"
        self.dest_entry.config(state=state)
        self.dest_btn.config(state=state)

    def _browse_dest(self):
        d = filedialog.askdirectory(title="Choose output folder", parent=self)
        if d:
            self.dest_dir.set(d)

    def _restore(self):
        d = DEFAULT_SETTINGS
        self.dest_mode.set(d["dest_mode"]); self.dest_dir.set(d["dest_dir"])
        self.suffix.set(d["suffix"]); self.on_exists.set(d["on_exists"])
        self.crf.set(d["crf"]); self.preset.set(d["preset"])
        self.deband_range.set(d["deband_range"]); self.deband_blur.set(d["deband_blur"])
        self.dither.set(d["dither"]); self.thr_custom.set(d["thr_custom"])
        self._toggle_dest()

    def _save(self):
        new = {
            "dest_mode": self.dest_mode.get(),
            "dest_dir": self.dest_dir.get().strip(),
            "suffix": self.suffix.get().strip() or "_10bit",
            "on_exists": self.on_exists.get(),
            "crf": int(self.crf.get()),
            "preset": self.preset.get(),
            "deband_range": int(self.deband_range.get()),
            "deband_blur": bool(self.deband_blur.get()),
            "dither": int(self.dither.get()),
            "thr_custom": self.thr_custom.get().strip() or "0.03",
        }
        if new["dest_mode"] == "custom" and not new["dest_dir"]:
            messagebox.showwarning("Pick a folder",
                                   "Choose an output folder, or switch back to "
                                   "“Next to each source”.", parent=self)
            return
        self.on_save(new)
        self.destroy()


if __name__ == "__main__":
    ensure_bundled_ffmpeg()
    try:
        from tkinterdnd2 import TkinterDnD
        root = TkinterDnD.Tk()
    except ImportError:
        root = tk.Tk()
    app = ConverterApp(root)
    root.mainloop()
