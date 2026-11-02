"""Measured UI audit for HES (Playwright): contrast, text size, targets, labels, keyboard, 3-D view.
Run the server first, then: python tests/ui_audit.py http://localhost:8000/ ui_audit_out"""
import json, sys, os
from playwright.sync_api import sync_playwright

URL, OUT = sys.argv[1], sys.argv[2]
os.makedirs(OUT, exist_ok=True)

MEASURE = r"""
() => {
  const lum = c => { const m = c.match(/[\d.]+/g); if (!m) return null;
    const [r,g,b] = m.slice(0,3).map(v => { v = v/255; return v <= .03928 ? v/12.92 : Math.pow((v+.055)/1.055, 2.4); });
    return { L: .2126*r + .7152*g + .0722*b, a: m[3] === undefined ? 1 : +m[3] }; };
  const bgOf = el => { for (let e = el; e; e = e.parentElement) {
      const c = getComputedStyle(e).backgroundColor; const l = lum(c);
      if (l && l.a > 0.5) return l.L; } return 1; };
  const vis = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && +s.opacity > 0.05; };
  const out = { contrast: [], small: [], targets: [], unnamedBtn: [], unlabeled: [], clickNoKey: [] };
  const seen = new Set();
  document.querySelectorAll('body *').forEach(el => {
    if (!vis(el)) return;
    const own = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
    if (own && !(el.closest('svg'))) {
      const s = getComputedStyle(el); const fg = lum(s.color); const bg = bgOf(el);
      const fs = parseFloat(s.fontSize); const bold = +s.fontWeight >= 700;
      const ratio = (Math.max(fg.L,bg)+.05)/(Math.min(fg.L,bg)+.05);
      const need = (fs >= 24 || (fs >= 18.66 && bold)) ? 3 : 4.5;
      const key = el.className + '|' + s.color;
      if (ratio < need - 0.01 && !seen.has(key)) { seen.add(key);
        out.contrast.push(`${el.tagName.toLowerCase()}.${el.className} "${el.textContent.trim().slice(0,30)}" ${ratio.toFixed(2)}:1`); }
      if (fs < 12) out.small.push(`${el.tagName.toLowerCase()}.${el.className} ${fs}px`);
    }
    const tag = el.tagName.toLowerCase();
    const interactive = tag === 'button' || (tag === 'a' && el.href) || tag === 'select' || (tag === 'input' && el.type !== 'hidden') || el.getAttribute('role') === 'button' || el.tabIndex >= 0 && el.hasAttribute('tabindex');
    if (interactive) {
      const r = el.getBoundingClientRect();
      if ((r.width < 24 || r.height < 24) && !(tag === 'input' && (el.type === 'checkbox' || el.type === 'radio')))
        out.targets.push(`${tag}#${el.id}.${el.className} ${r.width.toFixed(0)}x${r.height.toFixed(0)}`);
    }
    if ((tag === 'button' || el.getAttribute('role') === 'button') && !(el.getAttribute('aria-label') || el.textContent.trim() || el.getAttribute('aria-labelledby')))
      out.unnamedBtn.push(tag + '#' + el.id);
    if ((tag === 'input' && el.type !== 'hidden') || tag === 'select' || tag === 'textarea') {
      const named = el.labels && el.labels.length || el.getAttribute('aria-label') || el.getAttribute('aria-labelledby') || el.title;
      if (!named) out.unlabeled.push(`${tag}#${el.id}[${el.type || ''}]`);
    }
  });
  document.querySelectorAll('.hl, tr.clickable, circle, .q').forEach(el => {
    if (vis(el) && el.tabIndex < 0 && el.tagName !== 'BUTTON') out.clickNoKey.push(el.tagName.toLowerCase() + '.' + (el.getAttribute('class') || ''));
  });
  out.clickNoKey = [...new Set(out.clickNoKey)].map(k => k + ' x' + 1);
  out.small = [...new Set(out.small)];
  out.targets = [...new Set(out.targets)];
  out.hscroll = document.documentElement.scrollWidth > window.innerWidth + 1;
  out.ariaCurrent = !!document.querySelector('#nav [aria-current]');
  out.skipLink = !!document.querySelector('a[href="#main-content"], .skip-link');
  out.liveRegions = document.querySelectorAll('[aria-live], [role=status], [role=alert]').length;
  return out;
}
"""

def tab_focus_visible(page, n=40):
    bad = 0; checked = 0
    for _ in range(n):
        page.keyboard.press('Tab')
        r = page.evaluate("""() => { const e = document.activeElement; if (!e || e === document.body) return null;
          const s = getComputedStyle(e); const o = s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) > 0;
          const sh = s.boxShadow && s.boxShadow !== 'none';
          return { ok: o || sh, tag: e.tagName + '.' + e.className }; }""")
        if r: checked += 1; bad += 0 if r['ok'] else 1
    return checked, bad

