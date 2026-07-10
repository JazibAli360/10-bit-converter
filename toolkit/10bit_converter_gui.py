#!/usr/bin/env python3
"""
10bit_converter_gui.py
A polished Mac GUI for upconverting 8-bit AI-generated video to 10-bit
(debanding + dithering + true 10-bit encode). Standard Tkinter/ttk so it
renders on any Mac's Python (incl. the system Tk 8.5) with zero extra install.

SETUP (one-time, handled by Start_Here.command):
    brew install python-tk     # only if your Python has no Tk at all
    # ffmpeg is bundled in bin/<arch>/ — no install needed

RUN:
    python3 10bit_converter_gui.py

WHAT IT DOES:
    - Debands gradients (the actual cause of flat/blocky-looking 8-bit AI video)
    - Adds subtle dither noise so gradients read as smooth
    - Encodes to a true 10-bit codec (HEVC Main10 or ProRes 4444)

FEATURES:
    - Batch queue (table) with per-file status + live %
    - "Now running" panel with live frame / fps / speed / ETA from ffmpeg
    - Scopes preview: source vs processed frame + histogram, so you can SEE
      the debanding on a still before committing to a full encode
    - Deband strength Low / Medium / High / Custom
    - Cancel (instant) with partial-output cleanup
    - Settings (persisted): output folder, suffix, skip/overwrite, HEVC CRF +
      preset, deband range/blur, dither amount, custom threshold
    - Bundled ffmpeg (bin/<arch>) preferred over any system install
"""

import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, ttk, messagebox

VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm", ".mpg", ".mpeg", ".ts")

STRENGTH_THR = {"Low": "0.01", "Medium": "0.02", "High": "0.05", "Custom": None}
X265_PRESETS = ["ultrafast", "superfast", "veryfast", "faster", "fast",
                "medium", "slow", "slower", "veryslow"]

ACCENT = "#2d7dd2"; ACCENT_DK = "#1b5fa5"; DANGER = "#d9534f"; DANGER_DK = "#b52b27"
HEADER_BG = "#1e2a38"; HEADER_SUB = "#9db4cf"

SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             ".10bit_converter_settings.json")
DEFAULT_SETTINGS = {
    "dest_mode": "same", "dest_dir": "", "suffix": "_10bit", "on_exists": "skip",
    "crf": 18, "preset": "slow", "deband_range": 16, "deband_blur": True,
    "dither": 2, "thr_custom": "0.03",
}
STATUS_FG = {"Queued": "#8a8a8a", "Running": "#1f6feb", "Done": "#1a7f37",
             "Failed": "#b42318", "Skipped": "#8a8a8a", "Cancelled": "#8a8a8a"}


# ------------------------------------------------------------------ helpers
def ensure_bundled_ffmpeg():
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


def deband_noise_chain(thr, deband_range=16, deband_blur=True, dither=2):
    blur = 1 if deband_blur else 0
    return (f"deband=1thr={thr}:2thr={thr}:3thr={thr}:range={deband_range}:blur={blur},"
            f"noise=alls={dither}:allf=t+u")


def build_filters(thr, pix_fmt, deband_range=16, deband_blur=True, dither=2):
    return deband_noise_chain(thr, deband_range, deband_blur, dither) + f",format={pix_fmt}"


def probe_duration(path):
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                              "-of", "csv=p=0", path], capture_output=True, text=True,
                             check=True).stdout.strip()
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


def generate_scopes(in_path, deb_chain, outdir):
    """Render source vs processed frame + histogram (PPM, Tk8.5-safe). Returns dict of paths."""
    dur = probe_duration(in_path)
    t = max(0.0, dur * 0.4) if dur else 1.0
    paths = {"src_thumb": os.path.join(outdir, "src_thumb.ppm"),
             "src_hist": os.path.join(outdir, "src_hist.ppm"),
             "aft_thumb": os.path.join(outdir, "aft_thumb.ppm"),
             "aft_hist": os.path.join(outdir, "aft_hist.ppm")}
    common = ["ffmpeg", "-y", "-v", "error", "-ss", f"{t}", "-i", in_path, "-frames:v", "1", "-vf"]
    jobs = [
        (paths["src_thumb"], "scale=360:-2"),
        (paths["src_hist"], "histogram=level_height=150,scale=360:-2"),
        (paths["aft_thumb"], f"{deb_chain},scale=360:-2"),
        (paths["aft_hist"], f"{deb_chain},histogram=level_height=150,scale=360:-2"),
    ]
    for out, vf in jobs:
        subprocess.run(common + [vf, out], capture_output=True)
    return paths


