"use strict";

const config = window.habitConfig;
const $ = selector => document.querySelector(selector);
document.getElementById("user").textContent = config.displayName;

// Mirrors the server's read limit in stats.fetch_range so the back arrow
// disables before a request would be refused.
const HEATMAP_DAYS = 365, READ_LIMIT_DAYS = 731;
const METRICS = {
  water: {
    accent:"var(--water)", hero:"Water", chart:"water", lowerIsBetter:false,
    streak:"Days in a row at the goal, judged against today’s goal",
    metTile:"Goal met", metCaption:"reached today’s goal", best:"Best", markMet:true, metLabel:"goal met",
    breakdown:"Drink mix", humanize:true, hours:"Drinks by hour logged",
    empty:{title:"No drinks logged yet", body:"Log a drink on Water and your trend starts here."},
  },
  spending: {
    accent:"var(--money)", hero:"Spent", chart:"spending", lowerIsBetter:true,
    streak:"Days in a row within the daily allowance",
    metTile:"Within allowance", metCaption:"stayed within the allowance", best:"Lowest", markMet:true, metLabel:"within allowance",
    breakdown:"Where it went", humanize:true, hours:"Purchases by hour logged",
    empty:{title:"No spending recorded yet", body:"Record a purchase on Budget and your pace shows here."},
  },
  focus: {
    accent:"var(--focus)", hero:"Focus", chart:"focus", lowerIsBetter:false,
    streak:"Days in a row with any saved focus",
    metTile:"Days with focus", metCaption:"had saved focus", best:"Best", markMet:false, metLabel:"",
    breakdown:"What you focused on", humanize:false, hours:"Focus time by hour",
    empty:{title:"No focus saved yet", body:"Finish a session on Study and your time shows here."},
  },
};
const PERIODS = {week:"week", month:"month", year:"year"};

