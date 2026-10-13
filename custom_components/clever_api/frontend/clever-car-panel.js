/*
 * The car page: kilometres driven, charging sessions, where the energy goes, summaries per
 * week, month and year, and service intervals.
 *
 * Driving figures come from the car (the Hyundai/Kia integration's daily statistics and
 * odometer, kept by this integration), charging from Clever. Used as a Home Assistant sidebar
 * panel (the `hass` property is set by Home Assistant) or on any page by setting
 * `provider = {load(), saveCar(settings), save(settings), readOnly}`.
 *
 * The `sections` attribute picks which parts to show, e.g. `sections="service"`.
 */

import './clever-charging-panel.js';   // the battery section is a part of the charging overview

// Starting points only; the right intervals are in the car's service book.
const DEFAULT_SERVICES = [
  {id: 'service', name: 'Service', km: 30000, months: 24, last_km: null, last_date: null},
  {id: 'syn', name: 'Syn', km: null, months: 24, last_km: null, last_date: null},
];
const DEFAULTS = {services: DEFAULT_SERVICES, kmPerLitre: 15};
// A service counts as coming up when this little is left.
const SOON_KM = 1500, SOON_DAYS = 30;
const FAST_KW = 25;
const USES = [
  ['engine_consumption', 'Motor', 'var(--c1)'], ['climate_consumption', 'Klima', 'var(--c2)'],
  ['onboard_electronics_consumption', 'Elektronik', 'var(--c3)'], ['battery_care_consumption', 'Batteripleje', 'var(--c4)'],
];
const WEEKDAYS = ['man', 'tir', 'ons', 'tor', 'fre', 'lør', 'søn'];
const SECTIONS = ['summary', 'readings', 'periods', 'energy', 'habits', 'fun', 'battery', 'service'];
// Why the car was read: the moments of a home charging session the integration reacts to.
const EVENTS = {connected: 'Bilen blev sat til laderen', charging_started: 'Ladning startede', charging_stopped: 'Ladning stoppede', disconnected: 'Bilen blev taget fra laderen', seen: 'Home Assistant startede under en opladning'};

