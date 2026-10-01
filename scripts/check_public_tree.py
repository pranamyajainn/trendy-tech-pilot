"""Reject customer data and credentials in the public Git index."""

import re
import subprocess
from pathlib import Path

paths = subprocess.check_output(["git", "ls-files", "-z"]).decode().split("\0")
bad = []
for item in filter(None, paths):
    path = Path(item)
    if path.parts[0] in {"data", "outputs", "models", ".venv"} or path.suffix.lower() in {
        ".wav", ".ogg", ".mp3", ".flac", ".xlsx", ".docx", ".pdf",
    } or (path.name.startswith(".env") and path.name != ".env.example"):
        bad.append(item)
        continue
    content = path.read_text(errors="ignore") if path.exists() else ""
    if re.search(r"https://recordings[.]mcube[.]com/\S+", content):
        bad.append(item)
    if re.search(r"\b(?:sk-proj-|gsk_)[A-Za-z0-9_-]{20,}", content):
        bad.append(item)
if bad:
    raise SystemExit("Files must not be published: " + ", ".join(sorted(set(bad))))
print("Tracked files contain no prohibited data files, recording URLs or recognised API keys.")
