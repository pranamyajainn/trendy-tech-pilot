"""One freshness check shared by reporting, extraction and independent QA."""

from .audio import ASR_VERSION
from .extract import PROMPT_VERSION, SYSTEM, extraction_fingerprint, generation_config
from .labelled import ALIGNMENT, timed_segments
from .models import LOCAL_REVISIONS
from .remote_asr import GEMINI_ASR, LABELLED_ASR, STITCH
from .remote_sarvam import SARVAM_ASR
from .resolve import RESOLVER
from .schema import EVIDENCE_RULES_VERSION
from .storage import digest, read_json, write_json

# Method v2 (3 Oct 2026): extraction reads the three-system consensus of Whisper, Gemini Transcribe and Sarvam,
# with Gemini Pro resolving only stretches where all three disagree.
TRANSCRIPT_SOURCE = "consensus"
CONSENSUS_METHOD = {"second_asr": GEMINI_ASR, "third_asr": SARVAM_ASR, "resolver": RESOLVER}


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
    paths = [store.path("asr", name, call_id + ".json") for name in ("consensus", "gemini", "sarvam")]
    if not whisper or not all(p.exists() for p in paths):
        return None
    consensus, gemini, sarvam = (read_json(p) for p in paths)
    audio = read_json(store.path("audio", call_id + ".json"))
    if (gemini.get("fingerprint") != digest([audio["sha256"], GEMINI_ASR])
            or sarvam.get("fingerprint") != digest([audio["sha256"], SARVAM_ASR])
            or consensus.get("fingerprint") != digest([whisper["fingerprint"], gemini["fingerprint"],
                                                       sarvam["fingerprint"], RESOLVER])):
        return None
    frozen_path = store.path("method-freeze.json")
    if frozen_path.exists() and read_json(frozen_path).get("consensus") != CONSENSUS_METHOD:
        return None
    return {**consensus, "flags": whisper["flags"], "duration_seconds": whisper["duration_seconds"]}


def current_labelled(store, call_id):
    """Method v3: Gemini's labelled turns, timed from the current Whisper transcript (which also feeds the
    tripwire in ensemble.verified_extract)."""
    whisper = current_transcript(store, call_id)
    path = store.path("asr", "gemini-labelled", call_id + ".json")
    if not whisper or not path.exists():
        return None
    labelled, audio = read_json(path), read_json(store.path("audio", call_id + ".json"))
    if labelled.get("fingerprint") != digest([digest([audio["sha256"], LABELLED_ASR]), STITCH]):
        return None
    fingerprint = digest([labelled["fingerprint"], whisper["fingerprint"], ALIGNMENT])
    # Aligning a long call with Whisper takes a noticeable fraction of a second; the result is cached by fingerprint.
    cache = store.path("asr", "gemini-labelled-timed", call_id + ".json")
    cached = read_json(cache) if cache.exists() else None
    if cached and cached["fingerprint"] == fingerprint:
        segments = cached["segments"]
    else:
        segments = timed_segments(labelled["turns"], whisper["segments"])
        write_json(cache, {"fingerprint": fingerprint, "segments": segments})
    return {"call_id": call_id, "source": "gemini_labelled", "fingerprint": fingerprint, "segments": segments,
            "whisper_text": " ".join(s["text"] for s in whisper["segments"]),
            "flags": sorted(set(whisper["flags"]) | set(labelled["flags"])), "duration_seconds": whisper["duration_seconds"]}


def current_source(store, call_id):
    """The transcript that extraction, evidence timestamps and transcription QA use. Pilot calls keep their
    method v2 consensus; calls processed later (the customer cohort) have only the v3 labelled transcript."""
    if TRANSCRIPT_SOURCE == "consensus":
        return current_consensus(store, call_id) or current_labelled(store, call_id)
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
