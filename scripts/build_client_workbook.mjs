// Client workbook: two decision sheets, no costs, model names or processing details.
// Run with the Codex bundled Node runtime and @oai/artifact-tool via node_modules. Output stays in ignored data/.
import fs from 'node:fs/promises';
import path from 'node:path';
import { Workbook, SpreadsheetFile } from '@oai/artifact-tool';

const root = path.resolve(process.argv[2] || 'data');
const read = async name => JSON.parse(await fs.readFile(path.join(root, 'exports', 'client', name + '.json'), 'utf8'));
const optional = name => read(name).catch(() => []);
const [meta, insights, actions] = await Promise.all(['meta', 'sales_insights', 'lead_actions'].map(read));
const [swot, predictors, groups, who] = await Promise.all(['lead_swot', 'predictors', 'lead_groups', 'who_buys'].map(optional));
const workbook = Workbook.create();
const safe = value => {
  if (value == null) return '';
  return typeof value === 'string' && /^[\s]*[=+@-]/.test(value) ? "'" + value : value;
};
const col = n => {
  let label = '';
  for (n++; n > 0; n = Math.floor((n - 1) / 26)) label = String.fromCharCode(65 + (n - 1) % 26) + label;
  return label;
};

function sheet(name, title, rows, columns, subtitle = '') {
  const ws = workbook.worksheets.add(name);
  ws.showGridLines = false;
  ws.getCell(0, 0).values = [[title]];
  ws.getCell(0, 0).format.font = {name: 'Arial', size: 14, bold: true};
  ws.getCell(1, 0).values = [[(meta.review_draft ? 'REVIEW DRAFT. ' : '') + meta.scope_note + (subtitle ? ' ' + subtitle : '')]];
  ws.getCell(1, 0).format.font = {name: 'Arial', size: 10, italic: true, color: '#5A6B7B'};
  const keys = columns.map(c => c.key);
  const matrix = [columns.map(c => c.label), ...rows.map(row => keys.map(k => safe(row[k])))];
  const range = ws.getRangeByIndexes(3, 0, matrix.length, keys.length);
  range.values = matrix;
  range.format.font = {name: 'Arial', size: 10};
  range.format.verticalAlignment = 'top';
  range.format.wrapText = true;
  const header = ws.getRangeByIndexes(3, 0, 1, keys.length);
  header.format = {fill: '#243B53', font: {name: 'Arial', size: 10, color: '#FFFFFF', bold: true}, wrapText: true, rowHeight: 36};
  columns.forEach((c, i) => { ws.getRangeByIndexes(3, i, matrix.length, 1).format.columnWidth = c.width; });
  if (rows.length) {
    const table = ws.tables.add(`A4:${col(keys.length - 1)}${matrix.length + 3}`, true, name.replace(/[^A-Za-z]/g, '') + 'Table');
    table.style = 'TableStyleLight1';
    // Explicit heights from the longest wrapped cell: autofit undersizes some rows, which clips the last line.
    rows.forEach((row, r) => {
      const lines = Math.max(...columns.map(c => String(row[c.key] ?? '').split('\n')
        .reduce((n, part) => n + Math.max(1, Math.ceil(part.length / (c.width * 1.05))), 0)));
      ws.getRangeByIndexes(4 + r, 0, 1, keys.length).format.rowHeight = lines * 13.5 + 8;
    });
  }
  ws.freezePanes.freezeRows(4);
}

