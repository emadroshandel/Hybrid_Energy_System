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

  /* A degenerate range - a series that is flat, all zero, or not finite -
     still has to produce a usable axis. The early return here used to give
     back {max, step} and no `min`, so every consumer computing
     (v - min) / (max - min) got NaN and drew a path of "M48.0,NaN". It
     showed up on the state-of-charge chart of any design with no battery,
     where the series is 365 zeros. Returning a real min fixes every chart
     at once. */
  function nice(max, min = 0) {
    if (!isFinite(max) || !isFinite(min) || max === min) {
      const v = isFinite(max) ? max : 0;
      const lo = Math.min(v, 0);
      const hi = v > 0 ? v : (v < 0 ? 0 : 1);
      return { min: lo, max: hi, step: (hi - lo) / 4 };
    }
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

  /* ------------------------------------------------------ infrastructure
     The patterns follow the D3 discipline — data, then scales, then axes,
     then marks, each separate — without importing D3: the page has to run
     offline and from a file share, so the forty-odd lines it would save are
     not worth a 280 kB dependency. What D3 would add in behaviour is here:
     redraw on resize, a tooltip that follows the pointer AND the keyboard,
     and a table alternative for every chart (WCAG 1.1.1, 2.1.1). */

  const LAST = new WeakMap();          // host -> {fn, opts, w}
  const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(entries => {
    entries.forEach(en => {
      const rec = LAST.get(en.target);
      const w = Math.round(en.contentRect.width);
      if (!rec || w < 40 || Math.abs(w - rec.w) < 4) return;
      // Charts drawn while their page was hidden measured 0 px and fell back
      // to 460; this redraws them at their real width when they appear.
      requestAnimationFrame(() => rec.fn(en.target, rec.opts));
    });
  }) : null;

  function remember(host, fn, opts) {
    if (!LAST.has(host) && ro) ro.observe(host);
    LAST.set(host, { fn, opts, w: Math.round(host.clientWidth) });
  }

  const DASH = ['', '6 4', '2 3', '9 3 2 3'];
  const reduced = () => window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;

  function frame(host, opts) {
    const wasOpen = !!(host.querySelector('details.chart-data[open]'));
    const W = opts.width || host.clientWidth || 460;
    const H = opts.height || 210;
    const m = Object.assign({ t: 14, r: 12, b: 30, l: 48 }, opts.margin);
    host.innerHTML = '';
    let titleId = null;
    if (opts.title) {
      const t = document.createElement('div');
      t.className = 'chart-title';
      t.textContent = opts.title;
      titleId = 'ct-' + Math.random().toString(36).slice(2, 9);
      t.id = titleId;
      host.appendChild(t);
    }
    const wrap = document.createElement('div');
    wrap.className = 'chart-wrap';
    host.appendChild(wrap);
    const svg = el('svg', {
      viewBox: `0 0 ${W} ${H}`, width: '100%', height: H, class: 'chart',
      role: 'img', 'aria-label': (opts.title || 'Chart') + (opts.summary ? '. ' + opts.summary : ''),
    });
    wrap.appendChild(svg);
    return { host, wrap, svg, W, H, m, iw: W - m.l - m.r, ih: H - m.t - m.b, wasOpen, titleId };
  }

  /* Hover/keyboard read-out shared by every index-based chart.
     rowsAt(i) -> { head, items: [{name, value, k, line}] } */
  function crosshair(f, n, xAt, rowsAt) {
    const { svg, wrap, m, ih, iw, W } = f;
    if (!n) return;
    svg.setAttribute('tabindex', '0');
    svg.setAttribute('aria-roledescription', 'interactive chart');
    const guide = el('line', { y1: m.t, y2: m.t + ih, stroke: 'var(--ink-2)',
      'stroke-width': 1, 'stroke-dasharray': '3 3', visibility: 'hidden' });
    svg.appendChild(guide);
    const hit = el('rect', { x: m.l, y: m.t, width: iw, height: ih, fill: 'transparent' });
    svg.appendChild(hit);
    const tip = document.createElement('div');
    tip.className = 'chart-tip'; tip.hidden = true;
    tip.setAttribute('aria-hidden', 'true');
    wrap.appendChild(tip);
    const live = document.createElement('div');
    live.className = 'sr-only'; live.setAttribute('aria-live', 'polite');
    wrap.appendChild(live);
    let cur = -1;

    function showAt(i, speak) {
      cur = Math.max(0, Math.min(n - 1, i));
      const x = xAt(cur);
      guide.setAttribute('x1', x); guide.setAttribute('x2', x);
      guide.setAttribute('visibility', 'visible');
      const r = rowsAt(cur);
      tip.innerHTML = `<b>${r.head}</b>` + r.items.map(it =>
        `<div class="row"><span><i class="swatch${it.line ? ' line dash' + (it.k % 3) : ''}" style="${it.line ? 'border-color' : 'background'}:${col(it.k)}"></i>${it.name}</span><em>${it.value}</em></div>`).join('');
      tip.hidden = false;
      const scale = wrap.clientWidth / W;
      const px = x * scale;
      const left = px + 14 + tip.offsetWidth > wrap.clientWidth ? px - 14 - tip.offsetWidth : px + 14;
      tip.style.left = Math.max(0, left) + 'px';
      tip.style.top = (m.t * scale) + 'px';
      if (speak) live.textContent = r.head + ': ' + r.items.map(it => `${it.name} ${it.value}`).join(', ');
    }
    function hide() { tip.hidden = true; guide.setAttribute('visibility', 'hidden'); }

    svg.addEventListener('pointermove', e => {
      const box = svg.getBoundingClientRect();
      const vx = (e.clientX - box.left) * (W / box.width);
      let best = 0, bd = Infinity;
      for (let i = 0; i < n; i++) { const d = Math.abs(xAt(i) - vx); if (d < bd) { bd = d; best = i; } }
      showAt(best, false);
    });
    svg.addEventListener('pointerleave', () => { if (document.activeElement !== svg) hide(); });
    svg.addEventListener('focus', () => showAt(cur < 0 ? 0 : cur, true));
    svg.addEventListener('blur', hide);
    svg.addEventListener('keydown', e => {
      const big = Math.max(1, Math.round(n / 12));
      const k = { ArrowRight: 1, ArrowLeft: -1, PageDown: big, PageUp: -big }[e.key];
      if (k) { e.preventDefault(); showAt(cur + k, true); }
      else if (e.key === 'Home') { e.preventDefault(); showAt(0, true); }
      else if (e.key === 'End') { e.preventDefault(); showAt(n - 1, true); }
      else if (e.key === 'Escape') hide();
    });
  }

  /* The same numbers as a table, one click away. */
  function dataTable(f, caption, head, rows) {
    const d = document.createElement('details');
    d.className = 'chart-data';
    if (f.wasOpen) d.open = true;
    const esc = t => String(t).replace(/&/g, '&amp;').replace(/</g, '&lt;');
    d.innerHTML = `<summary>Show the data as a table</summary><div class="table-wrap"><table>
      <caption>${esc(caption)}</caption><thead><tr>${head.map(h => `<th scope="col">${esc(h)}</th>`).join('')}</tr></thead>
      <tbody>${rows.map(r => `<tr>${r.map((c, i) => i ? `<td>${esc(c)}</td>` : `<th scope="row">${esc(c)}</th>`).join('')}</tr>`).join('')}</tbody></table></div>`;
    f.host.appendChild(d);
  }

  const val = (v, unit) => (v === null || v === undefined || !isFinite(v)) ? '—'
    : unit ? eng(v, unit) : sig(v);

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
        'font-size': 11, fill: 'var(--muted)'
      }, tick(v)));
    });
    if (xLabels) {
      const n = xLabels.length;
      // Fewer ticks on a narrow chart, so labels never collide.
      // Labels are placed by spacing, not by index, so a sparse label array
      // (one per day, one per month) keeps every label that has room.
      const sparse = xLabels.filter(Boolean).length < n / 2;
      const room = Math.max(3, Math.floor(iw / 38));
      const step = sparse ? 1 : Math.ceil(n / Math.min(opts.maxXTicks || 12, room));
      let lastX = -Infinity;
      xLabels.forEach((lab, i) => {
        if (i % step || !lab) return;
        const x = opts.xAt ? opts.xAt(i) : m.l + (n === 1 ? iw / 2 : (i + 0.5) / n * iw);
        const w = String(lab).length * 6.2;
        if (x - lastX < Math.max(30, w + 8)) return;
        lastX = x;
        svg.appendChild(el('text', {
          x, y: m.t + ih + 15, 'text-anchor': 'middle',
          'font-size': 11, fill: 'var(--muted)'
        }, lab));
      });
    }
    if (opts.xTitle) svg.appendChild(el('text', { x: m.l + iw / 2, y: f.H - 3, 'text-anchor': 'middle', 'font-size': 11, fill: 'var(--ink-2)' }, opts.xTitle));
  }

  function legend(host, names, kinds) {
    const d = document.createElement('div');
    d.className = 'legend';
    names.forEach((n, i) => {
      const s = document.createElement('span');
      const line = kinds && kinds[i] === 'line';
      const k = kinds && kinds[i] === 'ref' ? null : i;
      s.innerHTML = kinds && kinds[i] === 'ref'
        ? `<i class="swatch line" style="border-color:var(--ink)"></i>${n}`
        : `<i class="swatch${line ? ' line dash' + (i % 3) : ''}" style="${line ? 'border-color' : 'background'}:${col(k)}"></i>${n}`;
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
        svg.appendChild(el('rect', {
          x, y, width: Math.max(1, inner), height: Math.max(0.5, h),
          fill: col(k), rx: 1.5,
          // a hairline between stacked segments so adjacent colours separate
          stroke: stacked && series.length > 1 ? 'var(--panel)' : null, 'stroke-width': .8,
        }));
        acc += h;
      });
    });
    crosshair(f, n, i => m.l + (i + 0.5) * bw, i => ({
      head: labels[i] + (stacked && series.length > 1 ? ` — total ${val(totals[i], opts.unit)}` : ''),
      items: series.map((ss, k) => ({ name: ss.name, value: val(ss.values[i] || 0, opts.unit), k })),
    }));
    legend(host, series.map(s => s.name));
    dataTable(f, opts.title || 'Chart data', ['', ...series.map(s => s.name)],
      labels.map((l, i) => [l, ...series.map(ss => val(ss.values[i] || 0, opts.unit))]));
    remember(host, bars, opts);
  }

  /* ------------------------------------------------------------- lines */
  function lines(host, opts) {
    const series = opts.series;
    const labels = opts.labels;
    const f = frame(host, opts);
    const all = series.flatMap(s => s.values).filter(v => isFinite(v));
    const ys = nice(Math.max(...all, 0), Math.min(...all, 0));
    const { svg, m, iw, ih } = f;
    const n = Math.max(...series.map(s => s.values.length));
    const px = i => m.l + (n === 1 ? iw / 2 : i / (n - 1) * iw);
    const py = v => m.t + ih - ((v - ys.min) / (ys.max - ys.min)) * ih;
    axes(f, ys, labels, Object.assign({}, opts, { xAt: px }));

    series.forEach((ss, k) => {
      const d = ss.values.map((v, i) => `${i ? 'L' : 'M'}${px(i).toFixed(1)},${py(v).toFixed(1)}`).join('');
      if (opts.fill) {
        svg.appendChild(el('path', {
          d: d + `L${px(ss.values.length - 1)},${py(ys.min)}L${px(0)},${py(ys.min)}Z`,
          fill: col(k), opacity: 0.12
        }));
      }
      svg.appendChild(el('path', {
        d, fill: 'none', stroke: col(k), 'stroke-width': 2,
        'stroke-dasharray': series.length > 1 ? DASH[k % DASH.length] || null : null,
        'stroke-linejoin': 'round', 'stroke-linecap': 'round'
      }));
    });
    const lab = i => (opts.pointLabels && opts.pointLabels[i]) || (labels && labels[i]) || String(i);
    crosshair(f, n, px, i => ({
      head: (opts.xName ? opts.xName + ' ' : '') + lab(i),
      items: series.map((ss, k) => ({ name: ss.name, value: val(ss.values[i], opts.unit), k, line: true })),
    }));
    if (series.length > 1 || opts.forceLegend) legend(host, series.map(s => s.name), series.map(() => 'line'));
    dataTable(f, opts.title || 'Chart data', [opts.xName || '', ...series.map(s => s.name)],
      Array.from({ length: n }, (_, i) => [lab(i), ...series.map(ss => val(ss.values[i], opts.unit))]));
    remember(host, lines, opts);
  }

  /* ------------------------------------------------------------ scatter */
  function scatter(host, opts) {
    const pts = opts.points;               // [{x,y,label,selected,index,recommended}]
    const f = frame(host, Object.assign({ height: 340, margin: { t: 16, r: 22, b: 44, l: 66 } }, opts));
    const xs = nice(Math.max(...pts.map(p => p.x)), Math.min(...pts.map(p => p.x)));
    const ys = nice(Math.max(...pts.map(p => p.y)), Math.min(...pts.map(p => p.y)));
    const { svg, wrap, m, iw, ih, W } = f;
    // Both axes here span a narrow range around a large number - a front
    // running from 1.27M to 1.39M, or 97.4% to 100% - so the tick labels
    // need their precision from the step, not from the magnitude.
    const ytick = tickFormatter(ys);
    const xtick = tickFormatter(xs);
    const xEvery = Math.max(1, Math.ceil(((xs.max - xs.min) / xs.step + 1) / Math.max(3, Math.floor(iw / 60))));

    let j = 0;
    for (let v = ys.min; v <= ys.max + 1e-9; v += ys.step) {
      const y = m.t + ih - ((v - ys.min) / (ys.max - ys.min)) * ih;
      svg.appendChild(el('line', { x1: m.l, y1: y, x2: m.l + iw, y2: y, stroke: 'var(--line-2)' }));
      svg.appendChild(el('text', { x: m.l - 8, y: y + 3.5, 'text-anchor': 'end', 'font-size': 11, fill: 'var(--muted)' }, ytick(v)));
    }
    for (let v = xs.min; v <= xs.max + 1e-9; v += xs.step, j++) {
      const x = m.l + ((v - xs.min) / (xs.max - xs.min)) * iw;
      svg.appendChild(el('line', { x1: x, y1: m.t, x2: x, y2: m.t + ih, stroke: 'var(--line-2)' }));
      if (j % xEvery === 0) svg.appendChild(el('text', { x, y: m.t + ih + 15, 'text-anchor': 'middle', 'font-size': 11, fill: 'var(--muted)' }, xtick(v)));
    }
    if (opts.xTitle) svg.appendChild(el('text', { x: m.l + iw / 2, y: f.H - 5, 'text-anchor': 'middle', 'font-size': 12, fill: 'var(--ink-2)' }, opts.xTitle));
    if (opts.yTitle) svg.appendChild(el('text', { x: 13, y: m.t + ih / 2, 'text-anchor': 'middle', 'font-size': 12, fill: 'var(--ink-2)', transform: `rotate(-90 13 ${m.t + ih / 2})` }, opts.yTitle));

    const px = v => m.l + ((v - xs.min) / (xs.max - xs.min)) * iw;
    const py = v => m.t + ih - ((v - ys.min) / (ys.max - ys.min)) * ih;

    // Connect the front so the trade-off reads as a curve, not a cloud.
    const sorted = [...pts].sort((a, b) => a.x - b.x || a.y - b.y);
    svg.appendChild(el('path', {
      d: sorted.map((p, i) => `${i ? 'L' : 'M'}${px(p.x).toFixed(1)},${py(p.y).toFixed(1)}`).join(''),
      fill: 'none', stroke: 'var(--c1)', 'stroke-width': 1.2, opacity: .45,
      'stroke-dasharray': '4 3'
    }));

    const focusRing = el('circle', { r: 11, fill: 'none', stroke: 'var(--ink)', 'stroke-width': 2, visibility: 'hidden' });
    const dots = new Map();
    sorted.forEach(p => {
      const g = el('g', { style: 'cursor:pointer' });
      // invisible 24 px hit target around a 9 px dot (WCAG 2.5.8)
      g.appendChild(el('circle', { cx: px(p.x), cy: py(p.y), r: 12, fill: 'transparent' }));
      const c = el('circle', { cx: px(p.x), cy: py(p.y), r: 4.5, fill: 'var(--c1)', stroke: 'var(--panel)', 'stroke-width': 1.4 });
      g.appendChild(c);
      if (p.recommended) {
        g.appendChild(el('circle', { cx: px(p.x), cy: py(p.y), r: 9, fill: 'none', stroke: 'var(--c2)', 'stroke-width': 1.6, 'stroke-dasharray': '2 2' }));
      }
      g.addEventListener('pointerenter', () => point(sorted.indexOf(p), false));
      g.addEventListener('pointerleave', () => { if (document.activeElement !== svg) tip.hidden = true; });
      if (opts.onPick) g.addEventListener('click', () => opts.onPick(p));
      svg.appendChild(g);
      dots.set(p.index, c);
    });
    svg.appendChild(focusRing);

    const tip = document.createElement('div');
    tip.className = 'chart-tip'; tip.hidden = true; tip.setAttribute('aria-hidden', 'true');
    wrap.appendChild(tip);
    const live = document.createElement('div');
    live.className = 'sr-only'; live.setAttribute('aria-live', 'polite');
    wrap.appendChild(live);

    let cur = Math.max(0, sorted.findIndex(p => p.selected));
    function point(k, kb) {
      cur = Math.max(0, Math.min(sorted.length - 1, k));
      const p = sorted[cur];
      tip.textContent = p.label + (p.recommended ? ' (recommended)' : '');
      tip.style.whiteSpace = 'normal'; tip.style.maxWidth = '280px';
      tip.hidden = false;
      const s = wrap.clientWidth / W;
      const x = px(p.x) * s, y = py(p.y) * s;
      tip.style.left = Math.max(0, Math.min(wrap.clientWidth - tip.offsetWidth, x - tip.offsetWidth / 2)) + 'px';
      tip.style.top = (y - tip.offsetHeight - 14 < 0 ? y + 16 : y - tip.offsetHeight - 14) + 'px';
      if (kb) {
        focusRing.setAttribute('cx', px(p.x)); focusRing.setAttribute('cy', py(p.y));
        focusRing.setAttribute('visibility', 'visible');
        live.textContent = `${cur + 1} of ${sorted.length}. ${tip.textContent}`;
      }
    }
    svg.setAttribute('tabindex', '0');
    svg.setAttribute('aria-roledescription', 'interactive scatter chart');
    svg.setAttribute('aria-label', (opts.title || 'Scatter chart') + '. ' + (opts.summary || '')
      + ' Use the left and right arrow keys to move between designs and Enter to open one.');
    svg.addEventListener('focus', () => point(cur, true));
    svg.addEventListener('blur', () => { tip.hidden = true; focusRing.setAttribute('visibility', 'hidden'); });
    svg.addEventListener('keydown', e => {
      const step = { ArrowRight: 1, ArrowUp: 1, ArrowLeft: -1, ArrowDown: -1 }[e.key];
      if (step) { e.preventDefault(); point(cur + step, true); }
      else if (e.key === 'Home') { e.preventDefault(); point(0, true); }
      else if (e.key === 'End') { e.preventDefault(); point(sorted.length - 1, true); }
      else if ((e.key === 'Enter' || e.key === ' ') && opts.onPick) { e.preventDefault(); opts.onPick(sorted[cur]); }
      else if (e.key === 'Escape') { tip.hidden = true; }
    });

    const lg = document.createElement('div');
    lg.className = 'legend';
    lg.innerHTML = `<span><i class="swatch" style="background:var(--c1);border-radius:50%"></i>Pareto-optimal design</span>`
      + `<span><i class="swatch" style="background:var(--c2);border-radius:50%"></i>Selected</span>`
      + (pts.some(p => p.recommended) ? `<span><i class="swatch" style="border:1.6px dashed var(--c2);background:none;border-radius:50%"></i>Recommended</span>` : '');
    host.appendChild(lg);

    host._select = idx => dots.forEach((c, i) => {
      const on = i === idx;
      c.setAttribute('r', on ? 7 : 4.5);
      c.setAttribute('fill', on ? 'var(--c2)' : 'var(--c1)');
      c.setAttribute('stroke-width', on ? 2.5 : 1.4);
      if (on) c.parentNode.parentNode.appendChild(c.parentNode);   // draw on top
    });
    host._select((pts.find(p => p.selected) || {}).index);
    svg.appendChild(focusRing);
    remember(host, scatter, opts);
  }

  /* Change the highlighted point without redrawing the chart. */
  function select(host, idx) {
    if (!host) return;
    const rec = LAST.get(host);
    if (rec) rec.opts.points.forEach(p => { p.selected = p.index === idx; });
    if (host._select) host._select(idx);
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
    const { svg, m, iw, ih } = f;
    const px = i => m.l + (n === 1 ? iw / 2 : i / (n - 1) * iw);
    const py = v => m.t + ih - (v / ys.max) * ih;
    axes(f, ys, opts.labels, { maxXTicks: 8, xAt: px });

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
      svg.appendChild(el('path', { d, fill: col(k), opacity: .8, stroke: 'var(--panel)', 'stroke-width': .6 }));
    });

    if (ref) {
      svg.appendChild(el('path', {
        d: ref.values.map((v, i) => `${i ? 'L' : 'M'}${px(i).toFixed(1)},${py(v).toFixed(1)}`).join(''),
        fill: 'none', stroke: 'var(--ink)', 'stroke-width': 1.8
      }));
    }
    const lab = i => (opts.pointLabels && opts.pointLabels[i]) || `hour ${i}`;
    crosshair(f, n, px, i => ({
      head: lab(i),
      items: series.map((ss, k) => ({ name: ss.name, value: val(ss.values[i] || 0, opts.unit), k }))
        .concat(ref ? [{ name: ref.name, value: val(ref.values[i], opts.unit), k: 0, line: true }] : []),
    }));
    legend(host, series.map(s => s.name).concat(ref ? [ref.name] : []),
      series.map(() => 'area').concat(ref ? ['ref'] : []));
    dataTable(f, opts.title || 'Hourly operation', ['Hour', ...series.map(s => s.name), ...(ref ? [ref.name] : [])],
      Array.from({ length: n }, (_, i) => [lab(i), ...series.map(ss => val(ss.values[i] || 0, opts.unit)),
        ...(ref ? [val(ref.values[i], opts.unit)] : [])]));
    remember(host, area, opts);
  }

  /* ---------------------------------------------------------- waterfall */
  function waterfall(host, opts) {
    const items = opts.items;              // [{name, value}]
    const n0 = (opts.items || []).length + 1;
    const long0 = Math.max(5, ...(opts.items || []).map(i => String(i.name).length));
    const crowded = long0 * 6.6 > ((host.clientWidth || 600) - 74) / n0 * 0.95;
    const f = frame(host, Object.assign({ height: crowded ? 250 : 220, margin: { t: 14, r: 12, b: crowded ? 70 : 34, l: 62 } }, opts));
    const total = items.reduce((s, i) => s + i.value, 0);
    const ys = nice(total); ys.min = 0;
    axes(f, ys, null);

    const { svg, m, iw, ih } = f;
    const bw = iw / (items.length + 1);
    // Labels lie flat when they fit under their bar; only a crowded
    // breakdown tips them to -40 degrees.
    const tilt = crowded;
    const lab = (x, y, extra) => Object.assign(tilt
      ? { x, y, 'text-anchor': 'end', transform: `rotate(-40 ${x} ${y})` }
      : { x, y: y + 2, 'text-anchor': 'middle' },
      { 'font-size': 11, fill: 'var(--ink-2)' }, extra || {});
    let acc = 0;
    const tops = [];
    items.forEach((it, k) => {
      const h = (it.value / ys.max) * ih;
      const x = m.l + k * bw + bw * 0.15;
      const y = m.t + ih - h - (acc / ys.max) * ih;
      tops.push(y);
      svg.appendChild(el('rect', {
        x, y, width: bw * 0.7, height: Math.max(1, h), fill: col(k), rx: 2
      }));
      svg.appendChild(el('text', lab(x + bw * 0.35, m.t + ih + 14), it.name));
      acc += it.value;
    });
    const x = m.l + items.length * bw + bw * 0.15;
    svg.appendChild(el('rect', {
      x, y: m.t + ih - (total / ys.max) * ih, width: bw * 0.7,
      height: (total / ys.max) * ih, fill: 'var(--ink-2)', rx: 2
    }));
    svg.appendChild(el('text', lab(x + bw * 0.35, m.t + ih + 14, { 'font-weight': 700 }), 'Total'));
    const fmtV = opts.format || fmt;
    crosshair(f, items.length + 1, i => m.l + (i + 0.5) * bw, i => i < items.length
      ? { head: items[i].name, items: [{ name: 'NPC', value: fmtV(items[i].value), k: i },
          { name: 'Share', value: (items[i].value / total * 100).toFixed(1) + '%', k: i }] }
      : { head: 'Total', items: [{ name: 'NPC', value: fmtV(total), k: 0 }] });
    dataTable(f, opts.title || 'Cost breakdown', ['Component', 'NPC', 'Share'],
      items.map(it => [it.name, fmtV(it.value), (it.value / total * 100).toFixed(1) + '%'])
        .concat([['Total', fmtV(total), '100%']]));
    remember(host, waterfall, opts);
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

  return { bars, lines, scatter, area, waterfall, select, fmt, eng, money, sig,
           MONTHS };
})();
