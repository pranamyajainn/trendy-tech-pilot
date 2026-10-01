"""Local atomic artifacts and an append-only processing ledger."""

import csv
import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path


def digest(value):
    raw = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text())


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".writing-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(value, f, indent=2, ensure_ascii=False, default=str)
            f.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def safe_cell(value):
    """Prevent formula execution when private CSVs are opened in Excel."""
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def write_csv(path, rows, fields=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = fields or (list(rows[0]) if rows else [])
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows({k: safe_cell(v) for k, v in row.items()} for row in rows)


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, *parts):
        return self.root.joinpath(*parts)

    def event(self, **record):
        record = {"recorded_at": datetime.now(UTC).isoformat(), **record}
        with self.path("ledger.jsonl").open("a") as f:
            f.write(json.dumps(record, default=str) + "\n")

    def events(self):
        path = self.path("ledger.jsonl")
        return [json.loads(line) for line in path.read_text().splitlines() if line] if path.exists() else []
