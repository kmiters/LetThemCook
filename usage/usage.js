'use strict';

const keys = ['fresh', 'read', 'write', 'output'];
const names = ['Świeże wejście', 'Odczyt cache', 'Zapis cache', 'Wyjście'];
const fmt = new Intl.NumberFormat('pl-PL', {maximumFractionDigits: 0});
const percent = new Intl.NumberFormat('pl-PL', {maximumFractionDigits: 1});
const dateFormat = new Intl.DateTimeFormat('pl-PL', {day: 'numeric', month: 'short', timeZone: 'UTC'});
const label = day => dateFormat.format(new Date(day + 'T12:00:00Z'));
const amount = row => keys.reduce((n, key) => n + row[key], 0);
const usdFormat = new Intl.NumberFormat('pl-PL', {style: 'currency', currency: 'USD', currencyDisplay: 'code'});
const money = n => n === null ? 'Brak wyceny' : n > 0 && n < .005 ? '< 0,01 USD' : usdFormat.format(n);
const zero = () => ({fresh: 0, read: 0, write: 0, output: 0, reasoning: 0,
  usd: {fresh:0, read:0, write:0, output:0}, priced_tokens:0, assumed_write_tokens:0});
function coverage(row) {
  const total = amount(row);
  if (!total) return 'Brak użycia';
  if (total === row.priced_tokens) return '100%';
  const share = 100 * row.priced_tokens / total;
  if (share > 99.95) return '>99,9%';
  if (share > 0 && share < .05) return '<0,1%';
  return percent.format(share) + '%';
}
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

function autoWindow(today, first) {
  const fullDays = first ? (Date.parse(today) - Date.parse(first)) / 86400000 : 0;
  return Math.max(1, Math.min(7, Math.floor(fullDays / 2)));
}
function measure(row, metric) {
  if (metric === 'cost') return amount(row) && !row.priced_tokens ? null : amount(row.usd);
  return metric === 'all' ? amount(row) : row[metric];
}
function periodSum(days, start, end) {
  const total = zero();
  for (let day = start; day <= end; day = shift(day, 1)) {
    if (days.has(day)) add(total, days.get(day));
  }
  return total;
}

function comparison(days, today, n, first, metric = 'all') {
  function average(end) {
    const start = shift(end, 1 - n);
    if (!first || start < first) return null;
    const total = measure(periodSum(days, start, end), metric);
    return total === null ? null : total / n;
  }
  const current = average(shift(today, -1));
  const prior = average(shift(today, -n - 1));
  const change = current !== null && prior > 0 ? 100 * (current - prior) / prior : null;
  return {current, prior, change};
}

if (typeof module !== 'undefined') module.exports = {comparison, shift, autoWindow};
if (typeof document !== 'undefined') startDashboard();

