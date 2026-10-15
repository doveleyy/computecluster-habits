"use strict";

// Hand-written SVG charts shared by Progress and Budget. Load after format.js.
const habitCharts = (() => {
  const SVG = "http://www.w3.org/2000/svg";
  let chartCount = 0;

  const trim = number => String(Number(number.toFixed(2)));
  const UNITS = {
    water: {
      value: ml => `${(ml / 1000).toFixed(2)} L`,
      steps: [100, 200, 250, 500, 1000, 2000, 2500, 5000, 10000, 20000, 25000, 50000, 100000, 200000, 250000],
      tick: (ml, top) => `${trim(ml / 1000)}${top ? " L" : ""}`,
      floor: 2500,
    },
    spending: {
      value: (cents, currency) => habitFormat.money(cents, currency),
      steps: [100, 200, 500, 1000, 2000, 2500, 5000, 10000, 20000, 25000, 50000, 100000, 200000, 500000],
      tick: (cents, top, currency) => habitFormat.money(cents, currency).replace(/\.00$/, ""),
      floor: 2000,
    },
    focus: {
      value: seconds => habitFormat.duration(seconds),
      steps: [600, 900, 1800, 3600, 7200, 10800, 18000, 36000, 72000, 108000, 180000, 360000, 720000],
      tick: (seconds, top, _currency, step) => step < 3600 ? `${seconds / 60}${top ? " min" : ""}` : `${trim(seconds / 3600)}${top ? " h" : ""}`,
      floor: 3600,
    },
  };

  function unit(metric, currency) {
    const spec = UNITS[metric];
    return {
      value: amount => spec.value(amount, currency),
      ticks(maximum) {
        const ceiling = Math.max(maximum, spec.floor);
        const step = spec.steps.find(candidate => Math.ceil(ceiling / candidate) <= 4) || spec.steps.at(-1);
        const count = Math.ceil(ceiling / step);
        return Array.from({length:count + 1}, (_, index) => ({value:index * step, label:spec.tick(index * step, index === count, currency, step)}));
      },
    };
  }

  function node(name, attributes = {}, parent) {
    const element = document.createElementNS(SVG, name);
    for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, value);
    parent?.append(element);
    return element;
  }

  function lastValue(values) {
    for (let index = values.length - 1; index >= 0; index--) if (values[index] !== null) return index;
    return -1;
  }

  // spec: {labels, series:[{values, style:"current"|"previous"|"target"}], reference:{value,label}|null,
  //        unit, readout(index) -> {title, items:[{style,label,value}]}, empty:{title,body}|null, description}
  function line(root, spec) {
    const id = `chart-${++chartCount}`;
    const current = spec.series.find(series => series.style === "current");
    const last = lastValue(current.values);
    let selected = last;
    root.classList.add("stats-chart");
    root.innerHTML = `<div class="chart-readout" aria-live="polite"></div><div class="chart-box"></div>`;
    const readout = root.querySelector(".chart-readout");
    const box = root.querySelector(".chart-box");
    const svg = node("svg", {preserveAspectRatio:"xMidYMid meet", tabindex:"0", role:"img", "aria-label":spec.description}, box);
    if (spec.empty) box.insertAdjacentHTML("beforeend", `<div class="chart-empty"><strong>${spec.empty.title}</strong><span>${spec.empty.body}</span></div>`);
    let geometry = null;

    function showReadout() {
      if (selected < 0 || spec.empty) { readout.innerHTML = ""; return; }
      const {title, items} = spec.readout(selected);
      readout.innerHTML = `<span class="readout-title">${title}</span>${items.map(item => `<span class="readout-item ${item.style}"><i aria-hidden="true"></i>${item.label ? `<span>${item.label}</span>` : ""}<strong>${item.value}</strong></span>`).join("")}`;
    }

    function draw() {
      const width = Math.round(box.clientWidth), height = Math.round(box.clientHeight);
      if (!width || !height) return;
      svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
      svg.replaceChildren();
      const values = spec.series.flatMap(series => series.values.filter(value => value !== null));
      const ticks = spec.unit.ticks(Math.max(0, ...values, spec.reference?.value || 0));
      const top = ticks.at(-1).value;
      const left = Math.max(...ticks.map(tick => tick.label.length)) * 6.2 + 10;
      const plot = {left, right:width - 8, top:10, bottom:height - 24};
      const count = spec.labels.length;
      const x = index => plot.left + (index + .5) / count * (plot.right - plot.left);
      const y = value => plot.bottom - value / top * (plot.bottom - plot.top);
      geometry = {plot, count, x};

      const defs = node("defs", {}, svg);
      const gradient = node("linearGradient", {id:`${id}-fill`, x1:0, y1:0, x2:0, y2:1}, defs);
      node("stop", {offset:"0", "stop-opacity":".26"}, gradient);
      node("stop", {offset:"1", "stop-opacity":"0"}, gradient);

      for (const tick of ticks) {
        node("line", {class:tick.value ? "grid" : "baseline", x1:plot.left, x2:plot.right, y1:y(tick.value), y2:y(tick.value)}, svg);
        node("text", {class:"tick", x:plot.left - 8, y:y(tick.value) + 3.5, "text-anchor":"end"}, svg).textContent = tick.label;
      }
      const shown = spec.labels.map((label, index) => label ? index : -1).filter(index => index >= 0);
      const widest = Math.max(...shown.map(index => spec.labels[index].length)) * 6.2 + 8;
      const gap = shown.length > 1 ? x(shown[1]) - x(shown[0]) : Infinity;
      const every = Math.ceil(widest / gap);
      shown.forEach((index, position) => {
        const label = spec.labels[index];
        if (position % every === 0) node("text", {class:`tick${index === last && spec.highlightLast ? " now" : ""}`, x:x(index), y:height - 6, "text-anchor":"middle"}, svg).textContent = label;
      });
      if (spec.reference) {
        node("line", {class:"reference", x1:plot.left, x2:plot.right, y1:y(spec.reference.value), y2:y(spec.reference.value)}, svg);
        node("text", {class:"reference-label", x:plot.right, y:y(spec.reference.value) - 6, "text-anchor":"end"}, svg).textContent = spec.reference.label;
      }
      if (!spec.empty) {
        for (const series of [...spec.series].reverse()) {
          const runs = [];
          series.values.forEach((value, index) => {
            if (value === null) return;
            const run = runs.at(-1);
            if (run && run.at(-1).index === index - 1) run.push({index, value}); else runs.push([{index, value}]);
          });
          for (const run of runs) {
            const points = run.map(point => `${x(point.index).toFixed(1)},${y(point.value).toFixed(1)}`);
            if (series.style === "current" && run.length > 1) node("path", {class:"area", fill:`url(#${id}-fill)`, d:`M${x(run[0].index).toFixed(1)},${plot.bottom}L${points.join("L")}L${x(run.at(-1).index).toFixed(1)},${plot.bottom}Z`}, svg);
            node("path", {class:`series ${series.style}`, d:`M${points.join("L")}`}, svg);
            if (series.style === "current" && count <= 12) for (const point of run) node("circle", {class:"point", cx:x(point.index), cy:y(point.value), r:2.5}, svg);
          }
        }
        if (last >= 0) node("circle", {class:"today", cx:x(last), cy:y(current.values[last]), r:4}, svg);
      }
      if (selected >= 0 && !spec.empty) {
        const guide = node("g", {class:"selection"}, svg);
        node("line", {x1:x(selected), x2:x(selected), y1:plot.top, y2:plot.bottom}, guide);
        for (const series of spec.series) {
          const value = series.values[selected];
          if (value !== null && value !== undefined) node("circle", {class:series.style, cx:x(selected), cy:y(value), r:4.5}, guide);
        }
      }
    }

    function select(index) {
      const next = Math.max(0, Math.min(last, index));
      if (last < 0 || next === selected) return;
      selected = next; draw(); showReadout();
    }

    function pick(event) {
      if (!geometry) return;
      const bounds = svg.getBoundingClientRect();
      const offset = (event.clientX - bounds.left) / bounds.width * Number(svg.viewBox.baseVal.width);
      select(Math.floor((offset - geometry.plot.left) / (geometry.plot.right - geometry.plot.left) * geometry.count));
    }

    svg.addEventListener("pointermove", event => { if (event.pointerType === "mouse") pick(event); });
    svg.addEventListener("pointerdown", pick);
    svg.addEventListener("keydown", event => {
      const moves = {ArrowLeft:selected - 1, ArrowRight:selected + 1, Home:0, End:last};
      if (!(event.key in moves)) return;
      event.preventDefault();
      select(moves[event.key]);
    });
    new ResizeObserver(draw).observe(box);
    draw(); showReadout();
  }

  // days: [{date, value, met}], oldest first; format(value) labels each cell.
  function heatmap(root, {days, format, markMet, metLabel}) {
    const maximum = Math.max(0, ...days.map(day => day.value));
    const offset = (new Date(`${days[0].date}T00:00:00Z`).getUTCDay() + 6) % 7;
    const cells = [];
    const months = [];
    days.forEach((day, index) => {
      const column = Math.floor((index + offset) / 7) + 2;
      const row = (index + offset) % 7 + 2;
      if (day.date.endsWith("-01") || index === 0) months.push({column, label:habitFormat.month(day.date).split(" ")[0]});
      const level = day.value ? Math.round(16 + 64 * day.value / maximum) : 0;
      const text = `${habitFormat.shortDay(day.date)} ${day.date.slice(0, 4)}: ${format(day.value)}${markMet && day.met ? `, ${metLabel}` : ""}`;
      cells.push(`<span class="heat-cell${level ? " filled" : ""}${markMet && day.met ? " met" : ""}" style="grid-area:${row}/${column}${level ? `;--heat-level:${level}%` : ""}" title="${text}" aria-label="${text}" role="img"></span>`);
    });
    const labels = months.filter((month, index) => !(index === 0 && months[1] && months[1].column - month.column < 3));
    root.innerHTML = `<div class="heatmap-scroll"><div class="heatmap">${["Mon", "", "Wed", "", "Fri", "", ""].map((label, index) => `<span class="heat-day" style="grid-area:${index + 2}/1">${label}</span>`).join("")}${labels.map(month => `<span class="heat-month" style="grid-area:1/${month.column}/2/span 4">${month.label}</span>`).join("")}${cells.join("")}</div></div>`;
    const scroll = root.querySelector(".heatmap-scroll");
    scroll.scrollLeft = scroll.scrollWidth;
  }

  return {unit, line, heatmap};
})();