# ------------------------------------------------------------------ main app
class ConverterApp:
    def __init__(self, root):
        self.root = root
        root.title("8-bit → 10-bit Converter")
        root.geometry("720x820")
        root.minsize(680, 760)
        try:
            root.configure(bg="#ececec")
        except tk.TclError:
            pass

        self.settings = load_settings()
        self.queue = []
        self.mode = tk.StringVar(value="HEVC (smaller, delivery)")
        self.strength = tk.StringVar(value="Medium")
        self.status = tk.StringVar(value="Add files or a folder to begin.")
        self.now_file = tk.StringVar(value="—")
        self.now_stats = tk.StringVar(value="idle")
        self._cancel = threading.Event()
        self._scope_imgs = []          # keep PhotoImage refs alive

        self._init_style()
        self._build_header()
        self._build_toolbar()
        self._build_queue()
        self._build_options()
        self._build_now_running()
        self._build_actions()
        self._build_statusbar()

    def _init_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")   # clam honours custom colours on macOS
        except tk.TclError:
            pass
        style.configure("Treeview", rowheight=30, font=("Helvetica", 12),
                        background="white", fieldbackground="white", borderwidth=0)
        style.configure("Treeview.Heading", font=("Helvetica", 11, "bold"))
        style.configure("Primary.TButton", background=ACCENT, foreground="white",
                        font=("Helvetica", 13, "bold"), padding=10, borderwidth=0)
        style.map("Primary.TButton",
                  background=[("active", ACCENT_DK), ("disabled", "#a9c4e2")])
        style.configure("Danger.TButton", background=DANGER, foreground="white",
                        font=("Helvetica", 12, "bold"), padding=10, borderwidth=0)
        style.map("Danger.TButton",
                  background=[("active", DANGER_DK), ("disabled", "#e6b3b1")])
        style.configure("Tool.TButton", padding=6)

    def _build_header(self):
        head = tk.Frame(self.root, bg=HEADER_BG)
        head.pack(fill="x")
        tk.Label(head, text="🎬  8-bit → 10-bit Converter", bg=HEADER_BG, fg="white",
                 font=("Helvetica", 20, "bold")).pack(anchor="w", padx=20, pady=(16, 0))
        tk.Label(head, text="Debands gradients and re-encodes to true 10-bit  •  batch-capable",
                 bg=HEADER_BG, fg=HEADER_SUB, font=("Helvetica", 12)).pack(
            anchor="w", padx=20, pady=(2, 16))

    def _build_toolbar(self):
        bar = tk.Frame(self.root)
        bar.pack(fill="x", padx=16, pady=(12, 4))
        ttk.Button(bar, text="＋ Add files", style="Tool.TButton", command=self.add_files).pack(side="left", padx=(0, 6))
        ttk.Button(bar, text="📁 Add folder", style="Tool.TButton", command=self.add_folder).pack(side="left", padx=6)
        ttk.Button(bar, text="Remove", style="Tool.TButton", command=self.remove_selected).pack(side="left", padx=6)
        ttk.Button(bar, text="Clear", style="Tool.TButton", command=self.clear_queue).pack(side="left", padx=6)
        ttk.Button(bar, text="⚙ Settings", style="Tool.TButton", command=self.open_settings).pack(side="right")
        ttk.Button(bar, text="📊 Preview scopes", style="Tool.TButton", command=self.preview_scopes).pack(side="right", padx=6)

    def _build_queue(self):
        wrap = tk.Frame(self.root)
        wrap.pack(fill="both", expand=True, padx=16, pady=4)
        cols = ("file", "status", "pct")
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings", selectmode="extended")
        self.tree.heading("file", text="File"); self.tree.heading("status", text="Status")
        self.tree.heading("pct", text="%")
        self.tree.column("file", width=420, anchor="w")
        self.tree.column("status", width=110, anchor="center")
        self.tree.column("pct", width=64, anchor="center")
        self.tree.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        sb.pack(side="right", fill="y"); self.tree.config(yscrollcommand=sb.set)
        for st, fg in STATUS_FG.items():
            self.tree.tag_configure(st, foreground=fg)
        try:
            from tkinterdnd2 import DND_FILES
            self.tree.drop_target_register(DND_FILES)
            self.tree.dnd_bind("<<Drop>>", self._on_drop)
        except Exception:
            pass

    def _build_options(self):
        opt = tk.Frame(self.root)
        opt.pack(fill="x", padx=16, pady=(6, 2))
        tk.Label(opt, text="Format:").grid(row=0, column=0, sticky="e", padx=(0, 8), pady=3)
        ttk.Combobox(opt, textvariable=self.mode, state="readonly", width=30,
                     values=["HEVC (smaller, delivery)", "ProRes 4444 (grading, huge file)"]).grid(
            row=0, column=1, sticky="w", pady=3)
        tk.Label(opt, text="Deband:").grid(row=0, column=2, sticky="e", padx=(16, 8), pady=3)
        ttk.Combobox(opt, textvariable=self.strength, state="readonly", width=10,
                     values=list(STRENGTH_THR.keys())).grid(row=0, column=3, sticky="w", pady=3)

    def _build_now_running(self):
        card = tk.LabelFrame(self.root, text=" Now running ", font=("Helvetica", 10, "bold"),
                             fg="#555", padx=12, pady=8)
        card.pack(fill="x", padx=16, pady=(8, 2))
        tk.Label(card, textvariable=self.now_file, font=("Helvetica", 14, "bold"),
                 anchor="w").pack(fill="x")
        tk.Label(card, textvariable=self.now_stats, fg="#666", anchor="w").pack(fill="x", pady=(0, 4))
        self.progress = ttk.Progressbar(card, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(2, 4))

    def _build_actions(self):
        act = tk.Frame(self.root)
        act.pack(pady=(8, 2))
        self.convert_btn = ttk.Button(act, text="Convert", style="Primary.TButton",
                                      state="disabled", command=self.start_convert)
        self.convert_btn.pack(side="left", padx=6, ipadx=30)
        self.cancel_btn = ttk.Button(act, text="Cancel", style="Danger.TButton",
                                     state="disabled", command=self.cancel_convert)
        self.cancel_btn.pack(side="left", padx=6, ipadx=8)

    def _build_statusbar(self):
        tk.Label(self.root, textvariable=self.status, fg="#555", anchor="w").pack(
            fill="x", padx=18, pady=(2, 12))

    # ---- queue ----
    def _add_paths(self, paths):
        added = 0
        for p in paths:
            if os.path.isdir(p):
                for f in sorted(os.listdir(p)):
                    fp = os.path.join(p, f)
                    if (os.path.isfile(fp) and f.lower().endswith(VIDEO_EXTS)
                            and "_10bit." not in f and fp not in self.queue):
                        self._insert_row(fp); added += 1
            elif os.path.isfile(p) and p.lower().endswith(VIDEO_EXTS) and p not in self.queue:
                self._insert_row(p); added += 1
        self.convert_btn.config(state="normal" if self.queue else "disabled")
        if added:
            self.status.set(f"{len(self.queue)} file(s) queued.")

    def _insert_row(self, path):
        self.queue.append(path)
        self.tree.insert("", "end", iid=path,
                         values=(os.path.basename(path), "Queued", ""), tags=("Queued",))

    def _set_row(self, path, status=None, pct=None):
        if not self.tree.exists(path):
            return
        if status is not None:
            self.tree.set(path, "status", status); self.tree.item(path, tags=(status,))
        if pct is not None:
            self.tree.set(path, "pct", pct)

    def _on_drop(self, event):
        self._add_paths([p.strip("{}") for p in re.findall(r"\{[^}]*\}|\S+", event.data)])

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

    def remove_selected(self):
        for iid in self.tree.selection():
            if iid in self.queue:
                self.queue.remove(iid)
            self.tree.delete(iid)
        self.convert_btn.config(state="normal" if self.queue else "disabled")

    def clear_queue(self):
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        self.queue.clear()
        self.convert_btn.config(state="disabled")
        self.status.set("Queue cleared.")

    # ---- settings ----
    def open_settings(self):
        SettingsDialog(self.root, self.settings, self._on_settings_saved)

    def _on_settings_saved(self, s):
        self.settings = s; save_settings(s); self.status.set("Settings saved.")

    def _resolve_thr(self):
        thr = STRENGTH_THR.get(self.strength.get())
        return thr if thr is not None else str(self.settings.get("thr_custom", "0.03"))

    def _current_deband_chain(self):
        return deband_noise_chain(self._resolve_thr(), self.settings["deband_range"],
                                  self.settings["deband_blur"], self.settings["dither"])

    # ---- scopes preview ----
    def _target_file(self):
        sel = self.tree.selection()
        if sel:
            return sel[0]
        return self.queue[0] if self.queue else None

    def preview_scopes(self):
        if not shutil.which("ffmpeg"):
            messagebox.showerror("ffmpeg not found", "Bundled ffmpeg missing and none installed.")
            return
        target = self._target_file()
        if not target:
            self.status.set("Add a file (or select one) first, then Preview scopes.")
            return
        self.status.set(f"Rendering scopes for {os.path.basename(target)}…")
        outdir = tempfile.mkdtemp(prefix="scopes_")
        chain = self._current_deband_chain()
        threading.Thread(target=lambda: self._scopes_worker(target, chain, outdir),
                         daemon=True).start()

    def _scopes_worker(self, target, chain, outdir):
        try:
            paths = generate_scopes(target, chain, outdir)
        except Exception as e:
            self.root.after(0, lambda: self.status.set(f"Scope render failed: {e}"))
            return
        self.root.after(0, lambda: self._show_scopes(target, paths))

    def _show_scopes(self, target, paths):
        self.status.set("Scopes ready.")
        win = tk.Toplevel(self.root)
        win.title(f"Scopes — {os.path.basename(target)}")
        win.configure(bg="#1e1e1e")
        imgs = []
        try:
            for k in ("src_thumb", "src_hist", "aft_thumb", "aft_hist"):
                imgs.append(tk.PhotoImage(file=paths[k]))
        except tk.TclError as e:
            self.status.set(f"Couldn't load scope images: {e}"); win.destroy(); return
        self._scope_imgs = imgs   # prevent GC
        tk.Label(win, text="Source (8-bit)", bg="#1e1e1e", fg="#ddd",
                 font=("Helvetica", 12, "bold")).grid(row=0, column=0, pady=(10, 4))
        tk.Label(win, text=f"After (deband + dither · {self.strength.get()})", bg="#1e1e1e",
                 fg="#8fd19e", font=("Helvetica", 12, "bold")).grid(row=0, column=1, pady=(10, 4))
        tk.Label(win, image=imgs[0], bg="#1e1e1e").grid(row=1, column=0, padx=10)
        tk.Label(win, image=imgs[2], bg="#1e1e1e").grid(row=1, column=1, padx=10)
        tk.Label(win, image=imgs[1], bg="#1e1e1e").grid(row=2, column=0, padx=10, pady=(6, 4))
        tk.Label(win, image=imgs[3], bg="#1e1e1e").grid(row=2, column=1, padx=10, pady=(6, 4))
        tk.Label(win, text="Histogram: banding shows as a comb (spikes with gaps). After debanding "
                           "the gaps fill in and gradients read smooth.",
                 bg="#1e1e1e", fg="#aaa", wraplength=740, justify="left").grid(
            row=3, column=0, columnspan=2, padx=12, pady=(2, 12))

    # ---- conversion ----
    def start_convert(self):
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            messagebox.showerror("ffmpeg not found", "Bundled ffmpeg missing and none installed.\n\n"
                                 "Install with:  brew install ffmpeg")
            return
        if not self.queue:
            return
        for p in self.queue:
            self._set_row(p, status="Queued", pct="")
        self._cancel.clear(); self._set_controls(False)
        threading.Thread(target=self.run_batch, daemon=True).start()

    def cancel_convert(self):
        self._cancel.set(); self.cancel_btn.config(state="disabled"); self.status.set("Cancelling…")

    def _set_controls(self, enabled):
        self.convert_btn.config(state="normal" if (enabled and self.queue) else "disabled")
        self.cancel_btn.config(state="disabled" if enabled else "normal")

    def run_batch(self):
        thr = self._resolve_thr()
        is_prores = self.mode.get().startswith("ProRes")
        dest_dir = self.settings["dest_dir"] if self.settings["dest_mode"] == "custom" else ""
        suffix = self.settings["suffix"] or "_10bit"
        overwrite = self.settings["on_exists"] == "overwrite"
        pix_fmt = "yuv444p10le" if is_prores else "yuv420p10le"
        filters = build_filters(thr, pix_fmt, self.settings["deband_range"],
                                self.settings["deband_blur"], self.settings["dither"])
        items = list(self.queue); total = len(items)
        done = failed = skipped = 0; last_output = None

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
            dur = probe_duration(in_path); name = os.path.basename(in_path)
            self._ui(lambda p=in_path: self._set_row(p, status="Running", pct="0%"))
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
                self._ui(lambda p=in_path: self._set_row(p, status="Cancelled", pct="—"))
                break
            if err_tail is not None:
                failed += 1
                self._ui(lambda p=in_path: self._set_row(p, status="Failed", pct="—"))
                self._ui(lambda e=err_tail, n=name: messagebox.showerror("ffmpeg error", f"{n}\n\n{e}"))
                continue
            done += 1; last_output = out_path
            self._ui(lambda p=in_path: self._set_row(p, status="Done", pct="100%"))

        cancelled = self._cancel.is_set()
        self._ui(lambda: (self.now_file.set("—"), self.now_stats.set("idle"),
                          self.progress.config(value=0)))

        def summary():
            parts = [f"{done} done"]
            if skipped: parts.append(f"{skipped} skipped")
            if failed: parts.append(f"{failed} failed")
            if cancelled: parts.append("cancelled")
            self.status.set("Finished: " + ", ".join(parts) + ".")
            self._set_controls(True)
        self._ui(summary)
        if last_output and not cancelled:
            subprocess.run(["open", "-R", last_output])

    def _run_ffmpeg(self, cmd, dur, row_path=None):
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, bufsize=1)

        # Watcher kills ffmpeg promptly on cancel. kill() not terminate(): under
        # SIGTERM libx265 finishes the encode first (10s+ lag); the partial file
        # is discarded on cancel, so a hard kill is correct and instant.
        def _watch():
            while proc.poll() is None:
                if self._cancel.wait(0.2):
                    proc.kill(); return
        threading.Thread(target=_watch, daemon=True).start()

        cur = {}
        for line in proc.stdout:
            if self._cancel.is_set():
                proc.kill(); break
            line = line.strip()
            if "=" not in line:
                continue
            key, val = line.split("=", 1); cur[key] = val
            if key == "progress":
                self._emit_progress(cur, dur, row_path); cur = {}
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
            self.progress.config(value=pct); self.now_stats.set(stat)
            if row_path is not None:
                self._set_row(row_path, pct=f"{pct:.0f}%")
        self._ui(apply)

    def _ui(self, fn):
        self.root.after(0, fn)


