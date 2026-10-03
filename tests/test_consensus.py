from trendytech_pilot.consensus import align_to_segments, transcript_agreement

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
    assert rows[1] == {"id": 1, "other_text": "", "matched_words": 0, "agreement": 0.0}
    assert transcript_agreement(BASE, other) == 11 / 18


def test_extra_speech_in_the_other_system_counts_against_agreement():
    other = "Hello, I am calling about the course. I think 38 May should be suitable. Please please send the brochure now."
    rows = by_id(align_to_segments(BASE, other))
    assert rows[3]["agreement"] == 4 / 6 and rows[3]["other_text"] == "please please send the brochure now"
