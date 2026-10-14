const config = window.habitConfig;
document.getElementById('user').textContent = config.displayName;
const state = { metric: new URLSearchParams(location.search).get('metric') || 'water', week: null, day: null, category: null, breakdown: 'type', data: null };
let TODAY, CURRENT_WEEK, requestSerial = 0, controller;
const addDays = (date, n) => new Date(Date.parse(`${date}T00:00:00Z`) + n * 86400000).toISOString().slice(0, 10);
const shortDate = date => new Date(`${date}T00:00:00Z`).toLocaleDateString('en-GB', {day:'numeric', month:'short', timeZone:'UTC'});
const fullDate = date => new Date(`${date}T00:00:00Z`).toLocaleDateString('en-GB', {weekday:'short', day:'numeric', month:'short', timeZone:'UTC'});
const money = cents => new Intl.NumberFormat('en-SG', {style:'currency', currency:state.data.currency}).format(cents / 100);
const volume = ml => `${(ml / 1000).toFixed(2)} L`;
const duration = seconds => { if (seconds > 0 && seconds < 60) return '<1 min'; const minutes = Math.floor(seconds / 60); return minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)}h${minutes % 60 ? ` ${minutes % 60}m` : ''}`; };
const sum = values => values.reduce((a, b) => a + b, 0);
const escapeHtml = text => String(text).replace(/[&<>"']/g, char => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[char]));
const label = value => value.replaceAll('_', ' ').replace(/^./, char => char.toUpperCase());
const METRICS = {
  water: {name:'Drink intake', noun:'All drink volume', color:'#88c9e6', format:volume, field:'ml', goal:0, reference:'Current daily volume goal', chart:'Daily drink volume', unit:'L'},
  spending: {name:'Spending', noun:'Daily spending', color:'#eda9c5', format:money, field:'cents', goal:0, chart:'Daily spending', unit:'SGD'},
  focus: {name:'Focus', noun:'Saved focus time', color:'#b49cff', format:duration, field:'seconds', goal:0, chart:'Daily focus time', unit:'min'},
};
if (!Object.hasOwn(METRICS, state.metric)) state.metric = 'water';
function events(metric, start, end, category = null) {
  const records = state.data.selected_week_records;
  const local = timestamp => {
    const parts = new Intl.DateTimeFormat('en-CA', {timeZone:state.data.timezone, year:'numeric', month:'2-digit', day:'2-digit'}).formatToParts(new Date(timestamp));
    const get = name => parts.find(part => part.type === name).value;
    return `${get('year')}-${get('month')}-${get('day')}`;
  };
  const time = timestamp => new Date(timestamp).toLocaleTimeString('en-GB', {timeZone:state.data.timezone, hour:'2-digit', minute:'2-digit'});
  const eventTime = (occurred, recorded) => recorded && local(occurred) !== local(recorded) ? 'Later' : time(occurred);
  let rows;
  if (metric === 'water') rows = records.drinks.map(d => ({id:d.id, kind:metric, date:local(d.consumed_at), time:eventTime(d.consumed_at, d.recorded_at), type:label(d.drink_type), temperature:label(d.temperature), ml:d.amount_ml}));
  else if (metric === 'spending') rows = records.purchases.map(p => ({id:p.id, kind:metric, date:local(p.occurred_at), time:eventTime(p.occurred_at, p.recorded_at), category:label(p.category), description:p.description, cents:p.amount_cents}));
  else rows = records.sessions.flatMap(s => s.day_segments.map(segment => ({id:s.id, kind:metric, date:segment.date, time:local(s.started_at) === segment.date ? time(s.started_at) : '00:00', name:s.activity, seconds:segment.seconds})));
  return rows.filter(row => row.date >= start && row.date <= end && (!category || row.category === category));
}
function total(metric, start, end, category = null) {
  return sum(state.data.days.filter(day => day.date >= start && day.date <= end && day.temporal !== 'future').map(day => metric === 'water' ? day.water.total_ml : metric === 'focus' ? day.focus.total_seconds : category ? Object.entries(day.spending.by_category_cents).filter(([key]) => label(key) === category).reduce((value, [, amount]) => value + amount, 0) : day.spending.spent_cents));
}
function noRecords() { return events(state.metric, state.week, period().end, state.category).length === 0; }
function period() {
  const current = state.week === CURRENT_WEEK;
  const elapsed = current ? state.data.completed_days + 1 : 7;
  return {current, elapsed, end:addDays(state.week, elapsed - 1), previous:addDays(state.week, -7), previousEnd:addDays(state.week, state.data.completed_days - 8)};
}
function deltaLabel(metric, current, previous) {
  if (!current && !previous) return 'No records in either period';
  if (!previous) return 'No previous-period comparison';
  const difference = current - previous;
  return difference ? `${METRICS[metric].format(Math.abs(difference))} ${difference > 0 ? 'more' : 'less'} · ${Math.round(Math.abs(difference) / previous * 100)}%` : 'Same as previous period';
}
function comparison(metric, category) {
  const count = state.data.completed_days;
  if (!count) return 'No completed days to compare yet';
  return `${deltaLabel(metric, total(metric, state.week, addDays(state.week, count - 1), category), total(metric, addDays(state.week, -7), addDays(state.week, count - 8), category))} · ${count} completed ${count === 1 ? 'day' : 'days'}`;
}
function metricTabs() {
  return `<div class="metric-tabs" aria-label="Chart metric">${Object.entries(METRICS).map(([key, metric]) => `<button data-action="metric" data-value="${key}" aria-pressed="${state.metric === key}" style="--accent:${metric.color}">${metric.name}</button>`).join('')}</div>`;
}
function weekNav() {
  return `<div class="week-navigation" aria-label="Browse weeks"><button data-action="week" data-value="-7" aria-label="Previous week" ${state.week <= '0001-01-15' ? 'disabled' : ''}>←</button><button data-action="reset" ${state.week === CURRENT_WEEK && !state.day ? 'disabled' : ''}>This week</button><button data-action="week" data-value="7" aria-label="Next week" ${state.week === CURRENT_WEEK ? 'disabled' : ''}>→</button></div>`;
}
function weekTitle() {
  const p = period();
  return `<div class="week-title"><div><div class="period">${shortDate(state.week)} – ${shortDate(addDays(state.week, 6))} ${state.week.slice(0, 4)}</div><div class="comparison-caption">${p.current ? `Totals include today · comparisons use ${state.data.completed_days} completed days` : 'Full week, compared with the previous full week'}</div></div><span class="period-badge">${p.current ? 'Week in progress' : 'Completed week'}</span></div>`;
}
function chart() {
  const metric = METRICS[state.metric];
  const category = state.metric === 'spending' ? state.category : null;
  const rows = Array.from({ length: 7 }, (_, index) => {
    const date = addDays(state.week, index);
    const previousDate = addDays(date, -7);
    const disabled = date > TODAY;
    return { date, disabled, index, value: total(state.metric, date, date, category), previous: disabled || date === TODAY ? 0 : total(state.metric, previousDate, previousDate, category) };
  });
  const largest = Math.max(metric.goal * 1.2, ...rows.map(row => Math.max(row.value, row.previous)));
  const step = state.metric === 'water' ? 500 : state.metric === 'spending' ? 1000 : 1800;
  const max = Math.max(step, Math.ceil(largest / step) * step);
  const axis = number => state.metric === 'water' ? `${Number((number / 1000).toFixed(3))}` : state.metric === 'spending' ? `${Number((number / 100).toFixed(2))}` : `${Math.round(number / 60)}`;
  const heading = category ? `${category} spending by day` : metric.chart;
  return `<div class="chart-top"><div><h3>${heading}</h3><p>${state.metric === 'water' ? 'Every drink counted by volume. No hydration weighting.' : state.metric === 'spending' ? 'Purchases only. Contributions and redemptions excluded.' : 'Saved sessions, including stopped sessions, in minutes.'}</p></div><div class="chart-legend"><span><i class="legend-mark"></i>Selected week</span><span><i class="legend-mark ghost"></i>Previous week</span></div></div><div class="chart-wrap"><div class="chart-scale">${[max, max * .75, max * .5, max * .25, 0].map(number => `<span>${axis(number)}${number === max ? ` ${metric.unit}` : ''}</span>`).join('')}</div><div class="plot">${metric.goal ? `<div class="reference-line" style="bottom:${metric.goal / max * 100}%"><span class="reference-label">${category ? 'Total daily allowance' : metric.reference} · ${metric.format(metric.goal)}</span></div>` : ''}<div class="chart-bars">${rows.map(row => `<button class="day-bar ${row.date === state.day ? 'selected' : ''}" data-action="day" data-value="${row.date}" aria-pressed="${row.date === state.day}" aria-label="${fullDate(row.date)}, ${row.disabled ? 'future date' : `${heading} ${metric.format(row.value)}, ${row.date === TODAY ? 'partial today, no comparison' : 'previous ' + metric.format(row.previous)}` }" ${row.disabled ? 'disabled' : ''}>${row.disabled ? '<span class="future-stub" aria-hidden="true"></span>' : `<span class="bar previous" aria-hidden="true" style="height:${row.previous / max * 100}%"></span><span class="bar" aria-hidden="true" style="height:${row.value / max * 100}%"></span>`}</button>`).join('')}</div>${noRecords() ? '<div class="chart-empty">No records this week<span>Log a habit to begin seeing your progress.</span></div>' : ''}</div><div class="chart-labels">${rows.map(row => `<div class="${row.date === TODAY ? 'is-today' : ''} ${row.disabled ? 'future' : ''}">${['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'][row.index]}<span>${row.date === TODAY ? 'Partial' : shortDate(row.date)}</span></div>`).join('')}</div></div><p class="chart-hint">Select a solid bar to inspect a day. The week stays in place.${period().current ? ' Today is partial; its comparison is hidden. Future dates have no data.' : ''}</p>${category ? `<div class="filter-chip"><span>Filtering ${category.toLowerCase()} purchases</span><button data-action="clear-category">Clear filter ×</button></div>` : ''}`;
}
function breakdown() {
  const p = period();
  const metric = METRICS[state.metric];
  const records = events(state.metric, state.week, p.end);
  const groups = new Map();
  for (const event of records) {
    const key = state.metric === 'spending' ? event.category : state.metric === 'water' ? event[state.breakdown] : event.name;
    groups.set(key, (groups.get(key) || 0) + event[metric.field]);
  }
  const ranked = Array.from(groups).sort((a, b) => b[1] - a[1]);
  const grandTotal = sum(ranked.map(row => row[1]));
  const peak = ranked[0]?.[1] || 1;
  const heading = state.metric === 'spending' ? 'Where your money went' : state.metric === 'water' ? 'Your drink mix' : 'What you focused on';
  const rows = ranked.map(([name, value]) => `<${state.metric === 'spending' ? 'button' : 'div'} class="rank-row ${state.metric === 'spending' ? 'clickable' : ''}" ${state.metric === 'spending' ? `data-action="category" data-value="${escapeHtml(name)}" aria-pressed="${state.category === name}"` : ''}><div class="rank-label"><span>${escapeHtml(name)}</span><span>${metric.format(value)} · ${Math.round(value / grandTotal * 100)}%</span></div><div class="rank-track" aria-hidden="true"><span style="width:${value / peak * 100}%"></span></div></${state.metric === 'spending' ? 'button' : 'div'}>`).join('');
  
  return `<div class="breakdown"><div class="breakdown-heading"><h3>${heading}</h3>${state.category ? '<button data-action="clear-category">Clear filter</button>' : ''}</div>${state.metric === 'water' ? `<div class="breakdown-tabs" aria-label="Drink breakdown"><button data-action="breakdown" data-value="type" aria-pressed="${state.breakdown === 'type'}">Drink type</button><button data-action="breakdown" data-value="temperature" aria-pressed="${state.breakdown === 'temperature'}">Temperature</button></div>` : ''}${rows || '<p class="dataset-empty">No records in this period.</p>'}<p class="breakdown-note">${state.metric === 'spending' ? `Select a category to filter the chart and day purchases. Fund movements are excluded from spending. The bars show each category relative to the largest.` : state.metric === 'water' ? 'All drink volume includes plain water, coffee, tea and other drinks. Plain water is also shown separately in the summary.' : 'Time comes from completed or stopped sessions. There is no focus target.'}</p></div>`;
}
function eventRows(records) {
  return `<div class="event-list">${records.sort((a, b) => a.time.localeCompare(b.time)).map(event => {
    const primary = event.kind === 'water' ? event.type : event.kind === 'spending' ? event.description : event.name;
    const detail = event.kind === 'water' ? event.temperature : event.kind === 'spending' ? event.category : 'Saved focus session';
    return `<div class="event-row"><time>${event.time}</time><div>${escapeHtml(primary)}<small>${escapeHtml(detail)}</small></div><div class="event-value">${METRICS[event.kind].format(event[METRICS[event.kind].field])}</div></div>`;
  }).join('')}</div>`;
}
function inspector() {
  const p = period();
  const metric = METRICS[state.metric];
  const category = state.metric === 'spending' ? state.category : null;
  if (!state.day) {
    const dates = Array.from({ length: p.elapsed }, (_, index) => addDays(state.week, index)).reverse().slice(0, 3);
    return `<div class="inspector"><div class="inspector-head"><h3>Day explorer</h3><span class="label">Read only</span></div><div class="empty-inspector"><p>Select a day above.</p><span>Use the arrows here to move between days. Back to week clears your selection without moving the chart.</span><div class="period-list">${dates.map(date => `<button data-action="day" data-value="${date}"><span>${fullDate(date)}</span><span>${metric.format(total(state.metric, date, date, category))}</span></button>`).join('')}</div></div></div>`;
  }
  const records = events(state.metric, state.day, state.day, category);
  const value = total(state.metric, state.day, state.day, category);
  const all = total(state.metric, state.day, state.day);
  const previous = total(state.metric, addDays(state.day, -7), addDays(state.day, -7), category);
  const allowance = state.data.days.find(day => day.date === state.day).spending.allowance_cents;
  const plain = state.metric === 'water' ? sum(records.filter(event => event.type === 'Water').map(event => event.ml)) : 0;
  const summary = state.metric === 'water' ? `${volume(plain)} plain water · ${records.length} drinks` : state.metric === 'spending' ? `${category ? `${category} · ${money(all)} all purchases` : `${allowance === null ? "No allowance for this day" : money(allowance) + " daily allowance"} · ${allowance === null ? '' : all > allowance ? `${money(all - allowance)} over allowance` : `${money(allowance - all)} remaining`}`} · fund movements excluded` : `${records.length} saved session${records.length === 1 ? '' : 's'} · saved time only`;
  return `<div class="inspector"><div class="inspector-head"><h3>${fullDate(state.day)}${state.day === TODAY ? ' · Today' : ''}</h3><div class="day-navigation" aria-label="Browse days in this week"><button data-action="move-day" data-value="-1" aria-label="Previous day" ${state.day === state.week ? 'disabled' : ''}>←</button><button data-action="clear-day">Back to week</button><button data-action="move-day" data-value="1" aria-label="Next day" ${state.day === p.end ? 'disabled' : ''}>→</button></div></div><div><div class="inspector-total">${noRecords() ? '—' : metric.format(value)}</div><p class="inspector-summary">${noRecords() ? 'No records on this day.' : summary}</p><p class="inspector-summary">${state.day === TODAY ? 'Today is incomplete. Comparison resumes after the day ends.' : deltaLabel(state.metric, value, previous).replace('previous period', 'the same weekday last week')}</p></div>${records.length ? eventRows(records) : `<div class="empty-inspector"><p>No ${category ? category.toLowerCase() : state.metric === 'water' ? 'drink' : state.metric} records on this day.</p><span>${category ? 'Clear the category filter to see all purchases.' : 'Dates remain available to explore.'}</span>${category ? '<button data-action="clear-category">Clear category filter</button>' : ''}</div>`}</div>`;
}
function studioKpis() {
  const p = period(), metric = METRICS[state.metric], count = state.data.completed_days;
  const amount = total(state.metric, state.week, p.end, state.category);
  const previous = total(state.metric, p.previous, p.previousEnd, state.category);
  const days = state.data.days.slice(7).filter(day => day.temporal !== 'future');
  const best = days.map(day => ({date:day.date, value:total(state.metric, day.date, day.date, state.category)})).sort((a,b) => b.value - a.value)[0];
  const goalDays = days.filter(day => day.temporal === 'past' && day.water.entry_count && day.water.total_ml >= state.data.current_goal_ml).length;
  const sessions = new Set(events('focus', state.week, p.end).map(row => row.id)).size;
  const third = state.metric === 'water' ? count ? `${goalDays}/${count} days` : '—' : state.metric === 'spending' ? shortDate(best.date) : `${sessions} sessions`;
  return `<div class="studio-kpis"><div class="studio-stat"><div class="label">Daily average</div><strong>${noRecords() ? '—' : metric.format(amount / p.elapsed)}</strong><span>Over ${p.elapsed} days${p.current ? ', including partial today' : ''}</span></div><div class="studio-stat"><div class="label">Previous ${count} days</div><strong>${count ? metric.format(previous) : '—'}</strong><span>Same completed weekdays last week</span></div><div class="studio-stat"><div class="label">${state.metric === 'water' ? 'Current goal reached' : state.metric === 'spending' ? 'Largest spending day' : 'Saved sessions'}</div><strong>${noRecords() ? '—' : third}</strong><span>${state.metric === 'water' ? `${volume(state.data.current_goal_ml)} current daily goal` : state.metric === 'spending' ? noRecords() ? 'No recorded purchases' : metric.format(best.value) : 'Completed or stopped sessions'}</span></div></div>`;
}
function render() {
  const metric = METRICS[state.metric], p = period();
  document.documentElement.style.setProperty('--accent', metric.color);
  document.getElementById('app').innerHTML = `<div class="section-heading">${weekNav()}</div>${weekTitle()}<div class="studio-layout"><section class="panel studio-main">${metricTabs()}<div class="studio-heading"><div><span class="label">${state.category ? `${state.category} spending` : metric.noun} · ${p.current ? 'Week so far' : 'Full week'}</span><div class="hero-reading">${noRecords() ? '—' : metric.format(total(state.metric, state.week, p.end, state.category))}</div><p class="hero-caption">${comparison(state.metric, state.category)}</p></div><span class="period-badge">${p.elapsed} elapsed days</span></div>${studioKpis()}${chart()}</section><aside class="panel studio-side">${breakdown()}</aside></div><section class="panel studio-inspector">${inspector()}</section>`;
}
function restoreFocus(action, value, previousDay) {
  const buttons = [...document.querySelectorAll('button[data-action]')];
  const target = buttons.find(button => button.dataset.action === action && button.dataset.value === value && !button.disabled) || buttons.find(button => button.dataset.action === 'day' && button.dataset.value === (state.day || previousDay || state.week) && !button.disabled);
  target?.focus({preventScroll:true});
}
async function loadWeek(week, focus) {
  const serial = ++requestSerial;
  controller?.abort(); controller = new AbortController();
  const status = document.getElementById('progress-status'), app = document.getElementById('app');
  app.setAttribute('aria-busy', 'true'); status.textContent = 'Loading progress…';
  try {
    const response = await fetch(`${config.basePath}/api/progress${week ? '?week=' + encodeURIComponent(week) : ''}`, {signal:controller.signal});
    if (!response.ok) throw new Error('Unable to load progress.');
    const data = await response.json();
    if (serial !== requestSerial) return;
    state.data = data; state.week = data.week_start; state.day = null;
    TODAY = data.today; CURRENT_WEEK = addDays(TODAY, -(new Date(`${TODAY}T00:00:00Z`).getUTCDay() + 6) % 7);
    METRICS.water.goal = data.current_goal_ml;
    render(); status.textContent = '';
    if (focus) restoreFocus(...focus);
  } catch (error) {
    if (serial !== requestSerial || error.name === 'AbortError') return;
    status.innerHTML = 'Unable to load progress. <button data-action="retry">Retry</button>';
    state.retryWeek = week;
  } finally { if (serial === requestSerial) app.setAttribute('aria-busy', 'false'); }
}
document.addEventListener('click', event => {
  const button = event.target.closest('button[data-action]');
  if (!button || button.disabled) return;
  const {action, value} = button.dataset, previousDay = state.day;
  if (action === 'retry') { loadWeek(state.retryWeek); return; }
  if (action === 'week' || action === 'reset') { loadWeek(action === 'reset' ? CURRENT_WEEK : addDays(state.week, Number(value)), [action, value, previousDay]); return; }
  if (action === 'metric') { state.metric = value; state.category = null; }
  if (action === 'day') state.day = value;
  if (action === 'move-day') state.day = addDays(state.day, Number(value));
  if (action === 'clear-day') state.day = null;
  if (action === 'category') state.category = state.category === value ? null : value;
  if (action === 'clear-category') state.category = null;
  if (action === 'breakdown') state.breakdown = value;
  render(); restoreFocus(action, value, previousDay);
});
loadWeek();
