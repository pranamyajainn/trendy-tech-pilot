"""Keep interrupted development attempts while allowing the same experiment to resume."""

from uuid import uuid4

from .storage import read_json


def completed_experiment_exists(path):
    if not path.exists():
        return False
    previous = read_json(path)
    if previous.get("status") != "interrupted":
        return True
    archive = path.parent / "interrupted" / f"{path.stem}-{uuid4().hex}.json"
    archive.parent.mkdir(exist_ok=True)
    path.replace(archive)
    return False
