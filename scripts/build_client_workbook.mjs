// Client workbook: the three sheets the proposal promises for the pilot (worklist, lead journeys, call data).
// No costs, model names or processing details. Input: data/exports/client/{meta,worklist,journeys,calls}.json,
// written by `pilot worklist export`. Run with the Codex bundled Node runtime and @oai/artifact-tool via
// node_modules. Output stays in ignored data/deliverables/.
import fs from 'node:fs/promises';
import path from 'node:path';
import { Workbook, SpreadsheetFile } from '@oai/artifact-tool';

const root = path.resolve(process.argv[2] || 'data');
const read = async name => JSON.parse(await fs.readFile(path.join(root, 'exports', 'client', name + '.json'), 'utf8'));
const [meta, worklist, journeys, calls] = await Promise.all(['meta', 'worklist', 'journeys', 'calls'].map(read));
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
const CATEGORY_FILL = {
  'Hot': '#F8D7DA', 'Warm': '#FDEBD0', 'Check status': '#D6E4F5', 'Dormant: reconfirm': '#E9ECEF',
  'Cold: declined': '#E2E3E5', 'Outside target': '#EFEFEF', 'Not reached': '#F4F4F4',
  'Recording unusable: confirm status': '#D6E4F5',
};

function sheet(name, title, rows, columns, subtitle) {
  const ws = workbook.worksheets.add(name);
  ws.showGridLines = false;
  ws.getCell(0, 0).values = [[title]];
  ws.getCell(0, 0).format.font = {name: 'Arial', size: 14, bold: true};
  ws.getCell(1, 0).values = [[(meta.review_draft ? 'REVIEW DRAFT. ' : '') + meta.scope_note + ' ' + subtitle]];
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
        .reduce((n, part) => n + Math.max(1, Math.ceil(part.length / (c.width * 1.2))), 0)));
      ws.getRangeByIndexes(4 + r, 0, 1, keys.length).format.rowHeight = Math.min(409, lines * 13.5 + 8);
    });
  }
  ws.freezePanes.freezeRows(4);
  return ws;
}

const ws = sheet('Worklist', 'Open leads: who to contact, why, and what to say', worklist, [
  {key: 'lead', label: 'Lead', width: 9},
  {key: 'priority', label: 'Priority', width: 14},
  {key: 'reason', label: 'Reason', width: 30},
  {key: 'next_action', label: 'Next action', width: 40},
  {key: 'what_to_say', label: 'What to say on the next call', width: 44},
  {key: 'evidence', label: 'Evidence (call, minute, exact words)', width: 46},
  {key: 'historical_context', label: 'Historical context', width: 30},
  {key: 'last_conversation', label: 'Last conversation', width: 16},
  {key: 'buying_intent', label: 'Buying intent', width: 34},
  {key: 'experience', label: 'Experience', width: 16},
  {key: 'role', label: 'Current role', width: 20},
  {key: 'ctc', label: 'CTC', width: 11},
  {key: 'location', label: 'Location', width: 12},
  {key: 'open_objections', label: 'Open objections', width: 36},
  {key: 'swot', label: 'Opportunity SWOT (S, W, O, T)', width: 44},
  {key: 'validation', label: 'Validation', width: 16},
], 'Priority follows a fixed rule. Historical context describes groups of past leads, not any lead\'s chance of buying. Play the cited minute to check any row.');
worklist.forEach((row, r) => {
  const fill = CATEGORY_FILL[row.priority];
  if (fill) ws.getRangeByIndexes(4 + r, 1, 1, 1).format.fill = fill;
});
// Lead and Priority stay visible while scrolling right.
ws.freezePanes.freezeColumns(2);
sheet('Lead journeys', 'Each lead\'s calls in order', journeys, [
  {key: 'lead', label: 'Lead', width: 9},
  {key: 'status', label: 'Status in the CRM export', width: 16},
  {key: 'calls', label: 'Calls', width: 7},
  {key: 'first_call', label: 'First call', width: 12},
  {key: 'last_call', label: 'Last call', width: 12},
  {key: 'journey', label: 'Journey (date, call type, length: what happened)', width: 110},
], 'One line per call; open leads and pilot customers.');
sheet('Calls', 'Structured call data', calls, [
  {key: 'lead', label: 'Lead', width: 9},
  {key: 'date', label: 'Date', width: 12},
  {key: 'type', label: 'Call type', width: 14},
  {key: 'length', label: 'Length', width: 9},
  {key: 'summary', label: 'What happened', width: 60},
  {key: 'objections', label: 'Objections raised and how they were handled', width: 50},
  {key: 'next_step', label: 'Next step noted', width: 40},
], 'One row per recorded call.');

const out = path.join(root, 'deliverables');
await fs.mkdir(out, {recursive: true});
const file = await SpreadsheetFile.exportXlsx(workbook);
await file.save(path.join(out, 'TrendyTech Pilot Workbook.xlsx'));
// The export tool leaves a large inspection log beside the workbook; it is not needed.
await fs.rm(path.join(out, 'TrendyTech Pilot Workbook.xlsx.inspect.ndjson'), {force: true});
console.log('Client workbook exported to the private deliverables directory.');
