/*
 * Clever charging overview: what has been charged, what Clever has cost, and what the
 * same energy would have cost with a charger of your own.
 *
 * Used as a Home Assistant sidebar panel (the `hass` property is set by Home Assistant)
 * and usable on any page by setting `provider = {load(), save(settings), readOnly}` instead.
 *
 * The `sections` attribute picks which parts to show, e.g. `sections="battery"`. Without it
 * everything is shown except the battery section, which belongs on the car page.
 * All figures are calculated here in the browser from the data described in README.md.
 */

const DEFAULTS = {
  power: 11, addon: 0.23, vat: 25, publicFast: 3.99, publicAc: 3.49, chargerPrice: 9000, lifetime: 10,
  extraMonthly: 0, km: 20000, kwh100: 17, homeShare: null, sinceHome: true, months: {},
  // When home charging takes place: within a fixed window, in the cheapest hours, or from plug-in.
  mode: 'window', windowFrom: 0, windowTo: 6, useMeasured: true,
  // Share of the energy from the charger that ends up in the battery, and the battery's size when new.
  efficiency: 90, nominalCapacity: null,
};
// Rates Clever has published. Other months are estimated, or filled in by the user.
const KNOWN_SURCHARGE = {'2025-12': 0, '2026-01': 0.12, '2026-08': 0.29, '2026-09': 0.44};
const KNOWN_REFUND = {'2026-08': 1.203, '2026-09': 1.27};
// Clever One list prices per month (DKK): with a home charger before and from 1 September 2026, and without one.
const FEE_FROM_2026_09 = 849, FEE_BEFORE = 799, FEE_WITHOUT_CHARGER = 899;
// The surcharge applies to the part of the month's average purchase price above this (DKK/kWh).
const SURCHARGE_THRESHOLD = 0.89;
// Away from home, a session averaging at least this power counts as fast charging.
const FAST_KW = 25;
const SECTIONS = ['summary', 'cost', 'energy', 'price', 'battery', 'months', 'explain', 'sessions', 'assumptions', 'year'];
const PATTERNS = {window: 'Fast tidsrum', cheapest: 'Billigste timer', now: 'Straks ved tilslutning', measured: 'Målte ladetimer'};
const two = n => String(n).padStart(2, '0');
const windowText = set => `kl. ${two(set.windowFrom)}–${two(set.windowTo)}`;
const inWindow = (hour, set) => set.windowFrom === set.windowTo ? true
  : set.windowFrom < set.windowTo ? hour >= set.windowFrom && hour < set.windowTo : hour >= set.windowFrom || hour < set.windowTo;
const patternText = (key, set) => key === 'window' ? `${PATTERNS.window} ${windowText(set)}` : PATTERNS[key];
// The assumptions live on the server, so every device shows the same. The browser only holds
// a copy of values the server could not save, and sends them again the next time the page opens.
const STORAGE_KEY = 'clever_api.charging_unsaved';

function readUnsaved() {
  try { const value = JSON.parse(localStorage.getItem(STORAGE_KEY)); return value && typeof value === 'object' ? value : null; }
  catch { return null; }   // private windows and blocked storage
}
function writeUnsaved(settings) {
  try { if (settings) localStorage.setItem(STORAGE_KEY, JSON.stringify(settings)); else localStorage.removeItem(STORAGE_KEY); return true; }
  catch { return false; }
}

