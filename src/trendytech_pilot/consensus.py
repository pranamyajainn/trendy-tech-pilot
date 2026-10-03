"""Cross-system transcript agreement. Agreement locates likely errors; it does not measure accuracy."""

import re
from collections import Counter
from difflib import SequenceMatcher

from .schema import normalise


def comparable_words(text):
    return normalise(text).split()


def compact(text):
    """Spacing and number punctuation are formatting, not content: 'Data bricks' = 'databricks', '7.30' = '7 30'."""
    return re.sub(r"[\s.:]", "", normalise(text))


def _align(words, other):
    """For each base word, whether the other system has it; for each other word, the base position it belongs to."""
    matched = [False] * len(words)
    owner = [0] * len(other)
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, words, other, autojunk=False).get_opcodes():
        if tag == "equal":
            matched[i1:i2] = [True] * (i2 - i1)
        for offset, j in enumerate(range(j1, j2)):
            # Spread replaced/inserted words across the base span they replace; inserts attach to the next word.
            owner[j] = i1 + (offset * (i2 - i1)) // max(j2 - j1, 1) if i2 > i1 else min(i1, max(len(words) - 1, 0))
    return matched, owner


def _spans(base_segments):
    spans, words = [], []
    for segment in base_segments:
        segment_words = comparable_words(segment["text"])
        spans.append((segment["id"], len(words), len(words) + len(segment_words)))
        words += segment_words
    return spans, words


def align_to_segments(base_segments, other_text):
    """Align another system's full transcript to the base segments by word sequence, not timestamps, because
    systems segment and time speech differently. Returns, per base segment, the other system's words for the
    same stretch of speech, the share of words both agree on, and a formatting-tolerant character similarity."""
    spans, words = _spans(base_segments)
    other = comparable_words(other_text)
    matched, owner = _align(words, other)
    texts = {s["id"]: s["text"] for s in base_segments}
    rows = []
    for segment_id, start, end in spans:
        other_words = [w for w, i in zip(other, owner) if start <= i < end]
        hits = sum(matched[start:end])
        a, b = compact(texts[segment_id]), compact(" ".join(other_words))
        rows.append({"id": segment_id, "other_text": " ".join(other_words), "matched_words": hits,
                     "agreement": hits / max(end - start, len(other_words)) if end > start or other_words else 1.0,
                     "char_agreement": SequenceMatcher(None, a, b, autojunk=False).ratio() if a or b else 1.0})
    return rows


def segment_speakers(base_segments, turns):
    """Majority diarization label of the other system's words aligned to each base segment (None if no words)."""
    spans, words = _spans(base_segments)
    labelled = [(w, turn["speaker"]) for turn in turns for w in comparable_words(turn["text"])]
    _, owner = _align(words, [w for w, _ in labelled])
    votes = {segment_id: Counter() for segment_id, _, _ in spans}
    for (_, speaker), i in zip(labelled, owner):
        for segment_id, start, end in spans:
            if start <= i < end:
                votes[segment_id][speaker] += 1
                break
    return {segment_id: (c.most_common(1)[0][0] if c else None) for segment_id, c in votes.items()}


def disputed_spans(base_segments, rows, threshold=0.9):
    """Group consecutive segments whose formatting-tolerant similarity falls below the threshold."""
    by_id = {s["id"]: s for s in base_segments}
    spans, last_position = [], None
    for position, row in enumerate(rows):
        if row["char_agreement"] >= threshold:
            continue
        if last_position is None or position != last_position + 1:
            spans.append({"segment_ids": [], "base_text": [], "other_text": []})
        span = spans[-1]
        span["segment_ids"].append(row["id"])
        span["base_text"].append(by_id[row["id"]]["text"])
        span["other_text"].append(row["other_text"])
        last_position = position
    for index, span in enumerate(spans):
        first, last = by_id[span["segment_ids"][0]], by_id[span["segment_ids"][-1]]
        span.update(span_id=index, start=first["start"], end=last["end"],
                    base_text=" ".join(span["base_text"]), other_text=" ".join(t for t in span["other_text"] if t))
    return spans


def transcript_agreement(base_segments, other_text):
    """Share of words two systems agree on across a whole call: max(len) in the denominator counts both
    omissions and insertions. 1.0 means identical comparable words."""
    base = [w for s in base_segments for w in comparable_words(s["text"])]
    other = comparable_words(other_text)
    if not base and not other:
        return 1.0
    hits = sum(block.size for block in SequenceMatcher(None, base, other, autojunk=False).get_matching_blocks())
    return hits / max(len(base), len(other))