const addDays = (iso, days) => new Date(Date.parse(`${iso}T00:00:00Z`) + days * 86400000).toISOString().slice(0, 10);
const daysBetween = (first, last) => Math.round((Date.parse(last) - Date.parse(first)) / 86400000);
const escapeHtml = text => String(text).replace(/[&<>"']/g, char => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[char]));
const humanize = key => key.replaceAll("_", " ").replace(/^./, char => char.toUpperCase());
const plural = (count, noun) => `${count.toLocaleString()} ${noun}${count === 1 ? "" : "s"}`;

function span(kind, iso) {
  const [year, month] = iso.split("-").map(Number);
  if (kind === "week") {
    const start = addDays(iso, -((new Date(`${iso}T00:00:00Z`).getUTCDay() + 6) % 7));
    return {start, end:addDays(start, 6)};
  }
  if (kind === "month") return {start:`${iso.slice(0, 7)}-01`, end:new Date(Date.UTC(year, month, 0)).toISOString().slice(0, 10)};
  return {start:`${year}-01-01`, end:`${year}-12-31`};
}

function readable(kind, anchor, today) {
  if (anchor > today) return false;
  const current = span(kind, anchor);
  const previous = span(kind, addDays(current.start, -1));
  const first = [addDays(today, 1 - HEATMAP_DAYS), previous.start].sort()[0];
  const end = addDays([current.end, today].sort()[1], 1);
  return daysBetween(first, end) <= READ_LIMIT_DAYS;
}

function viewFromUrl() {
  const query = new URLSearchParams(location.search);
  const anchor = query.get("anchor");
  return {
    metric:Object.hasOwn(METRICS, query.get("metric")) ? query.get("metric") : "water",
    period:Object.hasOwn(PERIODS, query.get("period")) ? query.get("period") : "week",
    anchor:/^\d{4}-\d{2}-\d{2}$/.test(anchor || "") ? anchor : null,
  };
}

function urlFor(view) {
  const query = new URLSearchParams({metric:view.metric, period:view.period});
  if (view.anchor) query.set("anchor", view.anchor);
  return `${location.pathname}?${query}`;
}

// An anchor is the start of a past period; the current period has none.
function anchored(kind, iso, today) {
  const period = span(kind, iso);
  return period.end >= today ? null : period.start;
}

let view = viewFromUrl(), data = null, controller = null;

function navigate(next) {
  view = next;
  history.pushState(null, "", urlFor(view));
  load();
}

async function load(notice = "") {
  controller?.abort();
  controller = new AbortController();
  const requested = view;
  const query = new URLSearchParams({metric:requested.metric, period:requested.period});
  if (requested.anchor) query.set("anchor", requested.anchor);
  const status = $("#progress-status"), app = $("#app");
  app.setAttribute("aria-busy", "true");
  pressSwitches();
  try {
    const response = await fetch(`${config.basePath}/api/stats?${query}`, {signal:controller.signal, cache:"no-store"});
    if (response.status === 422 && requested.anchor) {
      view = {...requested, anchor:null};
      history.replaceState(null, "", urlFor(view));
      return load("That period is too far back to show here, so this is the current one. One view reads at most two years of records.");
    }
    if (!response.ok) throw new Error(`Could not load your trends (${response.status}).`);
    data = await response.json();
    status.textContent = notice;
    render();
  } catch (error) {
    if (error.name === "AbortError") return;
    status.innerHTML = `${escapeHtml(error.message)} <button type="button" id="retry">Try again</button>`;
  } finally {
    if (requested === view) app.setAttribute("aria-busy", "false");
  }
}

function pressSwitches() {
  document.documentElement.style.setProperty("--accent", METRICS[view.metric].accent);
  for (const button of document.querySelectorAll("[data-metric]")) button.setAttribute("aria-pressed", String(button.dataset.metric === view.metric));
  for (const button of document.querySelectorAll("[data-period]")) button.setAttribute("aria-pressed", String(button.dataset.period === view.period));
}

function render() {
  const metric = METRICS[data.metric], noun = PERIODS[data.period];
  const unit = habitCharts.unit(data.metric, data.currency);
  const live = data.current.end >= data.today;
  const before = live ? `last ${noun}` : `previous ${noun}`;
  const summary = data.summary;
  const hasHistory = data.heatmap.days.some(day => day.value);

  $("#period-label").textContent = data.current.label;
  $("#previous-period").disabled = !readable(data.period, addDays(data.current.start, -1), data.today);
  $("#previous-period").title = $("#previous-period").disabled ? "One view reads at most two years of records" : "";
  $("#previous-period").setAttribute("aria-label", `Previous ${noun}`);
  $("#next-period").disabled = live;
  $("#next-period").setAttribute("aria-label", `Next ${noun}`);

  $("#hero-label").textContent = `${metric.hero} · ${live ? `this ${noun} so far` : data.current.label}`;
  $("#hero-total").textContent = unit.value(summary.total);
  $("#hero-average").textContent = `${unit.value(Math.round(summary.daily_average))} a day on average · ${plural(summary.elapsed_days, "day")}${live ? " including today" : ""}`;
  renderDelta(metric, summary, noun, before);
  renderChart(metric, unit, live, before, hasHistory);
  renderTiles(metric, unit, summary);
  renderHeatmap(metric, unit);
  renderBreakdown(metric, unit, live, noun);
  renderHours(metric, unit);
}

function renderDelta(metric, summary, noun, before) {
  const delta = $("#delta"), caption = $("#delta-caption");
  delta.hidden = summary.change_ratio === null;
  if (summary.change_ratio === null) {
    caption.textContent = summary.compared_days ? `Nothing logged ${before} to compare with` : "Comparison starts after the first full day";
    return;
  }
  const percent = Math.round(Math.abs(summary.change_ratio) * 100);
  const rising = summary.change_ratio > 0;
  delta.className = `delta ${percent === 0 ? "even" : rising !== metric.lowerIsBetter ? "good" : "bad"}`;
  delta.textContent = percent === 0 ? `Same as ${before}` : `${rising ? "▲" : "▼"} ${percent}% vs ${before}`;
  caption.textContent = summary.compared_days > daysBetween(data.current.start, data.current.end) ? `Full ${noun}s compared` : `First ${plural(summary.compared_days, "completed day")} of each ${noun}`;
}

function bucketDate(bucket) {
  return data.period === "year" ? habitFormat.month(bucket.start) : habitFormat.shortDay(bucket.start);
}

function renderChart(metric, unit, live, before, hasHistory) {
  const current = data.current.buckets, previous = data.previous.buckets;
  const pace = data.pace && data.metric === "spending";
  const labels = current.map(bucket => data.period !== "month" || bucket.label === "1" || Number(bucket.label) % 5 === 0 ? bucket.label : "");
  const periodWord = {week:"Daily", month:"Daily", year:"Monthly"}[data.period];
  const legend = pace
    ? [["current", "Spent so far"], ["target", "Allowance plan"]]
    : [["current", live ? `This ${PERIODS[data.period]}` : data.current.label], ["previous", humanize(before)], ...(data.metric === "water" && data.period !== "year" ? [["reference", "Goal"]] : [])];
  $("#chart-title").textContent = pace ? `Pace this ${PERIODS[data.period]}` : `${periodWord} ${metric.chart}`;
  $("#chart-legend").innerHTML = legend.map(([style, label]) => `<span class="${style}"><i aria-hidden="true"></i>${escapeHtml(label)}</span>`).join("");
  const goal = data.metric === "water" && data.period !== "year" ? current[0].target : null;
  const empty = !data.summary.total && !previous.some(bucket => bucket.value);
  habitCharts.line($("#main-chart"), {
    labels,
    highlightLast:live,
    unit,
    description:`${$("#chart-title").textContent}, ${data.current.label}. Use the arrow keys to read each ${data.period === "year" ? "month" : "day"}.`,
    empty:empty ? hasHistory ? {title:`Nothing logged this ${PERIODS[data.period]}`, body:"Earlier days are in the heatmap below."} : metric.empty : null,
    reference:goal ? {value:goal, label:`Goal ${unit.value(goal)}`} : null,
    series:pace
      ? [{style:"target", values:data.pace.target_cumulative}, {style:"current", values:data.pace.cumulative}]
      : [{style:"previous", values:current.map((_, index) => previous[index]?.value ?? null)}, {style:"current", values:current.map(bucket => bucket.value)}],
    readout(index) {
      const bucket = current[index];
      if (pace) {
        const plan = data.pace.target_cumulative[index];
        return {title:bucketDate(bucket), items:[
          {style:"plain", label:"Spent", value:unit.value(bucket.value)},
          {style:"current", label:"So far", value:unit.value(data.pace.cumulative[index])},
          ...(plan === null ? [] : [{style:"target", label:"Plan", value:unit.value(plan)}]),
        ]};
      }
      const earlier = previous[index];
      return {title:bucketDate(bucket), items:[
        {style:"current", label:"", value:unit.value(bucket.value)},
        ...(earlier ? [{style:"previous", label:bucketDate(earlier), value:unit.value(earlier.value)}] : []),
      ]};
    },
  });
}

function renderTiles(metric, unit, summary) {
  const tile = (label, value, caption) => `<div class="panel tile"><div class="label">${label}</div><strong>${value}</strong><span>${caption}</span></div>`;
  const year = data.period === "year";
  const best = summary.total ? summary.best : null;
  const share = summary.days_with_target ? `${Math.round(summary.days_met / summary.days_with_target * 100)}%` : "—";
  $("#tiles").innerHTML = [
    tile("Current streak", plural(data.streak.current, "day"), metric.streak),
    tile("Best streak", plural(data.streak.best_in_window, "day"), "Longest run in the last 12 months"),
    tile(metric.metTile, share, summary.days_with_target ? `${summary.days_met} of ${plural(summary.days_with_target, "day")} ${metric.metCaption}` : "No daily allowance set for these days"),
    tile(`${metric.best} ${year ? "month" : "day"}`, best ? unit.value(best.value) : "—", best ? bucketDate(best) : summary.total ? `No completed ${year ? "month" : "day"} yet` : "Nothing logged yet"),
  ].join("");
}

function renderHeatmap(metric, unit) {
  const days = data.heatmap.days;
  $("#heatmap-caption").textContent = `${habitFormat.day(days[0].date)} – today`;
  habitCharts.heatmap($("#heatmap"), {days, format:unit.value, markMet:metric.markMet, metLabel:metric.metLabel});
  $("#heat-key").innerHTML = `<span>Less</span>${[0, 20, 45, 70].map(level => `<i class="heat-cell${level ? " filled" : ""}" style="--heat-level:${level}%"></i>`).join("")}<span>More</span>${metric.markMet ? `<span class="met-key"><i class="heat-cell filled met" style="--heat-level:45%"></i>${humanize(metric.metLabel)}</span>` : ""}`;
}

function renderBreakdown(metric, unit, live, noun) {
  const items = data.breakdown;
  const total = items.reduce((sum, item) => sum + item.value, 0);
  $("#breakdown-title").textContent = metric.breakdown;
  $("#breakdown-caption").textContent = live ? `This ${noun}` : data.current.label;
  $("#breakdown").innerHTML = items.length
    ? items.map(item => `<div class="rank-row"><div class="rank-label"><span>${escapeHtml(metric.humanize ? humanize(item.key) : item.key)}</span><span>${unit.value(item.value)} · ${Math.round(item.value / total * 100)}%</span></div><div class="rank-track" aria-hidden="true"><span style="width:${item.value / items[0].value * 100}%"></span></div></div>`).join("")
    : `<p class="empty">Nothing logged ${live ? `this ${noun}` : `in ${data.current.label}`}.</p>`;
}

function renderHours(metric, unit) {
  const hours = data.hours;
  const peak = Math.max(...hours);
  const busiest = hours.indexOf(peak);
  const clock = hour => `${String(hour).padStart(2, "0")}:00`;
  $("#hours").innerHTML = `<div class="hour-bars">${hours.map((value, hour) => `<span title="${clock(hour)}–${clock((hour + 1) % 24)} · ${unit.value(value)}" style="--bar:${peak ? value / peak * 100 : 0}%"${!value ? ' class="none"' : hour === busiest ? ' class="peak"' : ""}></span>`).join("")}</div><div class="hour-labels" aria-hidden="true"><span>00</span><span>06</span><span>12</span><span>18</span></div><p class="hours-note">${peak ? `Busiest around ${clock(busiest)}. ${metric.hours} on the day; backdated entries are left out.` : "No timed entries in this period yet."}</p>`;
}

$("#metric-switch").addEventListener("click", event => {
  const metric = event.target.closest("[data-metric]")?.dataset.metric;
  if (metric && metric !== view.metric) navigate({...view, metric});
});
$("#period-switch").addEventListener("click", event => {
  const period = event.target.closest("[data-period]")?.dataset.period;
  if (!period || period === view.period) return;
  const anchor = view.anchor && data ? anchored(period, view.anchor, data.today) : null;
  navigate({...view, period, anchor:anchor && readable(period, anchor, data.today) ? anchor : null});
});
document.querySelector(".period-nav").addEventListener("click", event => {
  const step = Number(event.target.closest("[data-step]")?.dataset.step);
  if (!step || !data || event.target.disabled) return;
  const target = step < 0 ? addDays(data.current.start, -1) : addDays(data.current.end, 1);
  navigate({...view, anchor:anchored(view.period, target, data.today)});
});
$("#progress-status").addEventListener("click", event => { if (event.target.id === "retry") load(); });
window.addEventListener("popstate", () => { view = viewFromUrl(); load(); });
load();
