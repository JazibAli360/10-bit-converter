"""Pure export-path and destination checks, kept separate from the HTTP UI."""

import os
import uuid


def staging_output_path(final_path):
    """Same-folder temporary output so a successful encode is the only thing
    allowed to replace an existing export."""
    folder, filename = os.path.split(final_path)
    stem, ext = os.path.splitext(filename)
    return os.path.join(folder, f".{stem}.tenbit-partial-{uuid.uuid4().hex}{ext}")


def unique_output_path(path, reserved=()):
    """Return an unused sibling path, appending -2, -3, … when needed."""
    reserved = set(reserved)
    stem, ext = os.path.splitext(path)
    candidate, n = path, 2
    while candidate in reserved or os.path.exists(candidate):
        candidate = f"{stem}-{n}{ext}"
        n += 1
    return candidate


def resolve_output_path(path, on_exists, reserved=()):
    return unique_output_path(path, reserved) if on_exists == "rename" else path


def writable_parent(folder):
    """Return the nearest existing parent and whether it is writable."""
    probe = os.path.abspath(folder or os.path.expanduser("~"))
    while not os.path.exists(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            return probe, False
        probe = parent
    return probe, os.path.isdir(probe) and os.access(probe, os.W_OK | os.X_OK)
