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
    - Batch queue: add multiple files or a whole folder and convert them in one go
    - Real percentage progress bar (parsed from ffmpeg, not a fake spinner)
    - Deband strength control: Low / Medium / High
"""

import os
import re
import shutil
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, ttk, messagebox

VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm", ".mpg", ".mpeg", ".ts")

# Deband threshold per strength level (applied to all three planes).
STRENGTH_THR = {
    "Low (subtle, preserves detail)": "0.01",
    "Medium (balanced)": "0.02",
    "High (smooth skies/gradients)": "0.05",
}


def build_filters(thr: str, pix_fmt: str) -> str:
    return (
        f"deband=1thr={thr}:2thr={thr}:3thr={thr}:range=16:blur=1,"
        f"noise=alls=2:allf=t+u,format={pix_fmt}"
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


class ConverterApp:
    def __init__(self, root):
        self.root = root
        root.title("8-bit → 10-bit Converter")
        root.geometry("560x560")
        root.resizable(False, False)

        self.queue = []                       # list of input file paths
        self.mode = tk.StringVar(value="HEVC (smaller, delivery)")
        self.strength = tk.StringVar(value="Medium (balanced)")
        self.status = tk.StringVar(value="Add files or a folder to begin.")
        self._cancel = threading.Event()

        tk.Label(root, text="8-bit → 10-bit Video Converter",
                 font=("Helvetica", 16, "bold")).pack(pady=(16, 2))
        tk.Label(root, text="Debands gradients + upconverts to true 10-bit  •  batch-capable",
                 fg="#666").pack(pady=(0, 10))

        # --- Queue list ---
        list_frame = tk.Frame(root)
        list_frame.pack(fill="x", padx=20)
        self.listbox = tk.Listbox(list_frame, height=8, activestyle="none")
        self.listbox.pack(side="left", fill="both", expand=True)
        sb = tk.Scrollbar(list_frame, command=self.listbox.yview)
        sb.pack(side="right", fill="y")
        self.listbox.config(yscrollcommand=sb.set)

        # Optional native drag-and-drop if tkinterdnd2 is installed
        try:
            from tkinterdnd2 import DND_FILES  # noqa
            self.listbox.drop_target_register(DND_FILES)
            self.listbox.dnd_bind("<<Drop>>", self._on_drop)
        except Exception:
            pass

        # --- Queue buttons ---
        btns = tk.Frame(root)
        btns.pack(pady=8)
        tk.Button(btns, text="Add files…", command=self.add_files).pack(side="left", padx=4)
        tk.Button(btns, text="Add folder…", command=self.add_folder).pack(side="left", padx=4)
        tk.Button(btns, text="Remove selected", command=self.remove_selected).pack(side="left", padx=4)
        tk.Button(btns, text="Clear", command=self.clear_queue).pack(side="left", padx=4)

        # --- Options ---
        opt = tk.Frame(root)
        opt.pack(pady=6)
        tk.Label(opt, text="Format:").grid(row=0, column=0, sticky="e", padx=(0, 6), pady=3)
        ttk.Combobox(opt, textvariable=self.mode, state="readonly", width=32,
                     values=["HEVC (smaller, delivery)",
                             "ProRes 4444 (grading, huge file)"]).grid(row=0, column=1, pady=3)
        tk.Label(opt, text="Deband strength:").grid(row=1, column=0, sticky="e", padx=(0, 6), pady=3)
        ttk.Combobox(opt, textvariable=self.strength, state="readonly", width=32,
                     values=list(STRENGTH_THR.keys())).grid(row=1, column=1, pady=3)

        # --- Convert / Cancel buttons ---
        action = tk.Frame(root)
        action.pack(pady=12)
        self.convert_btn = tk.Button(action, text="Convert", font=("Helvetica", 13, "bold"),
                                     bg="#2d7dd2", fg="white", command=self.start_convert,
                                     state="disabled", width=16)
        self.convert_btn.pack(side="left", padx=4)
        self.cancel_btn = tk.Button(action, text="Cancel", font=("Helvetica", 13, "bold"),
                                    bg="#d9534f", fg="white", command=self.cancel_convert,
                                    state="disabled", width=10)
        self.cancel_btn.pack(side="left", padx=4)

        # --- Progress (determinate, real %) ---
        self.progress = ttk.Progressbar(root, mode="determinate", length=500, maximum=100)
        self.progress.pack(pady=4)
        tk.Label(root, textvariable=self.status, fg="#444", wraplength=500).pack(pady=8)

    # ------------------------------------------------------------------ queue
    def _add_paths(self, paths):
        added = 0
        for p in paths:
            if os.path.isdir(p):
                for f in sorted(os.listdir(p)):
                    fp = os.path.join(p, f)
                    if (os.path.isfile(fp) and f.lower().endswith(VIDEO_EXTS)
                            and "_10bit." not in f):
                        if fp not in self.queue:
                            self.queue.append(fp); added += 1
            elif os.path.isfile(p) and p.lower().endswith(VIDEO_EXTS):
                if p not in self.queue:
                    self.queue.append(p); added += 1
        self._refresh_list()
        if added:
            self.status.set(f"{len(self.queue)} file(s) queued.")

    def _refresh_list(self):
        self.listbox.delete(0, tk.END)
        for p in self.queue:
            self.listbox.insert(tk.END, os.path.basename(p))
        self.convert_btn.config(state="normal" if self.queue else "disabled")

    def _on_drop(self, event):
        # tkinterdnd2 gives a brace-wrapped, space-separated list
        raw = event.data
        paths = re.findall(r"\{[^}]*\}|\S+", raw)
        paths = [p.strip("{}") for p in paths]
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
        for i in reversed(self.listbox.curselection()):
            del self.queue[i]
        self._refresh_list()

    def clear_queue(self):
        self.queue.clear()
        self._refresh_list()
        self.status.set("Queue cleared.")

    # --------------------------------------------------------------- converting
    def start_convert(self):
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            messagebox.showerror("ffmpeg not found",
                                 "Install it in Terminal with:\n\nbrew install ffmpeg")
            return
        if not self.queue:
            return
        self._cancel.clear()
        self._set_controls(False)
        threading.Thread(target=self.run_batch, daemon=True).start()

    def cancel_convert(self):
        # Signal the worker thread; it terminates the running ffmpeg and stops the batch.
        self._cancel.set()
        self.cancel_btn.config(state="disabled")
        self.status.set("Cancelling…")

    def _set_controls(self, enabled: bool):
        # enabled=True  -> idle: Convert usable (if queue), Cancel off
        # enabled=False -> converting: Convert off, Cancel on
        self.convert_btn.config(state="normal" if (enabled and self.queue) else "disabled")
        self.cancel_btn.config(state="disabled" if enabled else "normal")

    def run_batch(self):
        total = len(self.queue)
        thr = STRENGTH_THR[self.strength.get()]
        is_prores = self.mode.get().startswith("ProRes")
        last_output = None

        for idx, in_path in enumerate(list(self.queue), start=1):
            base = os.path.splitext(os.path.basename(in_path))[0]
            directory = os.path.dirname(in_path)

            if is_prores:
                out_path = os.path.join(directory, f"{base}_10bit.mov")
                filters = build_filters(thr, "yuv444p10le")
                cmd = ["ffmpeg", "-y", "-nostats", "-i", in_path, "-vf", filters,
                       "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuv444p10le",
                       "-c:a", "pcm_s16le", "-progress", "pipe:1", out_path]
            else:
                out_path = os.path.join(directory, f"{base}_10bit.mp4")
                filters = build_filters(thr, "yuv420p10le")
                cmd = ["ffmpeg", "-y", "-nostats", "-i", in_path, "-vf", filters,
                       "-c:v", "libx265", "-pix_fmt", "yuv420p10le", "-crf", "18",
                       "-preset", "slow", "-tag:v", "hvc1", "-c:a", "aac", "-b:a", "192k",
                       "-progress", "pipe:1", out_path]

            dur = probe_duration(in_path)
            self._ui(lambda i=idx, b=os.path.basename(in_path):
                     self.status.set(f"[{i}/{total}] Converting {b}…"))
            self._ui(lambda: self.progress.config(value=0))

            err_tail = self._run_ffmpeg(cmd, dur)
            if self._cancel.is_set():
                # Remove the half-written output so no broken file is left behind.
                try:
                    if os.path.exists(out_path):
                        os.remove(out_path)
                except OSError:
                    pass
                self._ui(lambda: self.status.set("Cancelled."))
                self._ui(lambda: self.progress.config(value=0))
                self._ui(lambda: self._set_controls(True))
                return
            if err_tail is not None:
                self._ui(lambda e=err_tail: messagebox.showerror("ffmpeg error", e))
                self._ui(lambda: self.status.set("Conversion failed. See error dialog."))
                self._ui(lambda: self._set_controls(True))
                return
            last_output = out_path

        self._ui(lambda: self.progress.config(value=100))
        self._ui(lambda t=total: self.status.set(f"Done! Converted {t} file(s)."))
        self._ui(lambda: self._set_controls(True))
        if last_output:
            subprocess.run(["open", "-R", last_output])

    def _run_ffmpeg(self, cmd, dur):
        """Run ffmpeg, updating the progress bar. Returns None on success,
        or the tail of stderr on failure."""
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
        watcher = threading.Thread(target=_watch, daemon=True)
        watcher.start()

        for line in proc.stdout:
            if self._cancel.is_set():
                proc.kill()
                break
            line = line.strip()
            if line.startswith(("out_time_us=", "out_time_ms=")):
                val = line.split("=", 1)[1]
                if val.isdigit() and dur > 0:
                    pct = min(100.0, (int(val) / 1_000_000) / dur * 100)
                    self._ui(lambda p=pct: self.progress.config(value=p))
            elif line == "progress=end":
                self._ui(lambda: self.progress.config(value=100))
        proc.wait()
        if proc.returncode not in (0, None) and not self._cancel.is_set():
            err = proc.stderr.read() if proc.stderr else ""
            return err[-800:] if err else f"ffmpeg exited with code {proc.returncode}"
        return None

    def _ui(self, fn):
        """Schedule a UI update on the main thread."""
        self.root.after(0, fn)


if __name__ == "__main__":
    try:
        from tkinterdnd2 import TkinterDnD
        root = TkinterDnD.Tk()
    except ImportError:
        root = tk.Tk()
    app = ConverterApp(root)
    root.mainloop()
