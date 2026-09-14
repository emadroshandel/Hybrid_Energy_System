/* Minimal SVG charting.
 *
 * Written rather than imported so the whole application stays offline-capable
 * and dependency-free, matching the engine's own constraint. It draws the six
 * chart forms this tool actually needs and nothing else.
 *
 * Colours come from the CSS custom properties, so charts follow the theme
 * without any JavaScript re-rendering on theme change. */

const Chart = (() => {
  const NS = 'http://www.w3.org/2000/svg';
  const SERIES = ['--c1','--c2','--c3','--c4','--c5','--c6'];
  const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

  function el(name, attrs = {}, text) {
    const e = document.createElementNS(NS, name);
    for (const [k, v] of Object.entries(attrs)) {
      if (v !== null && v !== undefined) e.setAttribute(k, v);
    }
    if (text !== undefined) e.textContent = text;
    return e;
  }
  const col = i => `var(${SERIES[i % SERIES.length]})`;

  function nice(max, min = 0) {
    if (!isFinite(max) || max === min) return { max: max || 1, step: (max || 1) / 4 };
    const span = max - min;
    const mag = Math.pow(10, Math.floor(Math.log10(span)));
    const norm = span / mag;
    const step = (norm <= 1.5 ? 0.25 : norm <= 3 ? 0.5 : norm <= 7 ? 1 : 2) * mag;
    return { max: Math.ceil(max / step) * step, min: Math.floor(min / step) * step, step };
  }

  function fmt(v) {
    const a = Math.abs(v);
    if (a >= 1e9) return (v / 1e9).toFixed(1) + 'B';
    if (a >= 1e6) return (v / 1e6).toFixed(1) + 'M';
    if (a >= 1e4) return (v / 1e3).toFixed(0) + 'k';
    if (a >= 1e3) return (v / 1e3).toFixed(1) + 'k';
    if (a >= 10) return v.toFixed(0);
    if (a >= 1) return v.toFixed(1);
    if (a === 0) return '0';
    return v.toFixed(3);
  }

  // Axis labels need enough decimals to tell one tick from the next. The
  // general-purpose formatter picks its precision from the magnitude of the
  // value, which is right for a caption and wrong for an axis: a renewable
  // fraction running 97.5 to 100 in half-percent steps printed as
  // "98, 98, 99, 99, 100, 100" — the same label twice, on a chart whose
  // whole point is the difference between them.
  function tickFormatter(scale) {
    const span = Math.max(Math.abs(scale.max), Math.abs(scale.min));
    let div = 1, suffix = '';
    if (span >= 1e9) { div = 1e9; suffix = 'B'; }
    else if (span >= 1e6) { div = 1e6; suffix = 'M'; }
    else if (span >= 1e3) { div = 1e3; suffix = 'k'; }
    const step = Math.abs(scale.step) / div;
    // Use the fewest decimals that write the step exactly. Deriving the
    // precision from the step's order of magnitude instead rounds a 0.25M
    // step to "0.3M, 0.5M, 0.8M" — three gaps that are all the same size
    // and none of which look it.
    let d = 0;
    while (d < 4 && Math.abs(Math.round(step * 10 ** d) - step * 10 ** d) > 1e-6) {
      d += 1;
    }
    return v => (v / div).toFixed(d) + suffix;
  }

  function frame(host, opts) {
    const W = opts.width || host.clientWidth || 460;
    const H = opts.height || 210;
    const m = Object.assign({ t: 14, r: 12, b: 30, l: 48 }, opts.margin);
    host.innerHTML = '';
    if (opts.title) {
      const t = document.createElement('div');
      t.className = 'chart-title';
      t.textContent = opts.title;
      host.appendChild(t);
    }
    const svg = el('svg', {
      viewBox: `0 0 ${W} ${H}`, width: '100%', height: H, class: 'chart',
      role: 'img', 'aria-label': opts.title || 'chart'
    });
    host.appendChild(svg);
    return { svg, W, H, m, iw: W - m.l - m.r, ih: H - m.t - m.b };
  }

  function axes(f, yScale, xLabels, opts = {}) {
    const { svg, m, iw, ih } = f;
    const ticks = [];
    for (let v = yScale.min; v <= yScale.max + 1e-9; v += yScale.step) ticks.push(v);
    const tick = opts.tickFmt || tickFormatter(yScale);
    ticks.forEach(v => {
      const y = m.t + ih - ((v - yScale.min) / (yScale.max - yScale.min)) * ih;
      svg.appendChild(el('line', {
        x1: m.l, y1: y, x2: m.l + iw, y2: y,
        stroke: 'var(--line-2)', 'stroke-width': 1
      }));
      svg.appendChild(el('text', {
        x: m.l - 7, y: y + 3.5, 'text-anchor': 'end',
        'font-size': 10, fill: 'var(--muted)'
      }, tick(v)));
    });
    if (xLabels) {
      const n = xLabels.length;
      const step = Math.ceil(n / (opts.maxXTicks || 12));
      xLabels.forEach((lab, i) => {
        if (i % step) return;
        const x = m.l + (n === 1 ? iw / 2 : (i + 0.5) / n * iw);
        svg.appendChild(el('text', {
          x, y: m.t + ih + 15, 'text-anchor': 'middle',
          'font-size': 10, fill: 'var(--muted)'
        }, lab));
      });
    }
  }

  function legend(host, names) {
    const d = document.createElement('div');
    d.className = 'legend';
    names.forEach((n, i) => {
      const s = document.createElement('span');
      s.innerHTML = `<i class="swatch" style="background:${col(i)}"></i>${n}`;
      d.appendChild(s);
    });
    host.appendChild(d);
  }

  /* -------------------------------------------------------------- bars */
  function bars(host, opts) {
    const series = opts.series;            // [{name, values}]
    const labels = opts.labels || MONTHS;
    const stacked = opts.stacked !== false;
    const f = frame(host, opts);
    const n = labels.length;

    const totals = labels.map((_, i) =>
      stacked ? series.reduce((s, ss) => s + (ss.values[i] || 0), 0)
              : Math.max(...series.map(ss => ss.values[i] || 0)));
    const ys = nice(Math.max(...totals, 0));
    ys.min = 0;
    axes(f, ys, labels, opts);

    const { svg, m, iw, ih } = f;
    const bw = iw / n;
    const pad = bw * 0.18;

    labels.forEach((_, i) => {
      let acc = 0;
      series.forEach((ss, k) => {
        const v = ss.values[i] || 0;
        if (v <= 0) return;
        const h = (v / ys.max) * ih;
        const inner = stacked ? bw - pad * 2 : (bw - pad * 2) / series.length;
        const x = m.l + i * bw + pad + (stacked ? 0 : k * inner);
        const y = stacked ? m.t + ih - h - acc : m.t + ih - h;
        const r = svg.appendChild(el('rect', {
          x, y, width: Math.max(1, inner), height: Math.max(0.5, h),
          fill: col(k), rx: 1.5
        }));
        r.appendChild(el('title', {}, `${ss.name} ${labels[i]}: ${fmt(v)}`));
        acc += h;
      });
    });
    legend(host, series.map(s => s.name));
  }

  /* ------------------------------------------------------------- lines */
  function lines(host, opts) {
    const series = opts.series;
    const labels = opts.labels;
    const f = frame(host, opts);
    const all = series.flatMap(s => s.values).filter(v => isFinite(v));
    const ys = nice(Math.max(...all, 0), Math.min(...all, 0));
    axes(f, ys, labels, opts);

    const { svg, m, iw, ih } = f;
    const n = Math.max(...series.map(s => s.values.length));
    const px = i => m.l + (n === 1 ? iw / 2 : i / (n - 1) * iw);
    const py = v => m.t + ih - ((v - ys.min) / (ys.max - ys.min)) * ih;

    series.forEach((ss, k) => {
      const d = ss.values.map((v, i) => `${i ? 'L' : 'M'}${px(i).toFixed(1)},${py(v).toFixed(1)}`).join('');
      if (opts.fill) {
        svg.appendChild(el('path', {
          d: d + `L${px(ss.values.length - 1)},${py(ys.min)}L${px(0)},${py(ys.min)}Z`,
          fill: col(k), opacity: 0.12
        }));
      }
      svg.appendChild(el('path', {
        d, fill: 'none', stroke: col(k), 'stroke-width': 1.9,
        'stroke-linejoin': 'round', 'stroke-linecap': 'round'
      }));
    });
    if (series.length > 1 || opts.forceLegend) legend(host, series.map(s => s.name));
  }

  /* ------------------------------------------------------------ scatter */
  function scatter(host, opts) {
    const pts = opts.points;               // [{x,y,label,selected,index}]
    const f = frame(host, Object.assign({ height: 300, margin: { t: 16, r: 18, b: 42, l: 64 } }, opts));
    const xs = nice(Math.max(...pts.map(p => p.x)), Math.min(...pts.map(p => p.x)));
    const ys = nice(Math.max(...pts.map(p => p.y)), Math.min(...pts.map(p => p.y)));
    const { svg, m, iw, ih } = f;
    // Both axes here span a narrow range around a large number - a front
    // running from 1.27M to 1.39M, or 97.4% to 100% - so the tick labels
    // need their precision from the step, not from the magnitude.
    const ytick = tickFormatter(ys);
    const xtick = tickFormatter(xs);

    for (let v = ys.min; v <= ys.max + 1e-9; v += ys.step) {
      const y = m.t + ih - ((v - ys.min) / (ys.max - ys.min)) * ih;
      svg.appendChild(el('line', { x1: m.l, y1: y, x2: m.l + iw, y2: y, stroke: 'var(--line-2)' }));
      svg.appendChild(el('text', { x: m.l - 8, y: y + 3.5, 'text-anchor': 'end', 'font-size': 10, fill: 'var(--muted)' }, ytick(v)));
    }
    for (let v = xs.min; v <= xs.max + 1e-9; v += xs.step) {
      const x = m.l + ((v - xs.min) / (xs.max - xs.min)) * iw;
      svg.appendChild(el('line', { x1: x, y1: m.t, x2: x, y2: m.t + ih, stroke: 'var(--line-2)' }));
      svg.appendChild(el('text', { x, y: m.t + ih + 15, 'text-anchor': 'middle', 'font-size': 10, fill: 'var(--muted)' }, xtick(v)));
    }
    if (opts.xTitle) svg.appendChild(el('text', { x: m.l + iw / 2, y: f.H - 4, 'text-anchor': 'middle', 'font-size': 11, fill: 'var(--ink-2)' }, opts.xTitle));
    if (opts.yTitle) svg.appendChild(el('text', { x: 12, y: m.t + ih / 2, 'text-anchor': 'middle', 'font-size': 11, fill: 'var(--ink-2)', transform: `rotate(-90 12 ${m.t + ih / 2})` }, opts.yTitle));

    const px = v => m.l + ((v - xs.min) / (xs.max - xs.min)) * iw;
    const py = v => m.t + ih - ((v - ys.min) / (ys.max - ys.min)) * ih;

    // Connect the front so the trade-off reads as a curve, not a cloud.
    const sorted = [...pts].sort((a, b) => a.x - b.x);
    svg.appendChild(el('path', {
      d: sorted.map((p, i) => `${i ? 'L' : 'M'}${px(p.x).toFixed(1)},${py(p.y).toFixed(1)}`).join(''),
      fill: 'none', stroke: 'var(--c1)', 'stroke-width': 1.2, opacity: .38,
      'stroke-dasharray': '4 3'
    }));

    pts.forEach(p => {
      const c = el('circle', {
        cx: px(p.x), cy: py(p.y), r: p.selected ? 7 : 4.5,
        fill: p.selected ? 'var(--c2)' : 'var(--c1)',
        stroke: 'var(--panel)', 'stroke-width': p.selected ? 2.5 : 1.4,
        style: 'cursor:pointer'
      });
      c.appendChild(el('title', {}, p.label));
      if (opts.onPick) c.addEventListener('click', () => opts.onPick(p));
      svg.appendChild(c);
    });
  }

  /* ------------------------------------------------------- stacked area */
  function area(host, opts) {
    const series = opts.series;
    const f = frame(host, Object.assign({ height: 250 }, opts));
    const n = Math.max(...series.map(s => s.values.length));
    const totals = Array.from({ length: n }, (_, i) =>
      series.reduce((s, ss) => s + (ss.values[i] || 0), 0));
    const ref = opts.reference;
    const peak = Math.max(...totals, ...(ref ? ref.values : [0]));
    const ys = nice(peak); ys.min = 0;
    axes(f, ys, opts.labels, { maxXTicks: 8 });

    const { svg, m, iw, ih } = f;
    const px = i => m.l + (n === 1 ? iw / 2 : i / (n - 1) * iw);
    const py = v => m.t + ih - (v / ys.max) * ih;

    const acc = new Array(n).fill(0);
    series.forEach((ss, k) => {
      const top = [], bot = [];
      for (let i = 0; i < n; i++) {
        bot.push([px(i), py(acc[i])]);
        acc[i] += ss.values[i] || 0;
        top.push([px(i), py(acc[i])]);
      }
      const d = top.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join('')
        + bot.reverse().map(p => `L${p[0].toFixed(1)},${p[1].toFixed(1)}`).join('') + 'Z';
      const path = svg.appendChild(el('path', { d, fill: col(k), opacity: .78 }));
      path.appendChild(el('title', {}, ss.name));
    });

    if (ref) {
      svg.appendChild(el('path', {
        d: ref.values.map((v, i) => `${i ? 'L' : 'M'}${px(i).toFixed(1)},${py(v).toFixed(1)}`).join(''),
        fill: 'none', stroke: 'var(--ink)', 'stroke-width': 1.8
      }));
    }
    legend(host, series.map(s => s.name).concat(ref ? [ref.name] : []));
  }

  /* ---------------------------------------------------------- waterfall */
  function waterfall(host, opts) {
    const items = opts.items;              // [{name, value}]
    const f = frame(host, Object.assign({ height: 240, margin: { t: 14, r: 12, b: 62, l: 62 } }, opts));
    const total = items.reduce((s, i) => s + i.value, 0);
    const ys = nice(total); ys.min = 0;
    axes(f, ys, null);

    const { svg, m, iw, ih } = f;
    const bw = iw / (items.length + 1);
    let acc = 0;
    items.forEach((it, k) => {
      const h = (it.value / ys.max) * ih;
      const x = m.l + k * bw + bw * 0.15;
      const y = m.t + ih - h - (acc / ys.max) * ih;
      const r = svg.appendChild(el('rect', {
        x, y, width: bw * 0.7, height: Math.max(1, h), fill: col(k), rx: 2
      }));
      r.appendChild(el('title', {}, `${it.name}: ${fmt(it.value)}`));
      svg.appendChild(el('text', {
        x: x + bw * 0.35, y: m.t + ih + 14, 'text-anchor': 'end', 'font-size': 9.5,
        fill: 'var(--muted)', transform: `rotate(-40 ${x + bw * 0.35} ${m.t + ih + 14})`
      }, it.name));
      acc += it.value;
    });
    const x = m.l + items.length * bw + bw * 0.15;
    svg.appendChild(el('rect', {
      x, y: m.t + ih - (total / ys.max) * ih, width: bw * 0.7,
      height: (total / ys.max) * ih, fill: 'var(--ink-2)', rx: 2
    }));
    svg.appendChild(el('text', {
      x: x + bw * 0.35, y: m.t + ih + 14, 'text-anchor': 'end', 'font-size': 9.5,
      fill: 'var(--ink-2)', 'font-weight': 700,
      transform: `rotate(-40 ${x + bw * 0.35} ${m.t + ih + 14})`
    }, 'Total'));
  }

  /* ------------------------------------------------- engineering units */
  /* `fmt` above is an AXIS formatter: it compresses a number so it fits
     under a tick. Using it for a quantity that carries a unit produces
     "6.3k kWp" and "2 x 3.0k kW", which is not how anyone writes power —
     the unit scales, not the number. These do that instead. */

  const UNIT_STEPS = {
    W:   ['W', 'kW', 'MW', 'GW'],
    kW:  ['kW', 'MW', 'GW', 'TW'],
    kWp: ['kWp', 'MWp', 'GWp', 'TWp'],
    kWh: ['kWh', 'MWh', 'GWh', 'TWh'],
    kVA: ['kVA', 'MVA', 'GVA'],
    A:   ['A', 'kA'],
    V:   ['V', 'kV'],
    kg:  ['kg', 't', 'kt'],
  };

  function eng(value, unit, digits) {
    if (value === null || value === undefined || !isFinite(value)) return '—';
    const steps = UNIT_STEPS[unit];
    if (!steps) return sig(value, digits) + (unit ? ' ' + unit : '');
    let v = Math.abs(value);
    let i = 0;
    while (v >= 1000 && i < steps.length - 1) { v /= 1000; i += 1; }
    const scaled = value / Math.pow(1000, i);
    return sig(scaled, digits) + ' ' + steps[i];
  }

  /* Significant figures that read naturally: three for a small number,
     none once it is in the hundreds. */
  function sig(v, digits) {
    if (digits !== undefined) return v.toFixed(digits);
    const a = Math.abs(v);
    if (a === 0) return '0';
    if (a >= 100) return v.toLocaleString(undefined, { maximumFractionDigits: 0 });
    if (a >= 10) return v.toFixed(1);
    if (a >= 1) return v.toFixed(2);
    return v.toFixed(3);
  }

  /* Money. A figure with no currency on it is not a price, and four
     decimal places on a bill is false precision — but a levelised cost in
     a currency with a large unit genuinely needs them, so the precision
     follows the magnitude rather than being fixed. */
  function money(value, currency, opts) {
    if (value === null || value === undefined || !isFinite(value)) return '—';
    const o = opts || {};
    const cur = currency || '';
    const a = Math.abs(value);
    let body;
    if (o.unit) {                       // a rate, e.g. per kWh
      body = a >= 1000 ? Math.round(value).toLocaleString()
           : a >= 1 ? value.toFixed(2)
           : a >= 0.01 ? value.toFixed(3)
           : value.toPrecision(3);
    } else if (a >= 1e9) {
      body = (value / 1e9).toFixed(2) + ' billion';
    } else if (a >= 1e6) {
      body = (value / 1e6).toFixed(2) + ' million';
    } else {
      body = Math.round(value).toLocaleString();
    }
    const out = (cur ? cur + ' ' : '') + body;
    return o.unit ? out + '/' + o.unit : out;
  }

  return { bars, lines, scatter, area, waterfall, fmt, eng, money, sig,
           MONTHS };
})();
