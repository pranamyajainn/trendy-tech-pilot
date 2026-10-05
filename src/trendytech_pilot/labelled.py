"""Method v3 transcript: Gemini's labelled turns, timed and cross-checked against the local Whisper transcript.

Whisper costs nothing and hears the call independently. It supplies the time of each turn (Gemini's own timestamps
drift) and a tripwire: a claim whose quote Whisper did not hear is held for a listen instead of reaching a client
sheet. On the 16-call development test the tripwire held 7 of 72 claims, including both claims the audio judge
found wrong, and every claim it passed was judged correct or nearly correct.
"""

from difflib import SequenceMatcher

from .schema import normalise

# Part of the v3 transcript identity: changing a value makes stored transcripts and extractions stale.
ALIGNMENT = {"version": "whisper-align-v1", "tripwire": "quote words heard by Whisper", "tripwire_min_share": 0.6}


def timed_segments(turns, whisper_segments):
    """One segment per turn, starting at the Whisper passage where its first matched word was heard."""
    owner, words = [], []
    for segment in whisper_segments:
        for word in normalise(segment["text"]).split():
            owner.append(segment)
            words.append(word)
    spans, turn_words = [], []
    for index, turn in enumerate(turns):
        mine = normalise(turn["text"]).split()
        spans.append((len(turn_words), len(turn_words) + len(mine)))
        turn_words += [(index, w) for w in mine]
    matcher = SequenceMatcher(None, [w for _, w in turn_words], words, autojunk=False)
    first_heard = {}
    for block in matcher.get_matching_blocks():
        for k in range(block.size):
            first_heard.setdefault(turn_words[block.a + k][0], owner[block.b + k]["start"])
    segments, last = [], 0.0
    for index, turn in enumerate(turns):
        start = first_heard.get(index)
        timed = start is not None and start >= last
        last = start if timed else last  # an unmatched or out-of-order turn takes the previous time
        segments.append({"id": index + 1, "role": turn["role"], "text": turn["text"], "start": round(last, 2),
                         "time_from_whisper": timed})
    for segment, following in zip(segments, segments[1:] + [None]):
        segment["end"] = following["start"] if following else None
    return segments


def heard_by_whisper(quote, whisper_words):
    words = normalise(quote).split()
    return not words or sum(w in whisper_words for w in words) / len(words) >= ALIGNMENT["tripwire_min_share"]