const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const n0 = v => v == null || isNaN(v) ? '–' : Math.round(v).toLocaleString('da-DK');
const n1 = v => v == null || isNaN(v) ? '–' : v.toFixed(1).replace('.', ',');
const two = n => String(n).padStart(2, '0');
const dayKey = d => `${d.getFullYear()}-${two(d.getMonth() + 1)}-${two(d.getDate())}`;
const parseDay = key => new Date(+key.slice(0, 4), +key.slice(5, 7) - 1, +key.slice(8, 10));
const fmtDate = new Intl.DateTimeFormat('da-DK', {day: 'numeric', month: 'long', year: 'numeric'});
const fmtShort = new Intl.DateTimeFormat('da-DK', {day: 'numeric', month: 'short'});
const fmtMonth = new Intl.DateTimeFormat('da-DK', {month: 'short', year: '2-digit'});
const fmtFull = new Intl.DateTimeFormat('da-DK', {day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit'});
const isFast = s => !s.home && s.kwh / Math.max((s.stop - s.start) / 3600, 1 / 60) >= FAST_KW;

// ISO week: the week belongs to the year its Thursday falls in.
function isoWeek(date) {
  const d = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  d.setDate(d.getDate() + 3 - (d.getDay() + 6) % 7);
  const first = new Date(d.getFullYear(), 0, 4);
  return {year: d.getFullYear(), week: 1 + Math.round(((d - first) / 86400000 - 3 + (first.getDay() + 6) % 7) / 7)};
}
const PERIODS = {
  week: {name: 'Uge', key: d => { const w = isoWeek(d); return `${w.year}-U${two(w.week)}`; }, label: k => `uge ${+k.slice(6)}`, keep: 12},
  month: {name: 'Måned', key: d => `${d.getFullYear()}-${two(d.getMonth() + 1)}`, label: k => fmtMonth.format(new Date(+k.slice(0, 4), +k.slice(5) - 1, 1)), keep: 12},
  year: {name: 'År', key: d => String(d.getFullYear()), label: k => k, keep: 10},
};

export function carModel(data, set, period) {
  const now = new Date((data.now || Date.now() / 1000) * 1000), spec = PERIODS[period];
  const driving = Object.entries(data.driving || {}).map(([day, v]) => ({day, date: parseDay(day), ...v})).sort((a, b) => a.day.localeCompare(b.day));
  const odometer = Object.entries(data.odometer || {}).map(([day, km]) => ({day, date: parseDay(day), km})).sort((a, b) => a.day.localeCompare(b.day));
  const sessions = (data.sessions || []).map(s => ({...s, date: new Date(s.start * 1000), fast: isFast(s)}));

  const buckets = {};
  const bucket = date => (buckets[spec.key(date)] ||= {key: spec.key(date), label: spec.label(spec.key(date)), km: null, used: null, days: 0, n: 0, kwh: 0, home: 0, fast: 0, first: null, last: null});
  for (const d of driving) { const b = bucket(d.date); b.km = (b.km || 0) + (d.distance || 0); b.used = (b.used || 0) + (d.total_consumed || 0) / 1000; b.days++; }
  for (const s of sessions) { const b = bucket(s.date); b.n++; b.kwh += s.kwh; if (s.home) b.home += s.kwh; if (s.fast) b.fast += s.kwh; }
  // Without daily statistics, the distance is the change in the odometer within the period.
  for (const o of odometer) { const b = bucket(o.date); b.first ??= o.km; b.last = o.km; }
  const rows = Object.values(buckets).sort((a, b) => a.key.localeCompare(b.key));
  for (const b of rows) {
    if (b.km == null && b.first != null && b.last > b.first) b.km = b.last - b.first;
    b.per100 = b.km && b.used ? b.used / b.km * 100 : null;
  }

  const sum = (list, f) => list.reduce((a, x) => a + (f(x) || 0), 0);
  const cutoff = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 30);
  const recent = driving.filter(d => d.date >= cutoff);
  const kmRecent = sum(recent, d => d.distance), usedRecent = sum(recent, d => d.total_consumed) / 1000;
  const kmPerDay = recent.length ? kmRecent / 30 : null;
  const usedAll = sum(driving, d => d.total_consumed) / 1000, kmAll = sum(driving, d => d.distance);
  const uses = USES.map(([field, name, color]) => ({field, name, color, kwh: sum(driving, d => d[field]) / 1000}));
  const regenerated = sum(driving, d => d.regenerated_energy) / 1000;
  const car = data.vehicle_now || {}, odometerNow = car.odometer ?? (odometer.length ? odometer[odometer.length - 1].km : null);

  const weekdays = WEEKDAYS.map((label, i) => ({label, n: sessions.filter(s => (s.date.getDay() + 6) % 7 === i).length}));
  const hours = Array.from({length: 24}, (_, hour) => ({hour, label: hour % 3 ? '' : two(hour), n: sessions.filter(s => s.date.getHours() === hour).length}));
  const longestDay = driving.reduce((a, d) => !a || (d.distance || 0) > a.distance ? d : a, null);
  const biggest = sessions.reduce((a, s) => !a || s.kwh > a.kwh ? s : a, null);
  const perDay = {};
  for (const s of sessions) perDay[dayKey(s.date)] = (perDay[dayKey(s.date)] || 0) + 1;
  const busiest = Object.entries(perDay).sort((a, b) => b[1] - a[1])[0] || null;
  const kwhAll = sum(sessions, s => s.kwh);

  const services = (set.services || []).map(item => {
    const kmLeft = item.km && item.last_km != null && odometerNow != null ? item.last_km + item.km - odometerNow : null;
    let due = null, daysLeft = null;
    if (item.months && item.last_date) {
      const last = parseDay(item.last_date);
      due = new Date(last.getFullYear(), last.getMonth() + Math.round(item.months), last.getDate());
      daysLeft = Math.round((due - new Date(now.getFullYear(), now.getMonth(), now.getDate())) / 86400000);
    }
    // When the distance will run out at the pace of the last 30 days.
    const kmDays = kmLeft != null && kmPerDay ? Math.round(kmLeft / kmPerDay) : null;
    const expected = [daysLeft, kmDays].filter(v => v != null).sort((a, b) => a - b)[0] ?? null;
    const state = kmLeft == null && daysLeft == null ? 'unknown'
      : (kmLeft != null && kmLeft < 0) || (daysLeft != null && daysLeft < 0) ? 'overdue'
      : (kmLeft != null && kmLeft <= SOON_KM) || (daysLeft != null && daysLeft <= SOON_DAYS) ? 'soon' : 'ok';
    return {...item, kmLeft, due, daysLeft, kmDays, expected, state};
  });
  const next = services.filter(s => s.expected != null).sort((a, b) => a.expected - b.expected)[0] || null;

  // Every time the integration read the car, newest first, with what caused it.
  const readings = Object.entries(data.vehicle || {}).flatMap(([start, list]) => list.map(r => ({...r, session: +start})))
    .sort((a, b) => b.time - a.time || (a.event === 'charging_started' ? -1 : 1));

  return {
    readings,
    rows: rows.slice(-spec.keep).map(b => ({...b})), driving, sessions, car, odometerNow, kmRecent, usedRecent, kmPerDay,
    per100Recent: kmRecent ? usedRecent / kmRecent * 100 : null, kmAll, usedAll, uses, regenerated, weekdays, hours,
    longestDay, biggest, busiest, kwhAll, services, next, days: driving.length,
    from: driving.length ? driving[0].date : null, connectedHours: sum(sessions, s => (s.stop - s.start) / 3600),
  };
}

const STYLE = `
:host { display: block; --ink: var(--primary-text-color, #0b0b0b); --ink2: var(--secondary-text-color, #52514e); --surface: var(--card-background-color, #fcfcfb);
  --page: var(--primary-background-color, #f9f9f7); --line: var(--divider-color, #e1e0d9); --c1: #2a78d6; --c2: #eb6834; --c3: #1baf7a; --c4: #eda100;
  --good: #0ca30c; --warning: #fab219; --critical: #d03b3b;
  color: var(--ink); background: var(--page); font: 15px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; min-height: 100%; }
* { box-sizing: border-box; }
.wrap { max-width: 1180px; margin: 0 auto; padding: 16px 16px 64px; }
header { display: flex; align-items: center; gap: 8px; margin-bottom: 16px; }
h1 { font-size: 22px; margin: 0; } h2 { font-size: 16px; margin: 0 0 12px; } h3 { font-size: 14px; margin: 16px 0 6px; }
.muted { color: var(--ink2); } .small { font-size: 13px; }
.card { background: var(--surface); border: 1px solid var(--line); border-radius: 12px; padding: 16px; }
.section { margin-top: 16px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; }
.tile .label { color: var(--ink2); font-size: 13px; } .tile .value { font-size: 24px; font-weight: 600; margin-top: 2px; } .tile .sub { color: var(--ink2); font-size: 13px; }
.grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 16px; align-items: start; margin-top: 16px; }
@media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
.legend { display: flex; gap: 16px; font-size: 13px; color: var(--ink2); margin-bottom: 8px; flex-wrap: wrap; }
.swatch { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 6px; }
.chart { display: flex; gap: 8px; }
.yaxis { display: flex; flex-direction: column; justify-content: space-between; height: 120px; font-size: 12px; color: var(--ink2); text-align: right; min-width: 34px; font-variant-numeric: tabular-nums; }
.plot { flex: 1; min-width: 0; }
.bars { display: flex; height: 120px; border-bottom: 1px solid var(--ink2); border-top: 1px solid var(--line); }
.col { flex: 1; display: flex; justify-content: center; align-items: flex-end; min-width: 0; }
.col:hover { background: color-mix(in srgb, var(--ink) 6%, transparent); }
.bar { width: 60%; max-width: 24px; border-radius: 4px 4px 0 0; }
.xaxis { display: flex; font-size: 12px; color: var(--ink2); margin-top: 4px; } .xaxis span { flex: 1; text-align: center; white-space: nowrap; min-width: 0; }
.split { display: flex; height: 22px; gap: 2px; border-radius: 4px; overflow: hidden; margin: 8px 0; }
.scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; }
th, td { text-align: left; padding: 6px 10px 6px 0; border-bottom: 1px solid var(--line); vertical-align: top; }
th { color: var(--ink2); font-weight: 500; font-size: 13px; }
.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.tabs { display: flex; gap: 6px; margin-bottom: 12px; }
button { font: inherit; color: var(--ink); cursor: pointer; background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 6px 12px; }
button.on { background: var(--ink); color: var(--page); border-color: var(--ink); }
button.plain { padding: 2px 8px; }
input { background: var(--page); color: var(--ink); border: 1px solid var(--line); border-radius: 6px; padding: 4px 8px; font: inherit; width: 100%; min-width: 0; }
td input[type=number] { width: 92px; } td input[type=date] { width: 150px; } td input[type=text] { width: 150px; }
dl { display: grid; grid-template-columns: max-content 1fr; gap: 4px 14px; font-size: 14px; margin: 0; } dt { color: var(--ink2); } dd { margin: 0; }
.status { display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; }
.dot { width: 9px; height: 9px; border-radius: 50%; background: var(--ink2); flex: none; }
.dot.ok { background: var(--good); } .dot.soon { background: var(--warning); } .dot.overdue { background: var(--critical); }
.note { border-left: 3px solid var(--c4); padding: 8px 12px; margin-bottom: 12px; background: var(--surface); border-radius: 6px; font-size: 14px; }
`;

function bars(rows, key, color, unit, decimals = 0) {
  const show = v => decimals ? n1(v) : n0(v), max = Math.max(decimals ? 0.1 : 1, ...rows.map(r => r[key] || 0));
  return `<div class="chart"><div class="yaxis"><span>${show(max)}</span><span>0</span></div><div class="plot">
    <div class="bars">${rows.map(r => `<div class="col" title="${esc((r.hour != null ? 'kl. ' + two(r.hour) : r.label) + ': ' + (r[key] == null ? 'ingen data' : show(r[key]) + ' ' + unit))}"><div class="bar" style="background:${color};height:${Math.max(0, (r[key] || 0) / max * 100)}%"></div></div>`).join('')}</div>
    <div class="xaxis">${rows.map(r => `<span>${esc(r.label)}</span>`).join('')}</div></div></div>`;
}

const STATE = {ok: ['✓', 'I orden'], soon: ['!', 'Snart'], overdue: ['✕', 'Overskredet'], unknown: ['', 'Mangler oplysninger']};

class CleverCarPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({mode: 'open'});
    this._period = 'month';
    this.shadowRoot.addEventListener('click', ev => this._clicked(ev));
    this.shadowRoot.addEventListener('change', ev => this._changed(ev));
  }

  set hass(hass) { const first = !this._hass; this._hass = hass; if (first) this._start(); }
  set provider(provider) { this._provider = provider; this._start(); }
  set narrow(narrow) { this._narrow = narrow; }

  connectedCallback() { if (!this._data) this.shadowRoot.innerHTML = `<style>${STYLE}</style><div class="wrap"><p class="muted">Henter bilens data…</p></div>`; }

  async _start() {
    const provider = this._provider || {
      load: () => this._hass.callWS({type: 'clever_api/charging/get'}),
      saveCar: settings => this._hass.callWS({type: 'clever_api/car/save_settings', settings}),
    };
    this._save = provider.saveCar;
    // A read-only provider still lets you try other values; they are just not saved.
    this._readOnly = !!provider.readOnly;
    try { this._data = await provider.load(); }
    catch (error) { this.shadowRoot.innerHTML = `<style>${STYLE}</style><div class="wrap"><div class="note">Bilens data kunne ikke hentes: ${esc(error?.message || error)}</div></div>`; return; }
    const saved = this._data.car_settings || {};
    this._set = {...DEFAULTS, ...saved, services: (saved.services || DEFAULT_SERVICES).map(item => ({...item}))};
    this._status = '';
    this._draw();
  }

  _persist() {
    if (this._readOnly) { this._status = 'Du prøver andre værdier. De gemmes ikke.'; return; }
    clearTimeout(this._timer);
    this._timer = setTimeout(async () => {
      try { await this._save(this._set); this._status = 'Gemt. Værdierne er de samme på alle dine enheder.'; }
      catch { this._status = 'Kunne ikke gemmes.'; }
      const el = this.shadowRoot.getElementById('status');
      if (el) el.textContent = this._status;
    }, 500);
  }

  _clicked(ev) {
    const t = ev.target;
    if (t.closest('#allreadings')) { this._allReadings = true; return this._draw(); }
    if (t.closest('[data-period]')) { this._period = t.closest('[data-period]').dataset.period; return this._draw(); }
    if (t.closest('#add')) { this._set.services.push({id: 's' + Date.now().toString(36), name: 'Nyt punkt', km: null, months: 12, last_km: null, last_date: null}); this._persist(); return this._draw(); }
    if (t.closest('[data-remove]')) { this._set.services = this._set.services.filter(s => s.id !== t.closest('[data-remove]').dataset.remove); this._persist(); return this._draw(); }
    if (t.closest('[data-done]')) {
      // Mark as done today, at the current odometer reading.
      const item = this._set.services.find(s => s.id === t.closest('[data-done]').dataset.done), model = carModel(this._data, this._set, this._period);
      item.last_date = dayKey(new Date((this._data.now || Date.now() / 1000) * 1000));
      if (model.odometerNow != null) item.last_km = Math.round(model.odometerNow);
      this._persist(); return this._draw();
    }
  }

  _changed(ev) {
    const t = ev.target;
    if (t.dataset.sv) {
      const [id, field] = t.dataset.sv.split(':'), item = this._set.services.find(s => s.id === id);
      if (!item) return;
      if (field === 'name') item.name = t.value.trim().slice(0, 80) || 'Uden navn';
      else if (field === 'last_date') item.last_date = /^\d{4}-\d{2}-\d{2}$/.test(t.value) ? t.value : null;
      else { const value = parseFloat(t.value.replace(',', '.')); item[field] = t.value.trim() === '' || isNaN(value) ? null : Math.max(0, value); }
    } else if (t.dataset.cs === 'kmPerLitre') {
      const value = parseFloat(t.value.replace(',', '.'));
      this._set.kmPerLitre = isNaN(value) || value <= 0 ? DEFAULTS.kmPerLitre : value;
    } else return;
    this._persist(); this._draw();
  }

  // Keep only the chosen sections, and fill in the battery section from the charging overview.
  _applySections() {
    const root = this.shadowRoot, picked = this.getAttribute('sections');
    const wanted = new Set(picked ? picked.split(/[\s,]+/).filter(Boolean) : SECTIONS);
    root.querySelectorAll('[data-section]').forEach(el => { if (!wanted.has(el.dataset.section)) el.remove(); });
    const pair = root.querySelector('[data-pair]');
    if (pair && pair.children.length < 2) { pair.style.gridTemplateColumns = '1fr'; if (!pair.children.length) pair.remove(); }
    const slot = root.getElementById('battery');
    if (slot) {
      const battery = document.createElement('clever-charging-panel');
      battery.setAttribute('sections', 'battery');
      if (this._provider) battery.provider = this._provider; else battery.hass = this._hass;
      slot.appendChild(battery);
    }
    if (picked) {
      root.querySelector('header')?.remove();
      root.querySelector('.wrap').style.padding = '0';
      this.style.background = 'transparent'; this.style.minHeight = '0';
      const first = root.querySelector('.wrap > :first-child');
      if (first) first.style.marginTop = '0';
    }
  }

  _draw() {
    const set = this._set, data = this._data, m = carModel(data, set, this._period), car = m.car;
    const usedTotal = m.uses.reduce((a, u) => a + u.kwh, 0);
    const petrol = m.kmAll && set.kmPerLitre ? m.kmAll / set.kmPerLitre : null;
    const service = s => `<tr>
      <td><input type="text" data-sv="${esc(s.id)}:name" value="${esc(s.name)}" aria-label="Navn"></td>
      <td><input type="number" step="1000" data-sv="${esc(s.id)}:km" value="${s.km ?? ''}" placeholder="–" aria-label="Interval i km"></td>
      <td><input type="number" step="1" data-sv="${esc(s.id)}:months" value="${s.months ?? ''}" placeholder="–" aria-label="Interval i måneder"></td>
      <td><input type="date" data-sv="${esc(s.id)}:last_date" value="${esc(s.last_date || '')}" aria-label="Sidst udført, dato"></td>
      <td><input type="number" step="1" data-sv="${esc(s.id)}:last_km" value="${s.last_km ?? ''}" placeholder="–" aria-label="Sidst udført, km"></td>
      <td>${s.state === 'unknown' ? '<span class="muted small">udfyld sidst udført</span>' : `${s.kmLeft != null ? `${n0(Math.abs(s.kmLeft))} km ${s.kmLeft < 0 ? 'over' : 'tilbage'}` : ''}${s.kmLeft != null && s.due ? '<br>' : ''}${s.due ? `${esc(fmtDate.format(s.due))}` : ''}
        ${s.kmDays != null && s.kmLeft > 0 ? `<div class="small muted">km nås om ca. ${n0(s.kmDays)} dage</div>` : ''}`}</td>
      <td><span class="status"><span class="dot ${s.state}"></span>${STATE[s.state][0]} ${STATE[s.state][1]}</span></td>
      <td style="white-space:nowrap"><button class="plain" data-done="${esc(s.id)}" title="Sæt sidst udført til i dag og det aktuelle kilometertal">Udført i dag</button> <button class="plain" data-remove="${esc(s.id)}" title="Fjern punktet" aria-label="Fjern ${esc(s.name)}">✕</button></td></tr>`;
    this.shadowRoot.innerHTML = `<style>${STYLE}</style><div class="wrap">
      <header><span id="menu"></span><div><h1>Bil</h1><div class="small muted">Kørsel fra bilen, opladninger fra Clever.</div></div></header>
      ${m.days || m.odometerNow != null ? '' : '<div class="note">Der er endnu ingen data fra bilen. Kørselsstatistik og kilometertal kommer fra Hyundai/Kia-integrationen; uden den vises kun opladningerne.</div>'}
      <div class="tiles" data-section="summary">
        <div class="card tile"><div class="label">Kilometertal</div><div class="value">${m.odometerNow != null ? n0(m.odometerNow) + ' km' : '–'}</div><div class="sub">${car.soc != null ? `batteri ${n0(car.soc)} %` : ''}${car.range != null ? ` · rækkevidde ${n0(car.range)} km` : ''}</div></div>
        <div class="card tile"><div class="label">Kørt seneste 30 dage</div><div class="value">${m.days ? n0(m.kmRecent) + ' km' : '–'}</div><div class="sub">${m.kmPerDay ? `${n0(m.kmPerDay)} km om dagen i snit` : 'kræver kørselsdata'}</div></div>
        <div class="card tile"><div class="label">Forbrug seneste 30 dage</div><div class="value">${m.per100Recent ? n1(m.per100Recent) + ' kWh/100 km' : '–'}</div><div class="sub">${m.days ? `${n0(m.usedRecent)} kWh fra batteriet` : ''}${car.consumption_30d != null ? ` · bilen siger ${n1(car.consumption_30d / 10)}` : ''}</div></div>
        <div class="card tile"><div class="label">Opladninger i alt</div><div class="value">${m.sessions.length}</div><div class="sub">${n0(m.kwhAll)} kWh${m.sessions.length ? ` siden ${esc(fmtShort.format(m.sessions[0].date))}` : ''}</div></div>
        <div class="card tile"><div class="label">Næste på servicelisten</div><div class="value">${m.next ? esc(m.next.name) : '–'}</div><div class="sub">${m.next ? (m.next.expected < 0 ? `overskredet med ${n0(-m.next.expected)} dage` : `om ca. ${n0(m.next.expected)} dage`) : 'udfyld servicelisten nederst'}</div></div>
      </div>

      <div class="card section" data-section="readings"><h2>Aflæsninger fra bilen</h2>
        <p class="small muted" style="margin-top:-6px">Bilen bliver vækket og aflæst, når en hjemmeopladning starter og slutter. ${m.readings.length ? `Senest ${esc(fmtFull.format(new Date(m.readings[0].time * 1000)))}: ${esc(EVENTS[m.readings[0].event] || m.readings[0].event).toLowerCase()}.` : 'Der er endnu ingen aflæsninger.'}</p>
        ${m.readings.length ? `<div class="scroll"><table><tr><th>Tidspunkt</th><th>Anledning</th><th class="num">Batteri</th><th class="num">Kilometertal</th><th class="num">Rækkevidde</th></tr>
          ${m.readings.slice(0, this._allReadings ? 500 : 8).map(r => `<tr><td>${esc(fmtFull.format(new Date(r.time * 1000)))}</td><td>${esc(EVENTS[r.event] || r.event)}</td>
            <td class="num">${r.soc != null ? n0(r.soc) + ' %' : '–'}</td><td class="num">${r.odometer != null ? n0(r.odometer) + ' km' : '–'}</td><td class="num">${r.range != null ? n0(r.range) + ' km' : '–'}</td></tr>`).join('')}</table></div>
          ${m.readings.length > 8 && !this._allReadings ? `<p style="margin-bottom:0"><button id="allreadings">Vis alle ${m.readings.length}</button></p>` : ''}` : ''}</div>

      <div class="card section" data-section="periods"><h2>Opsummering</h2>
        <div class="tabs">${Object.entries(PERIODS).map(([key, p]) => `<button data-period="${key}" class="${this._period === key ? 'on' : ''}">${p.name}</button>`).join('')}</div>
        <div class="grid" style="margin-top:0"><div><h3 style="margin-top:0">Kørte km</h3>${bars(m.rows, 'km', 'var(--c1)', 'km')}</div>
          <div><h3 style="margin-top:0">Ladet kWh</h3>${bars(m.rows, 'kwh', 'var(--c3)', 'kWh')}</div></div>
        <div class="scroll"><table style="margin-top:12px"><tr><th>${PERIODS[this._period].name}</th><th class="num">Km</th><th class="num">Brugt fra batteriet</th><th class="num">kWh/100 km</th><th class="num">Opladninger</th><th class="num">Ladet</th><th class="num">Heraf hjemme</th><th class="num">Heraf hurtiglader</th></tr>
          ${[...m.rows].reverse().map(r => `<tr><td>${esc(r.label)}${r.days && this._period !== 'week' ? ` <span class="small muted">${r.days} dage med kørsel</span>` : ''}</td>
            <td class="num">${r.km != null ? n0(r.km) : '–'}</td><td class="num">${r.used != null ? n0(r.used) + ' kWh' : '–'}</td><td class="num">${r.per100 ? n1(r.per100) : '–'}</td>
            <td class="num">${r.n}</td><td class="num">${n0(r.kwh)} kWh</td><td class="num">${n0(r.home)}</td><td class="num">${n0(r.fast)}</td></tr>`).join('')}</table></div>
        <p class="small muted">Km og forbrug kommer fra bilens daglige kørselsstatistik${m.from ? `, som er gemt siden ${esc(fmtDate.format(m.from))}` : ''}. Opladninger kommer fra Clever og går længere tilbage, så ældre perioder har kun opladninger. "Brugt fra batteriet" er uden ladetab; "Ladet" er det, laderen leverede.</p></div>

      <div class="grid" data-pair>
        <div class="card" data-section="energy"><h2>Hvor strømmen går hen</h2>
          ${usedTotal ? `<div class="split">${m.uses.filter(u => u.kwh > 0).map(u => `<div style="background:${u.color};flex:${u.kwh}" title="${esc(u.name)}: ${n0(u.kwh)} kWh"></div>`).join('')}</div>
            <table>${m.uses.map(u => `<tr><td><span class="swatch" style="background:${u.color}"></span>${u.name}</td><td class="num">${n0(u.kwh)} kWh</td><td class="num">${n1(u.kwh / usedTotal * 100)} %</td></tr>`).join('')}
              <tr><td class="muted">Genvundet ved opbremsning</td><td class="num muted">${n0(m.regenerated)} kWh</td><td class="num muted">${n1(m.regenerated / usedTotal * 100)} %</td></tr></table>
            <p class="small muted">Fordelingen af de ${n0(usedTotal)} kWh, bilen har brugt over ${m.days} dage med kørsel. Det genvundne er trukket fra motorens forbrug af bilen selv.</p>`
            : '<p class="muted small">Kræver bilens kørselsstatistik.</p>'}</div>
        <div class="card" data-section="habits"><h2>Hvornår du lader</h2>
          <h3 style="margin-top:0">Opladninger pr. ugedag</h3>${bars(m.weekdays, 'n', 'var(--c3)', 'opladninger')}
          <h3>Opladninger efter starttidspunkt</h3>${bars(m.hours, 'n', 'var(--c3)', 'opladninger')}</div>
      </div>

      <div class="card section" data-section="fun"><h2>Sjove tal</h2>
        <dl>
          <dt>Længste køredag</dt><dd>${m.longestDay ? `${n0(m.longestDay.distance)} km, ${esc(fmtDate.format(m.longestDay.date))}` : '–'}</dd>
          <dt>Største opladning</dt><dd>${m.biggest ? `${n1(m.biggest.kwh)} kWh, ${esc(fmtFull.format(m.biggest.date))}${m.biggest.home ? ' hjemme' : ' ude'}` : '–'}</dd>
          <dt>Flest opladninger på én dag</dt><dd>${m.busiest ? `${m.busiest[1]}, ${esc(fmtDate.format(parseDay(m.busiest[0])))}` : '–'}</dd>
          <dt>Gennemsnitlig opladning</dt><dd>${m.sessions.length ? `${n1(m.kwhAll / m.sessions.length)} kWh` : '–'}</dd>
          <dt>Tilsluttet en lader</dt><dd>${m.sessions.length ? `${n0(m.connectedHours)} timer i alt, ${n1(m.connectedHours / m.sessions.length)} pr. opladning` : '–'}</dd>
          <dt>Km pr. opladning</dt><dd>${m.kmAll && m.driving.length ? (() => { const within = m.sessions.filter(s => s.date >= m.from).length; return within ? `${n0(m.kmAll / within)} km` : '–'; })() : '–'}</dd>
          <dt>Genvundet af bilen i alt</dt><dd>${car.regenerated != null ? `${n0(car.regenerated / 1000)} kWh${m.per100Recent ? `, nok til ca. ${n0(car.regenerated / 1000 / m.per100Recent * 100)} km` : ''}` : '–'}</dd>
          <dt>Benzin sparet</dt><dd>${petrol != null ? `ca. ${n0(petrol)} liter for de ${n0(m.kmAll)} km med kørselsdata` : '–'} <span class="small muted">ved <input type="number" step="0.5" data-cs="kmPerLitre" value="${set.kmPerLitre}" style="width:64px" aria-label="Km pr. liter"> km/l</span></dd>
          <dt>12 V-batteri</dt><dd>${car.battery_12v != null ? n0(car.battery_12v) + ' %' : '–'}</dd>
        </dl></div>

      <div class="section" data-section="battery" id="battery"></div>

      <div class="card section" data-section="service"><h2>Service og eftersyn</h2>
        <p class="small muted" style="margin-top:-6px">Skriv intervallet i km, måneder eller begge, og hvornår det sidst blev udført. Det, der først løber ud, bestemmer. Intervallerne nedenfor er kun udgangspunkter; de rigtige står i bilens servicebog.</p>
        <div class="scroll"><table><tr><th>Hvad</th><th>For hver km</th><th>For hver måned</th><th>Sidst udført</th><th>Ved km</th><th>Næste gang</th><th>Status</th><th></th></tr>
          ${m.services.map(service).join('') || '<tr><td colspan="8" class="muted">Ingen punkter endnu.</td></tr>'}</table></div>
        <p style="margin-bottom:0"><button id="add">Tilføj punkt</button> <span class="small muted" id="status" aria-live="polite">${esc(this._status || (this._readOnly ? 'Her kan du prøve andre værdier; de gemmes ikke. De gemte rettes på siden Bil i Home Assistant.' : 'Listen gemmes på serveren og er den samme på alle dine enheder.'))}</span></p>
        ${m.kmPerDay ? `<p class="small muted">"Km nås om" regner med de ${n0(m.kmPerDay)} km om dagen, du har kørt i snit de seneste 30 dage.</p>` : ''}</div>
    </div>`;
    this._applySections();
    if (this._hass && customElements.get('ha-menu-button')) {
      const menu = document.createElement('ha-menu-button');
      menu.hass = this._hass; menu.narrow = this._narrow;
      this.shadowRoot.getElementById('menu').appendChild(menu);
    }
  }
}

if (!customElements.get('clever-car-panel')) customElements.define('clever-car-panel', CleverCarPanel);
