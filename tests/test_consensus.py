from trendytech_pilot.consensus import (
    align_to_segments,
    disputed_spans,
    segment_speakers,
    transcript_agreement,
)

BASE = [{"id": 0, "text": "Hello, I am calling about the course."},
        {"id": 1, "text": "I think 38 May should be suitable."},
        {"id": 3, "text": "Please send the brochure."}]


def by_id(rows):
    return {row["id"]: row for row in rows}


def test_identical_text_agrees_everywhere_despite_different_segmentation():
    other = "hello I am calling about the course I think 38 may should be suitable please send the brochure"
    rows = by_id(align_to_segments(BASE, other))
    assert all(row["agreement"] == 1.0 for row in rows.values())
    assert rows[1]["other_text"] == "i think 38 may should be suitable"
    assert transcript_agreement(BASE, other) == 1.0


def test_a_misheard_number_lowers_only_its_own_segment():
    other = "Hello, I am calling about the course. I think 30th May should be suitable. Please send the brochure."
    rows = by_id(align_to_segments(BASE, other))
    assert rows[0]["agreement"] == rows[3]["agreement"] == 1.0
    assert rows[1]["agreement"] == 6 / 7 and "30th" in rows[1]["other_text"]


def test_speech_missing_from_the_other_system_scores_zero():
    other = "Hello, I am calling about the course. Please send the brochure."
    rows = by_id(align_to_segments(BASE, other))
    assert (rows[1]["other_text"], rows[1]["agreement"], rows[1]["char_agreement"]) == ("", 0.0, 0.0)
    assert transcript_agreement(BASE, other) == 11 / 18


def test_formatting_differences_are_tolerated_but_content_differences_are_not():
    segments = [{"id": 0, "text": "Data bricks at 7.30"}, {"id": 1, "text": "two clubs"}]
    rows = by_id(align_to_segments(segments, "databricks at 7 30 two clouds"))
    assert rows[0]["agreement"] < 1 and rows[0]["char_agreement"] == 1.0
    assert rows[1]["char_agreement"] < 0.9


def test_disputed_segments_group_into_consecutive_spans():
    segments = [{"id": i, "text": t, "start": i * 2.0, "end": i * 2.0 + 2}
                for i, t in enumerate(["hello there", "two clubs", "only on azure", "fine", "bye now"])]
    rows = align_to_segments(segments, "hello there two clouds only for azure fine bye")
    spans = disputed_spans(segments, rows)
    assert [s["segment_ids"] for s in spans] == [[1, 2], [4]]
    assert (spans[0]["start"], spans[0]["end"]) == (2.0, 6.0)
    assert spans[0]["base_text"] == "two clubs only on azure" and spans[0]["other_text"] == "two clouds only for azure"


def test_speaker_labels_follow_the_aligned_words():
    turns = [{"speaker": "spk:0", "text": "Hello there, two clouds."}, {"speaker": "spk:1", "text": "Fine, bye now."}]
    segments = [{"id": 0, "text": "hello there"}, {"id": 1, "text": "two clubs"}, {"id": 2, "text": "fine bye now"}]
    assert segment_speakers(segments, turns) == {0: "spk:0", 1: "spk:0", 2: "spk:1"}


def test_extra_speech_in_the_other_system_counts_against_agreement():
    other = "Hello, I am calling about the course. I think 38 May should be suitable. Please please send the brochure now."
    rows = by_id(align_to_segments(BASE, other))
    assert rows[3]["agreement"] == 4 / 6 and rows[3]["other_text"] == "please please send the brochure now"
