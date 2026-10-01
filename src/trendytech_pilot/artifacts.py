"""One freshness check shared by reporting, extraction and independent QA."""

from .audio import ASR_VERSION
from .extract import PROMPT_VERSION, SYSTEM, extraction_fingerprint
from .models import LOCAL_REVISIONS
from .storage import digest, read_json


def current_transcript(store, call_id):
    path = store.path("transcripts", call_id + ".json")
    meta_path = store.path("audio", call_id + ".json")
    if not path.exists() or not meta_path.exists():
        return None
    transcript, audio = read_json(path), read_json(meta_path)
    model = transcript.get("model")
    revision = LOCAL_REVISIONS.get(model)
    expected = digest([audio["sha256"], model, revision, ASR_VERSION])
    if (not revision or transcript.get("fingerprint") != expected
            or transcript.get("model_revision") != revision or transcript.get("version") != ASR_VERSION):
        return None
    frozen_path = store.path("method-freeze.json")
    if frozen_path.exists():
        frozen = read_json(frozen_path)
        if (model != frozen["asr_model"] or revision != frozen.get("asr_revision")
                or frozen.get("asr_version") != ASR_VERSION):
            return None
    return transcript


def current_extraction(store, call_id):
    path = store.path("extractions", call_id + ".json")
    transcript = current_transcript(store, call_id)
    if not transcript or not path.exists():
        return None
    artifact = read_json(path)
    revision = artifact.get("model_revision")
    if artifact["model"] in LOCAL_REVISIONS and revision != LOCAL_REVISIONS[artifact["model"]]:
        return None
    expected = extraction_fingerprint(transcript["fingerprint"], artifact["model"], revision)
    if artifact.get("fingerprint") != expected:
        return None
    frozen_path = store.path("method-freeze.json")
    if frozen_path.exists():
        frozen = read_json(frozen_path)
        if (artifact["model"] != frozen["llm_model"] or revision != frozen.get("llm_revision")
                or frozen.get("prompt_version") != PROMPT_VERSION or frozen.get("prompt_sha256") != digest(SYSTEM)):
            return None
    return artifact
