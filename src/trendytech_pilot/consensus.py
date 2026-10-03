"""Cross-system transcript agreement. Agreement locates likely errors; it does not measure accuracy."""

from difflib import SequenceMatcher

from .schema import normalise


def comparable_words(text):
    return normalise(text).split()


def align_to_segments(base_segments, other_text):
    """Align another system's full transcript to the base segments by word sequence, not timestamps, because
    systems segment and time speech differently. Returns, per base segment, the other system's words for the
    same stretch of speech and the share of words both systems agree on."""
    spans, words = [], []
    for segment in base_segments:
        segment_words = comparable_words(segment["text"])
        spans.append((segment["id"], len(words), len(words) + len(segment_words)))
        words += segment_words
    other = comparable_words(other_text)
    matched = [False] * len(words)
    # owner[j] is the base word position each other-system word is attached to.
    owner = [0] * len(other)
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, words, other, autojunk=False).get_opcodes():
        if tag == "equal":
            matched[i1:i2] = [True] * (i2 - i1)
        for offset, j in enumerate(range(j1, j2)):
            # Spread replaced/inserted words across the base span they replace; inserts attach to the next word.
            owner[j] = i1 + (offset * (i2 - i1)) // max(j2 - j1, 1) if i2 > i1 else min(i1, max(len(words) - 1, 0))
    rows = []
    for segment_id, start, end in spans:
        other_words = [w for w, i in zip(other, owner) if start <= i < end]
        hits = sum(matched[start:end])
        rows.append({"id": segment_id, "other_text": " ".join(other_words), "matched_words": hits,
                     "agreement": hits / max(end - start, len(other_words)) if end > start or other_words else 1.0})
    return rows


def transcript_agreement(base_segments, other_text):
    """Share of words two systems agree on across a whole call: max(len) in the denominator counts both
    omissions and insertions. 1.0 means identical comparable words."""
    base = [w for s in base_segments for w in comparable_words(s["text"])]
    other = comparable_words(other_text)
    if not base and not other:
        return 1.0
    hits = sum(block.size for block in SequenceMatcher(None, base, other, autojunk=False).get_matching_blocks())
    return hits / max(len(base), len(other))