sheet('Lead Priorities', 'Which leads to work first', actions, [
  {key: 'lead_identifier', label: 'Lead identifier', width: 13},
  {key: 'assigned_owner', label: 'Assigned owner', width: 18},
  {key: 'category', label: 'Category', width: 14},
  {key: 'why_this_category', label: 'Why this category', width: 40},
  {key: 'similar_past_leads', label: 'How similar past leads turned out', width: 30},
  {key: 'open_concerns', label: 'Open concerns', width: 20},
  {key: 'last_sales_conversation', label: 'Last sales conversation', width: 14},
  {key: 'recommended_next_action', label: 'Recommended next action', width: 44},
  {key: 'suggested_wording', label: 'Suggested question or wording', width: 40},
  {key: 'timing_status_check', label: 'Timing / status check', width: 32},
  {key: 'supporting_evidence', label: 'Supporting evidence', width: 46},
], 'Categories come from groups of past leads with known outcomes; see Lead Groups.');
if (swot.length) sheet('Lead SWOT', 'Opportunity SWOT for each open lead', swot, [
  {key: 'lead_identifier', label: 'Lead identifier', width: 13},
  {key: 'category', label: 'Category', width: 14},
  {key: 'summary', label: 'Summary', width: 40},
  {key: 'strengths', label: 'Strengths', width: 40},
  {key: 'weaknesses', label: 'Weaknesses', width: 40},
  {key: 'opportunities', label: 'Opportunities', width: 40},
  {key: 'threats', label: 'Threats', width: 40},
], 'Every point cites a moment in the lead\'s own calls.');
if (predictors.length) sheet('What Predicts Buying', 'What separates buyers from non-buyers', predictors, [
  {key: 'factor', label: 'Factor', width: 34},
  {key: 'buyers_with_it', label: 'Buyers with it', width: 14},
  {key: 'non_buyers_with_it', label: 'Non-buyers with it', width: 14},
  {key: 'bought_with_it', label: 'Bought, with it', width: 12},
  {key: 'bought_without_it', label: 'Bought, without it', width: 12},
  {key: 'difference', label: 'Difference', width: 12},
  {key: 'strength', label: 'Strength of the link', width: 30},
  {key: 'evidence', label: 'Evidence', width: 22},
], 'Sales calls before purchase only. "Clear" survives a correction for testing many factors; links are associations, not causes.');
if (groups.length) sheet('Lead Groups', 'How each group of past leads turned out', groups, [
  {key: 'group', label: 'Group', width: 16},
  {key: 'meaning', label: 'Meaning', width: 40},
  {key: 'category', label: 'Category', width: 14},
  {key: 'bought_jan_may', label: 'Bought, leads first called Jan-May (range)', width: 26},
  {key: 'leads_jan_may', label: 'Leads, Jan-May', width: 10},
  {key: 'bought_jun_sep', label: 'Bought, leads first called Jun-Sep (range)', width: 26},
  {key: 'leads_jun_sep', label: 'Leads, Jun-Sep', width: 10},
], 'Graded on Jan-May leads and checked on later, different leads (Jun-Sep). Hot or Cold means the range lies above or below the average.');
if (who.length) sheet('Who Buys', 'Who buys, compared with who does not', who, [
  {key: 'trait', label: 'Trait', width: 26},
  {key: 'value', label: 'Value', width: 36},
  {key: 'share_of_buyers', label: 'Share of buyers', width: 16},
  {key: 'share_of_non_buyers', label: 'Share of non-buyers', width: 18},
], 'Shares of leads where the trait was stated.');
sheet('Sales Insights', 'What should change in the sales approach', insights, [
  {key: 'finding', label: 'Finding', width: 34},
  {key: 'evidence_and_scale', label: 'Evidence and scale', width: 46},
  {key: 'sales_implication', label: 'Sales implication', width: 38},
  {key: 'recommended_change', label: 'Recommended change', width: 44},
  {key: 'suggested_wording', label: 'Suggested wording (proposed)', width: 40},
  {key: 'how_to_assess', label: 'How to assess improvement', width: 34},
  {key: 'source', label: 'Source', width: 46},
]);

const out = path.join(root, 'deliverables');
await fs.mkdir(out, {recursive: true});
for (const name of ['Lead Priorities', 'What Predicts Buying', 'Lead Groups']) {
  const preview = await workbook.render({sheetName: name, range: 'A1:E9', scale: 1, format: 'png'});
  await fs.writeFile(path.join(out, 'client-preview-' + name.replaceAll(' ', '-') + '.png'), new Uint8Array(await preview.arrayBuffer()));
}
const file = await SpreadsheetFile.exportXlsx(workbook);
await file.save(path.join(out, 'TrendyTech Pilot Workbook.xlsx'));
console.log('Client workbook exported to the private deliverables directory.');
