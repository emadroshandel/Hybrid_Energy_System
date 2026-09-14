/* EnerSys — transport bootstrap.
 *
 * The same interface runs two ways:
 *
 *   server mode   python server.py serves this page and answers api/* over HTTP
 *   browser mode  the page is opened with no server at all; Pyodide runs the
 *                 identical ensys package inside the browser
 *
 * app.js never learns which one it is in: it calls ENSYS.call(action, payload)
 * and awaits ENSYS.ready before its first call.
 *
 * The one rule that matters here, learned the hard way: THE INTERFACE IS
 * NEVER HIDDEN BEHIND THIS FILE. index.html paints the application on the
 * first frame. Nothing in start-up can leave the user looking at a spinner,
 * because there is nothing for a spinner to cover. A splash appears only in
 * browser mode, where there genuinely is a ten-megabyte download to wait
 * for, and it is removed when that finishes.
 */

'use strict';

window.ENSYS = (function () {

  const PYODIDE_CDN = 'https://cdn.jsdelivr.net/pyodide/v0.26.2/full/';
  const HEALTH_TIMEOUT_MS = 4000;
  const CALL_TIMEOUT_MS = 180000;      // a full optimisation is slow
  const QUICK_TIMEOUT_MS = 15000;      // calls that only read, never compute
  const QUICK = new Set(['info', 'session', 'presets']);
  const GET_LIMIT = 1500;              // longest payload that fits in a URL

  const log = [];
  const t0 = Date.now();
  const note = m => log.push(`${((Date.now() - t0) / 1000).toFixed(1)}s  ${m}`);

  const api = {
    mode: 'unknown',
    version: null,
    health: null,
    ready: null,
    diagnostics,
    call,
  };

  function diagnostics() {
    return [
      'EnerSys diagnostics',
      `time     : ${new Date().toISOString()}`,
      `page     : ${location.href}`,
      `transport: ${api.mode}`,
      `browser  : ${navigator.userAgent}`,
      '',
      ...log,
    ].join('\n');
  }

  /* --------------------------------------------------------------- splash */
  // Created on demand, never present in the HTML, always removed.

  function splash(msg, sub) {
    let el = document.getElementById('ensys-boot');
    if (!el) {
      el = document.createElement('div');
      el.id = 'ensys-boot';
      el.className = 'boot';
      el.innerHTML = '<div class="boot-inner"><div class="spinner"></div>'
        + '<p id="ensys-boot-msg"></p>'
        + '<p id="ensys-boot-sub" class="muted"></p></div>';
      document.body.appendChild(el);
    }
    if (msg !== undefined) document.getElementById('ensys-boot-msg').textContent = msg;
    if (sub !== undefined) document.getElementById('ensys-boot-sub').textContent = sub;
  }

  function splashDone() {
    const el = document.getElementById('ensys-boot');
    if (el) { el.style.opacity = '0'; setTimeout(() => el.remove(), 300); }
  }

  /* -------------------------------------------------------------- fetching */
  // Every request carries a deadline that covers reading the body as well as
  // getting the headers: fetch resolves as soon as the headers arrive, and a
  // response whose body then stalls will otherwise wait for ever.

  async function timed(url, opts, ms, label) {
    const c = new AbortController();
    const timer = setTimeout(() => c.abort(), ms);
    const started = Date.now();
    try {
      const r = await fetch(url, Object.assign({ signal: c.signal }, opts));
      const text = await r.text();
      note(`${label} -> ${r.status}, ${text.length} B, ${Date.now() - started} ms`);
      return { status: r.status, text };
    } catch (e) {
      const why = c.signal.aborted ? `timed out after ${ms} ms`
                                   : `failed: ${e.message}`;
      note(`${label} -> ${why}`);
      const err = new Error(`${label}: ${why}`);
      err.timedOut = c.signal.aborted;
      throw err;
    } finally {
      clearTimeout(timer);
    }
  }

  /* --------------------------------------------------------- server mode */

  let usePost = true;
  try {
    if (localStorage.getItem('ensys.transport') === 'get') usePost = false;
  } catch (e) { /* storage unavailable */ }

  async function serverCall(action, payload) {
    const body = JSON.stringify(payload || {});
    // A call that only reads gets a short deadline: it either answers
    // quickly or something is wrong, and waiting three minutes to find that
    // out helps nobody. A call that runs an optimisation gets the long one.
    const ms = QUICK.has(action) ? QUICK_TIMEOUT_MS : CALL_TIMEOUT_MS;
    const send = method => method === 'POST'
      ? timed('api/' + action, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body,
        }, ms, `POST api/${action}`)
      : timed('api/' + action + '?payload=' + encodeURIComponent(body),
              {}, ms, `GET api/${action}`);

    const parse = res => {
      try {
        return JSON.parse(res.text);
      } catch (e) {
        return {
          ok: false,
          error: `The engine replied to "${action}" with something that is `
               + `not JSON (HTTP ${res.status}): ${res.text.slice(0, 200)}`,
        };
      }
    };

    // POST first; if the request cannot get through, the same call is
    // retried as a GET. Some security software on Windows inspects loopback
    // traffic and stalls POST bodies while letting GETs past, and that is
    // otherwise indistinguishable from a dead engine.
    if (usePost) {
      try {
        return parse(await send('POST'));
      } catch (e) {
        if (body.length > GET_LIMIT) return { ok: false, error: reason(e, action) };
        note('POST did not get through; retrying as GET');
      }
    }
    try {
      const out = parse(await send('GET'));
      if (usePost) {
        usePost = false;
        try { localStorage.setItem('ensys.transport', 'get'); } catch (e) {}
      }
      return out;
    } catch (e) {
      usePost = true;
      try { localStorage.setItem('ensys.transport', 'post'); } catch (e) {}
      return { ok: false, error: reason(e, action) };
    }
  }

  function reason(e, action) {
    return e.timedOut
      ? `The engine did not answer "${action}" in time. It may be busy, or `
        + `stopped — check the window running server.py.`
      : `Could not reach the engine for "${action}". Check that the window `
        + `running server.py is still open.`;
  }

  async function findServer() {
    if (location.protocol === 'file:') return null;
    try {
      const r = await timed('api/health', {}, HEALTH_TIMEOUT_MS,
                            'GET api/health');
      if (r.status !== 200) return null;
      const h = JSON.parse(r.text);
      if (!h.ok) return null;
      note(`server ${h.version}`
           + (h.pid ? `, pid ${h.pid}` : '')
           + (h.root ? `, serving ${h.root}` : ''));
      if (!h.root) {
        note('NOTE: the running server predates the files on disk; '
             + 'stop it and start it again to load the current code.');
      }
      return h;
    } catch (e) {
      return null;
    }
  }

  /* -------------------------------------------------------- browser mode */

  const MODULES = [
    '__init__', 'timeseries', 'system', 'assets', 'dispatch', 'economics',
    'metrics', 'optimise', 'api', 'catalogue', 'costs', 'i18n', 'validate',
    'models/__init__', 'models/pv', 'models/wind', 'models/battery',
    'models/grid', 'models/genset', 'models/ev', 'models/generators',
    'models/storage', 'models/evfleet',
    'resources/__init__', 'resources/geo', 'resources/cache',
    'resources/providers', 'resources/synth',
    'optim/__init__', 'optim/pareto', 'optim/base', 'optim/mopso',
    'optim/nsga2', 'optim/exhaustive', 'optim/algorithms', 'optim/screen',
    'sizing/__init__', 'sizing/inverter', 'sizing/cables',
    'sizing/protection', 'sizing/voltage', 'sizing/design',
    'diagram/__init__', 'diagram/symbols', 'diagram/sheet',
    'diagram/topology', 'diagram/sld',
    'report/__init__',
  ];

  async function bootPyodide() {
    splash('Starting the calculation engine in your browser…',
           'This happens once; the files are then cached by the browser.');

    await new Promise((res, rej) => {
      const s = document.createElement('script');
      s.src = PYODIDE_CDN + 'pyodide.js';
      s.onload = res;
      s.onerror = () => rej(new Error(
        'Could not load Pyodide from the CDN. With no local server and no '
        + 'internet access there is no engine to run.'));
      document.head.appendChild(s);
    });

    splash('Loading Python…', 'about 10 MB, once');
    const py = await loadPyodide({ indexURL: PYODIDE_CDN });

    splash('Loading the sizing engine…', '');
    py.FS.mkdirTree('/home/pyodide/ensys');
    for (const d of ['models', 'resources', 'optim', 'sizing', 'diagram',
                     'report']) {
      py.FS.mkdirTree('/home/pyodide/ensys/' + d);
    }
    let done = 0;
    for (const m of MODULES) {
      const r = await fetch(`../ensys/${m}.py`);
      if (!r.ok) throw new Error(`Missing engine module: ensys/${m}.py`);
      py.FS.writeFile(`/home/pyodide/ensys/${m}.py`, await r.text());
      done += 1;
      splash(undefined, `${done} of ${MODULES.length} modules`);
    }

    splash('Starting up…', '');
    await py.runPythonAsync(`
import sys, json
sys.path.insert(0, "/home/pyodide")
from ensys import api as _api
from ensys.resources import providers as _providers
import pyodide.http as _http

def _fetch(url, timeout=45):
    # The browser's fetch is the only transport here. Some providers send no
    # CORS headers, so this can fail where the server build succeeds; say so
    # plainly rather than reporting a generic network fault.
    try:
        return _http.open_url(url).read()
    except Exception as e:
        raise _providers.ProviderError(
            f"The browser could not fetch {url.split('?')[0]}: {e}. "
            "This provider may not allow cross-origin requests. Run the "
            "local server (python server.py) for full provider access, or "
            "use offline mode with monthly averages."
        )

_providers.set_fetcher(_fetch)
`);
    const handle = py.runPython('_api.handle_json');
    splashDone();

    return async function browserCall(action, payload) {
      try {
        await new Promise(r => setTimeout(r, 0));   // let the page repaint
        return JSON.parse(handle(action, JSON.stringify(payload || {})));
      } catch (e) {
        return { ok: false, error: 'Engine error: ' + e.message };
      }
    };
  }

  /* --------------------------------------------------------------- start */

  let transport = null;

  async function call(action, payload) {
    if (!transport) {
      return { ok: false, error: 'The calculation engine is not available.' };
    }
    return transport(action, payload);
  }

  api.ready = (async function () {
    const health = await findServer();
    if (health) {
      api.mode = 'server';
      api.version = health.version;
      api.health = health;
      transport = serverCall;
      return api;
    }
    try {
      transport = await bootPyodide();
      api.mode = 'browser';
      api.version = 'browser';
      return api;
    } catch (e) {
      splashDone();
      api.mode = 'unavailable';
      note('no engine: ' + e.message);
      throw e;
    }
  })();

  return api;
})();
