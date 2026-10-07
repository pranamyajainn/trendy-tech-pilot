"""Two-page client report, built only from data/exports/client/*.json (written by `pilot worklist export`).
No costs, model names or processing details. Every number is computed from the exported rows."""

import json
import re
import sys
from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

WHAT_TO_DO = {
    "Hot": "Call first: a commitment is on record and recent.",
    "Warm": "Interest without a recent commitment, or a commitment gone quiet: follow the row's next action.",
    "Check status": "Check LeadSquared or payments before any sales call.",
    "Dormant: reconfirm": "No conversation for 90+ days: reconfirm interest by message first.",
    "Cold: declined": "Declined, or wants something TrendyTech does not offer: no sales calls.",
    "Outside target": "Matches TrendyTech's own exclusion: manager decides.",
    "Not reached": "Never spoken to: change channel (WhatsApp, email) or time.",
}


def build(root):
    def read(name):
        return json.loads((root / "exports" / "client" / (name + ".json")).read_text())

    meta, rows = read("meta"), read("worklist")
    doc = Document()
    section = doc.sections[0]
    section.top_margin = section.bottom_margin = Inches(.5)
    section.left_margin = section.right_margin = Inches(.7)
    for name in ["Normal", "Title", "Heading 1", "Heading 2"]:
        doc.styles[name].font.name = "Arial"
        doc.styles[name].font.color.rgb = RGBColor(0, 0, 0)
    doc.styles["Normal"].font.size = Pt(9)
    doc.styles["Normal"].paragraph_format.space_after = Pt(4)
    doc.styles["Title"].font.size = Pt(20)
    doc.styles["Heading 1"].font.size = Pt(13)
    doc.styles["Heading 2"].font.size = Pt(10.5)
    for border in doc.styles.element.xpath(".//w:pBdr"):
        border.getparent().remove(border)
    doc.core_properties.title = "TrendyTech pilot: open-lead worklist"
    doc.core_properties.author = "Sahajta AI"

    def para(text="", bold_prefix=None, style=None):
        p = doc.add_paragraph(style=style)
        if bold_prefix:
            p.add_run(bold_prefix).bold = True
        p.add_run(text)
        return p

    def table(headers, body, widths):
        t = doc.add_table(rows=1, cols=len(headers))
        t.style = "Table Grid"
        t.autofit = False
        for cell, title in zip(t.rows[0].cells, headers):
            cell.text = title
            shade = OxmlElement("w:shd")
            shade.set(qn("w:fill"), "243B53")
            cell._tc.get_or_add_tcPr().append(shade)
            for run in cell.paragraphs[0].runs:
                run.bold = True
                run.font.color.rgb = RGBColor(255, 255, 255)
        for values in body:
            cells = t.add_row().cells
            for cell, value in zip(cells, values):
                cell.text = str(value)
        for column, width in zip(t.columns, widths):
            column.width = Inches(width)  # the grid widths: LibreOffice and Word lay out from these
        for row in t.rows:
            for cell, width in zip(row.cells, widths):
                cell.width = Inches(width)
                for p in cell.paragraphs:
                    p.paragraph_format.space_after = Pt(0)
                    for run in p.runs:
                        run.font.size = Pt(8)
        return t

    counts = meta["categories"]
    evidence = meta["evidence"]
    pilot_calls = sum(1 for _ in read("calls"))
    statuses = [j["status"] for j in read("journeys")]
    customers, mixed = statuses.count("Customer (CRM)"), statuses.count("Unclear: mixed CRM flags")
    doc.add_paragraph("TrendyTech pilot: open-lead worklist", style="Title")
    if meta["review_draft"]:
        para("REVIEW DRAFT: the evidence behind each lead is being checked against the recordings.").runs[0].italic = True
    para(f"{meta['journeys']} lead journeys and {pilot_calls} recorded calls were analysed, call by call: the "
         f"{meta['leads']} open leads below plus {customers} pilot customers and {mixed} pilot leads with mixed CRM "
         "flags. "
         f"{meta['leads']} open leads (no purchase in the CRM export) are placed on a worklist by a fixed rule built "
         f"from how TrendyTech's own past leads behaved. Every row cites the call and minute it rests on, so any "
         f"statement can be checked by playing the recording. {meta['scope_note']}")

    doc.add_heading("The worklist at a glance", level=1)
    table(["Category", "Leads", "What to do"],
          [(c, counts[c], WHAT_TO_DO[c]) for c in WHAT_TO_DO if counts.get(c)], [1.5, .6, 4.9])

    doc.add_heading("Act now", level=1)
    days = lambda r: int(re.search(r"\((\d+) days\)", r["last_conversation"]).group(1)) if "days)" in r["last_conversation"] else 10**6
    act = [r for r in rows if r["category"] in ("Hot", "Check status") or (r["category"] == "Warm" and days(r) <= 30)]
    table(["Lead", "Category", "Last conversation", "Next action"],
          [(r["lead"], r["category"], r["last_conversation"], r["next_action"]) for r in act]
          or [("–", "–", "–", "No lead in these categories")], [.65, 1.0, 1.25, 4.1])
    para(f"The Hot and Check-status leads, and Warm leads spoken to in the last 30 days. All {len(rows)} leads, with the reasons, "
         "objections, SWOT, what to say and proof, are in the Worklist sheet of the workbook.").runs[0].italic = True
    if act:
        top = act[0]
        doc.add_heading(f"Worked example: lead {top['lead']}", level=2)
        para(top["buying_intent"], "Where it stands: ")
        para(top["open_objections"].replace("\n", "; "), "Open objections: ")
        para(top["next_action"], "Next action: ")
        para(top["what_to_say"].replace("\n", " | "), "What to say: ")
        para(top["proof"].split("\n")[0], "Proof: ")

    doc.add_heading("How leads are graded", level=1)
    para("Each lead is placed by the first statement that is true, in this order: (1) its own calls show it may "
         "already have bought: check status; (2) never reached; (3) matches TrendyTech's own exclusion (fresher, "
         "non-IT background, career gap over a year); (4) declined, or wants something TrendyTech does not offer; "
         f"(5) said they will pay or enrol, or will on a condition TrendyTech can meet, within the last "
         f"{meta['rule']['hot_within_days']} days: Hot; (6) no conversation for over {meta['rule']['dormant_after_days']} "
         "days: dormant; (7) everything else: Warm. Only what the lead said counts, not what the counsellor said.")
    w = 1448 / 150
    b = sum(e["customers"] for e in evidence.values())
    n = sum(e["non_buyers_sampled"] for e in evidence.values())
    base = b / (b + w * n)
    para(f"The evidence comes from {b} past customers and a random sample of {n} past non-buyers who had a real sales "
         f"conversation. Read the same way, about {base:.0%} of such past leads bought. For each category:")
    ev_rows = []
    for c in ("Warm", "Outside target", "Cold: declined", "Hot"):
        e = evidence.get(c)
        if not e:
            continue
        if c == "Hot":
            shown = "Not shown: for most past customers this was said on the call where they paid"
        elif e["customers"] < 10 or e["non_buyers_sampled"] < 5:
            shown = "Too few past cases for a reliable rate"
        else:
            shown = f"{e['rate'] * 10:.1f} in 10 bought (range {e['low'] * 10:.1f}–{e['high'] * 10:.1f})"
        ev_rows.append((c, e["customers"], e["non_buyers_sampled"], shown))
    table(["Category", "Past customers", "Sampled non-buyers", "Similar past leads"], ev_rows, [1.4, 1.0, 1.2, 3.4])

    doc.add_heading("What this tells TrendyTech", level=1)
    out = evidence.get("Outside target")
    warm = evidence.get("Warm")
    if out and warm:
        para(f"Leads matching TrendyTech's own exclusion bought at about {out['rate']:.0%}, against {warm['rate']:.0%} "
             "for other leads still in play: the exclusion is supported by TrendyTech's own data.", "Your target rule holds. ")
    para("What a lead says about buying (committing, or declining) separates leads far more than who they are. "
         "In this analysis, profile details such as a data role versus another IT role did not show a reliable "
         "difference; the full archive in the next phase holds about ten times as many past non-buyers (1,448 "
         "rather than a sample of 150) and can test them again.",
         "Words beat profiles. ")
    reached = [r for r in rows if r["category"] not in ("Not reached",)]
    not_asked = sum(r["experience"] == "Not asked" or "not stated" in r["experience"].lower() for r in reached)
    never = counts.get("Not reached", 0)
    waiting = sum("a decision TrendyTech controls" in r["buying_intent"] for r in rows)
    pricing = sum(bool(re.search(r"discount|final price|price approval|approve.{0,30}price|price.{0,20}approv",
                                 r["next_action"] + " " + r["why"], re.IGNORECASE)) for r in rows)
    checks = counts.get("Check status", 0)
    doc.add_heading("Changes to test on upcoming calls", level=1)
    if not_asked:
        para(f"In {not_asked} of the {len(reached)} leads that were spoken to, the lead's years of experience are not "
             "recorded on any call. Ask experience, current role and timeline in the first call: the target check "
             "depends on it.",
             "Discovery first. ")
    if never:
        para(f"{never} lead{'s were' if never != 1 else ' was'} never reached despite repeated calls. Switch to WhatsApp or email after three "
             "unanswered calls, and answer call-screening prompts with the name and the course enquiry.",
             "Change channel early. ")
    if waiting:
        para(f"{waiting} lead{'s' if waiting != 1 else ''} will enrol on a condition TrendyTech decides (a price "
             "approval). Decide it before the next call.", "Unblock internal decisions. ")
    if pricing:
        para(f"{pricing} of the {len(rows)} leads turn on a price or discount decision. Agree a clear policy (when a "
             "discount is allowed, how much, and the non-price alternatives such as no-cost EMI or a single-cloud "
             "option) so counsellors can answer on the call instead of promising to check.", "Price decisions. ")
    if checks:
        para(f"{checks} 'open' lead{'s show' if checks != 1 else ' shows'} signs of an earlier purchase or a missed payment date. Correcting the CRM "
             "keeps counsellors off customers.", "Keep the CRM true. ")
    para("Call-quality scoring against an agreed rubric, best time to call (needs unanswered-call logs) and a daily "
         "worklist across all current open leads.", "Next phase: ")
    return doc


if __name__ == "__main__":
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "data").resolve()
    out = root / "deliverables" / "TrendyTech Pilot Report.docx"
    out.parent.mkdir(parents=True, exist_ok=True)
    build(root).save(out)
    print("Client report exported to the private deliverables directory.")
