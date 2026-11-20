'use strict';

const keys = ['fresh', 'read', 'write', 'output'];
const names = ['Świeże wejście', 'Odczyt cache', 'Zapis cache', 'Wyjście'];
const fmt = new Intl.NumberFormat('pl-PL', {maximumFractionDigits: 0});
const dateFormat = new Intl.DateTimeFormat('pl-PL', {day: 'numeric', month: 'short', timeZone: 'UTC'});
const label = day => dateFormat.format(new Date(day + 'T12:00:00Z'));
const amount = row => keys.reduce((n, key) => n + row[key], 0);
const usdFormat = new Intl.NumberFormat('pl-PL', {style: 'currency', currency: 'USD', currencyDisplay: 'code'});
const money = n => n === null ? 'Brak wyceny' : n > 0 && n < .005 ? '< 0,01 USD' : usdFormat.format(n);
const zero = () => ({fresh: 0, read: 0, write: 0, output: 0, reasoning: 0,
  usd: {fresh:0, read:0, write:0, output:0}, priced_tokens:0, assumed_write_tokens:0});
const partiallyPriced = row => amount(row) > row.priced_tokens;
const cost = row => amount(row) && !row.priced_tokens ? null : amount(row.usd);
function add(target, row) {
  for (const k of [...keys, 'reasoning', 'priced_tokens', 'assumed_write_tokens']) target[k] += row[k] || 0;
  for (const k of keys) target.usd[k] += row.usd?.[k] || 0;
  return target;
}

