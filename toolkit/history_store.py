"""Durable, bounded conversion-history primitives with no UI knowledge."""

from collections import deque
import json
import os


MAX_REPORTS = 200


def append_report(path, report, limit=MAX_REPORTS):
    """Persist the newest reports without letting a long-running log grow forever."""
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    keep = max(1, int(limit))
    lines = deque(maxlen=keep)
    try:
        with open(path, encoding="utf-8") as handle:
            lines.extend(line for line in handle if line.strip())
    except (OSError, ValueError):
        pass
    lines.append(json.dumps(report) + "\n")
    with open(path, "w", encoding="utf-8") as handle:
        handle.writelines(lines)


def read_reports(path, limit=40, authorize_path=None):
    try:
        with open(path, encoding="utf-8") as handle:
            lines = deque(handle, maxlen=max(1, limit))
    except (OSError, ValueError):
        return []
    records = []
    for line in reversed(lines):
        try:
            report = json.loads(line)
        except (TypeError, ValueError):
            continue
        for item in report.get("items", []):
            for key in ("source", "output", "log_path"):
                if item.get(key) and authorize_path:
                    authorize_path(item[key])
        records.append(report)
    return records


__all__ = ["append_report", "read_reports"]