function startDashboard() {
  const el = id => document.getElementById(id);
  let data, selected, visible, daily, first;
  const metric = el('metric');
  const filter = el('filter'), range = el('range'), period = el('period'), dayInput = el('day');

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
    for (const [k, usage] of [...groups].sort((a,b) => (measure(b[1], metric.value) ?? -1) - (measure(a[1], metric.value) ?? -1))) {
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
        const price = usage.priced_tokens ? usage.usd[key] : measure(usage,'cost');
        const breakdown = element('small', names[i] + ' · ');
        breakdown.append(values`${fmt.format(usage[key])} · ${money(price)}`);
        details.append(breakdown);
      }
      name.append(details);
      row.insertCell().append(value(fmt.format(amount(usage))));
      const cost = row.insertCell();
      cost.append(value(money(measure(usage,'cost'))));
      if (usage.priced_tokens && usage.priced_tokens < amount(usage)) cost.append(element('small',value(coverage(usage))));
    }
    host.append(table);
  }
  function draw() {
    const tokenMetric = metric.value === 'cost' ? 'all' : metric.value;
    const tokenLabel = tokenMetric === 'all' ? 'Tokeny łącznie' : names[keys.indexOf(tokenMetric)];
    el('tokens-label').textContent = tokenLabel;
    const rows = visible.map(day => {
      const usage = daily.get(day) || zero();
      const known = first && day >= first;
      const today = day === data.today;
      const tokens = known ? measure(usage,tokenMetric) : null;
      const usd = known ? measure(usage,'cost') : null;
      return {day, today, tokens, usd, tokenLabel, dateLabel:day,
        tickLabel:today ? 'Dziś*' : label(day),
        tokenText:known ? fmt.format(tokens) : 'Brak historii',
        costText:known ? money(usd) : 'Brak historii',
        coverage:known && amount(usage) && usage.priced_tokens < amount(usage) ? coverage(usage) : ''};
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
      values`${fmt.format(amount(row))} tokenów · ${money(measure(row,'cost'))}`);
    if (first && d >= first && row.priced_tokens < amount(row)) dayTotal.append(values` · pokrycie ${coverage(row)}`);
    if (!first || d < first) {
      detail.textContent = 'Przed początkiem dostępnej historii.';
    } else {
      const total = element('div');
      total.append(element('small','Koszt API Standard'), element('span',value(money(measure(row,'cost')))),
                   element('small',values`Pokrycie · ${coverage(row)}`));
      detail.append(total);
      for (const [i, key] of keys.entries()) {
        const item = element('div');
        item.append(element('small',names[i]), element('span',value(fmt.format(row[key]))),
                    element('small', value(measure(row,'cost') === null ? 'Brak wyceny' : money(row.usd[key]))));
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
    const starts = new Map();
    for (const r of sourceRows) {
      if (amount(r) && (!starts.has(r.harness) || starts.get(r.harness) > r.day)) starts.set(r.harness, r.day);
    }
    const comparable = [...starts.values()].sort().pop();
    const automatic = autoWindow(data.today, comparable);
    el('auto-length').replaceChildren(period.value === 'auto' ? values` · ${automatic}` : '');
    for (const control of [range, period]) {
      for (const option of control.options) option.classList.toggle('value', /^\d+$/.test(option.value));
    }
    for (const control of [filter, range, period]) control.classList.toggle('value', control.selectedOptions[0].classList.contains('value'));
    const n = period.value === 'auto' ? automatic : Number(period.value);
    const metricName = metric.selectedOptions[0].textContent;
    el('avg-label').textContent = metric.value === 'cost' ? 'Szacowany koszt API Standard / dzień' : metricName + ' / pełny dzień';
    const {current, prior, change} = comparison(daily, data.today, n, comparable, metric.value);
    const currentSum = periodSum(daily, shift(data.today,-n), shift(data.today,-1));
    const priorSum = periodSum(daily, shift(data.today,-2*n), shift(data.today,-n-1));
    const priceMode = metric.value === 'cost';
    const partial = priceMode && [currentSum, priorSum].some(sum => sum.priced_tokens < amount(sum));
    el('trend-label').textContent = partial ? 'Zmiana wycenionej części' : 'Zmiana średniej';
    el('trend-label').classList.toggle('partial', partial);
    const format = value => priceMode ? money(value) : fmt.format(value);
    const available = start => comparable && shift(data.today,start) >= comparable;
    el('average').replaceChildren(!available(-n) ? 'Brak historii' : current === null ? 'Brak wyceny' : value(format(current)));
    el('change').className = change < 0 ? 'down' : '';
    let trend = 'Brak porównania';
    if (change !== null) trend = change === 0 ? 'Bez zmian' :
      values`${change < 0 ? '↓ Spadek o' : '↑ Wzrost o'} ${percent.format(Math.abs(change)) + '%'}`;
    else if (prior === 0 && current !== null) trend = current === 0 ? 'Bez zmian' : 'Wzrost od zera';
    el('change').replaceChildren(trend);
    const windowLabel = (start, end) => start === end ? label(shift(data.today,start)) : label(shift(data.today,start)) + ' – ' + label(shift(data.today,end));
    el('current-period').replaceChildren(values`${windowLabel(-n,-1)} · ${n} ${n === 1 ? 'dzień' : 'dni'} · bez dzisiaj`);
    const priorValue = !available(-2*n) ? 'niepełna historia' : prior === null ? 'brak wyceny' : format(prior);
    el('comparison').replaceChildren(values`${windowLabel(-2*n,-n-1)} · ${priorValue}`);
    el('coverage').replaceChildren(values`Pokrycie wyceny · ${available(-n) ? coverage(currentSum) : 'brak historii'} w bieżącym okresie · ${available(-2*n) ? coverage(priorSum) : 'brak historii'} w poprzednim.`);
    el('coverage').hidden = !priceMode;
    const start = range.value === 'all' ? first || data.today :
      shift(data.today,range.value === 'compare' ? -2*n : 1-Number(range.value));
    visible = [];
    for (let d = start; d <= data.today; d = shift(d,1)) visible.push(d);
    el('dates').replaceChildren(values`${start} — ${data.today} · *dziś trwa`);
    dayInput.replaceChildren(...visible.slice().reverse().map(d =>
      new Option(d, d)));
    modelTable(rows.filter(r => r.day >= start));
    selectDay(visible.includes(selected) ? selected : data.today);
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
  for (const control of [filter, metric, period, range]) control.addEventListener('change', render);
  dayInput.addEventListener('change', () => selectDay(dayInput.value));
  async function poll() {
    if (!document.hidden) await refresh();
    setTimeout(poll, 60000);
  }
  poll();
}
