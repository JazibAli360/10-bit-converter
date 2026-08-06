"""HTTP-free FFmpeg process supervision.

The application controller supplies cancellation and progress callbacks. This
keeps pipe draining, cancellation, and error normalization testable without a
web server or native window.
"""

import subprocess
import threading
import time


def run_ffmpeg(cmd, cancel_event, on_progress=None, on_process=None, stderr_limit=80,
               inactivity_timeout=None):
    """Run an FFmpeg progress command and return a concise error or ``None``.

    FFmpeg progress is read from stdout while stderr is drained concurrently;
    this is the important hang-prevention invariant for long encodes.
    """
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, bufsize=1)
    if on_process:
        on_process(proc)

    last_activity = [time.monotonic()]
    timed_out = threading.Event()

    def watch():
        while proc.poll() is None:
            if cancel_event.wait(0.2):
                try:
                    proc.kill()
                except OSError:
                    pass
                return
            if inactivity_timeout and time.monotonic() - last_activity[0] > inactivity_timeout:
                timed_out.set()
                try:
                    proc.kill()
                except OSError:
                    pass
                return

    threading.Thread(target=watch, daemon=True).start()
    errors = []

    def drain_stderr():
        if not proc.stderr:
            return
        for line in proc.stderr:
            last_activity[0] = time.monotonic()
            errors.append(line)
            if len(errors) > stderr_limit:
                del errors[:-stderr_limit]

    stderr_thread = threading.Thread(target=drain_stderr, daemon=True)
    stderr_thread.start()
    cur = {}
    if proc.stdout:
        for line in proc.stdout:
            last_activity[0] = time.monotonic()
            if cancel_event.is_set():
                try:
                    proc.kill()
                except OSError:
                    pass
                break
            line = line.strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            cur[key] = value
            if key == "progress":
                if on_progress:
                    on_progress(cur)
                cur = {}
    proc.wait()
    stderr_thread.join(timeout=1)
    if proc.stdout:
        proc.stdout.close()
    if proc.stderr:
        proc.stderr.close()
    if timed_out.is_set():
        return f"FFmpeg stopped reporting progress for {int(inactivity_timeout)} seconds and was stopped safely."
    if proc.returncode not in (0, None) and not cancel_event.is_set():
        text = "".join(errors)
        return text[-600:] if text else f"ffmpeg exited with code {proc.returncode}"
    return None


__all__ = ["run_ffmpeg"]
