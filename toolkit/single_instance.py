"""Small, dependency-free single-instance guard for the native desktop app."""

import errno
import os


class InstanceGuard:
    def __init__(self, path):
        self.path = path
        self.owned = False

    @staticmethod
    def _pid_is_running(pid):
        try:
            os.kill(pid, 0)
        except OSError as exc:
            return exc.errno == errno.EPERM
        return True

    def acquire(self):
        """Claim the lock, discarding a stale lock left by a crashed app."""
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(str(os.getpid()))
                self.owned = True
                return True
            except FileExistsError:
                try:
                    with open(self.path, encoding="utf-8") as handle:
                        existing = int(handle.read().strip())
                except (OSError, ValueError):
                    existing = 0
                if existing and self._pid_is_running(existing):
                    return False
                try:
                    os.remove(self.path)
                except OSError:
                    return False
        return False

    def release(self):
        if not self.owned:
            return
        try:
            os.remove(self.path)
        except OSError:
            pass
        self.owned = False


def activate_existing(bundle_identifier):
    """Best-effort foregrounding of an existing macOS or Windows app."""
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            # Find a visible top-level window owned by another process with the
            # converter's title. This avoids a hard dependency on a
            # Windows-only IPC library. Foreground rules can still reject this
            # request; that is harmless because the instance guard prevents a
            # duplicate conversion session either way.
            current_pid = os.getpid()
            found = []

            @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            def visit(hwnd, _):
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value != current_pid and user32.IsWindowVisible(hwnd):
                    length = user32.GetWindowTextLengthW(hwnd)
                    title = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, title, len(title))
                    if "10-bit Converter" in title.value:
                        found.append(hwnd)
                        return False
                return True

            user32.EnumWindows(visit, 0)
            if found:
                user32.ShowWindow(found[0], 9)  # SW_RESTORE
                user32.SetForegroundWindow(found[0])
                return True
        except Exception:
            pass
    try:
        from AppKit import NSApplicationActivateIgnoringOtherApps, NSRunningApplication
        current = NSRunningApplication.currentApplication().processIdentifier()
        for app in NSRunningApplication.runningApplicationsWithBundleIdentifier_(bundle_identifier):
            if app.processIdentifier() != current:
                app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
                return True
    except Exception:
        pass
    return False
