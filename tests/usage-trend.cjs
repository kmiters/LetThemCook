const assert = require('node:assert/strict');
const {shift, shiftMonths, periodBounds, periodSummary, periodSum, zero, add, cost, partiallyPriced} = require('../usage/usage.js');

assert.deepEqual(periodBounds('2026-10-26','day'), ['2026-10-26','2026-10-26']);
assert.deepEqual(periodBounds('2026-10-26','week'), ['2026-10-20','2026-10-26']);
assert.deepEqual(periodBounds('2026-10-26','month'), ['2026-09-26','2026-10-26']);
assert.deepEqual(periodBounds('2026-10-26','quarter'), ['2026-07-26','2026-10-26']);
assert.deepEqual(periodBounds('2026-10-26','year'), ['2025-10-26','2026-10-26']);
assert.deepEqual(periodBounds('2026-10-26','ytd'), ['2026-01-01','2026-10-26']);
assert.equal(shiftMonths('2024-03-31',-1), '2024-02-29');
assert.equal(shiftMonths('2025-03-31',-1), '2025-02-28');
assert.equal(shiftMonths('2024-02-29',-12), '2023-02-28');
assert.equal(periodBounds('2024-01-02','ytd')[0], '2024-01-01');
assert.equal(shift('2026-03-29',1), '2026-03-30');

// Fixture: source history is known even though the selected model has no activity.
const sourceRows = [{day:'2026-10-20', model:'model-a'}];
const selectedModelDays = new Map();
const sourceStart = sourceRows.reduce((min, row) => !min || row.day < min ? row.day : min, null);
const inactiveModel = periodSummary(sourceStart, selectedModelDays, '2026-10-20', '2026-10-26');
assert.equal(inactiveModel.historyAvailable, true);
assert.equal(inactiveModel.total.fresh, 0);
assert.equal(cost(inactiveModel.total), 0);
assert.equal(partiallyPriced(inactiveModel.total), false);

const empty = periodSummary(null, new Map(), '2026-10-20', '2026-10-26');
assert.equal(empty.historyAvailable, false);
const later = periodSummary('2026-10-24', new Map(), '2026-10-20', '2026-10-26');
assert.equal(later.start, '2026-10-24');
assert.equal(later.partialHistory, true);

const priced = {...zero(), fresh:100, priced_tokens:80, usd:{fresh:.25,read:0,write:0,output:0}};
assert.equal(cost(priced), .25);
assert.equal(partiallyPriced(priced), true);
const unpriced = {...zero(), output:20};
assert.equal(cost(unpriced), null);
assert.equal(partiallyPriced(unpriced), true);

const days = new Map([['2026-10-25', priced], ['2026-10-26', {...zero(), output:20}]]);
const total = periodSum(days, '2026-10-25', '2026-10-26');
assert.equal(total.fresh, 100);
assert.equal(total.output, 20);
assert.equal(cost(total), .25);
assert.equal(partiallyPriced(total), true);
console.log('Period checks passed (calendar boundaries, zero, missing history, partial and absent pricing, date arithmetic).');