const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const kr0 = v => v == null || isNaN(v) ? '–' : Math.round(v).toLocaleString('da-DK');
const kr2 = v => v == null || isNaN(v) ? '–' : v.toFixed(2).replace('.', ',');
const nice = v => Number.isInteger(v) ? String(v) : kr2(v);
const kr3 = v => v == null || isNaN(v) ? '–' : v.toFixed(3).replace('.', ',');
const fmtMonth = new Intl.DateTimeFormat('da-DK', {month: 'short', year: '2-digit'});
const fmtLongMonth = new Intl.DateTimeFormat('da-DK', {month: 'long', year: 'numeric'});
const fmtFull = new Intl.DateTimeFormat('da-DK', {day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit'});
const fmtHour = new Intl.DateTimeFormat('da-DK', {weekday: 'short', hour: '2-digit', minute: '2-digit'});
const monthKey = ts => { const d = new Date(ts * 1000); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`; };
const monthDate = k => new Date(+k.slice(0, 4), +k.slice(5) - 1, 1);
const isFast = s => !s.home && s.kwh / Math.max((s.stop - s.start) / 3600, 1 / 60) >= FAST_KW;

/* Hour by hour, what one home session costs on your own meter under each charging pattern:
   `now` fills from the moment the car is plugged in, `cheapest` picks the cheapest hours while it
   is connected, `window` uses the chosen hours of the day first, and `measured` is the energy
   that was actually recorded per hour, when the integration has followed the session. */
export function sessionPlan(s, set, spot, fallback, profile) {
  const priceAt = t => { const raw = spot.prices[Math.floor((t - spot.start) / 3600)]; return {raw, spot: raw ?? fallback, price: ((raw ?? fallback) + set.addon) * (1 + set.vat / 100)}; };
  const slots = [];
  for (let t = Math.floor(s.start / 3600) * 3600; t < Math.max(s.stop, s.start + 1); t += 3600) {
    const hours = (Math.min(t + 3600, s.stop) - Math.max(t, s.start)) / 3600;
    if (hours <= 0) continue;
    const at = priceAt(t);
    slots.push({t, hours, hour: new Date(t * 1000).getHours(), spot: at.spot, estimated: at.raw == null, price: at.price, now: 0, cheapest: 0, window: 0, measured: 0});
  }
  if (!slots.length) { const at = priceAt(s.start); slots.push({t: s.start, hours: 1, hour: new Date(s.start * 1000).getHours(), spot: at.spot, estimated: true, price: at.price, now: 0, cheapest: 0, window: 0, measured: 0}); }
  const average = slots.reduce((a, x) => a + x.price * x.hours, 0) / slots.reduce((a, x) => a + x.hours, 0);
  let overflow = 0;
  const fill = (order, field) => {
    let left = s.kwh, cost = 0;
    for (const slot of order) {
      const kwh = Math.min(left, slot.hours * set.power);
      slot[field] = kwh; cost += kwh * slot.price; left -= kwh;
      if (left <= 1e-9) break;
    }
    // More energy than the connection time allows at this power: the rest is priced at the session average.
    overflow = Math.max(0, left);
    return cost + overflow * average;
  };
  const cost = {
    now: fill(slots, 'now'),
    cheapest: fill([...slots].sort((a, b) => a.price - b.price), 'cheapest'),
    // The window's hours first, in order; whatever does not fit there is charged from plug-in.
    window: fill([...slots.filter(x => inWindow(x.hour, set)), ...slots.filter(x => !inWindow(x.hour, set))], 'window'),
    measured: null,
  };
  if (profile) {
    let kwh = 0, sum = 0;
    for (const [hour, value] of Object.entries(profile)) {
      const t = +hour, slot = slots.find(x => x.t === t);
      if (slot) slot.measured = value;
      kwh += value; sum += value * priceAt(t).price;
    }
    // Only trusted when nearly all of the session's energy was recorded; then scaled to the billed energy.
    if (kwh >= 0.9 * s.kwh && kwh > 0) cost.measured = sum * s.kwh / kwh;
  }
  return {slots, cost, overflow, average};
}

export function buildModel(data, set) {
  const {sessions, surcharges, spot} = data;
  const months = {}, order = [], spotSum = {};
  spot.prices.forEach((p, i) => { if (p == null) return; const k = monthKey(spot.start + i * 3600); (spotSum[k] ||= [0, 0]); spotSum[k][0] += p; spotSum[k][1]++; });
  const overall = Object.values(spotSum).reduce((a, x) => [a[0] + x[0], a[1] + x[1]], [0, 0]);
  const fallback = overall[1] ? overall[0] / overall[1] : 0.8;
  const firstHome = sessions.find(s => s.home), firstHomeMonth = firstHome ? monthKey(firstHome.start) : null;
  if (sessions.length) {
    const d = new Date(sessions[0].start * 1000), end = new Date((data.now || Date.now() / 1000) * 1000);
    for (let y = d.getFullYear(), m = d.getMonth(); y < end.getFullYear() || (y === end.getFullYear() && m <= end.getMonth()); m === 11 ? (y++, m = 0) : m++) {
      const k = `${y}-${String(m + 1).padStart(2, '0')}`; order.push(k);
      months[k] = {key: k, label: fmtMonth.format(monthDate(k)), n: 0, kwh: 0, home: 0, out: 0, outFast: 0, outAc: 0, homeNow: 0, homeCheapest: 0, homeWindow: 0, homeChosen: 0, measured: 0,
        spot: spotSum[k] ? spotSum[k][0] / spotSum[k][1] : null};
    }
  }
  // Recorded energy per hour, keyed by the session's start; matched to a session within two minutes.
  const profiles = Object.entries(data.profiles || {}).map(([start, hours]) => ({start: +start, hours}));
  const rows = sessions.map(s => {
    const m = months[monthKey(s.start)], fast = isFast(s);
    const profile = s.home ? profiles.find(x => Math.abs(x.start - s.start) <= 120)?.hours : null;
    const plan = s.home ? sessionPlan(s, set, spot, fallback, profile) : null;
    const pattern = plan ? (set.useMeasured && plan.cost.measured != null ? 'measured' : set.mode) : null;
    if (m) {
      m.n++; m.kwh += s.kwh;
      if (s.home) {
        m.home += s.kwh; m.homeNow += plan.cost.now; m.homeCheapest += plan.cost.cheapest; m.homeWindow += plan.cost.window;
        m.homeChosen += plan.cost[pattern]; if (pattern === 'measured') m.measured++;
      } else { m.out += s.kwh; m[fast ? 'outFast' : 'outAc'] += s.kwh; }
    }
    return {...s, fast, plan, pattern, homeCost: plan ? plan.cost[pattern] : null, outCost: s.home ? null : s.kwh * (fast ? set.publicFast : set.publicAc)};
  });
  for (const k of order) {
    const m = months[k], own = set.months[k] || {};
    m.feeDefault = k >= '2026-09' ? FEE_FROM_2026_09 : firstHomeMonth && k >= firstHomeMonth ? FEE_BEFORE : FEE_WITHOUT_CHARGER;
    m.fee = own.fee ?? m.feeDefault;
    m.surchargeEstimate = m.spot == null ? 0 : Math.max(0, Math.round((m.spot * 1.25 - SURCHARGE_THRESHOLD) * 100) / 100);
    [m.surchargeDefault, m.surchargeSource] = surcharges[k] != null ? [surcharges[k], 'fra Clever-appen']
      : KNOWN_SURCHARGE[k] != null ? [KNOWN_SURCHARGE[k], 'oplyst af Clever'] : [m.surchargeEstimate, 'anslået ud fra spotprisen'];
    m.surcharge = own.surcharge ?? m.surchargeDefault;
    if (own.surcharge != null) m.surchargeSource = 'indtastet';
    m.refundDefault = KNOWN_REFUND[k] ?? null;
    m.refund = own.refund ?? m.refundDefault;
    m.counted = !set.sinceHome || !firstHomeMonth || k >= firstHomeMonth;
    m.surchargeCost = m.surcharge * m.kwh;
    // With a known refund rate you pay for the home electricity and get the rate back per kWh.
    // Without one, the refund is taken to cover exactly what the electricity cost.
    // Both solutions are priced with the same charging pattern.
    m.refundNet = m.refund == null ? 0 : m.homeChosen - m.refund * m.home;
    m.clever = m.fee + m.surchargeCost + m.refundNet;
    m.depreciation = set.chargerPrice / (set.lifetime * 12) + set.extraMonthly;
    m.ownHome = m.homeChosen;
    m.ownOut = m.outFast * set.publicFast + m.outAc * set.publicAc;
    m.own = m.ownHome + m.ownOut + m.depreciation;
  }
  const list = order.map(k => months[k]), counted = list.filter(m => m.counted);
  const sum = f => counted.reduce((a, m) => a + f(m), 0);
  const total = {n: sum(m => m.n), kwh: sum(m => m.kwh), home: sum(m => m.home), out: sum(m => m.out), outFast: sum(m => m.outFast), outAc: sum(m => m.outAc),
    clever: sum(m => m.clever), own: sum(m => m.own), ownHome: sum(m => m.ownHome), ownOut: sum(m => m.ownOut), homeNow: sum(m => m.homeNow), homeCheapest: sum(m => m.homeCheapest), homeWindow: sum(m => m.homeWindow), measured: sum(m => m.measured), surchargeCost: sum(m => m.surchargeCost), months: counted.length};
  total.homePrice = total.home ? total.ownHome / total.home : (fallback + set.addon) * (1 + set.vat / 100);
  total.outPrice = total.out ? total.ownOut / total.out : set.publicFast;
  total.surchargeRate = total.kwh ? total.surchargeCost / total.kwh : 0;
  total.homeShare = total.kwh ? total.home / total.kwh : 0;
  // A year at the expected mileage, priced with the averages of the period shown.
  const fee = counted.length ? counted[counted.length - 1].fee : FEE_FROM_2026_09;
  const share = (set.homeShare ?? Math.round(total.homeShare * 100)) / 100, kwhYear = set.km * set.kwh100 / 100;
  const perKwhOwn = share * total.homePrice + (1 - share) * total.outPrice, fixedOwn = set.chargerPrice / set.lifetime + set.extraMonthly * 12;
  const year = {fee, share, kwhYear, perKwhOwn, fixedOwn, clever: fee * 12 + total.surchargeRate * kwhYear, own: fixedOwn + perKwhOwn * kwhYear,
    even: perKwhOwn > total.surchargeRate ? (fee * 12 - fixedOwn) / ((perKwhOwn - total.surchargeRate) * set.kwh100 / 100) : null};
  // Through the day: the average price of each hour, and where the home energy lands with the pattern in use.
  const keys = new Set(counted.map(m => m.key)), day = Array.from({length: 24}, (_, hour) => ({hour, label: hour % 3 ? '' : two(hour), sum: 0, n: 0, kwh: 0}));
  spot.prices.forEach((price, i) => { const t = spot.start + i * 3600; if (price == null || !keys.has(monthKey(t))) return; const d = day[new Date(t * 1000).getHours()]; d.sum += (price + set.addon) * (1 + set.vat / 100); d.n++; });
  for (const row of rows) if (row.plan && keys.has(monthKey(row.start))) for (const slot of row.plan.slots) day[slot.hour].kwh += slot[row.pattern] || 0;
  for (const d of day) d.price = d.n ? d.sum / d.n : null;
  total.allHoursPrice = day.reduce((a, d) => a + d.sum, 0) / Math.max(1, day.reduce((a, d) => a + d.n, 0));
  total.homeSessions = rows.filter(r => r.plan && keys.has(monthKey(r.start))).length;
  return {months: list, shown: counted, rows, total, year, day, firstHomeMonth, fallback, vehicle: vehicleModel(data, set, rows)};
}

/* What the car's own readings say about the battery and the consumption. A reading holds the
   battery level and odometer at a moment in a home session: connected, charging started or
   stopped, disconnected, or `seen` when the session was already running at start-up. */
export function vehicleModel(data, set, rows) {
  const readings = Object.entries(data.vehicle || {}).map(([start, list]) => ({start: +start, list}));
  const car = [];
  for (const row of rows) {
    const list = row.home ? readings.find(x => Math.abs(x.start - row.start) <= 120)?.list : null;
    if (!list?.length) continue;
    const levels = list.filter(r => r.soc != null), first = levels[0], top = levels.reduce((a, r) => !a || r.soc >= a.soc ? r : a, null);
    // Only a reading from the start of the session says what the battery held before charging.
    const fromStart = first && (first.event === 'connected' || first.event === 'charging_started');
    const gain = fromStart && top.time > first.time ? top.soc - first.soc : null;
    // Small top-ups are left out: one percentage point of rounding would dominate.
    const usable = gain != null && gain >= 20 ? row.kwh * (set.efficiency / 100) / (gain / 100) : null;
    car.push({start: row.start, kwh: row.kwh, socStart: fromStart ? first.soc : null, socEnd: top ? top.soc : null, gain, usable, odometer: list.find(r => r.odometer != null)?.odometer ?? null});
  }
  const withOdometer = car.filter(c => c.odometer != null);
  withOdometer.forEach((c, i) => { if (i) c.km = c.odometer - withOdometer[i - 1].odometer; });
  let consumption = null;
  if (withOdometer.length >= 2) {
    // Everything charged from the first reading up to the last one, at home and away, over the distance driven.
    const a = withOdometer[0], b = withOdometer[withOdometer.length - 1], km = b.odometer - a.odometer;
    const kwh = rows.filter(r => r.start >= a.start - 120 && r.start < b.start - 120).reduce((sum, r) => sum + r.kwh, 0);
    if (km >= 50) consumption = {km, kwh, per100: kwh / km * 100, from: a.start, to: b.start};
  }
  const estimates = car.map(c => c.usable).filter(v => v != null).sort((x, y) => x - y);
  return {car, consumption, estimates: estimates.length, median: estimates.length ? estimates[Math.floor(estimates.length / 2)] : null, now: data.vehicle_now || {}};
}

function vehicleHtml(model, set, field) {
  const v = model.vehicle, now = v.now, has = now.soc != null || now.odometer != null || v.car.length;
  if (!has) return '';
  const share = v.median != null && set.nominalCapacity ? ` (${kr0(v.median / set.nominalCapacity * 100)} % af ${nice(set.nominalCapacity)} kWh)` : '';
  const recent = [...v.car].reverse().slice(0, 12);
  return `<div class="card section" data-section="battery"><h2>Bil og batteri</h2>
    <p class="small muted" style="margin-top:-6px">Bilen aflæses, når en hjemmeopladning starter og slutter. Sammen med de kWh, Clever har målt, viser det forbruget og giver et skøn over batteriets brugbare kapacitet.</p>
    <dl><dt>Batteri nu</dt><dd>${now.soc != null ? kr0(now.soc) + ' %' : '–'}${now.range != null ? ` <span class="muted small">rækkevidde ${kr0(now.range)} km</span>` : ''}</dd>
      <dt>Kilometertal</dt><dd>${now.odometer != null ? kr0(now.odometer) + ' km' : '–'}</dd>
      <dt>Målt forbrug</dt><dd>${v.consumption ? `<b>${kr2(v.consumption.per100)} kWh/100 km</b> <span class="muted small">${kr0(v.consumption.kwh)} kWh ladet over ${kr0(v.consumption.km)} km, inkl. ladetab</span>` : '<span class="muted">kræver aflæsninger fra mindst to opladninger og 50 km imellem</span>'}</dd>
      <dt>Brugbar kapacitet</dt><dd>${v.median != null ? `<b>ca. ${kr2(v.median)} kWh</b>${share} <span class="muted small">median af ${v.estimates} opladninger</span>` : '<span class="muted">kræver en opladning på mindst 20 procentpoint, aflæst fra start</span>'}</dd></dl>
    <div style="margin-top:10px">${field('efficiency', 'Andel af strømmen der når batteriet', '%')}${field('nominalCapacity', 'Batteriets kapacitet som ny', 'kWh')}</div>
    ${recent.length ? `<div class="scroll"><table style="margin-top:8px"><tr><th>Opladning</th><th class="num">Batteri</th><th class="num">kWh fra Clever</th><th class="num">Anslået kapacitet</th><th class="num">Km siden forrige</th></tr>
      ${recent.map(c => `<tr><td>${esc(fmtFull.format(new Date(c.start * 1000)))}</td>
        <td class="num">${c.socStart != null ? kr0(c.socStart) + ' %' : '?'} → ${c.socEnd != null ? kr0(c.socEnd) + ' %' : '?'}</td><td class="num">${kr2(c.kwh)}</td>
        <td class="num">${c.usable != null ? kr2(c.usable) + ' kWh' : '–'}</td><td class="num">${c.km != null ? kr0(c.km) : '–'}</td></tr>`).join('')}</table></div>`
      : '<p class="small muted">Der er endnu ingen aflæsninger knyttet til en opladning. De kommer, næste gang bilen sættes til laderen.</p>'}
    <p class="small muted">Anslået kapacitet = kWh fra Clever × ${kr0(set.efficiency)} % ÷ stigningen i batteriniveau. Clever måler strømmen før ladetabet, og bilen oplyser kun hele procenter, så det enkelte tal er usikkert; det er udviklingen over tid, der siger noget. Kulde og forvarmning trækker tallet ned.</p>
    ${v.consumption ? `<p class="small muted">Under "Et år frem" regnes der med ${nice(set.kwh100)} kWh/100 km. Det målte forbrug er ${kr2(v.consumption.per100)}.</p>` : ''}</div>`;
}

const STYLE = `
:host { display: block; --ink: var(--primary-text-color, #0b0b0b); --ink2: var(--secondary-text-color, #52514e); --surface: var(--card-background-color, #fcfcfb);
  --page: var(--primary-background-color, #f9f9f7); --line: var(--divider-color, #e1e0d9); --c1: #2a78d6; --c2: #eb6834; --c3: #1baf7a; --c4: #eda100;
  color: var(--ink); background: var(--page); font: 15px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; min-height: 100%; }
* { box-sizing: border-box; }
.wrap { max-width: 1180px; margin: 0 auto; padding: 16px 16px 64px; }
header { display: flex; align-items: center; gap: 8px; margin-bottom: 16px; }
h1 { font-size: 22px; margin: 0; } h2 { font-size: 16px; margin: 0 0 12px; } h3 { font-size: 14px; margin: 16px 0 6px; }
.muted { color: var(--ink2); } .small { font-size: 13px; }
.card { background: var(--surface); border: 1px solid var(--line); border-radius: 12px; padding: 16px; }
.section { margin-top: 16px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px; }
.tile .label { color: var(--ink2); font-size: 13px; } .tile .value { font-size: 24px; font-weight: 600; margin-top: 2px; } .tile .sub { color: var(--ink2); font-size: 13px; }
.grid { display: grid; grid-template-columns: minmax(0, 1fr) 340px; gap: 16px; align-items: start; margin-top: 16px; }
@media (max-width: 960px) { .grid { grid-template-columns: 1fr; } }
.legend { display: flex; gap: 16px; font-size: 13px; color: var(--ink2); margin-bottom: 8px; flex-wrap: wrap; }
.swatch { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 6px; }
.chart { display: flex; gap: 8px; }
.yaxis { display: flex; flex-direction: column; justify-content: space-between; height: 130px; font-size: 12px; color: var(--ink2); text-align: right; min-width: 34px; font-variant-numeric: tabular-nums; }
.plot { flex: 1; min-width: 0; }
.bars { display: flex; height: 130px; border-bottom: 1px solid var(--ink2); border-top: 1px solid var(--line); }
.col { flex: 1; display: flex; justify-content: center; align-items: flex-end; gap: 2px; min-width: 0; }
.col.stack { flex-direction: column; justify-content: flex-end; align-items: center; }
.col:hover { background: color-mix(in srgb, var(--ink) 6%, transparent); }
.bar { width: 38%; max-width: 24px; border-radius: 4px 4px 0 0; }
.col.stack .bar { width: 60%; border-radius: 0; } .col.stack .bar:first-child { border-radius: 4px 4px 0 0; }
.xaxis { display: flex; font-size: 12px; color: var(--ink2); margin-top: 4px; } .xaxis span { flex: 1; text-align: center; white-space: nowrap; min-width: 0; }
.scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; }
th, td { text-align: left; padding: 6px 10px 6px 0; border-bottom: 1px solid var(--line); vertical-align: top; }
th { color: var(--ink2); font-weight: 500; font-size: 13px; }
.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.field { display: flex; gap: 6px; align-items: center; margin: 6px 0; font-size: 14px; }
.field label { flex: 1; } .field .unit { width: 62px; color: var(--ink2); font-size: 13px; }
.undo { width: 26px; flex: none; } button.undo { padding: 2px 0; line-height: 1.2; }
select { background: var(--page); color: var(--ink); border: 1px solid var(--line); border-radius: 6px; padding: 4px 6px; font: inherit; max-width: 170px; }
input[type=number] { width: 92px; background: var(--page); color: var(--ink); border: 1px solid var(--line); border-radius: 6px; padding: 4px 8px; font: inherit; }
td input[type=number] { width: 84px; }
button { font: inherit; color: var(--ink); cursor: pointer; background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 6px 12px; }
dl { display: grid; grid-template-columns: max-content 1fr; gap: 4px 14px; font-size: 14px; margin: 10px 0 0; } dt { color: var(--ink2); } dd { margin: 0; }
.calc { font-size: 14px; } .calc td, .calc th { padding: 4px 10px 4px 0; } .calc tr.sum td { font-weight: 600; border-top: 1px solid var(--ink2); }
.note { border-left: 3px solid var(--c4); padding: 8px 12px; margin-bottom: 12px; background: var(--surface); border-radius: 6px; font-size: 14px; }
ul { margin: 6px 0; padding-left: 20px; } li { margin: 3px 0; }
details > summary { cursor: pointer; font-weight: 600; font-size: 16px; }
`;

function barChart(rows, series, {stacked = false, unit = '', decimals = 0} = {}) {
  const show = v => decimals ? kr2(v) : kr0(v);
  const max = Math.max(decimals ? 0.01 : 1, ...rows.map(r => stacked ? series.reduce((a, s) => a + (r[s.key] || 0), 0) : Math.max(...series.map(s => r[s.key] || 0))));
  const cols = rows.map(r => {
    const title = `${r.hour != null ? 'kl. ' + two(r.hour) : r.label}: ` + series.map(s => `${s.name} ${show(r[s.key])} ${unit}`).join(', ');
    const bars = series.map(s => `<div class="bar" style="background:${s.color};height:${Math.max(0, (r[s.key] || 0) / max * 100)}%"></div>`);
    return `<div class="col ${stacked ? 'stack' : ''}" title="${esc(title)}">${(stacked ? bars.reverse() : bars).join('')}</div>`;
  }).join('');
  return `<div class="legend">${series.map(s => `<span><span class="swatch" style="background:${s.color}"></span>${esc(s.name)}</span>`).join('')}</div>
    <div class="chart"><div class="yaxis"><span>${show(max)}</span><span>0</span></div>
      <div class="plot"><div class="bars">${cols}</div><div class="xaxis">${rows.map(r => `<span>${esc(r.label)}</span>`).join('')}</div></div></div>`;
}

/* The assumptions and worked examples, written out with the numbers currently on the page. */
function explainHtml(model, set, data) {
  const t = model.total, y = model.year, area = data.spot.area || '';
  const month = [...model.shown].reverse().find(m => m.kwh > 0 && m.key !== monthKey(data.now || Date.now() / 1000)) || [...model.shown].reverse().find(m => m.kwh > 0);
  const session = [...model.rows].reverse().find(s => s.home && s.plan && s.plan.slots.length <= 30);
  const chosen = set.useMeasured && t.measured ? `${patternText(set.mode, set).toLowerCase()}, og de målte ladetimer hvor de findes` : patternText(set.mode, set).toLowerCase();
  const line = (label, calc, value) => `<tr><td>${label}</td><td class="muted">${calc}</td><td class="num">${value}</td></tr>`;
  let monthHtml = '<p class="muted small">Der er endnu ingen måned med opladninger at vise.</p>';
  if (month) {
    const m = month, name = fmtLongMonth.format(monthDate(m.key));
    monthHtml = `<p class="small muted">${esc(name)}: ${kr0(m.kwh)} kWh, heraf ${kr0(m.home)} hjemme, ${kr0(m.outFast)} på hurtiglader og ${kr0(m.outAc)} på almindelig ladestander ude.</p>
      <div class="scroll"><table class="calc"><tr><th>Clever</th><th>Udregning</th><th class="num">kr.</th></tr>
        ${line('Abonnement', 'fast pris pr. måned', kr0(m.fee))}
        ${line('Energitillæg', `${kr2(m.surcharge)} kr./kWh × ${kr0(m.kwh)} kWh <span class="small">(${esc(m.surchargeSource)})</span>`, kr0(m.surchargeCost))}
        ${m.refund == null ? line('Strøm hjemme og tilbagebetaling', 'satsen er ukendt, så de antages at gå lige op', '0')
          : line('Strøm hjemme', `${kr0(m.home)} kWh til timeprisen (${esc(chosen)})`, kr0(m.homeChosen)) + line('Tilbagebetaling fra Clever', `${kr3(m.refund)} kr./kWh × ${kr0(m.home)} kWh`, '−' + kr0(m.refund * m.home))}
        <tr class="sum"><td>Clever i alt</td><td></td><td class="num">${kr0(m.clever)}</td></tr></table></div>
      <div class="scroll"><table class="calc" style="margin-top:12px"><tr><th>Egen ladeboks</th><th>Udregning</th><th class="num">kr.</th></tr>
        ${line('Strøm hjemme', `${kr0(m.home)} kWh til timeprisen (${esc(chosen)}) = ${kr2(m.home ? m.ownHome / m.home : null)} kr./kWh`, kr0(m.ownHome))}
        ${line('Hurtigladning ude', `${kr2(set.publicFast)} kr./kWh × ${kr0(m.outFast)} kWh`, kr0(m.outFast * set.publicFast))}
        ${line('Almindelig ladestander ude', `${kr2(set.publicAc)} kr./kWh × ${kr0(m.outAc)} kWh`, kr0(m.outAc * set.publicAc))}
        ${line('Afskrivning', `${kr0(set.chargerPrice)} kr. ÷ (${nice(set.lifetime)} år × 12)${set.extraMonthly ? ` + ${kr0(set.extraMonthly)} kr. løbende` : ''}`, kr0(m.depreciation))}
        <tr class="sum"><td>Egen ladeboks i alt</td><td></td><td class="num">${kr0(m.own)}</td></tr></table></div>
      ${m.spot != null ? `<p class="small muted">Skøn over energitillægget for ${esc(name)}: månedens gennemsnitlige spotpris ${kr3(m.spot)} kr./kWh × 1,25 (moms) = ${kr3(m.spot * 1.25)}. Den del, der ligger over ${kr2(SURCHARGE_THRESHOLD)}, er ${kr2(m.surchargeEstimate)} kr./kWh. Skønnet bruges kun, når Clevers egen sats ikke kendes.</p>` : ''}`;
  }
  let sessionHtml = '<p class="muted small">Der er endnu ingen hjemmeopladning at vise.</p>';
  if (session) {
    const p = session.plan, cols = ['now', 'window', 'cheapest', ...(p.cost.measured != null ? ['measured'] : [])];
    const short = {now: 'Straks', window: `Kl. ${two(set.windowFrom)}–${two(set.windowTo)}`, cheapest: 'Billigste', measured: 'Målt'};
    const head = key => `${short[key]}${key === session.pattern ? ' ✓' : ''}`;
    sessionHtml = `<p class="small muted">Hjemmeopladningen ${esc(fmtFull.format(new Date(session.start * 1000)))}: ${kr2(session.kwh)} kWh, bilen var tilsluttet i ${kr2((session.stop - session.start) / 3600)} timer. Med ${nice(set.power)} kW kan der højst lades ${nice(set.power)} kWh pr. hel time. ✓ markerer det mønster, sammenligningen bruger.</p>
      <div class="scroll"><table class="calc"><tr><th>Time fra</th><th class="num">Spotpris</th><th class="num">Pris inkl. tillæg og moms</th>${cols.map(k => `<th class="num">${head(k)}</th>`).join('')}</tr>
        ${p.slots.map(x => `<tr><td>${esc(fmtHour.format(new Date(x.t * 1000)))}${x.hours < 0.999 ? ` <span class="muted small">(${Math.round(x.hours * 60)} min.)</span>` : ''}</td>
          <td class="num">${kr3(x.spot)}${x.estimated ? '*' : ''}</td><td class="num">${kr3(x.price)}</td>
          ${cols.map(k => `<td class="num">${x[k] ? kr2(x[k]) + ' kWh' : ''}</td>`).join('')}</tr>`).join('')}
        <tr class="sum"><td>Pris for opladningen</td><td></td><td></td>${cols.map(k => `<td class="num">${kr2(p.cost[k])} kr.</td>`).join('')}</tr>
        <tr><td class="muted">Pris pr. kWh</td><td></td><td></td>${cols.map(k => `<td class="num muted">${kr2(p.cost[k] / session.kwh)}</td>`).join('')}</tr></table></div>
      <p class="small muted">Prisen i hver time er (spotpris + ${kr2(set.addon)}) × ${kr2(1 + set.vat / 100)}. Første time i eksemplet: (${kr3(p.slots[0].spot)} + ${kr2(set.addon)}) × ${kr2(1 + set.vat / 100)} = ${kr3(p.slots[0].price)} kr./kWh.${p.slots.some(x => x.estimated) ? ' * Timeprisen mangler, så periodens gennemsnit er brugt.' : ''}${p.overflow > 0.01 ? ` De sidste ${kr2(p.overflow)} kWh kan ikke nås på den tid med ${nice(set.power)} kW og er regnet til opladningens gennemsnitspris.` : ''} Med fast tidsrum lades først i tidsrummet; det, der ikke kan nås dér, lades fra tilslutning.</p>`;
  }
  return `<div class="card section" data-section="explain"><details open><summary>Antagelser og regneeksempler</summary>
    <h3>Antagelser</h3>
    <ul class="small">
      <li><b>Opladninger</b> kommer fra Clever: start, slut, kWh og om det var på din egen ladeboks. Clever oplyser ikke, i hvilke timer der faktisk blev ladet, kun hvornår bilen var tilsluttet.</li>
      <li><b>Lademønster hjemme:</b> ${esc(chosen)}. Det samme mønster bruges for Clever og for egen ladeboks. Integrationen måler de faktiske ladetimer for opladninger, den har fulgt fra start; ${t.measured} af ${t.homeSessions} hjemmeopladninger i perioden er målt.</li>
      <li><b>Timepriser</b> er spotpriser for ${esc(area)} fra Energi Data Service, uden moms. Oven i lægges ${kr2(set.addon)} kr./kWh for nettarif, systemtarif og elafgift, og derefter ${kr0(set.vat)} % moms.</li>
      <li><b>Abonnement</b> er Clevers listepris: ${FEE_FROM_2026_09} kr./md. med ladeboks fra september 2026, ${FEE_BEFORE} kr. før, og ${FEE_WITHOUT_CHARGER} kr. i måneder før din første hjemmeopladning. Ret den i tabellen, hvis du betaler noget andet.</li>
      <li><b>Energitillæg</b> betales af alle kWh, hjemme og ude. Satsen hentes fra Clever-appen for den aktuelle måned, tages fra Clevers offentliggjorte satser, hvor de kendes, og anslås ellers ud fra månedens spotpris.</li>
      <li><b>Tilbagebetaling:</b> med Clevers ladeboks betaler du selv strømmen over din måler og får en sats pr. kWh tilbage. Kendes satsen ikke, antages tilbagebetalingen at dække præcis det, strømmen kostede.</li>
      <li><b>Udeladning uden abonnement</b> koster ${kr2(set.publicFast)} kr./kWh for hurtig- og lynladning og ${kr2(set.publicAc)} kr./kWh på almindelige ladestandere. En opladning ude tæller som hurtigladning, når den i snit gav mindst ${FAST_KW} kW.</li>
      <li><b>Egen ladeboks</b> koster ${kr0(set.chargerPrice)} kr. inkl. installation og afskrives lineært over ${nice(set.lifetime)} år uden restværdi${set.extraMonthly ? `, plus ${kr0(set.extraMonthly)} kr. om måneden` : ''}.</li>
      <li><b>Samme adfærd:</b> der regnes, som om du med egen ladeboks ladede de samme steder og lige så meget. I fremskrivningen kan andelen ladet hjemme ændres.</li>
    </ul>
    <h3>Regneeksempel: én måned</h3>${monthHtml}
    <h3>Regneeksempel: én hjemmeopladning</h3>${sessionHtml}
    <h3>Regneeksempel: et år frem</h3>
    <div class="scroll"><table class="calc"><tr><th></th><th>Udregning</th><th class="num">kr.</th></tr>
      ${line('Forbrug', `${kr0(set.km)} km × ${nice(set.kwh100)} kWh/100 km`, kr0(y.kwhYear) + ' kWh')}
      ${line('Clever: abonnement', `${kr0(y.fee)} kr. × 12`, kr0(y.fee * 12))}
      ${line('Clever: energitillæg', `${kr2(t.surchargeRate)} kr./kWh × ${kr0(y.kwhYear)} kWh`, kr0(t.surchargeRate * y.kwhYear))}
      <tr class="sum"><td>Clever pr. år</td><td></td><td class="num">${kr0(y.clever)}</td></tr>
      ${line('Egen: strøm hjemme', `${kr0(y.share * 100)} % × ${kr0(y.kwhYear)} kWh × ${kr2(t.homePrice)} kr./kWh`, kr0(y.share * y.kwhYear * t.homePrice))}
      ${line('Egen: udeladning', `${kr0((1 - y.share) * 100)} % × ${kr0(y.kwhYear)} kWh × ${kr2(t.outPrice)} kr./kWh`, kr0((1 - y.share) * y.kwhYear * t.outPrice))}
      ${line('Egen: afskrivning', `${kr0(set.chargerPrice)} kr. ÷ ${nice(set.lifetime)} år${set.extraMonthly ? ` + 12 × ${kr0(set.extraMonthly)} kr.` : ''}`, kr0(y.fixedOwn))}
      <tr class="sum"><td>Egen ladeboks pr. år</td><td></td><td class="num">${kr0(y.own)}</td></tr></table></div>
    <p class="small muted">Priserne pr. kWh er gennemsnittene i den viste periode: ${kr2(t.homePrice)} hjemme, ${kr2(t.outPrice)} ude (din egen blanding af hurtig og almindelig) og ${kr2(t.surchargeRate)} i energitillæg.
      ${y.even != null && y.even > 0 ? `Skæringspunktet er der, hvor de to løsninger koster det samme: (${kr0(y.fee * 12)} − ${kr0(y.fixedOwn)}) ÷ ((${kr2(y.perKwhOwn)} − ${kr2(t.surchargeRate)}) × ${nice(set.kwh100)} ÷ 100) = ca. ${kr0(y.even)} km om året.` : ''}</p>
  </details></div>`;
}

class CleverChargingPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({mode: 'open'});
    this._all = false;
    this.shadowRoot.addEventListener('change', ev => this._changed(ev));
    this.shadowRoot.addEventListener('click', ev => {
      if (ev.target.closest('#all')) { this._all = true; this._draw(); }
      if (ev.target.closest('#reset')) this._reset();
      if (ev.target.closest('#restore')) this._restore();
      const one = ev.target.closest('[data-default]');
      if (one) { this._set[one.dataset.default] = DEFAULTS[one.dataset.default]; this._persist(); this._draw(); }
    });
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) this._start();
  }

  set provider(provider) { this._provider = provider; this._start(); }
  set narrow(narrow) { this._narrow = narrow; }

  connectedCallback() { if (!this._data) this.shadowRoot.innerHTML = `<style>${STYLE}</style><div class="wrap"><p class="muted">Henter opladninger…</p></div>`; }

  async _start() {
    const provider = this._provider || {
      load: () => this._hass.callWS({type: 'clever_api/charging/get'}),
      save: settings => this._hass.callWS({type: 'clever_api/charging/save_settings', settings}),
    };
    this._save = provider.save;
    try {
      this._data = await provider.load();
    } catch (error) {
      this.shadowRoot.innerHTML = `<style>${STYLE}</style><div class="wrap"><div class="note">Opladningerne kunne ikke hentes: ${esc(error?.message || error)}</div></div>`;
      return;
    }
    // A read-only provider still lets you try other values; they are just not saved.
    this._readOnly = !!provider.readOnly;
    // Values the server could not save last time are used and sent again; otherwise the server's copy.
    const unsaved = this._readOnly ? null : readUnsaved();
    this._apply(unsaved || this._data.settings);
    this._draw();
    if (unsaved) this._persist();
  }

  _apply(saved) {
    saved = {...(saved || {})};
    delete saved.smart;   // replaced by `mode` in 0.8.0
    this._set = {...DEFAULTS, ...saved, months: {...(saved.months || {})}};
    this._status = '';
  }

  // Back to the values that are saved on the server.
  _restore() {
    this._apply(this._data.settings);
    this._draw();
  }

  _persist() {
    if (this._readOnly) { this._status = 'Du prøver andre værdier. De gemmes ikke.'; return; }
    clearTimeout(this._timer);
    this._timer = setTimeout(async () => {
      try {
        await this._save(this._set);
        writeUnsaved(null);
        this._status = 'Gemt. Værdierne er de samme på alle dine enheder.';
      } catch {
        this._status = writeUnsaved(this._set)
          ? 'Kunne ikke gemmes på serveren. Værdierne er kun gemt i denne browser og sendes igen, næste gang siden åbnes.'
          : 'Kunne ikke gemmes.';
      }
      const el = this.shadowRoot.getElementById('status');
      if (el) el.textContent = this._status;
    }, 500);
  }

  _reset() {
    this._set = {...DEFAULTS, months: {}};
    this._persist();
    this._draw();
  }

  _changed(ev) {
    const t = ev.target, set = this._set;
    if (!set || !(t.dataset.cs || t.dataset.cm)) return;
    const num = t.value.trim() === '' ? null : parseFloat(t.value.replace(',', '.'));
    if (t.dataset.cs) {
      const key = t.dataset.cs;
      if (t.type === 'checkbox') set[key] = t.checked;
      else if (t.tagName === 'SELECT') set[key] = t.value in PATTERNS ? t.value : DEFAULTS[key];
      else if (key === 'windowFrom' || key === 'windowTo') set[key] = num == null || isNaN(num) ? DEFAULTS[key] : Math.min(23, Math.max(0, Math.round(num)));
      else if (num == null || isNaN(num)) set[key] = DEFAULTS[key];
      else set[key] = key === 'lifetime' || key === 'power' ? Math.max(0.5, num) : Math.max(0, num);
    } else {
      const [month, field] = t.dataset.cm.split(':'), own = set.months[month] ||= {};
      if (num == null || isNaN(num)) delete own[field]; else own[field] = Math.max(0, num);
      if (!Object.keys(own).length) delete set.months[month];
    }
    this._persist();
    this._draw();
  }

  // Keep only the chosen sections. A page that picks sections brings its own heading.
  _applySections() {
    const root = this.shadowRoot, picked = this.getAttribute('sections');
    const wanted = new Set(picked ? picked.split(/[\s,]+/).filter(Boolean) : SECTIONS.filter(name => name !== 'battery'));
    root.querySelectorAll('[data-section]').forEach(el => { if (!wanted.has(el.dataset.section)) el.remove(); });
    const main = root.querySelector('.main'), aside = root.querySelector('.aside'), grid = root.querySelector('.grid');
    if (aside && !aside.children.length) { aside.remove(); if (grid) grid.style.gridTemplateColumns = '1fr'; }
    if (main && !main.children.length) { main.remove(); if (grid) grid.style.gridTemplateColumns = '1fr'; }
    if (picked) {
      root.querySelector('header')?.remove();
      root.querySelector('.wrap').style.padding = '0';
      this.style.background = 'transparent'; this.style.minHeight = '0';
      // The first card of a column should not push itself down.
      root.querySelectorAll('.main > :first-child, .aside > :first-child').forEach(el => { el.style.marginTop = '0'; });
      if (grid && !root.querySelector('.tiles')) grid.style.marginTop = '0';
    }
  }

  _draw() {
    const set = this._set, data = this._data, model = buildModel(data, set), t = model.total, y = model.year, shown = model.shown;
    // A value that differs from the default gets a button that puts the default back.
    const undo = key => set[key] !== DEFAULTS[key] ? `<button class="undo" data-default="${key}" title="Tilbage til standardværdien ${esc(String(PATTERNS[DEFAULTS[key]] ?? DEFAULTS[key] ?? 'fra dine opladninger').replace('.', ','))}" aria-label="Tilbage til standardværdien">↺</button>` : '<span class="undo"></span>';
    const field = (key, label, unit, step = 'any') => `<div class="field"><label>${label}</label><input type="number" step="${step}" data-cs="${key}" value="${set[key] ?? ''}"><span class="unit">${unit}</span>${undo(key)}</div>`;
    const select = (key, label, options) => `<div class="field"><label>${label}</label><select data-cs="${key}">${Object.entries(options).map(([value, text]) => `<option value="${value}" ${set[key] === value ? 'selected' : ''}>${esc(text)}</option>`).join('')}</select>${undo(key)}</div>`;
    const check = (key, label) => `<label class="field"><input type="checkbox" data-cs="${key}" ${set[key] ? 'checked' : ''}> ${label}</label>`;
    const diff = t.own - t.clever, sessions = [...model.rows].reverse().slice(0, this._all ? 2000 : 15);
    this.shadowRoot.innerHTML = `<style>${STYLE}</style><div class="wrap">
      <header><span id="menu"></span><div><h1>Opladning</h1><div class="small muted">Dine opladninger hos Clever, og hvad de samme kWh ville have kostet med din egen ladeboks.</div></div></header>
      ${data.sessions.length ? '' : '<div class="note">Der er endnu ingen opladninger. De dukker op, når Clever har rapporteret den første afsluttede opladning.</div>'}
      ${data.spot.prices.length ? '' : '<div class="note">Timepriserne kunne ikke hentes fra Energi Data Service, så hjemmestrøm er regnet med en fast gennemsnitspris.</div>'}
      <div class="tiles" data-section="summary">
        <div class="card tile"><div class="label">Ladet i perioden</div><div class="value">${kr0(t.kwh)} kWh</div><div class="sub">${t.n} opladninger over ${t.months} måneder</div></div>
        <div class="card tile"><div class="label">Hjemme</div><div class="value">${kr0(t.homeShare * 100)} %</div><div class="sub">${kr0(t.home)} kWh hjemme, ${kr0(t.outFast)} hurtiglader, ${kr0(t.outAc)} anden udeladning</div></div>
        <div class="card tile"><div class="label">Clever har kostet</div><div class="value">${kr0(t.clever)} kr.</div><div class="sub">${kr2(t.kwh ? t.clever / t.kwh : null)} kr./kWh</div></div>
        <div class="card tile"><div class="label">Egen ladeboks ville koste</div><div class="value">${kr0(t.own)} kr.</div><div class="sub">${kr2(t.kwh ? t.own / t.kwh : null)} kr./kWh</div></div>
        <div class="card tile"><div class="label">${diff >= 0 ? 'Clever er billigere med' : 'Egen ladeboks er billigere med'}</div><div class="value">${kr0(Math.abs(diff))} kr.</div><div class="sub">i perioden, med dine forudsætninger</div></div>
      </div>
      <div class="grid"><div class="main">
        <div class="card" data-section="cost"><h2>Pris pr. måned</h2>${barChart(shown, [{key: 'clever', name: 'Clever', color: 'var(--c1)'}, {key: 'own', name: 'Egen ladeboks', color: 'var(--c2)'}], {unit: 'kr.'})}</div>
        <div class="card section" data-section="energy"><h2>Ladet pr. måned</h2>${barChart(shown, [{key: 'home', name: 'Hjemme', color: 'var(--c3)'}, {key: 'out', name: 'Ude', color: 'var(--c4)'}], {stacked: true, unit: 'kWh'})}</div>
        <div class="card section" data-section="price"><h2>Hvad strømmen koster hjemme</h2>
          <p class="small muted" style="margin-top:-6px">Prisen pr. kWh på din egen måler for periodens ${kr0(t.home)} kWh hjemme, alt efter hvornår der lades. Priserne er inkl. ${kr2(set.addon)} kr./kWh i tariffer og afgifter og ${kr0(set.vat)} % moms.</p>
          <div class="scroll"><table><tr><th>Lademønster</th><th class="num">kr./kWh</th><th class="num">I alt</th><th></th></tr>
            ${[['window', t.homeWindow], ['cheapest', t.homeCheapest], ['now', t.homeNow]].map(([key, cost]) => `<tr><td>${esc(patternText(key, set))}</td><td class="num">${kr2(t.home ? cost / t.home : null)}</td><td class="num">${kr0(cost)} kr.</td><td class="small muted">${key === set.mode ? 'valgt' : ''}</td></tr>`).join('')}
            ${t.measured ? `<tr><td>Med målte ladetimer hvor de findes (${t.measured} af ${t.homeSessions} opladninger)</td><td class="num">${kr2(t.home ? t.ownHome / t.home : null)}</td><td class="num">${kr0(t.ownHome)} kr.</td><td class="small muted">${set.useMeasured ? 'bruges' : ''}</td></tr>` : ''}
            <tr><td class="muted">Til sammenligning: gennemsnit af alle timer i perioden</td><td class="num muted">${kr2(t.allHoursPrice)}</td><td></td><td></td></tr>
          </table></div>
          <p class="small muted"><b>Sammenligningen bruger ${kr2(t.homePrice)} kr./kWh.</b> ${t.measured ? '' : 'Ingen opladninger er målt endnu; de faktiske ladetimer registreres fra nu af, mens bilen lader.'}</p>
          <h3>Gennemsnitspris for hver time på døgnet</h3>
          ${barChart(model.day, [{key: 'price', name: 'kr./kWh inkl. tillæg og moms', color: 'var(--c1)'}], {unit: 'kr./kWh', decimals: 2})}
          <h3>Hvor dine kWh hjemme lander med det valgte mønster</h3>
          ${barChart(model.day, [{key: 'kwh', name: 'kWh ladet i timen', color: 'var(--c3)'}], {unit: 'kWh'})}
        </div>
        ${vehicleHtml(model, set, field)}
        <div class="card section" data-section="months"><h2>Måned for måned</h2>
          <p class="small muted" style="margin-top:-6px">Abonnement, energitillæg og tilbagebetaling kan rettes, så de passer med dine fakturaer. Et tomt felt bruger standardværdien.</p>
          <div class="scroll"><table>
            <tr><th>Måned</th><th class="num">kWh</th><th class="num">Hjemme</th><th class="num">Strømpris hjemme</th><th>Abonnement</th><th>Energitillæg kr./kWh</th><th>Tilbagebetaling kr./kWh</th><th class="num">Clever</th><th class="num">Egen boks</th></tr>
            ${shown.map(m => { const own = set.months[m.key] || {};
              const cell = (f, def) => `<input type="number" step="any" data-cm="${m.key}:${f}" value="${own[f] ?? ''}" placeholder="${def == null ? 'ukendt' : String(def).replace('.', ',')}">`;
              return `<tr><td>${esc(m.label)}</td><td class="num">${kr0(m.kwh)}</td><td class="num">${kr0(m.home)}</td><td class="num">${m.home ? kr2(m.homeChosen / m.home) : '–'}</td><td>${cell('fee', m.feeDefault)}</td>
                <td>${cell('surcharge', m.surchargeDefault)}<div class="small muted">${esc(m.surchargeSource)}</div></td><td>${cell('refund', m.refundDefault)}</td>
                <td class="num">${kr0(m.clever)}</td><td class="num">${kr0(m.own)}</td></tr>`; }).join('')}
            <tr><th>I alt</th><th class="num">${kr0(t.kwh)}</th><th class="num">${kr0(t.home)}</th><th class="num">${kr2(t.homePrice)}</th><th></th><th></th><th></th><th class="num">${kr0(t.clever)}</th><th class="num">${kr0(t.own)}</th></tr>
          </table></div></div>
        ${explainHtml(model, set, data)}
        <div class="card section" data-section="sessions"><h2>Opladninger</h2><div class="scroll"><table>
          <tr><th>Start</th><th>Sted</th><th class="num">Tilsluttet</th><th class="num">kWh</th><th class="num">Egen boks</th><th class="num">kr./kWh</th><th>Grundlag</th></tr>
          ${sessions.map(s => `<tr><td>${esc(fmtFull.format(new Date(s.start * 1000)))}</td><td>${s.home ? 'Hjemme' : s.fast ? 'Ude, hurtiglader' : 'Ude'}</td>
            <td class="num">${kr2((s.stop - s.start) / 3600)} t</td><td class="num">${kr2(s.kwh)}</td>
            <td class="num">${kr0(s.plan ? s.homeCost : s.outCost)} kr.</td><td class="num">${kr2((s.plan ? s.homeCost : s.outCost) / s.kwh)}</td>
            <td class="small muted">${s.plan ? esc(patternText(s.pattern, set)) : 'pris uden abonnement'}</td></tr>`).join('')}
        </table></div>${model.rows.length > 15 && !this._all ? `<p><button id="all">Vis alle ${model.rows.length}</button></p>` : ''}</div>
      </div><div class="aside">
        <div class="card" data-section="assumptions"><h2>Forudsætninger</h2>
          ${check('sinceHome', 'Regn kun fra første hjemmeopladning')}
          <div class="small muted" style="margin:8px 0 4px">Strøm på egen måler</div>
          ${field('addon', 'Tariffer og afgifter', 'kr./kWh')}${field('vat', 'Moms', '%')}${field('power', 'Ladeeffekt', 'kW')}
          <div class="small muted" style="margin:8px 0 4px">Hvornår der lades hjemme</div>
          ${select('mode', 'Lademønster', {window: 'Fast tidsrum', cheapest: 'Billigste timer', now: 'Straks ved tilslutning'})}
          ${set.mode === 'window' ? field('windowFrom', 'Fra klokken', '', '1') + field('windowTo', 'Til klokken', '', '1') : ''}
          ${check('useMeasured', `Brug målte ladetimer, når de findes (${t.measured} af ${t.homeSessions})`)}
          <div class="small muted" style="margin:8px 0 4px">Egen ladeboks</div>
          ${field('chargerPrice', 'Ladeboks og installation', 'kr.')}${field('lifetime', 'Afskrives over', 'år')}${field('extraMonthly', 'Løbende udgift', 'kr./md.')}
          <div class="small muted" style="margin:8px 0 4px">Udeladning uden abonnement</div>
          ${field('publicFast', 'Hurtig- og lynladning', 'kr./kWh')}${field('publicAc', 'Almindelig ladestander', 'kr./kWh')}
          <p class="small muted" id="status" aria-live="polite">${esc(this._status || (this._readOnly ? 'Her kan du prøve andre værdier; de gemmes ikke. De gemte værdier rettes på siden Opladning i Home Assistant.' : 'Dine værdier gemmes på serveren og er de samme på alle dine enheder.'))}</p>
          ${this._readOnly ? '<button id="restore">Gendan de gemte værdier</button> ' : ''}<button id="reset">${this._readOnly ? 'Prøv standardværdierne' : 'Nulstil til standardværdier'}</button>
          <p class="small muted">Standardværdierne er fra oktober 2026: Circle K's lynpris, Clevers pris uden abonnement på almindelige ladere, og de tariffer og afgifter Clever selv oplyste for september.</p></div>
        <div class="card section" data-section="year"><h2>Et år frem</h2>
          ${field('km', 'Forventet kørsel', 'km/år', '1000')}${field('kwh100', 'Forbrug', 'kWh/100 km')}
          <div class="field"><label>Andel ladet hjemme</label><input type="number" data-cs="homeShare" value="${set.homeShare ?? ''}" placeholder="${Math.round(t.homeShare * 100)}"><span class="unit">%</span>${undo('homeShare')}</div>
          <dl><dt>Forbrug</dt><dd>${kr0(y.kwhYear)} kWh om året</dd>
            <dt>Clever</dt><dd><b>${kr0(y.clever)} kr.</b></dd>
            <dt>Egen ladeboks</dt><dd><b>${kr0(y.own)} kr.</b></dd>
            <dt>Forskel</dt><dd>${y.clever <= y.own ? 'Clever' : 'Egen ladeboks'} er ${kr0(Math.abs(y.clever - y.own))} kr. billigere om året</dd>
            <dt>Skæringspunkt</dt><dd>${y.even != null && y.even > 0 ? `ca. ${kr0(y.even)} km om året. Over det er Clever billigst.` : 'Ingen: den ene løsning er billigst uanset kørsel.'}</dd></dl>
          <p class="small muted">Udregningen står under "Antagelser og regneeksempler".</p></div>
      </div></div></div>`;
    this._applySections();
    // Inside Home Assistant the sidebar is opened from this button on narrow screens.
    if (this._hass && customElements.get('ha-menu-button')) {
      const menu = document.createElement('ha-menu-button');
      menu.hass = this._hass; menu.narrow = this._narrow;
      this.shadowRoot.getElementById('menu').appendChild(menu);
    }
  }
}

if (!customElements.get('clever-charging-panel')) customElements.define('clever-charging-panel', CleverChargingPanel);
