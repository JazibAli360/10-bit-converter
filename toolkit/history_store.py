"""Durable conversion-history primitives with no UI knowledge."""

import json
import os


def append_report(path, report):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(report) + "\n")


def read_reports(path, limit=40, authorize_path=None):
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.readlines()[-max(1, limit):]
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
