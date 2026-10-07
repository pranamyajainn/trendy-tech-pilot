"""Client text guard, dates and the validation status that gates the REVIEW DRAFT label (synthetic data)."""
import pytest

from trendytech_pilot import client
from trendytech_pilot.quality import validation_status, wilson_interval
from trendytech_pilot.storage import Store, write_csv


def test_validation_needs_the_full_audit_sample_signed(tmp_path):
    store = Store(tmp_path)
    assert not validation_status(store)["complete"]
    write_csv(store.path("qa", "audit-claims-sample.csv"),
              [{"correct": "yes" if i < 92 else "no", "reviewer": "R", "reviewed_at": "2026-10-04"} for i in range(100)])
    status = validation_status(store)
    assert status["complete"] and status["audit_precision"] == 0.92
    assert status["audit_precision_95ci"] == pytest.approx(wilson_interval(92, 100))
    write_csv(store.path("qa", "audit-claims-sample.csv"), [{"correct": "", "reviewer": "", "reviewed_at": ""}])
    assert not validation_status(store)["complete"]

def test_wilson_interval_handles_perfect_scores_without_claiming_certainty():
    low, high = wilson_interval(50, 50)
    assert high == 1.0 and low < 0.95
    assert wilson_interval(0, 0) is None


def test_client_text_guard_and_dates():
    assert client.INTERNAL_TEXT.search("Checked by Gemini") and client.INTERNAL_TEXT.search("see C0123456789abcdef")
    assert client.INTERNAL_TEXT.search("price_question") and not client.INTERNAL_TEXT.search("The lead asked the fee.")
    assert client.human_date("2026-05-01T10:00:00") == "1 May 2026"
