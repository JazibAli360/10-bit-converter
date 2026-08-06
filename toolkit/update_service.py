"""GitHub Release metadata for the desktop app's manual update flow.

The app never downloads, installs, or executes updates. This module only reads
the latest public GitHub Release, so the UI can let a person choose whether to
open the download page.
"""

import json
from urllib.error import URLError
from urllib.request import Request, urlopen


REPOSITORY = "JazibAli360/10-bit-converter"
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"
ISSUES_URL = f"https://github.com/{REPOSITORY}/issues/new/choose"
LATEST_RELEASE_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"


def version_key(value):
    """Return a comparable key for ordinary numeric release tags."""
    text = str(value or "").strip().lstrip("vV")
    parts = text.split(".")
    if not parts or len(parts) > 4 or any(not part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in (parts + ["0"] * (4 - len(parts))))


def check_for_update(current_version, opener=urlopen):
    """Read the latest stable GitHub Release with a short, bounded request."""
    current = str(current_version or "0.0.0")
    request = Request(
        LATEST_RELEASE_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"10-bit-Converter/{current}",
        },
    )
    try:
        with opener(request, timeout=6) as response:
            raw = response.read(128 * 1024 + 1)
        if len(raw) > 128 * 1024:
            raise ValueError("release response was unexpectedly large")
        release = json.loads(raw.decode("utf-8"))
        latest = str(release.get("tag_name") or "").strip()
        latest_key, current_key = version_key(latest), version_key(current)
        if not latest_key:
            raise ValueError("latest release has no valid version tag")
        return {
            "ok": True,
            "current_version": current,
            "latest_version": latest.lstrip("vV"),
            "update_available": bool(current_key and latest_key > current_key),
        }
    except (OSError, URLError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        # Avoid showing connection internals or remote response details.
        return {
            "ok": False,
            "current_version": current,
            "message": "Couldn’t check GitHub right now. You can still open the Releases page.",
        }
