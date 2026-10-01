"""Build the private pilot readout from the same JSON used by the workbook."""

import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor


def build(root):
    def read(name):
        return json.loads((root / "exports" / (name + ".json")).read_text())

    overview, calls, worklist = read("overview"), read("calls"), read("worklist")
    analysed = [c for c in calls if c.get("conversation_type")]
    types = Counter(c["conversation_type"] for c in analysed)
    doc = Document()
    section = doc.sections[0]
    section.top_margin = section.bottom_margin = Inches(.65)
    section.left_margin = section.right_margin = Inches(.75)
    for name in ["Normal", "Title", "Heading 1", "Heading 2"]:
        style = doc.styles[name]
        style.font.name = "Arial"
        style.font.color.rgb = RGBColor(0, 0, 0)
    doc.styles["Normal"].font.size = Pt(10)
    doc.styles["Normal"].paragraph_format.space_after = Pt(6)
    doc.styles["Title"].font.size = Pt(24)
    doc.styles["Heading 1"].font.size = Pt(15)
    doc.styles["Heading 2"].font.size = Pt(11)
    doc.core_properties.title = "TrendyTech call intelligence pilot review"
    doc.core_properties.author = "TrendyTech Pilot Team"

    def para(text, style=None):
        return doc.add_paragraph(text, style)

    def table(headers, rows):
        t = doc.add_table(rows=1, cols=len(headers))
        t.style = "Table Grid"
        for cell, title in zip(t.rows[0].cells, headers):
            cell.text = title
            props = cell._tc.get_or_add_tcPr()
            shade = OxmlElement("w:shd")
            shade.set(qn("w:fill"), "243B53")
            props.append(shade)
            for run in cell.paragraphs[0].runs:
                run.bold = True
                run.font.color.rgb = RGBColor(255, 255, 255)
        borders = OxmlElement("w:tblBorders")
        for edge in ["top", "left", "bottom", "right", "insideH", "insideV"]:
            element = OxmlElement("w:" + edge)
            for key, value in [("val", "single"), ("sz", "4"), ("color", "D9D9D9")]:
                element.set(qn("w:" + key), value)
            borders.append(element)
        t._tbl.tblPr.append(borders)
        for i, values in enumerate(rows):
            row = t.add_row()
            for cell, text in zip(row.cells, values):
                cell.text = str(text)
                if i % 2:
                    shade = OxmlElement("w:shd")
                    shade.set(qn("w:fill"), "F2F5F8")
                    cell._tc.get_or_add_tcPr().append(shade)
        para("")
        return t

    para("TrendyTech call intelligence pilot review", "Title")
    para(datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%d %B %Y") + "  |  Private review copy")
    para(f"We selected {overview['selected_calls']} recordings across {overview['selected_leads']} leads to test whether call history can produce useful sales guidance. "
         f"This version contains {overview['analysed_calls']} extractions that passed automated evidence checks. "
         "Independent transcription and meaning checks remain pending. Treat the worklist as a review draft.")
    table(["Pilot measure", "Result"], [
        ["Calls with current extraction", f"{overview['analysed_calls']} / {overview['selected_calls']}"],
        ["Journeys with all available calls extracted", f"{overview['complete_extracted_journeys']} / {overview['selected_leads']}"],
        ["Leads reserved for independent QA", overview["holdout_leads"]],
        ["External API spend", f"INR {overview['costs']['external_api_spend_inr']:.2f}"],
        ["Verified conversion prediction", "Unavailable without verified outcomes"],
    ])
    para("What the calls contain", "Heading 1")
    if types:
        for kind, count in types.most_common():
            para(f"{kind.replace('_', ' ').capitalize()}: {count} of {len(analysed)} analysed calls.", "List Bullet")
    else:
        para("Call content findings are pending successful extraction. Source-file counts alone are not conversation analysis.")
    objections = overview["objection_mentions_by_call"]
    if objections:
        para("Most frequently extracted objections are " + ", ".join(f"{k.replace('_', ' ')} ({v} calls)" for k, v in sorted(objections.items(), key=lambda x: -x[1])[:5]) + ". Counts describe this selected sample and can overlap.")
    para("How to apply the findings", "Heading 1")
    para("Separate learner support from new sales follow-up. Confirm enrollment for records already flagged Yes in the CRM. For sales leads, review the evidence behind each concern and prepare the suggested next step before contacting them.")
    para("These recordings are historical. Confirm the lead's current status and any request to stop contact before using an old recommendation.")

    doc.add_page_break()
    para("Lead worklist examples", "Heading 1")
    complete = [w for w in worklist if w["calls_analysed"] == w["calls_in_export"]]
    rank = {"Do not contact": 0, "Hot signal": 1, "Warm signal": 2, "Cold signal": 3, "Service follow-up": 4}
    chosen = sorted(complete, key=lambda w: (rank.get(w["priority"], 5), w["lead_alias"]))[:5]
    if not chosen:
        para("No journey has completed extraction yet. The workbook tracks the pending calls; no partial journey is presented as a finished sales assessment.")
    for w in chosen:
        para(w["lead_alias"] + "  " + w["priority"], "Heading 2")
        para(f"{w['calls_in_export']} calls, {w['total_call_minutes_crm']} recorded minutes. Last call: {w['as_of_recorded_call'][:10]}. " + w["priority_reason"])
        if w["open_objections"]:
            para("Concerns recorded: " + w["open_objections"][:700])
        para("Suggested action: " + w["next_action_suggestion"])
    para("How to trace an answer", "Heading 1")
    para("Use the lead alias in Worklist, then find its call in Calls. Evidence contains the source quote, call identifier and recording timestamps. Profile changes and historical unresolved objections remain visible for review. An unresolved concern in one call is not automatically still unresolved today.")

    doc.add_page_break()
    para("Validation and processing cost", "Heading 1")
    cost = overview["costs"]
    table(["Measure", "Result"], [
        ["Unique transcribed audio minutes", cost["unique_transcribed_audio_minutes"]],
        ["Inference hours including retries", cost["inference_wall_hours_including_retries"]],
        ["External API INR per audio minute", cost["external_api_inr_per_audio_minute"]],
        ["Allocated processing INR per minute", cost["processing_inr_per_audio_minute"] if cost["processing_inr_per_audio_minute"] is not None else "Not measured"],
        ["Proposal processing ceiling", "INR 0.60 per audio minute, subject to satisfactory accuracy"],
        ["Independent accuracy", overview["accuracy_status"]],
    ])
    para("Local inference has no API fee. Electricity, hardware allocation, engineering time, human QA and setup are not free and are not fully measured here. A zero API bill does not establish that the proposal's full processing-cost gate has passed.")
    para("What needs review before acceptance", "Heading 1")
    for text in ["Listen to the held-out recordings independently and complete the full-call reference sheets. Include speech that the model missed and mark genuine silence explicitly.",
                 "Check extracted facts, omissions, speaker attribution, objections and suggested actions. Automatic quote matching only checks that words occur in the transcript.",
                 "Agree numerical accuracy criteria with the client. The proposal did not specify a numerical accuracy threshold."]:
        para(text, "List Bullet")
    para("Scope and data limits", "Heading 1")
    para("The sample contains all exported calls for each selected lead; lifetime journey completeness is unconfirmed. Selection favours multi-call journeys and is not representative of archive conversion. CRM blanks mean unknown, and Yes flags lack independently verified purchase dates. The approved script was not supplied, so script adherence is unavailable. Call-quality columns describe provisional evidence coverage rather than validated salesperson performance scores.")
    para("Source: client call-metrics export dated 30 September 2026. Pilot scope: proposal SAI-Q-2026-013. The accompanying workbook and private JSON/CSV files contain the supporting records and processing ledger.")
    footer = section.footer.paragraphs[0]
    footer.text = "TrendyTech pilot  |  Private review copy"
    footer.runs[0].font.size = Pt(8)
    output = root / "deliverables" / "TrendyTech Pilot Review.docx"
    output.parent.mkdir(parents=True, exist_ok=True)
    doc.save(output)
    print(output)


if __name__ == "__main__":
    build(Path(sys.argv[1] if len(sys.argv) > 1 else "data"))
