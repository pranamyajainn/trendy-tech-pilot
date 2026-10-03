import importlib.util
import json
from pathlib import Path

import pytest

from trendytech_pilot.storage import Store, read_json, write_json


def test_interrupted_staged_inference_reruns_and_preserves_previous_attempt(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[1] / "scripts" / "calibrate_staged.py"
    spec = importlib.util.spec_from_file_location("staged_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", [str(script), "Ctest"])
    store = Store("data")
    write_json(store.path("selection.json"), {"calls": [{"call_id": "Ctest", "split": "development"}]})
    transcript = {"fingerprint": "synthetic", "segments": [{"id": 0, "text": "Please unlock my existing lessons."}]}
    monkeypatch.setattr(module, "current_transcript", lambda *args: transcript)
    attempts = []

    class FakeExtractor:
        model_revision = "synthetic-revision"

        def __init__(self, model):
            pass

        def generate(self, prompt, user, **kwargs):
            attempts.append(prompt)
            if len(attempts) == 1:
                raise KeyboardInterrupt
            if prompt == module.PURPOSE:
                data = {"conversation_type": "learner_support", "purpose_evidence": 0,
                        "summary": "Learner requests access.", "next_action": "Check access.",
                        "uncertainties": [], "signals": []}
            else:
                data = {"facts": []}
            return json.dumps(data), {}

    monkeypatch.setattr(module, "LocalExtractor", FakeExtractor)
    with pytest.raises(KeyboardInterrupt):
        module.main()
    module.main()
    assert len(attempts) == 3  # Interrupted purpose, repeated purpose, profile.
    artifacts = list(store.path("experiments").glob("*.json"))
    archived = list(store.path("experiments", "interrupted").glob("*.json"))
    assert len(artifacts) == len(archived) == 1
    assert read_json(artifacts[0])["status"] == "reference_validated_only"
    assert read_json(archived[0])["status"] == "interrupted"
    assert [e["status"] for e in store.events()] == ["interrupted", "experimental", "experimental"]
