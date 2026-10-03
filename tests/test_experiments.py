import importlib.util
import json
from pathlib import Path

import pytest

from trendytech_pilot.remote import GeminiExtractor
from trendytech_pilot.storage import Store, read_json, write_json


def load_script(name):
    script = Path(__file__).resolve().parents[1] / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py") + "_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return script, module


def test_interrupted_staged_inference_reruns_and_preserves_previous_attempt(tmp_path, monkeypatch):
    script, module = load_script("calibrate_staged.py")
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


def test_remote_trial_writes_experiments_never_production_extractions(tmp_path, monkeypatch):
    script, module = load_script("calibrate_remote.py")
    monkeypatch.chdir(tmp_path)
    store = Store("data")
    write_json(store.path("selection.json"), {"calls": [{"call_id": "Cdev", "split": "development"},
                                                        {"call_id": "Chold", "split": "holdout"}]})
    transcript = {"fingerprint": "synthetic", "segments": [{"id": 0, "text": "Please unlock my existing lessons."}]}
    monkeypatch.setattr(module, "current_transcript", lambda *args: transcript)
    extraction = {"conversation_type": "learner_support",
                  "purpose_evidence": {"segment_id": 0, "quote": "unlock my existing lessons"},
                  "summary": "Learner requests access.", "facts": [], "signals": [], "objections": [], "pitches": [],
                  "next_action": "Check access.", "uncertainties": []}
    replies, prompts = ["not json", json.dumps(extraction)], []

    class FakeGemini:
        model_id = GeminiExtractor.model_id

        def __init__(self, store):
            pass

        def generate(self, system, user):
            prompts.append(user)
            return replies.pop(0), {"input_tokens": 1, "output_tokens": 1}

    monkeypatch.setattr(module, "GeminiExtractor", FakeGemini)
    monkeypatch.setattr("sys.argv", [str(script), "Chold"])
    with pytest.raises(ValueError, match="development"):
        module.main()
    monkeypatch.setattr("sys.argv", [str(script), "Cdev"])
    module.main()
    [artifact] = store.path("experiments").glob("Cdev-gemini-*.json")
    result = read_json(artifact)
    assert result["status"] == "evidence_checked_only" and len(result["attempts"]) == 2
    assert "error" in result["attempts"][0] and "failed validation" in prompts[1]
    assert not store.path("extractions").exists()
    assert [e["status"] for e in store.events()] == ["experimental"]
