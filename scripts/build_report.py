"""Build the client report from the same JSON as the client workbook. No costs or processing details."""

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
        return json.loads((root / "exports" / "client" / (name + ".json")).read_text())

    meta, insights, actions = read("meta"), read("sales_insights"), read("lead_actions")
    validation = meta.get("validation", {})
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
    doc.core_properties.title = "TrendyTech sales call review"
    doc.core_properties.author = "TrendyTech Pilot Team"

    def para(text, style=None, bold_prefix=None):
        p = doc.add_paragraph(style=style)
        if bold_prefix:
            p.add_run(bold_prefix).bold = True
        p.add_run(text)
        return p

    def table(headers, rows):
        t = doc.add_table(rows=1, cols=len(headers))
        t.style = "Table Grid"
        for cell, title in zip(t.rows[0].cells, headers):
            cell.text = title
            shade = OxmlElement("w:shd")
            shade.set(qn("w:fill"), "243B53")
            cell._tc.get_or_add_tcPr().append(shade)
            for run in cell.paragraphs[0].runs:
                run.bold = True
                run.font.color.rgb = RGBColor(255, 255, 255)
        for values in rows:
            for cell, text in zip(t.add_row().cells, values):
                cell.text = str(text)
        for index, row in enumerate(t.rows):
            props = row._tr.get_or_add_trPr()
            props.append(OxmlElement("w:cantSplit"))
            if index == 0:
                props.append(OxmlElement("w:tblHeader"))
        para("")

    para("TrendyTech sales call review", "Title")
    para(datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%d %B %Y")
         + ("  |  Review draft" if meta.get("review_draft") else ""))
    para(meta["scope_note"])
    para(f"We reviewed 300 recorded calls across 50 leads from the September 2026 call export to answer two "
         f"questions: what should change in the sales approach, and what should happen next with each lead. "
         f"This report gives {len(insights)} findings and a recommended next action for each of the "
         f"{len(actions)} leads. Every finding and action points to the call and moment it came from.")

    para("What should change in the sales approach", "Heading 1")
    for index, insight in enumerate(insights, 1):
        para(f"{index}. {insight['finding']}", "Heading 2")
        para(insight["evidence_and_scale"], bold_prefix="Evidence: ")
        para(insight["sales_implication"], bold_prefix="Why it matters: ")
        para(insight["recommended_change"], bold_prefix="Recommended change: ")
        para(insight["suggested_wording"], bold_prefix="Proposed wording: ")
        para(insight["how_to_assess"], bold_prefix="How to check it worked: ")
        para(insight["source"], bold_prefix="Source: ")

    para("What to do next with the leads", "Heading 1")
    table(["Recommended action", "Leads"], Counter(a["action_category"] for a in actions).most_common())
    para("The Lead Actions sheet lists every lead with a specific next step, an opening question, and the "
         "recorded moment that supports it. Recommendations reflect the calls as recorded; confirm each lead's "
         "current status before contacting them.")
    for action in [a for a in actions if a["action_category"] in
                   ("Resolve a purchase condition", "Answer a specific concern")][:4]:
        para(f"Lead {action['lead_identifier']}: {action['action_category']}", "Heading 2")
        para(action["latest_position"], bold_prefix="Position: ")
        para(action["recommended_next_action"], bold_prefix="Next action: ")
        para(action["suggested_wording"], bold_prefix="Opening question: ")

    para("How to trace any statement", "Heading 1")
    para("Each finding and action gives a lead number, call date and time into the recording. Open that call in "
         "the CRM recording and go to the time shown to hear the original words.")
    para("Limits of this review", "Heading 1")
    for text in [("The calls are a selected sample of leads with several recorded calls, so counts describe this "
                  "sample, not every TrendyTech lead."),
                 "Recordings are historical. A plan or date mentioned in a call may have changed since.",
                 "CRM conversion flags were not independently verified, and this review does not predict who will buy.",
                 "The approved sales script was not available, so script adherence was not assessed."]:
        para(text, "List Bullet")
    if validation.get("complete"):
        low, high = validation["audit_precision_95ci"]
        para(f"Reviewers checked all {validation['client_claims']} statements behind these findings and actions "
             f"against the recordings, and audited a random sample of {validation['audit_reviewed']} extracted "
             f"statements: {validation['audit_precision']:.0%} were correct (95% range {low:.0%} to {high:.0%}).")
    else:
        para("Human checking of these statements against the recordings is in progress; treat this as a review draft.")
    footer = section.footer.paragraphs[0]
    footer.text = "TrendyTech sales call review" + ("  |  Review draft" if meta.get("review_draft") else "")
    footer.runs[0].font.size = Pt(8)
    output = root / "deliverables" / "TrendyTech Sales Call Review.docx"
    output.parent.mkdir(parents=True, exist_ok=True)
    doc.save(output)
    print(output)


if __name__ == "__main__":
    build(Path(sys.argv[1] if len(sys.argv) > 1 else "data"))
