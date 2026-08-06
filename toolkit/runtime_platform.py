"""Small platform adapter for the desktop shell.

The conversion pipeline deliberately stays platform-neutral.  This module owns
only desktop concerns: where packaged binaries and user data live, OS dialogs,
notifications, file reveal, and the temporary "keep awake" request used while
an export is running.
"""

import os
import platform
import subprocess
import sys
import webbrowser


IS_WINDOWS = sys.platform.startswith("win")
IS_MACOS = sys.platform == "darwin"


def binary_platform_dir():
    """Return the stable release-artifact directory for this CPU/OS pair."""
    machine = platform.machine().lower()
    if IS_WINDOWS:
        return "win-arm64" if "arm" in machine else "win-x64"
    if IS_MACOS:
        return "arm64" if "arm" in machine else "x86_64"
    return "linux-arm64" if "arm" in machine else "linux-x64"


def ffmpeg_tool_names():
    suffix = ".exe" if IS_WINDOWS else ""
    return (f"ffmpeg{suffix}", f"ffprobe{suffix}")


def app_support_dir(vendor, app_name):
    """Use the conventional writable settings location for each platform."""
    if IS_WINDOWS:
        root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if root:
            return os.path.join(root, vendor, app_name)
        return os.path.join(os.path.expanduser("~"), "AppData", "Local", vendor, app_name)
    if IS_MACOS:
        return os.path.join(os.path.expanduser("~/Library/Application Support"), vendor, app_name)
    root = os.environ.get("XDG_DATA_HOME", os.path.join(os.path.expanduser("~"), ".local", "share"))
    return os.path.join(root, vendor, app_name)


def remove_macos_quarantine(path):
    """Clear only macOS's download attribute; Windows never needs this step."""
    if IS_MACOS:
        try:
            subprocess.run(["xattr", "-dr", "com.apple.quarantine", path], capture_output=True)
        except OSError:
            pass


def show_error(title, message):
    """Present a useful launch error even for a windowed frozen build."""
    if IS_WINDOWS:
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, str(message), str(title), 0x10)
            return
        except Exception:
            pass
    if IS_MACOS:
        escaped = str(message).replace("\\", "\\\\").replace('"', '\\"')
        try:
            subprocess.run(["osascript", "-e", f'display alert "{title}" message "{escaped}"'],
                           capture_output=True, timeout=5)
            return
        except OSError:
            pass
    print(f"{title}: {message}", file=sys.stderr)


def notify(title, message):
    """Best-effort completion feedback without adding a platform SDK dependency."""
    if IS_MACOS:
        safe_title = str(title).replace("\\", "\\\\").replace('"', '\\"')
        safe_message = str(message).replace("\\", "\\\\").replace('"', '\\"')
        try:
            subprocess.run(
                ["osascript", "-e", f'display notification "{safe_message}" with title "{safe_title}" sound name "Glass"'],
                capture_output=True, timeout=5,
            )
        except OSError:
            pass
    elif IS_WINDOWS:
        # A real Windows toast needs a registered app identity/shortcut. The
        # native window already surfaces the finished state, so play the stock
        # completion sound rather than adding an unreliable toast dependency.
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except Exception:
            pass


def fallback_file_dialog(kind, prompt, multiple=False):
    """Use a Windows Tk dialog only when no native PyWebView shell is active."""
    if not IS_WINDOWS:
        return None
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        if kind == "folder":
            selected = filedialog.askdirectory(parent=root, title=prompt)
            result = [selected] if selected else []
        elif multiple:
            result = list(filedialog.askopenfilenames(
                parent=root, title=prompt,
                filetypes=[("Video files", "*.mp4 *.mov *.mkv *.avi *.m4v *.webm *.mpg *.mpeg *.ts")],
            ))
        else:
            selected = filedialog.askopenfilename(
                parent=root, title=prompt,
                filetypes=[("Video files", "*.mp4 *.mov *.mkv *.avi *.m4v *.webm *.mpg *.mpeg *.ts")],
            )
            result = [selected] if selected else []
        root.destroy()
        return result
    except Exception:
        return []


def reveal_files(paths):
    """Reveal existing output files in the platform file manager."""
    paths = [os.path.abspath(path) for path in paths if path]
    if not paths:
        return False
    try:
        if IS_WINDOWS:
            # Explorer has no reliable multi-select command line, so reveal
            # each requested result.  This is intentionally non-blocking.
            for path in paths:
                subprocess.Popen(["explorer", "/select,", path])
        elif IS_MACOS:
            subprocess.Popen(["open", "-R", *paths])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(paths[0])])
        return True
    except OSError:
        return False


def open_url(url):
    try:
        return bool(webbrowser.open(url))
    except Exception:
        return False


class KeepAwake:
    """Scoped, reversible sleep prevention for a user-started batch."""

    def __init__(self):
        self._process = None
        self._windows_active = False

    def start(self):
        if IS_WINDOWS:
            try:
                import ctypes
                continuous = 0x80000000
                system_required = 0x00000001
                self._windows_active = bool(
                    ctypes.windll.kernel32.SetThreadExecutionState(continuous | system_required)
                )
            except Exception:
                pass
        elif IS_MACOS:
            try:
                self._process = subprocess.Popen(["caffeinate", "-i"])
            except OSError:
                pass

    def stop(self):
        if self._process:
            try:
                self._process.terminate()
            except OSError:
                pass
            self._process = None
        if self._windows_active:
            try:
                import ctypes
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
            except Exception:
                pass
            self._windows_active = False
