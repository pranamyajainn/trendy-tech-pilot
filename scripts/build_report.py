"""Build the private pilot readout from the same JSON used by the workbook."""

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

CALL_TYPES = {"sales": "Pre-sale conversations", "enrollment_or_payment": "Enrollment or payment",
              "learner_support": "Existing-learner support", "administrative": "Administrative",
              "brief_followup": "Brief follow-ups", "unusable": "Unusable (voicemail, no conversation)",
              "unclear": "Unclear purpose"}
SIGNALS = {"price_question": "Asked about price", "payment_intent": "Stated intent to pay or enroll",
           "payment_claim": "Said they had paid", "followup_agreed": "Agreed a follow-up",
           "demo_requested": "Asked for a demo or samples", "low_interest": "Low interest",
           "no_time": "Short of time", "not_a_fit": "Not a fit", "do_not_contact": "Asked not to be contacted",
           "goal": "Stated a career goal", "urgency": "Urgency", "other": "Other"}
COVERAGE = {"qualification": "Qualification (role, experience, interest or budget stated)",
            "discovery": "Discovery (goal, target role or timing stated)", "pitch": "Value proposition pitched",
            "objection_handling": "Agent response recorded (calls with objections)",
            "closing": "Next step agreed, demo requested or payment intent"}


def label(key):
    return key.replace("_", " ").capitalize()


def clock(seconds):
    return "?" if seconds is None else f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