// Calendar arithmetic on already-local ISO dates stays independent of DST and browser timezone.
function shift(day, n) {
  const d = new Date(day + 'T12:00:00Z');
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

function shiftMonths(day, n) {
  const [year, month, date] = day.split('-').map(Number);
  const first = new Date(Date.UTC(year, month - 1 + n, 1, 12));
  const last = new Date(Date.UTC(first.getUTCFullYear(), first.getUTCMonth() + 1, 0, 12)).getUTCDate();
  first.setUTCDate(Math.min(date, last));
  return first.toISOString().slice(0, 10);
}

function periodBounds(today, period) {
  if (period === 'day') return [today, today];
  if (period === 'week') return [shift(today, -6), today];
  if (period === 'month') return [shiftMonths(today, -1), today];
  if (period === 'quarter') return [shiftMonths(today, -3), today];
  if (period === 'year') return [shiftMonths(today, -12), today];
  return [today.slice(0, 4) + '-01-01', today];
}

function periodSum(days, start, end) {
  const total = zero();
  for (let day = start; day <= end; day = shift(day, 1)) {
    if (days.has(day)) add(total, days.get(day));
  }
  return total;
}

function periodSummary(first, days, requestedStart, end) {
  const start = first && requestedStart < first ? first : requestedStart;
  return {start, total:periodSum(days, start, end), historyAvailable:Boolean(first && first <= end),
    partialHistory:Boolean(first && first > requestedStart && first <= end)};
}

if (typeof module !== 'undefined') module.exports = {shift, shiftMonths, periodBounds, periodSummary, periodSum, zero, add, cost, partiallyPriced};
if (typeof document !== 'undefined') startDashboard();

function startDashboard() {
  const el = id => document.getElementById(id);
  let data, selected, visible, daily, first;
  const filter = el('filter'), period = el('period'), dayInput = el('day');

  function element(tag, text) {
    const node = document.createElement(tag);
    if (text !== undefined) node.append(text);
    return node;
  }
  function value(text) {
    if (!/\d/.test(text)) return String(text);
    const span = element('span', String(text));
    span.className = 'value';
    return span;
  }
  // Callers explicitly identify measurements. Model identifiers stay intact.
  function values(parts, ...measurements) {
    const fragment = document.createDocumentFragment();
    parts.forEach((part, i) => {
      fragment.append(part);
      if (i < measurements.length) fragment.append(value(measurements[i]));
    });
    return fragment;
  }
  function options() {
    const choice = filter.value;
    filter.replaceChildren(new Option('Wszystkie', '[]'));
    for (const harness of Object.keys(data.sources)) {
      const group = element('optgroup');
      group.label = harness;
      group.append(new Option(harness + ' · wszystkie modele', JSON.stringify([harness])));
      const models = data.rows.filter(row => row.harness === harness).map(row => row.model);
      for (const model of [...new Set(models)].sort()) {
        const option = new Option(model, JSON.stringify([harness, model]));
        if (model !== 'Model nieznany') option.className = 'value';
        group.append(option);
      }
      filter.append(group);
    }
    if (Array.from(filter.options).some(o => o.value === choice)) filter.value = choice;
  }
  function modelTable(rows) {
    const host = el('models');
    const expanded = new Set([...host.querySelectorAll('details[open]')].map(d => d.dataset.model));
    host.replaceChildren();
    const groups = new Map();
    for (const row of rows) {
      const k = JSON.stringify([row.harness, row.model]);
      groups.set(k, add(groups.get(k) || zero(), row));
    }
    if (!groups.size) {
      host.textContent = 'Brak zarejestrowanego użycia w tym zakresie.';
      return;
    }
    const table = element('table');
    const head = table.createTHead().insertRow();
    const columns = ['Model', 'Tokeny łącznie', 'Koszt API'];
    for (const name of columns) {
      const th = element('th', name); th.scope = 'col'; head.append(th);
    }
    const body = table.createTBody();
    for (const [k, usage] of [...groups].sort((a,b) => (cost(b[1]) ?? -1) - (cost(a[1]) ?? -1))) {
      const [source, model] = JSON.parse(k);
      const row = body.insertRow();
      const name = row.insertCell();
      const details = element('details');
      details.dataset.model = k;
      details.open = expanded.has(k);
      const summary = element('summary');
      const provider = source === 'Codex' && model.startsWith('gpt-') ? 'openai'
        : source === 'Claude Code' && model.startsWith('claude-') ? 'claude' : null;
      if (provider) {
        const mark = element('img');
        mark.src = '/marks/' + provider + '.svg';
        mark.alt = '';
        mark.className = 'provider-mark ' + provider;
        summary.append(mark);
      }
      const identifier = element('span',model);
      if (model !== 'Model nieznany') identifier.className = 'value';
      summary.append(identifier);
      details.append(summary, element('small', source));
      for (const [i,key] of keys.entries()) {
        const price = cost(usage) === null ? null : usage.usd[key];
        const breakdown = element('small', names[i] + ' · ');
        breakdown.append(values`${fmt.format(usage[key])} · ${money(price)}`);
        details.append(breakdown);
      }
      name.append(details);
      row.insertCell().append(value(fmt.format(amount(usage))));
      const costCell = row.insertCell();
      costCell.append(value(money(cost(usage))));
      if (cost(usage) !== null && partiallyPriced(usage)) costCell.append(element('small','Częściowa wycena'));
    }
    host.append(table);
  }
  function draw() {
    el('tokens-label').textContent = 'Tokeny łącznie';
    const rows = visible.map(day => {
      const usage = daily.get(day) || zero();
      const known = first && day >= first;
      const today = day === data.today;
      const tokens = known ? amount(usage) : null;
      const usd = known ? cost(usage) : null;
      return {day, today, tokens, usd, tokenLabel:'Tokeny łącznie', dateLabel:day,
        tickLabel:today ? 'Dziś*' : label(day),
        tokenText:known ? fmt.format(tokens) : 'Brak historii',
        costText:known ? money(usd) : 'Brak historii'};
    });
    window.renderUsageHistory({rows, selected, selectDay});
  }
  function selectDay(d) {
    selected = dayInput.value = d;
    const row = daily.get(d) || zero();
    const detail = el('day-detail');
    detail.replaceChildren();
    const dayTotal = el('day-total');
    dayTotal.replaceChildren(!first || d < first ? 'Brak historii' :
      values`${fmt.format(amount(row))} tokenów · ${money(cost(row))}`);
    if (first && d >= first && cost(row) !== null && partiallyPriced(row)) dayTotal.append(' · częściowa wycena');
    if (!first || d < first) {
      detail.textContent = 'Przed początkiem dostępnej historii.';
    } else {
      const total = element('div');
      total.append(element('small','Koszt API Standard'), element('span',value(money(cost(row)))));
      if (cost(row) !== null && partiallyPriced(row)) total.append(element('small','Częściowa wycena'));
      detail.append(total);
      for (const [i, key] of keys.entries()) {
        const item = element('div');
        item.append(element('small',names[i]), element('span',value(fmt.format(row[key]))),
                    element('small', value(cost(row) === null ? 'Brak wyceny' : money(row.usd[key]))));
        detail.append(item);
      }
      const note = element('p',values`Łącznie ${fmt.format(amount(row))} tokenów · w wyjściu ${fmt.format(row.reasoning)} tokenów rozumowania.`);
      if (row.assumed_write_tokens) note.append(values` Dla ${fmt.format(row.assumed_write_tokens)} zapisanych tokenów przyjęto krótszy czas cache.`);
      detail.append(note);
    }
    draw();
  }
  function render() {
    const [source, model] = JSON.parse(filter.value);
    const sourceRows = data.rows.filter(r => (!source || r.harness === source) && r.day <= data.today);
    first = sourceRows.reduce((min, r) => !min || r.day < min ? r.day : min, null);
    const rows = sourceRows.filter(r => !model || r.model === model);
    daily = new Map();
    for (const r of rows) daily.set(r.day, add(daily.get(r.day) || zero(), r));
    for (const control of [filter, period]) control.classList.toggle('value', control.selectedOptions[0].classList.contains('value'));
    const [requestedStart, end] = periodBounds(data.today, period.value);
    const summary = periodSummary(first, daily, requestedStart, end);
    const {start} = summary;
    const inRange = rows.filter(r => r.day >= requestedStart && r.day <= end);
    const {total, historyAvailable, partialHistory} = summary;
    el('average').replaceChildren(!historyAvailable ? 'Brak historii' : cost(total) === null ? 'Brak wyceny' : value(money(cost(total))));
    el('current-period').replaceChildren(values`${requestedStart} — ${end}${end === data.today ? ' · dziś trwa' : ''}${partialHistory ? ' · historia od ' + first : ''}`);
    const pricingStatus = !historyAvailable ? 'Brak historii dla wybranego źródła.' : amount(total) === 0 ? 'Brak użycia w tym okresie.' :
      cost(total) !== null && partiallyPriced(total) ? 'Częściowa wycena.' : '';
    el('pricing-status').textContent = pricingStatus;
    visible = [];
    for (let d = start; d <= end; d = shift(d,1)) visible.push(d);
    dayInput.replaceChildren(...visible.slice().reverse().map(d =>
      new Option(d, d)));
    modelTable(inRange);
    selectDay(visible.includes(selected) ? selected : end);
  }
  async function refresh() {
    try {
      const reply = await fetch('/api/usage', {cache:'no-store'});
      if (!reply.ok) throw Error();
      const next = await reply.json();
      const changed = data?.updated !== next.updated;
      data = next;
      el('status').hidden = true;
      if (changed) {
        options();
        el('pricing-note').replaceChildren(values`Wersja cennika API Standard · ${data.pricing_version}`);
        const sources = el('sources');
        sources.replaceChildren();
        for (const [name, source] of Object.entries(data.sources)) {
          if (sources.childNodes.length) sources.append(' / ');
          sources.append(name + ' · ', source.available ? values`${source.files} plików JSONL` : 'brak katalogu');
          if (source.errors) sources.append(values` · błędy odczytu: ${source.errors}`);
          if (source.skipped) sources.append(values` · pominięte rekordy: ${source.skipped}`);
        }
        sources.append('.');
        el('dashboard').hidden = false;
        render();
      }
    } catch {
      el('status').hidden = false;
      el('status').textContent = 'Dane chwilowo niedostępne. Kolejna próba za minutę.';
    }
  }
  for (const control of [filter, period]) control.addEventListener('change', render);
  dayInput.addEventListener('change', () => selectDay(dayInput.value));
  async function poll() {
    if (!document.hidden) await refresh();
    setTimeout(poll, 60000);
  }
  poll();
}