report = {}
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={'width': 1440, 'height': 900})
    page = ctx.new_page()
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.on('console', lambda m: errors.append(m.text) if m.type == 'error' else None)
    page.goto(URL); page.wait_for_selector('#engine-badge:not(:empty)', timeout=20000)
    page.wait_for_timeout(800)
    report['site'] = page.evaluate(MEASURE)
    report['focus_site'] = tab_focus_visible(page)
    page.screenshot(path=f'{OUT}/01_site_1440.png', full_page=True)

    # offline resource + synthetic load -> study
    page.click('#res-mode [data-mode=offline]')
    page.fill('#monthly-ghi', '150,160,185,195,215,230,225,210,190,170,150,140')
    page.fill('#monthly-temp', '7,10,14,20,25,31,33,32,28,21,14,9')
    page.click('#btn-fetch'); page.wait_for_function("!document.querySelector('#res-charts').hidden", timeout=60000)
    page.click('#site-scale [data-scale=house]')
    page.click('#load-mode [data-mode=synthetic]')
    page.click('#btn-load'); page.wait_for_function("!document.querySelector('#load-charts').hidden", timeout=60000)
    page.wait_for_timeout(300)
    report['load'] = page.evaluate(MEASURE)
    page.screenshot(path=f'{OUT}/02_load_1440.png', full_page=True)
    page.evaluate("() => { document.querySelectorAll('.nav-item')[4].click(); }")
    page.fill('#particles', '12'); page.fill('#iterations', '8')
    page.click('#btn-run'); page.wait_for_function("!document.querySelector('#front-card').hidden", timeout=170000)
    page.wait_for_timeout(2500)
    report['optimise'] = page.evaluate(MEASURE)
    page.screenshot(path=f'{OUT}/03_front_1440.png', full_page=True)
    extra = {}
    if page.query_selector('#chart-front svg[tabindex]'):
        page.focus('#chart-front svg'); page.keyboard.press('ArrowRight'); page.keyboard.press('ArrowRight')
        extra['scatter_kb_tip'] = page.evaluate("() => !document.querySelector('#chart-front .chart-tip').hidden && document.querySelector('#chart-front .chart-tip').textContent.slice(0,80)")
        page.keyboard.press('Enter'); page.wait_for_timeout(1200)
        extra['selected_after_enter'] = page.evaluate("() => document.querySelector('.row-pick[aria-pressed=true]')?.textContent")
        q = page.query_selector('#pages .page:not([hidden]) .q')
        if q:
            q.focus(); extra['help_on_focus'] = page.evaluate("() => !document.querySelector('#tip').hidden")
            page.keyboard.press('Escape'); extra['help_esc_closes'] = page.evaluate("() => document.querySelector('#tip').hidden")
    if page.query_selector('#front-view'):
        page.click('#front-view [data-view="3d"]')
        page.wait_for_function("() => window.Front3D && document.querySelector('#front-3d canvas')", timeout=20000)
        page.wait_for_timeout(800)
        extra['three_stats'] = page.evaluate("() => Front3D.stats()")
        page.locator('#front-card').screenshot(path=f'{OUT}/03b_front_3d.png')
        page.click('#front-view [data-view="2d"]')
    report['interactions'] = extra
    page.evaluate("() => { document.querySelectorAll('.nav-item')[5].click(); }")
    page.wait_for_timeout(600)
    report['results'] = page.evaluate(MEASURE)
    page.screenshot(path=f'{OUT}/04_results_1440.png', full_page=True)
    if page.query_selector('#chart-monthly svg[tabindex]'):
        page.focus('#chart-monthly svg'); page.keyboard.press('ArrowRight')
        report['interactions']['bar_kb_tip'] = page.evaluate("() => document.querySelector('#chart-monthly .chart-tip').innerText.replace(/\\n/g,' | ')")
        report['interactions']['chart_widths'] = page.evaluate("() => [...document.querySelectorAll('#results-body svg.chart')].map(s => s.viewBox.baseVal.width)")
    page.evaluate("() => { document.querySelectorAll('.nav-item')[2].click(); }")
    report['components'] = page.evaluate(MEASURE)
    page.evaluate("() => { document.documentElement.setAttribute('data-theme','dark'); document.querySelectorAll('.nav-item')[5].click(); }")
    page.wait_for_timeout(300)
    report['results_dark'] = page.evaluate(MEASURE)
    page.screenshot(path=f'{OUT}/05_results_dark.png', full_page=True)
    page.evaluate("() => { document.documentElement.setAttribute('data-theme','light'); }")
    for w in (390, 1920):
        page.set_viewport_size({'width': w, 'height': 900})
        page.evaluate("() => { document.querySelectorAll('.nav-item')[4].click(); }")
        page.wait_for_timeout(400)
        report[f'optimise_{w}'] = {k: v for k, v in page.evaluate(MEASURE).items() if k in ('hscroll', 'targets')}
        page.screenshot(path=f'{OUT}/06_optimise_{w}.png', full_page=False)
    report['errors'] = errors
    b.close()

summ = {}
for k, v in report.items():
    if isinstance(v, dict) and 'contrast' in v:
        summ[k] = {kk: (len(vv) if isinstance(vv, list) else vv) for kk, vv in v.items()}
    else:
        summ[k] = v
json.dump(report, open(f'{OUT}/report.json', 'w'), indent=1)
print(json.dumps(summ, indent=1))