# ------------------------------------------------------------------ settings dialog
class SettingsDialog(tk.Toplevel):
    def __init__(self, parent, settings, on_save):
        super().__init__(parent)
        self.title("Settings"); self.resizable(False, False); self.on_save = on_save
        s = dict(settings)
        self.dest_mode = tk.StringVar(value=s["dest_mode"]); self.dest_dir = tk.StringVar(value=s["dest_dir"])
        self.suffix = tk.StringVar(value=s["suffix"]); self.on_exists = tk.StringVar(value=s["on_exists"])
        self.crf = tk.IntVar(value=int(s["crf"])); self.preset = tk.StringVar(value=s["preset"])
        self.deband_range = tk.IntVar(value=int(s["deband_range"]))
        self.deband_blur = tk.BooleanVar(value=bool(s["deband_blur"]))
        self.dither = tk.IntVar(value=int(s["dither"])); self.thr_custom = tk.StringVar(value=str(s["thr_custom"]))
        pad = {"padx": 10, "pady": 4}; r = 0

        def section(t):
            nonlocal r
            tk.Label(self, text=t, font=("Helvetica", 12, "bold")).grid(
                row=r, column=0, columnspan=3, sticky="w", padx=10, pady=(12, 2)); r += 1

        section("Output")
        tk.Label(self, text="Save to:").grid(row=r, column=0, sticky="e", **pad)
        tk.Radiobutton(self, text="Next to each source", variable=self.dest_mode, value="same",
                       command=self._toggle).grid(row=r, column=1, sticky="w"); r += 1
        tk.Radiobutton(self, text="A specific folder:", variable=self.dest_mode, value="custom",
                       command=self._toggle).grid(row=r, column=1, sticky="w"); r += 1
        self.dest_entry = tk.Entry(self, textvariable=self.dest_dir, width=32)
        self.dest_entry.grid(row=r, column=1, sticky="w", padx=10)
        self.dest_btn = tk.Button(self, text="Browse…", command=self._browse)
        self.dest_btn.grid(row=r, column=2, sticky="w"); r += 1
        tk.Label(self, text="Filename suffix:").grid(row=r, column=0, sticky="e", **pad)
        tk.Entry(self, textvariable=self.suffix, width=14).grid(row=r, column=1, sticky="w", padx=10); r += 1
        tk.Label(self, text="If output exists:").grid(row=r, column=0, sticky="e", **pad)
        ef = tk.Frame(self); ef.grid(row=r, column=1, sticky="w")
        tk.Radiobutton(ef, text="Skip", variable=self.on_exists, value="skip").pack(side="left")
        tk.Radiobutton(ef, text="Overwrite", variable=self.on_exists, value="overwrite").pack(side="left"); r += 1

        section("Quality (HEVC only)")
        tk.Label(self, text="CRF (lower = better/bigger):").grid(row=r, column=0, sticky="e", **pad)
        tk.Spinbox(self, from_=0, to=51, textvariable=self.crf, width=6).grid(row=r, column=1, sticky="w", padx=10); r += 1
        tk.Label(self, text="Encoder preset:").grid(row=r, column=0, sticky="e", **pad)
        ttk.Combobox(self, textvariable=self.preset, state="readonly", width=12,
                     values=X265_PRESETS).grid(row=r, column=1, sticky="w", padx=10); r += 1

        section("Deband & dither")
        tk.Label(self, text="Deband range:").grid(row=r, column=0, sticky="e", **pad)
        tk.Spinbox(self, from_=1, to=64, textvariable=self.deband_range, width=6).grid(row=r, column=1, sticky="w", padx=10); r += 1
        tk.Checkbutton(self, text="Deband blur", variable=self.deband_blur).grid(row=r, column=1, sticky="w", padx=10); r += 1
        tk.Label(self, text="Dither amount:").grid(row=r, column=0, sticky="e", **pad)
        tk.Spinbox(self, from_=0, to=20, textvariable=self.dither, width=6).grid(row=r, column=1, sticky="w", padx=10); r += 1
        tk.Label(self, text="Custom threshold:").grid(row=r, column=0, sticky="e", **pad)
        tk.Entry(self, textvariable=self.thr_custom, width=8).grid(row=r, column=1, sticky="w", padx=10)
        tk.Label(self, text="(used by the “Custom” strength)", fg="#888").grid(row=r, column=2, sticky="w"); r += 1

        bf = tk.Frame(self); bf.grid(row=r, column=0, columnspan=3, pady=14)
        tk.Button(bf, text="Restore defaults", command=self._restore).pack(side="left", padx=6)
        tk.Button(bf, text="Cancel", command=self.destroy).pack(side="left", padx=6)
        tk.Button(bf, text="Save", font=("Helvetica", 11, "bold"), command=self._save).pack(side="left", padx=6)
        self._toggle(); self.transient(parent); self.grab_set()

    def _toggle(self):
        st = "normal" if self.dest_mode.get() == "custom" else "disabled"
        self.dest_entry.config(state=st); self.dest_btn.config(state=st)

    def _browse(self):
        d = filedialog.askdirectory(title="Choose output folder", parent=self)
        if d:
            self.dest_dir.set(d)

    def _restore(self):
        d = DEFAULT_SETTINGS
        self.dest_mode.set(d["dest_mode"]); self.dest_dir.set(d["dest_dir"]); self.suffix.set(d["suffix"])
        self.on_exists.set(d["on_exists"]); self.crf.set(d["crf"]); self.preset.set(d["preset"])
        self.deband_range.set(d["deband_range"]); self.deband_blur.set(d["deband_blur"])
        self.dither.set(d["dither"]); self.thr_custom.set(d["thr_custom"]); self._toggle()

    def _save(self):
        new = {"dest_mode": self.dest_mode.get(), "dest_dir": self.dest_dir.get().strip(),
               "suffix": self.suffix.get().strip() or "_10bit", "on_exists": self.on_exists.get(),
               "crf": int(self.crf.get()), "preset": self.preset.get(),
               "deband_range": int(self.deband_range.get()), "deband_blur": bool(self.deband_blur.get()),
               "dither": int(self.dither.get()), "thr_custom": self.thr_custom.get().strip() or "0.03"}
        if new["dest_mode"] == "custom" and not new["dest_dir"]:
            messagebox.showwarning("Pick a folder",
                                   "Choose an output folder, or switch back to “Next to each source”.",
                                   parent=self); return
        self.on_save(new); self.destroy()


if __name__ == "__main__":
    ensure_bundled_ffmpeg()
    try:
        from tkinterdnd2 import TkinterDnD
        root = TkinterDnD.Tk()
    except ImportError:
        root = tk.Tk()
    ConverterApp(root)
    # macOS: force the window forward so it activates and paints immediately.
    root.update_idletasks()
    root.lift()
    root.attributes("-topmost", True)
    root.after(300, lambda: root.attributes("-topmost", False))
    try:
        root.focus_force()
    except tk.TclError:
        pass
    root.mainloop()
