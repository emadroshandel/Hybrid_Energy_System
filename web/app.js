/* EnerSys application logic.
 *
 * Transport-agnostic: every call goes through window.ENSYS.call(action,
 * payload), which boot.js binds either to the HTTP server or to Pyodide.
 * Nothing in this file knows or cares which one is in use. */

const App = (() => {
  const PAGES = [
    { id: 'site',       title: 'Site',        sub: 'Location and resource data' },
    { id: 'load',       title: 'Load',        sub: 'Demand profile to be served' },
    { id: 'components', title: 'Components',  sub: 'Technologies available to the optimiser' },
    { id: 'economics',  title: 'Economics',   sub: 'Financial parameters and objectives' },
    { id: 'optimise',   title: 'Optimise',    sub: 'Search for the Pareto-optimal designs' },
    { id: 'results',    title: 'Results',     sub: 'Energy balance and performance' },
    { id: 'design',     title: 'Design',      sub: 'Converters, cables and protection' },
    { id: 'diagrams',   title: 'Diagrams',    sub: 'Single-line and three-line schematics' },
    { id: 'report',     title: 'Report',      sub: 'Export the study' },
  ];

  const S = {
    page: 0, done: new Set(), info: null, front: [], selected: null,
    detail: null, session: 'default', resourceLoaded: false, loadLoaded: false,
  };

  const $ = s => document.querySelector(s);
  const $$ = s => Array.from(document.querySelectorAll(s));
  const call = (a, p) => window.ENSYS.call(a, p || {});

  /* Units and money go through Chart.eng / Chart.money, never through the
     axis formatter: a quantity carries a unit, and the unit is what
     scales. "6.3k kWp" is not a power. */
  const curCode = () => (($('#currency') || {}).value) || 'USD';
  const eng = (v, u, d) => Chart.eng(v, u, d);
  const cash = (v, o) => Chart.money(v, curCode(), o);
  const perKwh = v => Chart.money(v, curCode(), { unit: 'kWh' });

  function status(el, msg, kind) {
    const n = typeof el === 'string' ? $(el) : el;
    n.className = 'status' + (kind ? ' ' + kind : '');
    n.textContent = msg || '';
  }

  /* ------------------------------------------------------------ startup */
  /* ------------------------------------------------------- engine banner */
  // The interface never waits on the engine to become visible. When the
  // engine cannot be reached the application is still there to look at and
  // move around in, and this banner says what is wrong.

  function banner(msg, hint, detail) {
    const b = $('#engine-banner');
    if (!b) return;
    if (!msg) { b.hidden = true; return; }
    $('#engine-banner-msg').textContent = msg;
    $('#engine-banner-hint').textContent = hint || '';
    $('#engine-banner-detail').textContent =
      (detail ? detail + '\n\n' : '')
      + (window.ENSYS && window.ENSYS.diagnostics
         ? window.ENSYS.diagnostics() : '');
    b.hidden = false;
  }

  function wireBanner() {
    const retry = $('#engine-retry');
    const copy = $('#engine-copy');
    if (retry) retry.addEventListener('click', () => location.reload());
    if (copy) copy.addEventListener('click', async () => {
      const text = $('#engine-banner-detail').textContent;
      try {
        await navigator.clipboard.writeText(text);
        copy.textContent = 'Copied';
      } catch (e) {
        const r = document.createRange();
        r.selectNodeContents($('#engine-banner-detail'));
        const sel = window.getSelection();
        sel.removeAllRanges(); sel.addRange(r);
        copy.textContent = 'Selected — press Ctrl+C';
      }
    });
  }

  async function init() {
    // Step one, before anything that can fail: put the application on the
    // screen. Everything after this is enrichment.
    buildNav();
    wireChrome();
    wireTips();
    wireBanner();
    show(0);

    let info;
    try {
      await window.ENSYS.ready;
      $('#engine-badge').textContent = window.ENSYS.mode;
      info = await call('info');
    } catch (e) {
      return banner(
        'The calculation engine could not be started.',
        'The interface is usable, but nothing can be calculated until the '
        + 'engine is reachable. Run "python server.py" from the project '
        + 'folder — or, on Windows, double-click START_EnerSys.bat — and '
        + 'reload this page.',
        (e && e.message) || String(e));
    }

    if (!info || !info.ok) {
      return banner(
        'The calculation engine is not answering.',
        'The interface is usable, but the option lists below could not be '
        + 'loaded and no study can be run until this is resolved.',
        (info && info.error) || 'no response');
    }

    S.info = info;
    fillPresets(info.presets);
    buildComponents();
    buildObjectives(info.presets.objectives);
    banner(null);
  }

  function buildNav() {
    const nav = $('#nav');
    nav.innerHTML = '';
    PAGES.forEach((p, i) => {
      const b = document.createElement('button');
      b.className = 'nav-item';
      b.type = 'button';
      b.innerHTML = `<span class="nav-num">${i + 1}</span><span>${p.title}</span>`;
      b.addEventListener('click', () => show(i));
      nav.appendChild(b);
    });
  }

  function show(i) {
    S.page = Math.max(0, Math.min(PAGES.length - 1, i));
    const p = PAGES[S.page];
    $$('#pages .page').forEach(s => { s.hidden = s.dataset.page !== p.id; });
    $$('.nav-item').forEach((b, k) => {
      b.classList.toggle('active', k === S.page);
      b.classList.toggle('done', S.done.has(PAGES[k].id));
    });
    $('#page-title').textContent = p.title;
    $('#page-sub').textContent = p.sub;
    $('#btn-prev').disabled = S.page === 0;
    $('#btn-next').disabled = S.page === PAGES.length - 1;
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  function wireChrome() {
    $('#btn-prev').addEventListener('click', () => show(S.page - 1));
    $('#btn-next').addEventListener('click', () => show(S.page + 1));
    $('#theme-toggle').addEventListener('click', toggleTheme);

    $('#res-mode').addEventListener('click', e => {
      const b = e.target.closest('.seg-btn'); if (!b) return;
      $$('#res-mode .seg-btn').forEach(x => x.classList.toggle('active', x === b));
      $('#res-online').hidden = b.dataset.mode !== 'online';
      $('#res-offline').hidden = b.dataset.mode !== 'offline';
    });
    $('#load-mode').addEventListener('click', e => {
      const b = e.target.closest('.seg-btn'); if (!b) return;
      $$('#load-mode .seg-btn').forEach(x => x.classList.toggle('active', x === b));
      ['file', 'paste', 'synthetic'].forEach(m => {
        $('#load-' + m).hidden = b.dataset.mode !== m;
      });
    });

    $('#loc-preset').addEventListener('change', applyPreset);
    $('#btn-fetch').addEventListener('click', fetchResources);
    const scaleHost = $('#site-scale');
    if (scaleHost) {
      scaleHost.addEventListener('click', e => {
        const b = e.target.closest('.seg-btn');
        if (!b) return;
        $$('#site-scale .seg-btn').forEach(x => x.classList.toggle('active', x === b));
        applySiteScale(b.dataset.scale);
      });
    }
    $('#btn-load').addEventListener('click', loadProfile);
    $('#btn-wind').addEventListener('click', loadWind);
    $('#btn-run').addEventListener('click', runStudy);
    $('#btn-report').addEventListener('click', makeReport);
    $('#btn-svg').addEventListener('click', downloadDiagrams);
    $('#btn-csv').addEventListener('click', downloadCsv);
    $('#btn-json').addEventListener('click', saveProject);
    const opener = $('#project-open');
    if (opener) opener.addEventListener('change', importProject);

    const saved = localStorage.getItem('ensys-theme');
    if (saved) document.documentElement.setAttribute('data-theme', saved);
  }

  function toggleTheme() {
    const cur = document.documentElement.getAttribute('data-theme');
    const next = cur === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    try { localStorage.setItem('ensys-theme', next); } catch (e) { /* private mode */ }
  }

  function wireTips() {
    const tip = $('#tip');
    document.addEventListener('mouseover', e => {
      const q = e.target.closest('.q'); if (!q) return;
      tip.textContent = q.dataset.help;
      tip.hidden = false;
      const r = q.getBoundingClientRect();
      tip.style.left = Math.min(window.innerWidth - 310, r.left) + 'px';
      tip.style.top = (r.bottom + 8) + 'px';
    });
    document.addEventListener('mouseout', e => {
      if (e.target.closest('.q')) tip.hidden = true;
    });
  }

  /* --------------------------------------------------------------- site */
  function fillPresets(p) {
    const sel = $('#loc-preset');
    sel.innerHTML = '<option value="">Custom…</option>';
    Object.entries(p.locations).forEach(([k, v]) => {
      sel.appendChild(new Option(v.name, k));
    });
    const t = $('#terrain');
    t.innerHTML = '';
    Object.entries(p.terrain).forEach(([k, v]) => {
      t.appendChild(new Option(`${k.replace(/_/g, ' ')} (α=${v})`, k));
    });
    t.value = 'open_farmland';
    const st = $('#strategy');
    st.innerHTML = '';
    p.strategies.forEach(s => st.appendChild(new Option(s.replace(/_/g, ' '), s)));

    // The algorithm menu is built from what the engine reports, never from
    // a list written here. A build that gains an algorithm should gain the
    // menu entry with it, and one that loses it should not offer a choice
    // the engine will refuse.
    const al = $('#algorithm');
    al.innerHTML = '';
    S.algorithms = p.algorithms || [];
    S.algorithms.forEach(a => al.appendChild(new Option(a.label, a.key)));
    if (p.default_algorithm) al.value = p.default_algorithm;
    al.addEventListener('change', describeAlgorithm);
    describeAlgorithm();
  }

  function describeAlgorithm() {
    const key = $('#algorithm').value;
    const a = (S.algorithms || []).find(x => x.key === key);
    $('#algorithm-note').textContent = a ? a.detail : '';
    // "Swarm size" is the swarm's name for it. A genetic algorithm has a
    // population and an enumeration has neither, so the label follows the
    // choice rather than quietly meaning something else.
    const labels = {
      mopso: 'Swarm size', nsga2: 'Population size',
      grid: 'Budget width',
    };
    $('#particles-label').textContent = labels[key] || 'Population size';
    const exact = a && a.exact;
    $('#seed').disabled = !!exact;
    $('#seed').title = exact
      ? 'An exhaustive search has no random element, so the seed does nothing.'
      : '';
  }

  function applyPreset() {
    const k = $('#loc-preset').value;
    if (!k) return;
    const l = S.info.presets.locations[k];
    $('#lat').value = l.latitude; $('#lon').value = l.longitude;
    $('#elev').value = l.elevation_m; $('#tz').value = l.utc_offset_hours;
    $('#site-name').value = l.name; $('#terrain').value = l.terrain;
  }

  function location() {
    return {
      name: $('#site-name').value, latitude: +$('#lat').value,
      longitude: +$('#lon').value, elevation_m: +$('#elev').value,
      utc_offset_hours: +$('#tz').value, terrain: $('#terrain').value,
    };
  }

  const nums = s => (s || '').split(/[,\s]+/).filter(Boolean).map(Number).filter(n => isFinite(n));

  async function fetchResources() {
    const mode = $('#res-mode .active').dataset.mode;
    status('#res-status', 'Retrieving…');
    $('#btn-fetch').disabled = true;

    const payload = { session: S.session, location: location(), mode };
    if (mode === 'online') {
      payload.providers = [$('#res-provider').value, 'open_meteo', 'nasa_power'];
      payload.year = +$('#res-year').value;
    } else {
      payload.monthly_ghi = nums($('#monthly-ghi').value);
      payload.monthly_temperature = nums($('#monthly-temp').value);
      payload.mean_wind_speed = +$('#mean-wind').value;
      if (payload.monthly_ghi.length !== 12) {
        status('#res-status', `Twelve monthly GHI values are needed; ${payload.monthly_ghi.length} were given.`, 'err');
        $('#btn-fetch').disabled = false; return;
      }
    }

    const r = await call('fetch_resources', payload);
    if (r.ok) renderFindings('#res-findings', r.findings);
    $('#btn-fetch').disabled = false;
    if (!r.ok) { status('#res-status', r.error, 'err'); return; }

    S.resourceLoaded = true;
    S.done.add('site');
    let msg = `${r.provider} — ${Chart.sig(r.annual_ghi_kwh_m2)} kWh/m²/yr`;
    status('#res-status', msg, r.synthetic ? 'warn' : 'ok');

    $('#res-charts').hidden = false;
    if (r.monthly_ghi) {
      Chart.bars($('#chart-ghi'), {
        title: 'Monthly irradiation (kWh/m²)',
        series: [{ name: 'GHI', values: r.monthly_ghi }],
      });
    }
    if (r.monthly_temperature) {
      Chart.lines($('#chart-temp'), {
        title: 'Monthly mean temperature (°C)', labels: Chart.MONTHS,
        series: [{ name: 'Air temperature', values: r.monthly_temperature }], fill: true,
      });
    }
    renderProvenance(r);
    show(1);
  }

  function renderProvenance(r) {
    const box = $('#res-provenance');
    let h = '';
    if (r.long_term_average && r.long_term_average.ghi_kwh_m2_year) {
      const l = r.long_term_average;
      h += `<strong>Global Solar Atlas cross-check:</strong> GHI ${Chart.sig(l.ghi_kwh_m2_year)}, `
        + `DNI ${Chart.sig(l.dni_kwh_m2_year)} kWh/m²/yr, PV yield ${Chart.sig(l.pvout_kwh_kwp_year)} kWh/kWp, `
        + `optimum tilt ${l.optimum_tilt_deg}°, elevation ${l.elevation_m} m.`;
    }
    if (r.licence) h += `<br>Licence: ${r.licence}`;
    box.innerHTML = h;
    if (r.warnings && r.warnings.length) {
      const ul = document.createElement('ul');
      ul.className = 'warn-list';
      r.warnings.forEach(w => { const li = document.createElement('li'); li.textContent = w; ul.appendChild(li); });
      box.appendChild(ul);
    }
  }

  /* --------------------------------------------------------------- load */
  function readFile(input) {
    return new Promise((res, rej) => {
      const f = input.files && input.files[0];
      if (!f) return res(null);
      const r = new FileReader();
      r.onload = () => res(r.result);
      r.onerror = () => rej(new Error('Could not read the file.'));
      r.readAsText(f);
    });
  }

  async function loadProfile() {
    const mode = $('#load-mode .active').dataset.mode;
    const payload = { session: S.session, name: 'load', scale: +$('#load-scale').value };

    if (mode === 'file') {
      const text = await readFile($('#load-upload'));
      if (!text) { status('#load-status', 'Choose a file first.', 'err'); return; }
      payload.csv = text;
      payload.column = $('#load-column').value || null;
    } else if (mode === 'paste') {
      const t = $('#load-text').value.trim();
      if (!t) { status('#load-status', 'Paste some data first.', 'err'); return; }
      if (/[a-zA-Z]/.test(t.split('\n')[0])) payload.csv = t;
      else payload.values = nums(t);
    } else {
      payload.values = synthProfile(
        +$('#syn-peak').value, +$('#syn-lf').value, $('#syn-shape').value);
    }

    status('#load-status', 'Processing…');
    const r = await call('import_series', payload);
    if (!r.ok) { status('#load-status', r.error, 'err'); return; }

    S.loadLoaded = true; S.done.add('load');
    const s = r.stats;
    status('#load-status',
      `${r.hours} hours loaded. ${r.notes.join(' ')}`.trim(), 'ok');

    renderFindings('#load-findings', r.findings);

    $('#load-charts').hidden = false;
    $('#load-kpis').innerHTML = [
      kpi(eng(s.sum, 'kWh'), 'Annual demand'),
      kpi(eng(s.max, 'kW'), 'Peak demand'),
      kpi(eng(s.mean, 'kW'), 'Average demand'),
      kpi((s.load_factor * 100).toFixed(0) + '%', 'Load factor',
          'average / peak'),
      kpi(eng(s.p95, 'kW'), '95th percentile'),
      kpi(eng(s.min, 'kW'), 'Minimum (base load)'),
    ].join('');

    Chart.bars($('#chart-load-month'), {
      title: 'Monthly demand (kWh)',
      series: [{ name: 'Demand', values: r.monthly }],
    });
    Chart.lines($('#chart-load-day'), {
      title: 'Average day (kW)',
      labels: Array.from({ length: 24 }, (_, i) => String(i)),
      series: [{ name: 'Demand', values: r.daily_profile }], fill: true,
    });
  }

  function synthProfile(peak, lf, shape) {
    /* Build a plausible 8760-hour profile. This is for exploring the tool,
       not for a real study - a synthetic profile has none of the weekday,
       holiday or weather correlation that drives real sizing. */
    const shapes = {
      commercial:  [.32,.30,.29,.29,.30,.36,.52,.74,.92,.98,1,.99,.95,.97,.99,.96,.86,.70,.55,.46,.41,.38,.36,.34],
      residential: [.44,.39,.36,.35,.36,.44,.60,.72,.66,.58,.54,.53,.54,.53,.55,.62,.78,.94,1,.97,.88,.75,.62,.51],
      industrial:  [.48,.47,.47,.48,.56,.78,.94,.99,1,1,.99,.96,.94,.99,1,.99,.92,.74,.58,.52,.50,.49,.49,.48],
      continuous:  Array(24).fill(0).map((_, i) => 0.92 + 0.08 * Math.sin(i / 24 * 6.283)),
    };
    const sh = shapes[shape] || shapes.commercial;
    const shMean = sh.reduce((a, b) => a + b) / 24;
    const out = [];
    for (let t = 0; t < 8760; t++) {
      const d = Math.floor(t / 24), h = t % 24;
      const dow = d % 7;
      const weekend = (shape !== 'continuous' && (dow === 5 || dow === 6)) ? 0.68 : 1;
      const seasonal = 1 + 0.16 * Math.cos(2 * Math.PI * (d - 200) / 365);
      const noise = 0.96 + 0.08 * Math.random();
      out.push(peak * (sh[h] / 1) * weekend * seasonal * noise);
    }
    // Rescale so the requested load factor is actually achieved.
    const mx = Math.max(...out);
    const mean = out.reduce((a, b) => a + b) / out.length;
    const wantMean = peak * lf;
    const k = wantMean / mean;
    return out.map(v => Math.min(peak, v * k));
  }

  async function loadWind() {
    const text = await readFile($('#wind-upload'));
    if (!text) { status('#wind-status', 'Choose a file first.', 'err'); return; }
    const r = await call('import_series', {
      session: S.session, name: 'wind_speed', csv: text,
      column: $('#wind-column').value || null,
    });
    if (!r.ok) { status('#wind-status', r.error, 'err'); return; }
    status('#wind-status',
      `${r.hours} hours, mean ${r.stats.mean.toFixed(2)} m/s, max ${r.stats.max.toFixed(1)} m/s. ${r.notes.join(' ')}`, 'ok');
  }

  /* Plausibility findings from the engine. These are the difference
     between a study that silently sizes the wrong site and one that says
     out loud what it thinks it was asked, so they are rendered prominently
     rather than tucked into a status line. */
  function renderFindings(hostSel, findings) {
    const host = $(hostSel);
    if (!host) return;
    const list = (findings || []).filter(f => f && f.message);
    if (!list.length) { host.innerHTML = ''; host.hidden = true; return; }
    const rank = { error: 0, warning: 1, info: 2 };
    list.sort((a, b) => (rank[a.level] ?? 3) - (rank[b.level] ?? 3));
    const label = { error: 'Check this', warning: 'Worth checking',
                    info: 'How this was read' };
    host.innerHTML = list.map(f => `
      <div class="finding finding-${f.level}">
        <div class="finding-tag">${label[f.level] || f.level}</div>
        <div class="finding-msg">${escapeHtml(f.message)}</div>
      </div>`).join('');
    host.hidden = false;
  }

  const escapeHtml = t => String(t)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

  const kpi = (v, l, sub) =>
    `<div class="kpi"><div class="kpi-val">${v}</div><div class="kpi-lbl">${l}</div>`
    + (sub ? `<div class="kpi-sub">${sub}</div>` : '') + '</div>';

  /* ------------------------------------------------------- site scale */
  /* The optimiser buys whole units, so the unit sizes decide what answers
     it can even express. Defaults sized for a factory make a house
     unanswerable - the smallest non-zero PV array is already eight times
     the site - and nothing in the numbers says so. These presets move the
     load, the unit sizes, the connection and the costs together, because
     changing one without the others is exactly how a study ends up
     internally inconsistent.

     The unit COSTS are derived from specific rates rather than carried
     across, so a 1 kWp block never keeps a 25 kWp block's price tag. */
  const RATES = {
    pv:      { capital: 700, replacement: 600, om: 8.8 },   // per kWp
    battery: { capital: 250, replacement: 200, om: 2.5 },   // per kWh
    wind:    { capital: 1800, replacement: 1500, om: 40 },  // per kW
    genset:  { capital: 300, replacement: 280 },            // per kW
  };

  const SITE_SCALES = {
    house: {
      label: 'House', peak: 5, lf: 0.35, shape: 'residential',
      pv_kwp: 0.5, battery_kwh: 2.5, c_rate: 0.5, wind_kw: 3,
      genset_kw: 5, import_kw: 15, export_kw: 5,
      note: 'a single dwelling: about 15 MWh a year',
    },
    commercial: {
      label: 'Small commercial', peak: 100, lf: 0.45, shape: 'commercial',
      pv_kwp: 10, battery_kwh: 25, c_rate: 0.5, wind_kw: 50,
      genset_kw: 100, import_kw: 150, export_kw: 50,
      note: 'a shop, clinic or office: about 400 MWh a year',
    },
    industrial: {
      label: 'Industrial', peak: 1000, lf: 0.60, shape: 'industrial',
      pv_kwp: 100, battery_kwh: 250, c_rate: 0.5, wind_kw: 250,
      genset_kw: 500, import_kw: 1200, export_kw: 400,
      note: 'a factory or campus: about 5 GWh a year',
    },
  };

  const round2 = v => Math.round(v * 100) / 100;

  function setField(comp, field, value) {
    const el = $(`[data-c="${comp}"][data-f="${field}"]`);
    if (el) el.value = value;
  }

  function applySiteScale(key) {
    const p = SITE_SCALES[key];
    if (!p) return;

    const synPeak = $('#syn-peak'), synLf = $('#syn-lf'), synShape = $('#syn-shape');
    if (synPeak) synPeak.value = p.peak;
    if (synLf) synLf.value = p.lf;
    if (synShape) synShape.value = p.shape;

    setField('pv', 'unit_kwp', p.pv_kwp);
    setField('pv', 'capital_cost', round2(p.pv_kwp * RATES.pv.capital));
    setField('pv', 'replacement_cost', round2(p.pv_kwp * RATES.pv.replacement));
    setField('pv', 'om_cost_per_year', round2(p.pv_kwp * RATES.pv.om));

    setField('battery', 'unit_kwh', p.battery_kwh);
    setField('battery', 'unit_kw', round2(p.battery_kwh * p.c_rate));
    setField('battery', 'capital_cost', round2(p.battery_kwh * RATES.battery.capital));
    setField('battery', 'replacement_cost', round2(p.battery_kwh * RATES.battery.replacement));
    setField('battery', 'om_cost_per_year', round2(p.battery_kwh * RATES.battery.om));

    setField('wind', 'rated_kw', p.wind_kw);
    setField('wind', 'capital_cost', round2(p.wind_kw * RATES.wind.capital));
    setField('wind', 'replacement_cost', round2(p.wind_kw * RATES.wind.replacement));
    setField('wind', 'om_cost_per_year', round2(p.wind_kw * RATES.wind.om));

    setField('genset', 'rated_kw', p.genset_kw);
    setField('genset', 'capital_cost', round2(p.genset_kw * RATES.genset.capital));
    setField('genset', 'replacement_cost', round2(p.genset_kw * RATES.genset.replacement));

    setField('grid', 'import_limit_kw', p.import_kw);
    setField('grid', 'export_limit_kw', p.export_kw);

    status('#scale-status',
      `Set up for ${p.label.toLowerCase()} - ${p.note}. Peak demand, unit `
      + `sizes, the grid connection and every per-unit cost have been moved `
      + `together; adjust any of them freely from here.`, 'ok');
  }

  /* Changing a unit SIZE without changing its price is the quietest way to
     get a wrong answer: the optimiser keeps buying blocks at the old block's
     cost. Rescaling the cost fields with the size keeps the specific rate
     the user actually chose, and says so. */
  const COST_LINKS = {
    pv: { size: 'unit_kwp',
          costs: ['capital_cost', 'replacement_cost', 'om_cost_per_year'] },
    battery: { size: 'unit_kwh',
               costs: ['capital_cost', 'replacement_cost', 'om_cost_per_year'],
               also: ['unit_kw'] },
    wind: { size: 'rated_kw',
            costs: ['capital_cost', 'replacement_cost', 'om_cost_per_year'] },
    genset: { size: 'rated_kw',
              costs: ['capital_cost', 'replacement_cost'] },
  };

  function linkUnitCosts() {
    Object.entries(COST_LINKS).forEach(([comp, spec]) => {
      const sizeEl = $(`[data-c="${comp}"][data-f="${spec.size}"]`);
      if (!sizeEl) return;
      sizeEl.dataset.prev = sizeEl.value;
      sizeEl.addEventListener('change', () => {
        const before = parseFloat(sizeEl.dataset.prev);
        const after = parseFloat(sizeEl.value);
        sizeEl.dataset.prev = sizeEl.value;
        if (!(before > 0) || !(after > 0) || before === after) return;
        const k = after / before;
        [...spec.costs, ...(spec.also || [])].forEach(f => {
          const el = $(`[data-c="${comp}"][data-f="${f}"]`);
          if (!el) return;
          const v = parseFloat(el.value);
          if (v > 0) el.value = round2(v * k);
        });
        status('#scale-status',
          `The ${comp} unit changed from ${before} to ${after}, so its `
          + `per-unit costs were scaled by ${k.toFixed(3)} to keep the same `
          + `price per kW or kWh. Overwrite them if your quote says `
          + `otherwise.`, 'ok');
      });
    });
  }

  /* --------------------------------------------------- component cards */
  /* Rendered from the engine's own catalogue, not from a list kept here.
     The catalogue's docstring always said the interface should be generated
     from it "rather than hand-written per technology and then drifting out
     of step with the model" - and the interface had drifted a long way. It
     offered five technologies; the engine models twenty, each with a cost
     entry, a dispatch model, translations and a diagram symbol. The other
     fifteen could not be selected, so they were never considered, so no
     study could recommend one.

     Building the cards from the schema also means every cost field the
     engine accepts is on screen and editable, which is the only way "leave
     it at zero to use the regional library" can be a true statement. */

  // Shown and enabled by default. Everything else is a click away.
  const DEFAULT_ON = { pv: true, battery: true };
  const CORE = ['pv', 'wind', 'battery', 'genset', 'ev_fleet'];

  function fieldInput(tech, f) {
    const k = f.key;
    const dflt = f.default;
    const attrs = `data-c="${tech}" data-f="${k}"`;

    if (f.kind === 'select' && (f.options || []).length) {
      const opts = f.options.map(o => {
        const key = (o && o.key !== undefined) ? o.key : o;
        const lbl = (o && o.label !== undefined) ? o.label
                  : String(key).replace(/_/g, ' ');
        return `<option value="${key}"${key === dflt ? ' selected' : ''}>${escapeHtml(lbl)}</option>`;
      }).join('');
      return `<select ${attrs}>${opts}</select>`;
    }
    if (dflt === null || dflt === undefined) {
      // A blank number, e.g. PV tilt meaning "use the latitude optimum".
      return `<input type="number" step="any" ${attrs} value="" placeholder="auto">`;
    }
    if (typeof dflt === 'boolean') {
      return `<select ${attrs}><option value="1"${dflt ? ' selected' : ''}>Yes</option>`
           + `<option value="0"${dflt ? '' : ' selected'}>No</option></select>`;
    }
    if (typeof dflt === 'number') {
      return `<input type="number" step="any" ${attrs} value="${dflt}">`;
    }
    return `<input type="text" ${attrs} value="${escapeHtml(String(dflt))}">`;
  }

  function unitSuffix(unit) {
    if (!unit) return '';
    if (unit === 'currency') {
      const c = $('#currency');
      return c && c.value ? ` (${c.value})` : ' (per unit)';
    }
    if (unit === 'fraction') return ' (0-1)';
    return ` (${unit})`;
  }

  function componentCard(entry) {
    const tech = entry.technology;
    const on = !!DEFAULT_ON[tech];
    const rows = entry.fields.map(f => {
      const help = f.help
        ? ` <i class="q" data-help="${escapeHtml(f.help).replace(/"/g, '&quot;')}">?</i>` : '';
      return `<label class="field"><span>${escapeHtml(f.label)}`
           + `${escapeHtml(unitSuffix(f.unit))}${help}</span>`
           + `${fieldInput(tech, f)}</label>`;
    }).join('');

    const card = document.createElement('div');
    card.className = 'card';
    card.innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
        <h2 style="margin:0">${escapeHtml(entry.label)}</h2>
        <label style="display:flex;gap:7px;align-items:center;font-size:12.5px;font-weight:600">
          <input type="checkbox" data-enable="${tech}" ${on ? 'checked' : ''}> Include
        </label>
      </div>
      <div class="row-3" data-body="${tech}">${rows}</div>`;

    const cb = card.querySelector(`[data-enable="${tech}"]`);
    const body = card.querySelector(`[data-body="${tech}"]`);
    const sync = () => { body.style.opacity = cb.checked ? '1' : '.4';
                         body.style.pointerEvents = cb.checked ? '' : 'none'; };
    cb.addEventListener('change', sync); sync();
    return card;
  }

  /* The grid is not in the catalogue - it is not sized and has no unit
     count - so it keeps a hand-written card. */
  const GRID_FIELDS = [
    ['import_limit_kw', 'Import limit (kW)', 250],
    ['export_limit_kw', 'Export limit (kW)', 100, 'Often far below the import capacity of the same connection.'],
    ['import_price', 'Import price per kWh', 0.09],
    ['export_price', 'Export price per kWh', 0.03],
    ['demand_charge', 'Demand charge per kW-month', 0, 'Charged on the monthly peak. Often the largest line on a commercial bill.'],
    ['standing_charge', 'Standing charge per year', 0],
    ['emission_factor', 'Grid CO2 (kg/kWh)', 0.4],
    ['availability', 'Availability', 1.0, 'Fraction of hours the supply is present. Below 1.0 models an unreliable network.'],
    ['mean_outage_hours', 'Mean outage length (h)', 4, 'How long one interruption lasts on average. With the availability this fixes how OFTEN the supply fails, and that is what sizes the backup: 5% lost as 440 one-hour blips needs almost no storage, the same 5% as 35 outages of twelve hours needs a great deal.'],
  ];

  function gridCard() {
    const rows = GRID_FIELDS.map(([k, label, dflt, help]) =>
      `<label class="field"><span>${label}`
      + (help ? ` <i class="q" data-help="${help.replace(/"/g, '&quot;')}">?</i>` : '')
      + `</span><input type="number" step="any" data-c="grid" data-f="${k}" value="${dflt}"></label>`
    ).join('');
    const card = document.createElement('div');
    card.className = 'card';
    card.innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
        <h2 style="margin:0">Grid connection</h2>
        <label style="display:flex;gap:7px;align-items:center;font-size:12.5px;font-weight:600">
          <input type="checkbox" data-enable="grid" checked> Include
        </label>
      </div>
      <div class="row-3" data-body="grid">${rows}</div>`;
    const cb = card.querySelector('[data-enable="grid"]');
    const body = card.querySelector('[data-body="grid"]');
    const sync = () => { body.style.opacity = cb.checked ? '1' : '.4';
                         body.style.pointerEvents = cb.checked ? '' : 'none'; };
    cb.addEventListener('change', sync); sync();
    return card;
  }

  let ALL_TECHS = [];

  function buildComponents() {
    const schema = (S.info.presets.technologies || {});
    const groups = schema.groups || {};
    ALL_TECHS = [];
    const byTech = {};
    Object.entries(groups).forEach(([group, entries]) => {
      entries.forEach(e => { byTech[e.technology] = e; ALL_TECHS.push(e.technology); });
    });

    const host = $('#components');
    const extra = $('#components-extra');
    host.innerHTML = '';
    extra.innerHTML = '';

    // The five named technologies first, in the order the decision vector
    // uses, then the grid — this is the page people expect to see.
    CORE.forEach(t => { if (byTech[t]) host.appendChild(componentCard(byTech[t])); });
    host.appendChild(gridCard());

    // Everything else, grouped as the catalogue groups it.
    Object.entries(groups).forEach(([group, entries]) => {
      const rest = entries.filter(e => !CORE.includes(e.technology));
      if (!rest.length) return;
      const h = document.createElement('h2');
      h.className = 'group-heading';
      h.textContent = group;
      extra.appendChild(h);
      rest.forEach(e => extra.appendChild(componentCard(e)));
    });

    buildCostBasis(schema);
    linkUnitCosts();
  }

  function buildCostBasis(schema) {
    const regions = schema.regions || [];
    const currencies = schema.currencies || [];
    const rSel = $('#cost-region'), cSel = $('#cost-currency');
    if (rSel && regions.length) {
      rSel.innerHTML = regions.map(r =>
        `<option value="${r.key}"${r.key === 'global' ? ' selected' : ''}>${escapeHtml(r.label)}</option>`
      ).join('');
      rSel.addEventListener('change', () => {
        const r = regions.find(x => x.key === rSel.value);
        if (r && r.currency && cSel) cSel.value = r.currency;
        status('#cost-status', r && r.note
          ? `${r.label}: ${r.note}` : '', 'ok');
      });
    }
    if (cSel && currencies.length) {
      cSel.innerHTML = currencies.map(c =>
        `<option value="${c.key}"${c.key === 'USD' ? ' selected' : ''}>${escapeHtml(c.label)}</option>`
      ).join('');
    }
    const btn = $('#btn-more-tech');
    if (btn) {
      btn.addEventListener('click', () => {
        const ex = $('#components-extra');
        ex.hidden = !ex.hidden;
        btn.textContent = ex.hidden
          ? 'Show all technologies' : 'Hide the other technologies';
      });
    }
  }

  function costBasis() {
    return {
      region: ($('#cost-region') || {}).value || 'global',
      currency: ($('#cost-currency') || {}).value || null,
      level: ($('#cost-level') || {}).value || 'typical',
    };
  }

  function componentConfig() {
    const cfg = {};
    ALL_TECHS.concat(['grid']).forEach(tech => {
      const en = $(`[data-enable="${tech}"]`);
      if (!en) return;
      const o = { enabled: en.checked };
      $$(`[data-c="${tech}"]`).forEach(inp => {
        const v = inp.value;
        o[inp.dataset.f] = (v === '' ? null : (isNaN(+v) ? v : +v));
      });
      cfg[tech] = o;
    });
    // The interface has called the fleet "ev" since the first version, and
    // saved projects still use that key.
    if (cfg.ev_fleet) cfg.ev = cfg.ev_fleet;
    return cfg;
  }

  function buildObjectives(list) {
    const host = $('#objectives');
    host.innerHTML = list.map((o, i) => `
      <label style="display:flex;gap:8px;align-items:center;margin-bottom:6px;font-size:13px">
        <input type="checkbox" data-obj="${o.key}" ${['npc','lpsp','renewable_fraction'].includes(o.key) ? 'checked' : ''}>
        ${o.label} <span class="tag">${o.direction}</span>
      </label>`).join('');
  }

  /* ----------------------------------------------------------- optimise */
  async function runStudy() {
    if (!S.loadLoaded) { status('#run-status', 'Load a demand profile first — the sizing has nothing to serve without it.', 'err'); return; }

    const objectives = $$('[data-obj]').filter(c => c.checked).map(c => c.dataset.obj);
    if (objectives.length < 2) {
      status('#run-status', 'Choose at least two objectives — a Pareto front needs a trade-off.', 'err'); return;
    }

    const constraints = {};
    const lpsp = +$('#max-lpsp').value;
    if (lpsp > 0) constraints.lpsp = ['<=', lpsp];
    const rf = +$('#min-rf').value;
    if (rf > 0) constraints.renewable_fraction = ['>=', rf];

    $('#btn-run').disabled = true;
    $('#run-progress').hidden = false;
    $('#run-bar').style.width = '8%';
    $('#run-msg').textContent = 'Screening the search space…';
    status('#run-status', '');

    const t0 = performance.now();
    const r = await call('run_study', {
      session: S.session, config: componentConfig(), objectives,
      costs: costBasis(),
      constraints, strategy: $('#strategy').value,
      particles: +$('#particles').value, iterations: +$('#iterations').value,
      seed: +$('#seed').value, algorithm: $('#algorithm').value,
      economics: {
        project_years: +$('#proj-years').value,
        interest_rate: +$('#interest').value,
        escalation_rate: +$('#escalation').value,
        currency: $('#currency').value,
        load_growth_rate: +($('#load-growth') || { value: 0 }).value,
        unmet_load_penalty: +$('#unmet-penalty').value,
        emissions_price: +$('#carbon-price').value,
      },
    });

    $('#btn-run').disabled = false;
    $('#run-bar').style.width = '100%';
    if (!r.ok) { $('#run-progress').hidden = true; status('#run-status', r.error, 'err'); return; }

    const secs = ((performance.now() - t0) / 1000).toFixed(1);
    const how = r.summary.algorithm_label || 'the optimiser';
    $('#run-msg').textContent =
      `${how}: ${r.summary.evaluations} designs evaluated in ${secs} s — `
      + `${r.front.length} on the Pareto front`
      + (r.summary.exact
          ? ', and every design in the search space was tried, so this front '
            + 'is exact.'
          : '.');
    S.front = r.front;
    S.done.add('optimise');

    if (r.summary.warnings && r.summary.warnings.length) {
      status('#run-status', r.summary.warnings.join(' '), 'warn');
    } else {
      status('#run-status', '', 'ok');
    }

    renderFindings('#run-findings', r.findings);
    renderFront(r);
    $('#front-card').hidden = false;
  }

  function renderFront(r) {
    const objs = r.summary.objectives;
    const xk = objs.includes('npc') ? 'npc' : objs[0];
    const yk = objs.includes('renewable_fraction') ? 'renewable_fraction'
             : objs.includes('lpsp') ? 'lpsp' : objs[1];
    const yLabel = { renewable_fraction: 'Renewable fraction', lpsp: 'LPSP',
                     lcoe: 'LCOE', emissions_kg: 'Emissions (kg/yr)' }[yk] || yk;

    Chart.scatter($('#chart-front'), {
      title: 'Cost against performance — each point is a buildable design',
      xTitle: xk === 'npc' ? 'Net present cost' : xk,
      yTitle: yLabel,
      points: r.front.map((f, i) => ({
        x: f[xk], y: yk === 'renewable_fraction' ? f[yk] * 100 : f[yk],
        index: i, selected: i === r.recommended_index,
        label: `${eng(f.pv_kwp, 'kWp')} PV · ${eng(f.battery_kwh, 'kWh')} · `
             + `NPC ${cash(f.npc)} · LCOE ${perKwh(f.lcoe)}`,
      })),
      onPick: p => selectDesign(p.index),
    });

    const H = r.highlights;
    const notes = r.highlight_notes || {};
    $('#highlights').innerHTML = [
      ['Recommended', H.recommended, 'recommended'],
      ['Lowest cost', H.cheapest, 'cheapest'],
      ['Most renewable', H.greenest, 'greenest'],
      ['Most reliable', H.most_reliable, 'most_reliable'],
    ].filter(([, d]) => d).map(([tag, d, key]) => `
      <div class="hl" data-x='${JSON.stringify(d.x)}'>
        <div class="hl-tag">${tag}</div>
        <div class="hl-main">${describe(d)}</div>
        <div class="hl-sub">${cash(d.npc)} NPC · ${perKwh(d.lcoe)} · `
          + `${(d.renewable_fraction * 100).toFixed(0)}% renewable</div>`
          + (notes[key]
              ? `<div class="hl-note">${escapeHtml(notes[key])}</div>` : '')
          + `</div>`).join('');
    $$('#highlights .hl').forEach(h => h.addEventListener('click', () => {
      const x = JSON.parse(h.dataset.x);
      selectDesign(S.front.findIndex(f => JSON.stringify(f.x) === JSON.stringify(x)));
    }));

    const cur = $('#currency').value;
    const t = $('#front-table');
    t.innerHTML = `<thead><tr>
      <th>Design</th><th>PV</th><th>Wind</th><th>Battery</th>
      <th>Genset</th><th>NPC</th><th>LCOE</th><th>LPSP</th>
      <th>Renewable</th><th>Capital</th></tr></thead><tbody>` +
      r.front.map((f, i) => `<tr class="clickable${i === r.recommended_index ? ' sel' : ''}" data-i="${i}">
        <td>${i + 1}</td><td>${eng(f.pv_kwp, 'kWp')}</td><td>${eng(f.wind_kw, 'kW')}</td>
        <td>${eng(f.battery_kwh, 'kWh')}</td><td>${eng(f.genset_kw, 'kW')}</td>
        <td>${cash(f.npc)}</td><td>${perKwh(f.lcoe)}</td>
        <td>${(f.lpsp * 100).toFixed(2)}%</td>
        <td>${(f.renewable_fraction * 100).toFixed(1)}%</td>
        <td>${cash(f.initial_capital)}</td></tr>`).join('') + '</tbody>';
    t.querySelectorAll('tr[data-i]').forEach(tr =>
      tr.addEventListener('click', () => selectDesign(+tr.dataset.i)));

    if (r.recommended_index != null) selectDesign(r.recommended_index);
  }

  const describe = d => [
    d.pv_kwp ? `${eng(d.pv_kwp, 'kWp')} PV` : null,
    d.wind_kw ? `${eng(d.wind_kw, 'kW')} wind` : null,
    d.battery_kwh ? `${eng(d.battery_kwh, 'kWh')} storage` : null,
    d.genset_kw ? `${eng(d.genset_kw, 'kW')} genset` : null,
    d.chargers ? `${d.chargers} EV charge point${d.chargers > 1 ? 's' : ''}` : null,
  ].filter(Boolean).join(' + ') || 'Grid only';

  async function selectDesign(i) {
    if (i < 0 || i >= S.front.length) return;
    S.selected = i;
    $$('#front-table tr[data-i]').forEach(tr =>
      tr.classList.toggle('sel', +tr.dataset.i === i));

    const d = await call('detail', { session: S.session, x: S.front[i].x });
    if (!d.ok) { status('#run-status', d.error, 'err'); return; }
    S.detail = d;
    S.done.add('results'); S.done.add('design'); S.done.add('diagrams');
    renderResults(d); renderDesign(d); renderDiagrams(d);
    $$('.nav-item').forEach((b, k) => b.classList.toggle('done', S.done.has(PAGES[k].id)));
  }

  /* ------------------------------------------------------------ results */
  function renderResults(d) {
    const e = d.energy, m = d.metrics, ec = d.economics, cur = ec.currency;
    const host = $('#results-body');
    host.innerHTML = `
      <div class="card">
        <h2>${describe(S.front[S.selected])}</h2>
        <div class="kpis">
          ${kpi(Chart.money(ec.npc, cur), 'Net present cost', `over ${$('#proj-years').value} years`)}
          ${kpi(Chart.money(ec.lcoe, cur, { unit: 'kWh' }), 'Levelised cost', 'of energy served')}
          ${kpi(Chart.money(ec.initial_capital, cur), 'Initial capital')}
          ${kpi((m.renewable_fraction * 100).toFixed(1) + '%', 'Renewable fraction', 'of served demand')}
          ${kpi((m.lpsp * 100).toFixed(2) + '%', 'Unserved energy', `${eng(m.unmet_kwh, 'kWh')}/yr`)}
          ${kpi((m.self_sufficiency * 100).toFixed(0) + '%', 'Self-sufficiency')}
          ${kpi((m.curtailment_rate * 100).toFixed(1) + '%', 'Curtailment', `${eng(m.curtailed_kwh, 'kWh')} wasted`)}
          ${kpi(eng(m.emissions_kg, 'kg'), 'CO₂ per year')}
        </div>
      </div>
      <div class="card"><h2>Cost breakdown</h2><div id="chart-cost"></div></div>
      <div class="card"><h2>Energy flows</h2>
        <div class="chart-row"><div id="chart-monthly"></div><div id="chart-daily"></div></div>
      </div>
      <div class="card"><h2>A week of operation</h2>
        <p class="help">Stacked supply against the demand line. Where the
          stack falls short of the line, energy is unserved.</p>
        <div id="chart-week"></div>
      </div>
      <div class="card"><h2>Battery state of charge</h2><div id="chart-soc"></div></div>`;

    Chart.waterfall($('#chart-cost'), {
      title: `Net present cost by component (${cur})`,
      items: Object.entries(ec.items)
        .map(([k, v]) => ({ name: k.replace(/_/g, ' '), value: v.npc }))
        .filter(i => i.value > 0).sort((a, b) => b.value - a.value),
    });

    const g = d.charts.monthly_generation;
    Chart.bars($('#chart-monthly'), {
      title: 'Monthly energy (kWh)',
      series: [
        { name: 'PV', values: g.pv }, { name: 'Wind', values: g.wind },
        { name: 'Generator', values: g.genset }, { name: 'Grid import', values: g.import },
      ].filter(s => s.values.some(v => v > 0)),
    });

    const dp = d.charts.daily_profile;
    Chart.lines($('#chart-daily'), {
      title: 'Average day (kW)',
      labels: Array.from({ length: 24 }, (_, i) => String(i)),
      series: [
        { name: 'Demand', values: dp.load }, { name: 'PV', values: dp.pv },
        { name: 'Wind', values: dp.wind }, { name: 'Battery out', values: dp.battery_discharge },
        { name: 'Import', values: dp.import },
      ].filter(s => s.values.some(v => v > 0.01)),
    });

    const w = d.sample_week;
    Chart.area($('#chart-week'), {
      labels: w.hours.map((h, i) => i % 24 === 0 ? 'd' + Math.floor(h / 24) : ''),
      series: [
        { name: 'PV', values: w.pv }, { name: 'Wind', values: w.wind },
        { name: 'Battery', values: w.battery_discharge },
        { name: 'Generator', values: w.genset }, { name: 'Grid', values: w.import },
      ].filter(s => s.values.some(v => v > 0.01)),
      reference: { name: 'Demand', values: w.load },
    });

    Chart.lines($('#chart-soc'), {
      title: 'Daily mean state of charge', height: 170,
      labels: Array.from({ length: 365 }, (_, i) => i % 30 === 0 ? String(i) : ''),
      series: [{ name: 'SOC', values: d.charts.soc_daily }], fill: true,
    });
  }

  /* ------------------------------------------------------------- design */
  function renderDesign(d) {
    const de = d.design;
    const host = $('#design-body');
    let h = '';

    if (de.pv) {
      const iv = de.pv.inverter, st = de.pv.strings;
      h += `<div class="card"><h2>PV array and inverter</h2>
        <div class="kpis">
          ${kpi(eng(de.pv.capacity_kwp, 'kWp'), 'Array capacity')}
          ${kpi(iv.units + ' × ' + eng(iv.rated_ac_kw, 'kW'), 'Inverter')}
          ${kpi(iv.dc_ac_ratio.toFixed(2), 'DC/AC ratio')}
          ${kpi((iv.clipping_loss * 100).toFixed(2) + '%', 'Clipping loss', iv.hours_clipped + ' hours/yr')}
          ${kpi(de.pv.tilt_deg.toFixed(1) + '°', 'Tilt')}
        </div>`;
      if (st) {
        h += `<h3>String design ${st.valid ? '<span class="pill ok">valid</span>' : '<span class="pill bad">check required</span>'}</h3>
        <div class="table-wrap"><table><tbody>
          <tr><td>Modules per string</td><td>${st.modules_per_string}</td></tr>
          <tr><td>Strings per inverter</td><td>${st.strings}</td></tr>
          <tr><td>Total modules</td><td>${st.array_modules_total || st.modules_total}</td></tr>
          <tr><td>String Voc at ${st.limits.design_t_min_c}°C</td><td>${st.string_voc_cold_v.toFixed(0)} V (limit ${st.limits.inverter_v_max} V)</td></tr>
          <tr><td>String Vmp at ${st.limits.design_t_max_cell_c}°C cell</td><td>${st.string_vmp_hot_v.toFixed(0)} V (MPPT min ${st.limits.mppt_window_v[0]} V)</td></tr>
          <tr><td>Current per MPPT input</td><td>${st.current_per_mppt_a.toFixed(1)} A</td></tr>
        </tbody></table></div>`;
      }
      h += '</div>';
    }

    if (de.battery) {
      const p = de.battery.pcs;
      h += `<div class="card"><h2>Battery and converter</h2><div class="kpis">
        ${kpi(eng(de.battery.capacity_kwh, 'kWh'), 'Energy')}
        ${kpi(eng(de.battery.power_kw, 'kW'), 'Power')}
        ${kpi(eng(p.rated_kw, 'kW'), 'PCS rating', '4-quadrant')}
        ${kpi(de.battery.cycles_per_year.toFixed(0), 'Cycles per year')}
        ${kpi(de.battery.expected_life_years.toFixed(1) + ' yr', 'Expected life')}
      </div></div>`;
    }

    h += `<div class="card"><h2>Cable schedule</h2>
      <p class="help">Each conductor is sized against ampacity, voltage drop
        and short-circuit withstand; the governing criterion is named.</p>
      <div class="table-wrap"><table><thead><tr>
        <th>Circuit</th><th>mm²</th><th>Runs</th><th>Length m</th>
        <th>Design A</th><th>Capacity A</th><th>Drop %</th><th>Governed by</th>
      </tr></thead><tbody>` +
      de.schedules.cables.map(c => `<tr><td>${c.circuit}</td><td>${c.csa_mm2}</td>
        <td>${c.runs}</td><td>${c.length_m}</td><td>${c.current_a}</td>
        <td>${c.ampacity_a}</td><td>${c.voltage_drop_pct}</td>
        <td>${(c.governing || '').replace(/_/g, ' ')}</td></tr>`).join('')
      + `</tbody></table></div>
      <p class="muted" style="margin-top:10px">Estimated conductor mass:
        ${eng(de.totals.copper_kg_estimate, 'kg')} of copper.</p></div>`;

    h += `<div class="card"><h2>Protection schedule</h2>
      <p class="help">Each device satisfies both IEC 60364-4-43 conditions:
        I<sub>B</sub> ≤ I<sub>N</sub> ≤ I<sub>Z</sub> and I₂ ≤ 1.45·I<sub>Z</sub>.</p>
      <div class="table-wrap"><table><thead><tr>
        <th>Circuit</th><th>Device</th><th>Rating A</th><th>Curve</th>
        <th>Design A</th><th>Compliant</th></tr></thead><tbody>` +
      de.schedules.protection.map(p => `<tr><td>${p.circuit}</td>
        <td>${String(p.device).toUpperCase()}</td><td>${p.rating_a}</td>
        <td>${p.curve || '—'}</td><td>${p.design_current_a}</td>
        <td><span class="pill ${p.compliant ? 'ok' : 'bad'}">${p.compliant ? 'yes' : 'review'}</span></td>
        </tr>`).join('') + '</tbody></table></div></div>';

    h += `<div class="card"><h2>Isolation and safety</h2><div class="table-wrap"><table><thead><tr>
      <th>Device</th><th>Rating</th><th>Location</th><th>Standard</th></tr></thead><tbody>` +
      de.schedules.isolation.map(i => `<tr><td>${i.device}</td><td>${i.rating}</td>
        <td>${i.location}</td><td>${i.standard}</td></tr>`).join('')
      + `</tbody></table></div>
      <h3>Residual current protection</h3>
      <p class="muted">Type ${de.rcd.type}, ${de.rcd.rating_ma} mA. ${de.rcd.reason}</p>
      <h3>Surge protection</h3>
      <p class="muted">AC: ${de.spd_ac.class}, ${de.spd_ac.discharge_current}, at the ${de.spd_ac.location.toLowerCase()}.`
      + (de.pv ? ` DC: ${de.pv.spd_dc.class}, Uc ${de.pv.spd_dc.uc_v.toFixed(0)} V.` : '')
      + '</p></div>';

    if (de.warnings && de.warnings.length) {
      h += '<div class="card"><h2>Design notes requiring attention</h2><ul class="warn-list">'
        + de.warnings.map(w => `<li>${w}</li>`).join('') + '</ul></div>';
    }
    if (de.assumptions && de.assumptions.length) {
      h += '<div class="card"><h2>Assumptions</h2><ul class="note-list">'
        + de.assumptions.map(a => `<li>${a}</li>`).join('') + '</ul></div>';
    }
    host.innerHTML = h;
  }

  function renderDiagrams(d) {
    $('#diagram-body').innerHTML = `
      <div class="card"><h2>Single-line diagram</h2>
        <div class="diagram-box">${d.diagrams.single_line}</div></div>
      <div class="card"><h2>Three-line diagram</h2>
        <p class="help">The same design with each AC feeder shown as three
          phase conductors plus neutral, for termination work.</p>
        <div class="diagram-box">${d.diagrams.three_line}</div></div>`;
  }

  /* ------------------------------------------------------------- export */
  function download(name, text, type = 'text/plain') {
    const blob = new Blob([text], { type });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }

  function downloadDiagrams() {
    if (!S.detail) { status('#report-status', 'Select a design first.', 'err'); return; }
    download('single_line_diagram.svg', S.detail.diagrams.single_line, 'image/svg+xml');
    download('three_line_diagram.svg', S.detail.diagrams.three_line, 'image/svg+xml');
    status('#report-status', 'Diagrams downloaded.', 'ok');
  }

  function downloadCsv() {
    if (!S.detail) { status('#report-status', 'Select a design first.', 'err'); return; }
    const w = S.detail.sample_week;
    const cols = ['hours','load','pv','wind','genset','import','export',
                  'battery_charge','battery_discharge','soc','curtailed','unmet'];
    const rows = [cols.join(',')];
    for (let i = 0; i < w.hours.length; i++) {
      rows.push(cols.map(c => (w[c][i] ?? 0)).join(','));
    }
    download('dispatch_week.csv', rows.join('\n'), 'text/csv');
    status('#report-status', 'Sample week exported. The full 8760-hour export is available from the API.', 'ok');
  }

  /* ----------------------------------------------------- project files

     A project file is the whole study and not merely its result: where the
     site is, how the resource was obtained, what the demand is, which cost
     basis was used, every component field, the economics, and the search
     settings. Version 1 of the format carried only the location, the
     components and the economics; it is still read, and the sections it
     lacks simply keep whatever is already on the screen.

     One thing cannot be carried across. A demand profile chosen from disk
     is held by the browser, and no page is permitted to put a file back
     into a file input, so a project saved in that mode records the mode and
     asks for the file again. Pasted and synthetic profiles restore
     completely, which is why the worked examples use them: an example that
     needs a second file is not an example, it is an errand. */

  const PROJECT_VERSION = 2;

  const val = (sel, dflt) => { const el = $(sel); return el ? el.value : dflt; };
  const segMode = (sel, key, dflt) => {
    const b = $(sel + ' .active');
    return b ? b.dataset[key] : dflt;
  };

  function projectSnapshot() {
    const loadMode = segMode('#load-mode', 'mode', 'synthetic');
    return {
      application: 'EnerSys',
      version: PROJECT_VERSION,
      saved: new Date().toISOString(),
      title: val('#report-title', 'Hybrid energy system study'),
      location: location(),
      site_scale: segMode('#site-scale', 'scale', null),
      resources: {
        mode: segMode('#res-mode', 'mode', 'online'),
        provider: val('#res-provider', 'pvgis'),
        year: +val('#res-year', 2020),
        monthly_ghi: val('#monthly-ghi', ''),
        monthly_temperature: val('#monthly-temp', ''),
        mean_wind_speed: +val('#mean-wind', 0),
      },
      load: {
        mode: loadMode,
        scale: +val('#load-scale', 1),
        column: val('#load-column', ''),
        text: loadMode === 'paste' ? val('#load-text', '') : '',
        synthetic: {
          peak_kw: +val('#syn-peak', 0),
          load_factor: +val('#syn-lf', 0),
          shape: val('#syn-shape', 'commercial'),
        },
      },
      costs: costBasis(),
      components: savedComponents(),
      economics: {
        project_years: +val('#proj-years', 20),
        interest_rate: +val('#interest', 0.08),
        escalation_rate: +val('#escalation', 0.02),
        currency: val('#currency', 'USD'),
        load_growth_rate: +val('#load-growth', 0),
        unmet_load_penalty: +val('#unmet-penalty', 0),
        emissions_price: +val('#carbon-price', 0),
      },
      study: {
        objectives: $$('[data-obj]').filter(c => c.checked).map(c => c.dataset.obj),
        max_lpsp: +val('#max-lpsp', 0),
        min_renewable_fraction: +val('#min-rf', 0),
        strategy: val('#strategy', 'load_following'),
        algorithm: val('#algorithm', 'mopso'),
        particles: +val('#particles', 24),
        iterations: +val('#iterations', 40),
        seed: +val('#seed', 1234),
      },
      selected: S.selected != null ? S.front[S.selected] : null,
    };
  }

  /* Twenty technologies are on the page and a study uses three or four of
     them. Writing all twenty buries the four that matter under six hundred
     lines of switched-off defaults, so what is written is: everything that
     is switched on, plus the five sizable technologies and the grid, whose
     being switched OFF is itself a decision. A technology the file does not
     mention is simply left as the page has it, which for these is off. */
  function savedComponents() {
    const all = componentConfig();
    const keep = {};
    Object.entries(all).forEach(([tech, o]) => {
      if (tech === 'ev') return;               // alias of ev_fleet; read, not written
      if (o.enabled || CORE.includes(tech) || tech === 'grid') keep[tech] = o;
    });
    return keep;
  }

  function saveProject() {
    const slug = (val('#site-name', '') || 'project')
      .toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
    download(`ensys_${slug || 'project'}.json`,
             JSON.stringify(projectSnapshot(), null, 2), 'application/json');
    status('#report-status',
      'Project saved. "Open project", at the top of any page, reads it back.',
      'ok');
  }

  /* Writing a value without raising a change event: the cost links listen
     for one, and a project already holds the costs it wants. */
  function put(sel, v) {
    const el = $(sel);
    if (!el || v === undefined || v === null || v === '') return;
    el.value = v;
    if ('prev' in el.dataset) el.dataset.prev = String(v);
  }

  function segment(hostSel, key, wanted) {
    if (!wanted) return;
    const btn = $$(`${hostSel} .seg-btn`).find(b => b.dataset[key] === wanted);
    if (btn && !btn.classList.contains('active')) btn.click();
  }

  function applyComponents(cfg) {
    if (!cfg || typeof cfg !== 'object') return;
    let hidden = false;
    Object.entries(cfg).forEach(([raw, o]) => {
      if (!o || typeof o !== 'object') return;
      const tech = raw === 'ev' ? 'ev_fleet' : raw;
      const en = $(`[data-enable="${tech}"]`);
      if (!en) return;
      if (typeof o.enabled === 'boolean' && en.checked !== o.enabled) {
        en.checked = o.enabled;
        en.dispatchEvent(new Event('change'));
      }
      if (o.enabled && tech !== 'grid' && !CORE.includes(tech)) hidden = true;
      Object.entries(o).forEach(([f, v]) => {
        if (f === 'enabled') return;
        put(`[data-c="${tech}"][data-f="${f}"]`, v);
      });
    });
    // A project that switched on a technology behind the "show all" fold is
    // unreadable until the fold is open.
    if (hidden) {
      const ex = $('#components-extra'), btn = $('#btn-more-tech');
      if (ex && ex.hidden) {
        ex.hidden = false;
        if (btn) btn.textContent = 'Hide the other technologies';
      }
    }
  }

  function applyProject(p) {
    if (!p || typeof p !== 'object') throw new Error('That file does not contain a project.');
    if (!p.location && !p.components) {
      throw new Error('That file is JSON, but it is not an EnerSys project — it has neither a location nor any components.');
    }

    if (p.location) {
      const l = p.location;
      put('#site-name', l.name); put('#lat', l.latitude); put('#lon', l.longitude);
      put('#elev', l.elevation_m); put('#tz', l.utc_offset_hours);
      put('#terrain', l.terrain);
      const pre = $('#loc-preset'); if (pre) pre.value = '';
    }

    // The site-scale buttons rewrite the demand and every unit cost, so they
    // are pressed before the sections that hold the real values.
    if (p.site_scale) segment('#site-scale', 'scale', p.site_scale);

    const r = p.resources || {};
    segment('#res-mode', 'mode', r.mode);
    put('#res-provider', r.provider); put('#res-year', r.year);
    put('#monthly-ghi', r.monthly_ghi); put('#monthly-temp', r.monthly_temperature);
    put('#mean-wind', r.mean_wind_speed);

    const ld = p.load || {};
    segment('#load-mode', 'mode', ld.mode);
    put('#load-scale', ld.scale); put('#load-column', ld.column);
    put('#load-text', ld.text);
    const sy = ld.synthetic || {};
    put('#syn-peak', sy.peak_kw); put('#syn-lf', sy.load_factor); put('#syn-shape', sy.shape);

    const c = p.costs || {};
    put('#cost-region', c.region); put('#cost-currency', c.currency); put('#cost-level', c.level);

    applyComponents(p.components);

    const e = p.economics || {};
    put('#proj-years', e.project_years); put('#interest', e.interest_rate);
    put('#escalation', e.escalation_rate); put('#currency', e.currency);
    put('#load-growth', e.load_growth_rate);
    put('#unmet-penalty', e.unmet_load_penalty); put('#carbon-price', e.emissions_price);

    const st = p.study || {};
    if (Array.isArray(st.objectives) && st.objectives.length) {
      $$('[data-obj]').forEach(x => { x.checked = st.objectives.includes(x.dataset.obj); });
    }
    put('#max-lpsp', st.max_lpsp); put('#min-rf', st.min_renewable_fraction);
    put('#strategy', st.strategy);
    if (st.algorithm) { put('#algorithm', st.algorithm); describeAlgorithm(); }
    put('#particles', st.particles); put('#iterations', st.iterations);
    put('#seed', st.seed);

    put('#report-title', p.title);

    // Nothing computed survives the import: the front on screen belongs to
    // the previous study and would be read as if it belonged to this one.
    S.front = []; S.selected = null; S.detail = null;
    S.resourceLoaded = false; S.loadLoaded = false;
    S.done = new Set();
    const fc = $('#front-card'); if (fc) fc.hidden = true;

    return ld.mode || 'synthetic';
  }

  /* Opening a project restores the settings and then walks the study
     forward as far as it can go without the user: the resource and the
     demand are cheap and deterministic, the optimisation is neither, so
     that one is left for the Run button. */
  async function importProject(ev) {
    const input = (ev && ev.target) || $('#project-open');
    let text;
    try { text = await readFile(input); } catch (err) { text = null; }
    input.value = '';                 // so the same file opens twice in a row
    if (!text) return;

    let p;
    try { p = JSON.parse(text); }
    catch (err) {
      show(0);
      status('#res-status', 'That file is not valid JSON, so there is no project in it to open.', 'err');
      return;
    }

    let loadMode;
    try { loadMode = applyProject(p); }
    catch (err) { show(0); status('#res-status', err.message, 'err'); return; }

    const name = (p.location && p.location.name) || 'the project';
    show(0);
    status('#res-status', `Opened ${name}. Retrieving the resource data…`);

    await fetchResources();
    if (!S.resourceLoaded) return;    // fetchResources has already said why

    if (loadMode === 'file') {
      show(1);
      status('#load-status',
        'Every other setting was restored, but a demand file cannot be carried '
        + 'inside a project — the browser will not hand a file back to a page. '
        + 'Choose the CSV again and press "Load profile".', 'warn');
      return;
    }

    status('#load-status', 'Rebuilding the demand profile…');
    await loadProfile();
    if (!S.loadLoaded) return;

    show(4);
    status('#run-status',
      `${name} is loaded and ready. Press "Run study" to search for the `
      + `Pareto front with the settings this project was saved with.`, 'ok');
  }

  function makeReport() {
    if (!S.detail) { status('#report-status', 'Select a design on the Pareto front first.', 'err'); return; }
    const rtl = $('#report-lang').value === 'fa';
    const title = $('#report-title').value;
    const d = S.detail;
    const cur = d.economics.currency;

    const html = `<!DOCTYPE html><html lang="${rtl ? 'fa' : 'en'}" dir="${rtl ? 'rtl' : 'ltr'}">
<head><meta charset="utf-8"><title>${title}</title><style>
body{font:13px/1.6 ui-sans-serif,system-ui,"Segoe UI",Tahoma,sans-serif;max-width:1000px;margin:32px auto;padding:0 24px;color:#14181d}
h1{font-size:23px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 8px;padding-bottom:5px;border-bottom:2px solid #0f766e}
h3{font-size:13px;text-transform:uppercase;letter-spacing:.05em;color:#4a5560;margin:18px 0 6px}
table{border-collapse:collapse;width:100%;font-size:12px;margin:10px 0}
th,td{border:1px solid #dfe3e8;padding:6px 9px;text-align:${rtl ? 'right' : 'left'}}
th{background:#f6f7f9;font-weight:650}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:14px 0}
.kpi{border:1px solid #dfe3e8;border-radius:8px;padding:10px}
.kpi b{display:block;font-size:18px}.kpi span{font-size:11px;color:#6b7280}
.warn{background:#fdf3e3;color:#b45309;padding:9px 12px;border-radius:6px;margin:6px 0;font-size:12px}
.meta{color:#6b7280;font-size:11.5px;margin-bottom:20px}
svg{max-width:100%;height:auto}
@media print{body{margin:0}h2{break-after:avoid}table{break-inside:avoid}}
</style></head><body>
<h1>${title}</h1>
<div class="meta">${location().name} — ${location().latitude.toFixed(4)}°, ${location().longitude.toFixed(4)}°,
${location().elevation_m} m · Generated ${new Date().toLocaleString()} · EnerSys ${S.info.version}</div>

<h2>Recommended system</h2>
<div class="kpis">
<div class="kpi"><b>${Chart.money(d.economics.npc, cur)}</b><span>Net present cost</span></div>
<div class="kpi"><b>${Chart.money(d.economics.lcoe, cur, { unit: 'kWh' })}</b><span>Levelised cost of energy</span></div>
<div class="kpi"><b>${(d.metrics.renewable_fraction * 100).toFixed(1)}%</b><span>Renewable fraction</span></div>
<div class="kpi"><b>${(d.metrics.lpsp * 100).toFixed(2)}%</b><span>Unserved energy</span></div>
</div>
<table><tr><th>Component</th><th>Size</th></tr>
${d.system.pv_capacity_kwp ? `<tr><td>Solar PV</td><td>${eng(d.system.pv_capacity_kwp, 'kWp')}</td></tr>` : ''}
${d.system.wind_capacity_kw ? `<tr><td>Wind</td><td>${eng(d.system.wind_capacity_kw, 'kW')}</td></tr>` : ''}
${d.system.battery_capacity_kwh ? `<tr><td>Battery</td><td>${eng(d.system.battery_capacity_kwh, 'kWh')} / ${eng(d.system.battery_power_kw, 'kW')}</td></tr>` : ''}
${d.system.genset_capacity_kw ? `<tr><td>Generator</td><td>${eng(d.system.genset_capacity_kw, 'kW')}</td></tr>` : ''}
<tr><td>Grid connection</td><td>${eng(d.system.grid_import_limit_kw, 'kW')} import</td></tr></table>

<h2>Energy balance</h2>
<table><tr><th>Quantity</th><th>MWh/year</th></tr>
${['load_kwh','pv_kwh','wind_kwh','genset_kwh','imported_kwh','exported_kwh',
   'battery_discharge_kwh','curtailed_kwh','unmet_kwh']
  .map(k => `<tr><td>${k.replace(/_kwh$/, '').replace(/_/g, ' ')}</td><td>${(d.energy[k] / 1000).toFixed(1)}</td></tr>`).join('')}
</table>

<h2>Cost breakdown</h2>
<table><tr><th>Component</th><th>Capital</th><th>Replacement</th><th>O&amp;M</th><th>Recurring</th><th>NPC</th></tr>
${Object.entries(d.economics.items).map(([k, v]) =>
  `<tr><td>${k.replace(/_/g, ' ')}</td><td>${Chart.money(v.capital, cur)}</td><td>${Chart.money(v.replacement, cur)}</td>
   <td>${Chart.money(v.om, cur)}</td><td>${Chart.money(v.recurring, cur)}</td><td><b>${Chart.money(v.npc, cur)}</b></td></tr>`).join('')}
</table>

<h2>Cable schedule</h2>
<table><tr><th>Circuit</th><th>mm²</th><th>Runs</th><th>Length</th><th>Design A</th><th>Capacity A</th><th>Drop %</th><th>Governed by</th></tr>
${d.design.schedules.cables.map(c => `<tr><td>${c.circuit}</td><td>${c.csa_mm2}</td><td>${c.runs}</td>
  <td>${c.length_m} m</td><td>${c.current_a}</td><td>${c.ampacity_a}</td><td>${c.voltage_drop_pct}</td>
  <td>${(c.governing || '').replace(/_/g, ' ')}</td></tr>`).join('')}</table>

<h2>Protection schedule</h2>
<table><tr><th>Circuit</th><th>Device</th><th>Rating</th><th>Curve</th><th>Compliant</th></tr>
${d.design.schedules.protection.map(p => `<tr><td>${p.circuit}</td><td>${String(p.device).toUpperCase()}</td>
  <td>${p.rating_a} A</td><td>${p.curve || '—'}</td><td>${p.compliant ? 'Yes' : 'Review'}</td></tr>`).join('')}</table>

<h2>Single-line diagram</h2>${d.diagrams.single_line}
<h2>Three-line diagram</h2>${d.diagrams.three_line}

${(d.design.warnings || []).length ? '<h2>Items requiring attention</h2>'
  + d.design.warnings.map(w => `<div class="warn">${w}</div>`).join('') : ''}

<h2>Assumptions and limitations</h2>
<ul>${(d.design.assumptions || []).map(a => `<li>${a}</li>`).join('')}
<li>Results are based on a single simulated year at hourly resolution. Sub-hourly
peaks and inter-annual weather variation are not captured.</li>
<li>Component ratings are selected against standard product sizes; verify
against the manufacturer's data before procurement.</li>
<li>This study is a design aid. It does not replace a qualified engineer's
review, a site survey, or compliance sign-off against the local wiring rules.</li></ul>
</body></html>`;

    download('ensys_report.html', html, 'text/html');
    status('#report-status', 'Report generated. Open it in a browser and print to PDF if you need one.', 'ok');
  }

  return { init };
})();

// Start as soon as the page is parsed. boot.js has already defined
// window.ENSYS; init() awaits ENSYS.ready itself, so nothing here depends on
// the engine being up before the interface is shown.
App.init();
