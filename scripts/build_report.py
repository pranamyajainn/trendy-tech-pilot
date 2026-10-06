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
    optional = lambda name: read(name) if (root / "exports" / "client" / (name + ".json")).exists() else []
    predictors, groups, who = optional("predictors"), optional("lead_groups"), optional("who_buys")
    comparison = meta.get("comparison") or {}
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
    para(f"We reviewed 300 recorded calls across 50 leads from the September 2026 call export. To judge which "
         f"leads are likely to buy, we compared {comparison.get('buyers', 'the')} past customers with "
         f"{comparison.get('non_buyers', 'a sample of')} randomly chosen leads who had a real sales conversation "
         f"but did not buy. This report covers which leads to work first, what separates buyers from non-buyers, "
         f"who buys, and {len(insights)} changes to the sales approach. Every statement shows the calls, and the "
         f"moments in them, that it rests on.")

    para("Which leads to work first", "Heading 1")
    table(["Category", "Leads"], Counter(a["category"] for a in actions).most_common())
    para("Each open lead falls into one group based on what they said in their sales calls, and each group's "
         "category comes from how similar past leads turned out. The Lead Priorities sheet shows every lead's "
         "group, how similar past leads turned out with a range, open concerns, a next action and the moments "
         "in the calls behind it; the Lead SWOT sheet gives an evidence-backed SWOT for each open lead.")
    for lead in [a for a in actions if a["category"] == "Hot"]:
        para(f"Lead {lead['lead_identifier']}: Hot", "Heading 2")
        para(lead["why_this_category"] + " " + lead["similar_past_leads"] + ".", bold_prefix="Why: ")
        para(lead["recommended_next_action"], bold_prefix="Next action: ")
        para(lead["suggested_wording"], bold_prefix="Opening question: ")

    if predictors:
        para("What separates buyers from non-buyers", "Heading 1")
        para("Only sales conversations before a purchase are compared, so what customers say after buying does not "
             "count. A factor is marked clear only if the difference survives a correction for testing many factors "
             "at once. These are associations from past calls, not proof that changing a factor causes a sale.")
        table(["Factor", "Bought, with it", "Bought, without it", "Strength of the link"],
              [(f["factor"], f["bought_with_it"], f["bought_without_it"], f["strength"])
               for f in predictors if f["evidence"] == "Clear"])
        unclear = [f["factor"].lower() for f in predictors if f["evidence"] != "Clear"]
        para(f"No clear link was found for the other {len(unclear)} factors, including "
             + ", ".join(unclear[:6]) + ". In particular, agreeing to a follow-up call did not predict buying.")
    if groups:
        para("How the groups held up on later leads", "Heading 1")
        para("Groups were graded on leads first called from January to May, then checked on different leads first "
             "called from June to September.")
        table(["Group", "Category", "Bought, Jan to May", "Bought, Jun to Sep"],
              [(g["group"], g["category"], g["bought_jan_may"], g["bought_jun_sep"]) for g in groups])
    if who:
        para("Who buys", "Heading 1")
        para("Shares among leads who stated the trait, buyers compared with non-buyers.")
        table(["Trait", "Value", "Share of buyers", "Share of non-buyers"],
              [(w["trait"], w["value"], w["share_of_buyers"], w["share_of_non_buyers"]) for w in who
               if w["trait"] in ("Main reason for the course", "Background", "Experience")])

    para("What should change in the sales approach", "Heading 1")
    for index, insight in enumerate(insights, 1):
        para(f"{index}. {insight['finding']}", "Heading 2")
        para(insight["evidence_and_scale"], bold_prefix="Evidence: ")
        para(insight["sales_implication"], bold_prefix="Why it matters: ")
        para(insight["recommended_change"], bold_prefix="Recommended change: ")
        para(insight["suggested_wording"], bold_prefix="Proposed wording: ")
        para(insight["how_to_assess"], bold_prefix="How to check it worked: ")
        para(insight["source"], bold_prefix="Source: ")

    para("How this review was done", "Heading 1")
    para("Each pilot recording was transcribed by three independent speech-recognition systems, and passages where "
         "they disagreed were checked against the audio. Each comparison recording was transcribed by two systems, "
         "and a statement whose words the second system did not hear was held back. Statements were then taken "
         "from the transcript, and each one was checked a second time against the words that support it; "
         "statements that could not be supported were left out.")
    para("To trace any statement, open the call in the CRM recording for the lead and date shown, and go to the time "
         "given to hear the original words.")
    para("Limits of this review", "Heading 1")
    for text in [("Buyers are leads the CRM marks as converted; this was not checked against payment records. Leads "
                  "with no purchase recorded may still buy."),
                 ("Purchase dates were not available, so a customer's calls before their first payment, onboarding or "
                  "support call are treated as before the purchase. Customers whose sales calls predate the export "
                  "could not be compared on what they said."),
                 ("Non-buyers are a random sample of 150 of the 1,448 such leads; rates are scaled to that population "
                  "and the ranges reflect the sample size."),
                 ("The 50 pilot leads are a selected sample of leads with several recorded calls, so their mix does "
                  "not describe every TrendyTech lead."),
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
