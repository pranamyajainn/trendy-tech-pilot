// Run with the Codex bundled Node runtime and @oai/artifact-tool available via node_modules.
// No workbook or private JSON produced by this script belongs in Git.
import fs from 'node:fs/promises';
import path from 'node:path';
import { Workbook, SpreadsheetFile } from '@oai/artifact-tool';

const root = path.resolve(process.argv[2] || 'data');
const read = async name => JSON.parse(await fs.readFile(path.join(root, 'exports', name + '.json'), 'utf8'));
const [overview, worklist, calls, evidence, profiles] = await Promise.all(
  ['overview', 'worklist', 'calls', 'evidence', 'profiles'].map(read)
);
const workbook = Workbook.create();
const safe = value => {
  if (value == null) return '';
  if (typeof value === 'object') value = JSON.stringify(value);
  return typeof value === 'string' && /^[\s]*[=+@-]/.test(value) ? "'" + value : value;
};
const titleCase = key => key === 'open_objections' ? 'Historically unresolved concerns — confirm current status' : key.replaceAll('_', ' ').replace(/^./, c => c.toUpperCase());
const col = n => {
  let label = '';
  for (n++; n > 0; n = Math.floor((n - 1) / 26)) label = String.fromCharCode(65 + (n - 1) % 26) + label;
  return label;
};
function table(name, rows, keys, widths = {}) {
  const sheet = workbook.worksheets.add(name);
  sheet.showGridLines = false;
  sheet.getCell(0, 0).values = [[name]];
  sheet.getCell(0, 0).format.font = {name: 'Arial', size: 14, bold: true};
  const matrix = [keys.map(titleCase), ...rows.map(row => keys.map(k => safe(row[k])))];
  const range = sheet.getRangeByIndexes(2, 0, matrix.length, keys.length);
  range.values = matrix;
  range.format.font = {name: 'Arial', size: 10};
  range.format.rowHeight = 35;
  range.format.verticalAlignment = 'center';
  const header = sheet.getRangeByIndexes(2, 0, 1, keys.length);
  header.format = {fill: '#183F35', font: {name: 'Arial', size: 10, color: '#FFFFFF', bold: true}, wrapText: true, rowHeight: 32};
  const bottom = matrix.length + 2;
  for (let i = 0; i < keys.length; i++) {
    const column = sheet.getRangeByIndexes(2, i, matrix.length, 1);
    column.format.columnWidth = widths[keys[i]] || 20;
    column.format.wrapText = true;
  }
  if (rows.length) {
    sheet.tables.add(`A3:${col(keys.length - 1)}${bottom}`, true, name.replace(/[^A-Za-z]/g, '') + 'Data');
    sheet.getRangeByIndexes(3, 0, rows.length, keys.length).format.rowHeight = name === 'Evidence' ? 68 : 78;
  }
  sheet.freezePanes.freezeRows(3);
  return sheet;
}

const counts = [
  {item: 'Selected calls', value: overview.selected_calls},
  {item: 'Selected leads', value: overview.selected_leads},
  {item: 'Calls with evidence-checked extraction', value: overview.analysed_calls},
  {item: 'Journeys with all available calls extracted', value: overview.complete_extracted_journeys},
  {item: 'Held-out leads', value: overview.holdout_leads},
  {item: 'Unique transcribed audio minutes', value: overview.costs.unique_transcribed_audio_minutes},
  {item: 'Measured external API spend (INR)', value: overview.costs.external_api_spend_inr},
  {item: 'Full processing cost per minute (INR)', value: overview.costs.processing_inr_per_audio_minute ?? 'Unmeasured'},
  {item: 'Accuracy status', value: overview.accuracy_status},
  {item: 'Worklist use', value: 'Retrospective suggestions as of last exported call. Confirm current status before outreach.'},
  {item: 'Sampling', value: '50 multi-call leads selected to give 300 calls. Findings do not estimate archive conversion.'},
  {item: 'Outcomes', value: 'CRM Yes flags unverified; blanks unknown. No conversion probabilities.'},
  {item: 'Quality', value: 'Exact-quote checks are automated. Meaning, speaker attribution and transcription need independent review.'},
  {item: 'Rubric', value: 'Call-quality columns show provisional evidence coverage, not approved performance scores. Script adherence unavailable.'},
];
table('Pilot readout', counts, ['item', 'value'], {item: 46, value: 105});
table('Worklist', worklist, ['lead_alias', 'lead_number', 'current_owner', 'priority', 'as_of_recorded_call', 'goal', 'open_objections', 'objection_history_note', 'next_action_suggestion', 'calls_analysed', 'calls_in_export', 'outcome'],
  {goal: 38, open_objections: 65, next_action_suggestion: 75, outcome: 40});
table('Journey review', worklist, ['lead_alias', 'total_call_minutes_crm', 'mean_gap_days', 'strengths', 'weaknesses_or_unknowns', 'opportunity', 'threats', 'conflicting_profile_fields', 'effort_review', 'journey_coverage', 'split'],
  {strengths: 60, weaknesses_or_unknowns: 36, opportunity: 55, threats: 35, journey_coverage: 45});
table('Lead profiles', profiles, ['lead_alias', 'current_role', 'experience', 'company', 'location', 'current_ctc', 'target_role', 'technology_interest', 'course', 'goal', 'timeline', 'budget', 'availability', 'conflicting_fields'],
  {goal: 55, timeline: 38, technology_interest: 35});
table('Calls', calls, ['lead_alias', 'call_id', 'call_number_in_export', 'created_on', 'salesperson', 'duration_seconds_audio', 'conversation_type', 'processing_status', 'summary', 'objections', 'next_action_suggestion', 'qualification', 'discovery', 'pitch', 'objection_handling', 'script_adherence', 'closing', 'asr_flags', 'uncertainties', 'split'],
  {summary: 85, objections: 65, next_action_suggestion: 70, processing_status: 38, uncertainties: 60, script_adherence: 40});
table('Evidence', evidence, ['lead_alias', 'call_id', 'collection', 'field', 'evidence_type', 'claim', 'quote', 'start_seconds', 'end_seconds', 'segment_id', 'review_status'],
  {claim: 65, quote: 95, review_status: 38});
table('Processing costs', Object.entries(overview.costs).map(([item, value]) => ({item: titleCase(item), value: value ?? 'Unmeasured'})), ['item', 'value'], {item: 55, value: 105});

workbook.recalculate();
const inspection = await workbook.inspect({kind: 'region', sheetId: 'Pilot readout', range: 'A1:B12', maxChars: 1800});
console.log(inspection.ndjson);
const out = path.join(root, 'deliverables');
await fs.mkdir(out, {recursive: true});
for (const sheetName of ['Pilot readout', 'Worklist', 'Journey review', 'Lead profiles', 'Calls', 'Evidence', 'Processing costs']) {
  const preview = await workbook.render({sheetName, range: sheetName === 'Pilot readout' || sheetName === 'Processing costs' ? 'A1:B12' : 'A1:F7', scale: 1, format: 'png'});
  await fs.writeFile(path.join(out, 'preview-' + sheetName.replaceAll(' ', '-') + '.png'), new Uint8Array(await preview.arrayBuffer()));
}
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(path.join(out, 'TrendyTech Pilot Workbook.xlsx'));
console.log('Workbook exported to local private deliverables directory.');
