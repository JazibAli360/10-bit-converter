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
"""

import os
import subprocess
import shutil
import threading
import tkinter as tk
from tkinter import filedialog, ttk, messagebox

FILTERS = "deband=1thr=0.02:2thr=0.02:3thr=0.02:range=16:blur=1,noise=alls=2:allf=t+u,format=yuv420p10le"


class ConverterApp:
    def __init__(self, root):
        self.root = root
        root.title("8-bit → 10-bit Converter")
        root.geometry("520x360")
        root.resizable(False, False)

        self.input_path = tk.StringVar()
        self.mode = tk.StringVar(value="HEVC (smaller, delivery)")
        self.status = tk.StringVar(value="Drop a file or click Browse to begin.")

        pad = {"padx": 16, "pady": 8}

        tk.Label(root, text="8-bit → 10-bit Video Converter", font=("Helvetica", 16, "bold")).pack(pady=(16, 4))
        tk.Label(root, text="Debands gradients + upconverts to true 10-bit", fg="#666").pack(pady=(0, 12))

        # Drop zone / file display
        self.drop_frame = tk.Frame(root, bg="#f0f0f0", height=90, highlightbackground="#ccc", highlightthickness=2)
        self.drop_frame.pack(fill="x", padx=20, pady=4)
        self.drop_frame.pack_propagate(False)
        self.drop_label = tk.Label(self.drop_frame, text="Drag a video here (or use Browse below)", bg="#f0f0f0", fg="#888")
        self.drop_label.pack(expand=True)

        try:
            # Optional native drag-and-drop if tkinterdnd2 is installed
            from tkinterdnd2 import DND_FILES, TkinterDnD  # noqa
            self.drop_frame.drop_target_register(DND_FILES)
            self.drop_frame.dnd_bind('<<Drop>>', self._on_drop)
        except Exception:
            pass  # falls back to Browse button only

        tk.Button(root, text="Browse for video...", command=self.browse_file).pack(pady=6)

        # Mode selector
        mode_frame = tk.Frame(root)
        mode_frame.pack(pady=8)
        tk.Label(mode_frame, text="Output format:").pack(side="left", padx=(0, 8))
        ttk.Combobox(
            mode_frame, textvariable=self.mode, state="readonly", width=30,
            values=["HEVC (smaller, delivery)", "ProRes 4444 (grading, huge file)"]
        ).pack(side="left")

        # Convert button
        self.convert_btn = tk.Button(root, text="Convert", font=("Helvetica", 13, "bold"),
                                      bg="#2d7dd2", fg="white", command=self.start_convert,
                                      state="disabled", width=20)
        self.convert_btn.pack(pady=14)

        self.progress = ttk.Progressbar(root, mode="indeterminate", length=460)
        self.progress.pack(pady=4)

        tk.Label(root, textvariable=self.status, fg="#444", wraplength=460).pack(pady=8)

    def _on_drop(self, event):
        path = event.data.strip("{}")
        self.input_path.set(path)
        self.drop_label.config(text=os.path.basename(path), fg="#000")
        self.convert_btn.config(state="normal")

    def browse_file(self):
        path = filedialog.askopenfilename(
            title="Select a video",
            filetypes=[("Video files", "*.mp4 *.mov *.mkv *.avi"), ("All files", "*.*")]
        )
        if path:
            self.input_path.set(path)
            self.drop_label.config(text=os.path.basename(path), fg="#000")
            self.convert_btn.config(state="normal")

    def start_convert(self):
        if not shutil.which("ffmpeg"):
            messagebox.showerror("ffmpeg not found", "Install it in Terminal with:\n\nbrew install ffmpeg")
            return
        self.convert_btn.config(state="disabled")
        self.progress.start(12)
        self.status.set("Converting... this can take a while for long clips.")
        threading.Thread(target=self.run_ffmpeg, daemon=True).start()

    def run_ffmpeg(self):
        input_path = self.input_path.get()
        directory = os.path.dirname(input_path)
        name = os.path.splitext(os.path.basename(input_path))[0]
        is_prores = self.mode.get().startswith("ProRes")

        if is_prores:
            output_path = os.path.join(directory, f"{name}_10bit.mov")
            cmd = [
                "ffmpeg", "-y", "-i", input_path,
                "-vf", FILTERS,
                "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuv444p10le",
                "-c:a", "pcm_s16le",
                output_path,
            ]
        else:
            output_path = os.path.join(directory, f"{name}_10bit.mp4")
            cmd = [
                "ffmpeg", "-y", "-i", input_path,
                "-vf", FILTERS,
                "-c:v", "libx265", "-pix_fmt", "yuv420p10le", "-crf", "18", "-preset", "slow",
                "-tag:v", "hvc1",
                "-c:a", "aac", "-b:a", "192k",
                output_path,
            ]

        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            self.root.after(0, self.on_success, output_path)
        except subprocess.CalledProcessError as e:
            self.root.after(0, self.on_error, e.stderr[-800:] if e.stderr else str(e))

    def on_success(self, output_path):
        self.progress.stop()
        self.status.set(f"Done! Saved to:\n{output_path}")
        self.convert_btn.config(state="normal")
        subprocess.run(["open", "-R", output_path])

    def on_error(self, err_msg):
        self.progress.stop()
        self.status.set("Conversion failed. See error dialog.")
        self.convert_btn.config(state="normal")
        messagebox.showerror("ffmpeg error", err_msg)


if __name__ == "__main__":
    try:
        from tkinterdnd2 import TkinterDnD
        root = TkinterDnD.Tk()
    except ImportError:
        root = tk.Tk()
    app = ConverterApp(root)
    root.mainloop()
