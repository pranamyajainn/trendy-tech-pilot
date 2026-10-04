// Client workbook: two decision sheets, no costs, model names or processing details.
// Run with the Codex bundled Node runtime and @oai/artifact-tool via node_modules. Output stays in ignored data/.
import fs from 'node:fs/promises';
import path from 'node:path';
import { Workbook, SpreadsheetFile } from '@oai/artifact-tool';

const root = path.resolve(process.argv[2] || 'data');
const read = async name => JSON.parse(await fs.readFile(path.join(root, 'exports', 'client', name + '.json'), 'utf8'));
const [meta, insights, actions] = await Promise.all(['meta', 'sales_insights', 'lead_actions'].map(read));
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

function sheet(name, title, rows, columns) {
  const ws = workbook.worksheets.add(name);
  ws.showGridLines = false;
  ws.getCell(0, 0).values = [[title]];
  ws.getCell(0, 0).format.font = {name: 'Arial', size: 14, bold: true};
  ws.getCell(1, 0).values = [[(meta.review_draft ? 'REVIEW DRAFT. ' : '') + meta.scope_note]];
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

sheet('Sales Insights', 'What should change in the sales approach', insights, [
  {key: 'finding', label: 'Finding', width: 34},
  {key: 'evidence_and_scale', label: 'Evidence and scale', width: 46},
  {key: 'sales_implication', label: 'Sales implication', width: 38},
  {key: 'recommended_change', label: 'Recommended change', width: 44},
  {key: 'suggested_wording', label: 'Suggested wording (proposed)', width: 40},
  {key: 'how_to_assess', label: 'How to assess improvement', width: 34},
  {key: 'source', label: 'Source', width: 46},
]);
sheet('Lead Actions', 'What to do next with each lead', actions, [
  {key: 'lead_identifier', label: 'Lead identifier', width: 13},
  {key: 'assigned_owner', label: 'Assigned owner', width: 18},
  {key: 'action_category', label: 'Action', width: 22},
  {key: 'last_substantive_conversation', label: 'Last substantive conversation', width: 15},
  {key: 'goal_and_context', label: 'Goal and relevant context', width: 38},
  {key: 'latest_position', label: 'Latest position / unresolved concern', width: 44},
  {key: 'recommended_next_action', label: 'Recommended next action', width: 44},
  {key: 'suggested_wording', label: 'Suggested question or wording', width: 40},
  {key: 'timing_status_check', label: 'Timing / status check', width: 32},
  {key: 'supporting_evidence', label: 'Supporting evidence', width: 46},
]);

const out = path.join(root, 'deliverables');
await fs.mkdir(out, {recursive: true});
for (const name of ['Sales Insights', 'Lead Actions']) {
  const preview = await workbook.render({sheetName: name, range: 'A1:E9', scale: 1, format: 'png'});
  await fs.writeFile(path.join(out, 'client-preview-' + name.replaceAll(' ', '-') + '.png'), new Uint8Array(await preview.arrayBuffer()));
}
const file = await SpreadsheetFile.exportXlsx(workbook);
await file.save(path.join(out, 'TrendyTech Pilot Workbook.xlsx'));
console.log('Client workbook exported to the private deliverables directory.');
