"""Watch-folder coordination, independent from HTTP routes and native UI."""

import os


class WatchService:
    def __init__(self, job, state, last_run, video_exts, authorize_path,
                 load_settings, output_path, run_batch):
        self.job = job
        self.state = state
        self.last_run = last_run
        self.video_exts = video_exts
        self.authorize_path = authorize_path
        self.load_settings = load_settings
        self.output_path = output_path
        self.run_batch = run_batch
        self._sizes = {}

    def tick(self):
        if not self.state["enabled"] or not self.state["folder"] or self.job.running:
            return
        folder = self.state["folder"]
        if not os.path.isdir(folder):
            return
        settings = self.load_settings()
        is_prores = self.last_run["mode"].startswith("ProRes")
        destination = settings["dest_dir"] if settings["dest_mode"] == "custom" else ""
        suffix = settings["suffix"] or "_10bit"
        ready, seen = [], set()
        for name in sorted(os.listdir(folder)):
            if not name.lower().endswith(self.video_exts) or "_10bit." in name or f"{suffix}." in name:
                continue
            path = os.path.join(folder, name)
            if not os.path.isfile(path):
                continue
            seen.add(path)
            if os.path.exists(self.output_path(path, is_prores, destination, suffix)):
                continue
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            if self._sizes.get(path) == size:
                ready.append(path)
            self._sizes[path] = size
        for stale in set(self._sizes) - seen:
            del self._sizes[stale]
        if not ready:
            return
        items = [{"path": self.authorize_path(path), "name": os.path.basename(path),
                  "status": "Queued", "pct": ""} for path in ready]
        with self.job.lock:
            self.job.reset()
            self.job.running = True
            self.job.items = items
        self.state["processed"] += len(items)
        self.run_batch(items, self.last_run["mode"], self.last_run["strength"],
                       self.last_run["rate"], settings)
