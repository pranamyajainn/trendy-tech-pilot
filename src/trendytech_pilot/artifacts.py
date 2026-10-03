"""One freshness check shared by reporting, extraction and independent QA."""

from .audio import ASR_VERSION
from .extract import PROMPT_VERSION, SYSTEM, extraction_fingerprint, generation_config
from .models import LOCAL_REVISIONS
from .remote_asr import GEMINI_ASR
from .resolve import RESOLVER
from .schema import EVIDENCE_RULES_VERSION
from .storage import digest, read_json

# Method v2 (3 Oct 2026): extraction reads the consensus of Whisper and Gemini Transcribe, resolved by Gemini Pro.
TRANSCRIPT_SOURCE = "consensus"
CONSENSUS_METHOD = {"second_asr": GEMINI_ASR, "resolver": RESOLVER}


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


def current_consensus(store, call_id):
    """The consensus is current only if every link is: audio, Whisper, Gemini Transcribe and resolver settings."""
    whisper = current_transcript(store, call_id)
    path, gemini_path = store.path("asr", "consensus", call_id + ".json"), store.path("asr", "gemini", call_id + ".json")
    if not whisper or not path.exists() or not gemini_path.exists():
        return None
    consensus, gemini = read_json(path), read_json(gemini_path)
    audio = read_json(store.path("audio", call_id + ".json"))
    if (gemini.get("fingerprint") != digest([audio["sha256"], GEMINI_ASR])
            or consensus.get("fingerprint") != digest([whisper["fingerprint"], gemini["fingerprint"], RESOLVER])):
        return None
    frozen_path = store.path("method-freeze.json")
    if frozen_path.exists() and read_json(frozen_path).get("consensus") != CONSENSUS_METHOD:
        return None
    return {**consensus, "flags": whisper["flags"], "duration_seconds": whisper["duration_seconds"]}


def current_source(store, call_id):
    """The transcript that extraction, evidence timestamps and transcription QA use under the current method."""
    if TRANSCRIPT_SOURCE == "consensus":
        return current_consensus(store, call_id)
    return current_transcript(store, call_id)


def current_extraction(store, call_id):
    path = store.path("extractions", call_id + ".json")
    transcript = current_source(store, call_id)
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
                or frozen.get("prompt_version") != PROMPT_VERSION or frozen.get("prompt_sha256") != digest(SYSTEM)
                or frozen.get("generation") != generation_config(artifact["model"])
                or frozen.get("evidence_rules") != EVIDENCE_RULES_VERSION):
            return None
    return artifact
