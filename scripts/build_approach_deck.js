// Plain client deck explaining how the pilot was done: process, research, experiments and findings.
// One idea per slide, no decoration. Every number is read from private data (the worklist export and the
// research figures), so the deck always matches the workbook and the report; nothing client-specific is written
// in this public file. No costs, model names or internal labels. Output stays in ignored data/deliverables/.
// Run: node scripts/build_approach_deck.js [data-dir]
const fs = require('fs');
const path = require('path');
const pptxgen = require('pptxgenjs');

const root = path.resolve(process.argv[2] || 'data');
const read = p => JSON.parse(fs.readFileSync(path.join(root, p), 'utf8'));
const meta = read('exports/client/meta.json');
const rows = read('exports/client/worklist.json');
const fig = read('research/review-scripts/review_figures.json');  // the independently recomputed research figures

const pct = x => `${Math.round(x * 100)}%`;
const day = iso => new Date(iso + 'T00:00:00Z').toLocaleDateString('en-GB', {day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC'});
const cutoff = day(meta.as_of);
const inv = fig.inventory;
const ev = meta.evidence;
const cat = meta.categories;
const W = inv.population_nonbuyers / inv.nonbuyers;
const comparedB = Object.values(ev).reduce((n, e) => n + e.customers, 0);
const comparedN = Object.values(ev).reduce((n, e) => n + e.non_buyers_sampled, 0);
const base = comparedB / (comparedB + W * comparedN);
const reached = rows.filter(r => !['Not reached', 'Recording unusable: confirm status'].includes(r.priority));
const notAsked = reached.filter(r => r.experience === 'Not asked' || r.experience.toLowerCase().includes('not stated')).length;
const pricing = rows.filter(r => /discount|final price|price approval|approve.{0,30}price|price.{0,20}approv/i.test(r.next_action + ' ' + r.reason)).length;
const range = e => `${pct(e.rate)} (range ${pct(e.low)}–${pct(e.high)})`;

const pres = new pptxgen();
pres.layout = 'LAYOUT_16x9';
pres.title = 'TrendyTech pilot: how the worklist was built';
pres.theme = {headFontFace: 'Arial', bodyFontFace: 'Arial'};
pres.defineSlideMaster({
  title: 'PLAIN',
  background: {color: 'FFFFFF'},
  objects: [
    {placeholder: {options: {name: 'title', type: 'title', x: 0.6, y: 0.45, w: 8.8, h: 0.9, fontSize: 28, bold: true,
                             color: '111111', valign: 'top', margin: 0}, text: ''}},
    {placeholder: {options: {name: 'body', type: 'body', x: 0.6, y: 1.55, w: 8.8, h: 3.6, fontSize: 20, color: '222222',
                             valign: 'top', margin: 0, paraSpaceAfter: 14}, text: ''}},
  ],
  slideNumber: {x: 9.2, y: 5.2, fontSize: 10, color: '888888'},
});

function slide(title, lines, notes) {
  const s = pres.addSlide({masterName: 'PLAIN'});
  s.addText(title, {placeholder: 'title'});
  s.addText(lines.map((t, i) => ({text: t, options: {bullet: true, breakLine: i < lines.length - 1}})), {placeholder: 'body'});
  if (notes) s.addNotes(notes);
}

slide('TrendyTech pilot: how the worklist was built', [
  `Based on recordings available through ${cutoff}`,
  'What we did, what we tested, what we found',
]);
slide('What the pilot set out to do', [
  'Turn recorded sales calls into a worklist: who to contact, why, and what to say',
  'Base it on how TrendyTech\'s own past leads behaved',
  'Make every statement traceable to a call and a minute',
]);
slide('What we worked with', [
  `${meta.journeys} lead journeys and ${meta.calls} recorded calls for the pilot worklist`,
  `The calls of ${inv.buyers} past customers`,
  `A random sample of ${inv.nonbuyers} of ${inv.population_nonbuyers.toLocaleString('en-IN')} past non-buyers`,
]);
slide('Step 1: calls to text', [
  'Each recording is transcribed, with the two speakers separated',
  'Pilot calls were cross-checked by three independent transcription systems',
]);
slide('Step 2: text to a structured record', [
  'For each call: profile, goals, timing, objections and how they were handled, next step',
  'Every item carries the exact words and the minute they were said',
  'A second automated pass checks every item against the call text',
]);
slide('Step 3: calls to journeys', [
  'All calls of a lead are linked in date order',
  'Only what was said before a purchase is used to learn what leads to buying',
]);
slide('Research: what to look at', [
  'About 40 parameters in 8 groups: experience, role, need, ability to pay, decision, timing, where the lead stands',
  'Only what the lead said counts, not what the counsellor said',
  '"Not asked" is never treated as "no"',
]);
slide('Traps we found and removed', [
  `Only ${pct(inv.buyers_with_pre / inv.buyers_eligible)} of customers had a recorded sales conversation before buying`,
  `${pct(fig.profile_post_share.share)} of what is known about customers was learnt after they paid`,
  'So only facts said before purchase are used in the comparison',
]);
slide('Who was compared', [
  `${comparedB} past customers and ${comparedN} past non-buyers who had a real (3+ minute) sales conversation`,
  `The non-buyer sample is weighted to stand for all ${inv.population_nonbuyers.toLocaleString('en-IN')} non-buyers`,
  `About ${pct(base)} of such leads went on to buy`,
], 'Customers who bought without any recorded sales conversation (for example on WhatsApp) cannot show what was said before buying, so they are not part of the comparison.');
slide('What separates buyers (group findings)', [
  'Saying they will pay or enrol is the strongest signal; declining is the strongest negative',
  `Leads matching TrendyTech's exclusion bought at ${range(ev['Outside target'])}, against ${range(ev.Warm)} for other leads in play`,
  'Profile details such as a data role versus another IT role showed no reliable difference in this data',
]);
slide('Methods we tested', [
  'Nine ways to score a lead, from a points scorecard to machine-learning models',
  'None showed a reliable improvement over the simplest signal',
  'Trained models also cannot show why a lead is ranked, so we chose a fixed, traceable rule',
]);
slide('The rule: first "yes" wins', [
  'Status first: Check status → Recording unusable → Not reached',
  'Then fit and refusal: Outside target → Cold: declined',
  'Then where they stand: Hot (commitment within 45 days) → Dormant (quiet 90+ days) → Warm',
], 'Each lead gets exactly one priority, with a reason, a next action and what to say. Day counts run to the recordings cutoff.');
slide('Guardrails', [
  'A set of leads was kept aside and graded only after the rule was fixed',
  'Every lead was read twice, independently; every quote is checked word for word',
  'No discount or expired offer is presented as available; no personal names in the files',
]);
slide('Results: the 50 open leads', [
  `Hot ${cat.Hot} · Warm ${cat.Warm} · Check status ${cat['Check status']} · Recording unusable ${cat['Recording unusable: confirm status']}`,
  `Dormant ${cat['Dormant: reconfirm']} · Cold ${cat['Cold: declined']} · Outside target ${cat['Outside target']} · Not reached ${cat['Not reached']}`,
  'Each row: reason, next action, what to say, and the call minute it rests on',
]);
slide('What the worklist already shows', [
  `${pricing} of ${rows.length} leads turn on a price or discount decision`,
  `${cat['Not reached']} leads were never reached despite repeated calls; ${cat['Check status']} open leads show signs of an earlier purchase or missed payment`,
  `Experience is not recorded for ${notAsked} of the ${reached.length} leads that were spoken to`,
]);
slide('How to check our work', [
  'Pick any row and play the cited minute of the cited call',
  'Rows stay marked "Recording validation pending" until checked by a person',
  'Categories describe the lead as of the recordings cutoff; confirm current status before acting',
]);
slide('What this is not', [
  'Not a prediction of whether one lead will buy',
  'Group figures are estimates with ranges, based on the pilot sample',
  'Not a replacement for the counsellor\'s judgement on the call',
]);
slide('Next phase', [
  'The same steps on all recorded calls: about ten times as many past non-buyers, so finer findings',
  'Call-quality scoring against an agreed rubric; best time to call once unanswered-call logs are shared',
  'A worklist refreshed for all current open leads',
]);

const out = path.join(root, 'deliverables', 'TrendyTech Pilot Approach.pptx');
pres.writeFile({fileName: out}).then(() => console.log('Approach deck exported to the private deliverables directory.'));