def build(root):
    def read(name, default=None):
        path = root / "exports" / (name + ".json")
        return json.loads(path.read_text()) if path.exists() else default

    overview, worklist = read("overview"), read("worklist")
    findings = read("findings", {})
    key_path = root / "review" / "key-findings.json"
    key_findings = json.loads(key_path.read_text())["findings"] if key_path.exists() else []
    qa_path = root / "qa" / "status.json"
    qa = json.loads(qa_path.read_text()) if qa_path.exists() else {}
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
    for border in doc.styles.element.xpath(".//w:pBdr"):
        border.getparent().remove(border)
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

    def cite(example):
        return f"{example['lead_alias']}, call {example['call_id']} at {clock(example.get('start_seconds'))}"

    analysed, sales = findings.get("analysed_calls", overview["analysed_calls"]), findings.get("sales_facing_calls", 0)
    para("TrendyTech call intelligence pilot review", "Title")
    para(datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%d %B %Y") + "  |  Private review copy")
    para(f"We selected {overview['selected_calls']} recordings across {overview['selected_leads']} leads to test whether call history can produce useful sales guidance. "
         f"Calls with an extraction that passed automated evidence checks: {overview['analysed_calls']}. "
         "Every finding below links to the call and moment it came from. Independent accuracy review is described in the validation section.")
    table(["Pilot measure", "Result"], [
        ["Calls with current extraction", f"{overview['analysed_calls']} / {overview['selected_calls']}"],
        ["Journeys with all available calls extracted", f"{overview['complete_extracted_journeys']} / {overview['selected_leads']}"],
        ["Leads reserved for independent QA", overview["holdout_leads"]],
        ["External API spend", f"INR {overview['costs']['external_api_spend_inr']:.2f}"],
        ["Verified conversion prediction", "Unavailable without verified outcomes"],
    ])

    para("Key findings", "Heading 1")
    if key_findings:
        for item in key_findings:
            para(item["title"], "Heading 2")
            para(item["detail"])
            for example in item.get("evidence", [])[:3]:
                para(f"“{example['quote']}” ({cite(example)})", "List Bullet")
    else:
        para("Key findings are written after the extracted calls have been reviewed. The counts below are descriptive.")

    para("What the calls contain", "Heading 1")
    if analysed:
        for kind, count in findings.get("call_types", {}).items():
            para(f"{CALL_TYPES.get(kind, label(kind))}: {count} of {analysed} analysed calls.", "List Bullet")
        para("Learner-support and administrative calls are kept out of sales signals, so service work is not mistaken for buying interest.")
    else:
        para("Call content findings are pending successful extraction. Source-file counts alone are not conversation analysis.")

    if sales:
        para("Who the prospects are", "Heading 1")
        stated = findings.get("profile_fields_stated_in_sales_calls", {})
        para(f"Across {sales} pre-sale and enrollment conversations, prospects most often stated: "
             + ", ".join(f"{label(k).lower()} ({v} calls)" for k, v in list(stated.items())[:6]) + ".")
        roles = findings.get("common_current_roles", {})
        if roles:
            para("Most common current roles: " + ", ".join(f"{k} ({v})" for k, v in list(roles.items())[:6]) + ".")
        interests = findings.get("common_technology_interests", {})
        if interests:
            para("Most common technology interests: " + ", ".join(f"{k} ({v})" for k, v in list(interests.items())[:6]) + ".")

    objections = findings.get("objections", [])
    if objections:
        para("Objections and how they were handled", "Heading 1")
        para("An objection counts as resolved only when the prospect explicitly accepted the answer. An agent's reply alone is “partly addressed”.")
        table(["Concern", "Calls", "Resolved", "Partly addressed", "Unresolved", "Unclear"],
              [[label(o["category"]), o["calls"], o["resolved"], o["partly_addressed"], o["unresolved"], o["unclear"]]
               for o in objections])
        for o in objections[:4]:
            for example in o["examples"][:1]:
                para(f"{label(o['category'])}: {example['claim']} — “{example['quote']}” ({cite(example)})", "List Bullet")

    signals = findings.get("signals", [])
    if signals:
        para("Buying and follow-up signals", "Heading 1")
        table(["Signal", "Calls"], [[SIGNALS.get(s["kind"], label(s["kind"])), s["calls"]] for s in signals])
        para("A payment claim or a sent payment link is not verified payment. Stated intent is not a conversion.")

    coverage = findings.get("quality_coverage", [])
    if sales and coverage:
        para("Call-quality evidence coverage", "Heading 1")
        table(["Dimension", "Calls with evidence"],
              [[COVERAGE[c["dimension"]], f"{c['calls_with_evidence']} / {c['applicable_calls']}"] for c in coverage])
        para("These show whether evidence of each step appears in a call. They are not salesperson performance scores. "
             "Script adherence is unavailable because the approved script was not supplied.")

    complete = [w for w in worklist if w["calls_analysed"] == w["calls_in_export"]]
    rank = {"Do not contact": 0, "Hot signal": 1, "Warm signal": 2, "Cold signal": 3, "Service follow-up": 4}
    chosen = sorted(complete, key=lambda w: (rank.get(w["priority"], 5), w["lead_alias"]))[:5]
    doc.add_page_break()
    para("Lead worklist", "Heading 1")
    priorities = findings.get("worklist_priorities", {})
    if priorities:
        table(["Worklist label", "Leads"], [[k, v] for k, v in priorities.items()])
        para("Labels follow transparent rules based on the last available call. They are not conversion probabilities. "
             "Recordings are historical: confirm each lead's current status and any request to stop contact before acting.")
    if not chosen:
        para("No journey has completed extraction yet. No partial journey is presented as a finished sales assessment.")
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
    qa_rows = []
    if qa:
        qa_rows = [["Held-out calls with signed transcription review", f"{qa.get('reviewed_calls', 0)} / {qa.get('expected_holdout_calls', 0)}"],
                   ["Word error rate on reviewed calls", "Not measured" if qa.get("wer") is None else f"{qa['wer']:.1%}"],
                   ["Reviewed extraction fields", qa.get("reviewed_fields", 0)],
                   ["Field accuracy on reviewed fields", "Not measured" if qa.get("field_accuracy") is None else f"{qa['field_accuracy']:.1%}"]]
    table(["Measure", "Result"], [
        ["Unique transcribed audio minutes", cost["unique_transcribed_audio_minutes"]],
        ["Inference hours including retries", cost["inference_wall_hours_including_retries"]],
        ["External API INR per audio minute", cost["external_api_inr_per_audio_minute"]],
        ["Allocated processing INR per minute", cost["processing_inr_per_audio_minute"] if cost["processing_inr_per_audio_minute"] is not None else "Not measured"],
        ["Proposal processing ceiling", "INR 0.60 per audio minute, subject to satisfactory accuracy"],
        ["Independent accuracy", overview["accuracy_status"]],
        *qa_rows,
    ])
    para("Electricity, hardware allocation, engineering time, human QA and setup are not free and are not fully measured here. The API figure alone does not establish that the proposal's full processing-cost gate has passed.")
    para("What needs review before acceptance", "Heading 1")
    for text in ["Listen to the held-out recordings independently and complete the full-call reference sheets. Include speech that the model missed and mark genuine silence explicitly.",
                 "Check extracted facts, omissions, speaker attribution, objections and suggested actions. Automatic quote matching only checks that words occur in the transcript.",
                 "Agree numerical accuracy criteria with the client. The proposal did not specify a numerical accuracy threshold."]:
        para(text, "List Bullet")
    para("Scope and data limits", "Heading 1")
    para("The sample contains all exported calls for each selected lead; lifetime journey completeness is unconfirmed. Selection favours multi-call journeys and is not representative of archive conversion. CRM blanks mean unknown, and Yes flags lack independently verified purchase dates. The approved script was not supplied, so script adherence is unavailable. Call-quality columns describe provisional evidence coverage rather than validated salesperson performance scores. Speakers are inferred from conversation; the mono recordings are not diarized.")
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
