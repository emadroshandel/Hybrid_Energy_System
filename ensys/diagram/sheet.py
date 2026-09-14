"""
Drawing sheet: border, title block, legend and notes.

A single-line diagram that an installer or a network operator will accept is
a controlled document, not a picture. It carries a title block naming the
project and who signed it off, a legend defining every symbol used, a notes
panel citing the standards it was drawn to, and a sheet number. Without
those it is a sketch.

The legend is built from the symbols the drawing actually used, collected
during rendering rather than hard-coded, so it can never list a symbol that
is not on the sheet or omit one that is.
"""

from __future__ import annotations

from . import symbols as sym

# Sheet geometry, in SVG user units. Proportioned like an A3 landscape
# sheet so a printed page needs no rescaling.
SHEET_W = 1560
SHEET_H = 1040

MARGIN = 18
LEGEND_W = 250
NOTES_H = 132
TITLE_H = 118

# The drawing area, once the furniture is subtracted.
DRAW_X0 = MARGIN + 14
DRAW_Y0 = MARGIN + 14
DRAW_X1 = SHEET_W - MARGIN - LEGEND_W - 16
DRAW_Y1 = SHEET_H - MARGIN - TITLE_H - 16

CSS = """
.dg-root { font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI",
           Roboto, Helvetica, Arial, sans-serif; }
.dg-label   { font-size: 11px; font-weight: 650; fill: var(--dg-text); }
.dg-sublabel{ font-size: 9.5px; fill: var(--dg-muted); }
.dg-rating  { font-size: 9.5px; font-weight: 600; fill: var(--dg-accent); }
.dg-symbol  { font-size: 14px; font-weight: 650; fill: var(--dg-line); }
.dg-tiny    { font-size: 8px; fill: var(--dg-muted); }
.dg-tiny-b  { font-size: 8px; font-weight: 700; fill: var(--dg-text); }
.dg-encl    { font-size: 11.5px; font-weight: 700; fill: var(--dg-text); }
.dg-title   { font-size: 15px; font-weight: 700; fill: var(--dg-text); }
.dg-sub     { font-size: 10.5px; fill: var(--dg-muted); }
.dg-bus     { stroke: var(--dg-bus); stroke-width: 5; stroke-linecap: round; }
.dg-wire    { stroke: var(--dg-line); stroke-width: 1.5; fill: none; }
.dg-wire-dc { stroke: var(--dg-dc); stroke-width: 1.5; fill: none; }
.dg-comms   { stroke: var(--dg-muted); stroke-width: 1; fill: none;
              stroke-dasharray: 3 3; }
.dg-encl-box{ stroke: var(--dg-line); stroke-width: 2.4; fill: none; }
.dg-frame   { stroke: var(--dg-line); stroke-width: 1.4; fill: none; }
.dg-rule    { stroke: var(--dg-frame); stroke-width: 0.8; }
.dg-divider { stroke: var(--dg-muted); stroke-width: 1;
              stroke-dasharray: 8 4; }
.dg-note    { font-size: 8.5px; fill: var(--dg-ink2); }
.dg-field   { font-size: 8px; font-weight: 700; fill: var(--dg-muted);
              letter-spacing: .04em; }
.dg-value   { font-size: 10.5px; fill: var(--dg-text); }
"""

THEME = """
:root {
  --dg-bg:#ffffff; --dg-line:#12171c; --dg-text:#0d1116; --dg-ink2:#333b44;
  --dg-muted:#5d6874; --dg-bus:#8a4b00; --dg-dc:#12459e; --dg-frame:#b9c1c9;
  --dg-accent:#0d5c52;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --dg-bg:#0f1418; --dg-line:#dbe2e9; --dg-text:#f2f5f8; --dg-ink2:#c3ccd6;
    --dg-muted:#94a0ad; --dg-bus:#f0a44a; --dg-dc:#6ba7f5; --dg-frame:#333d47;
    --dg-accent:#4fd6c0;
  }
}
:root[data-theme="dark"] {
  --dg-bg:#0f1418; --dg-line:#dbe2e9; --dg-text:#f2f5f8; --dg-ink2:#c3ccd6;
  --dg-muted:#94a0ad; --dg-bus:#f0a44a; --dg-dc:#6ba7f5; --dg-frame:#333d47;
  --dg-accent:#4fd6c0;
}
@media print {
  :root { --dg-bg:#fff; --dg-line:#000; --dg-text:#000; --dg-ink2:#222;
          --dg-muted:#444; --dg-bus:#000; --dg-dc:#000; --dg-frame:#888;
          --dg-accent:#000; }
}
"""

# Every legend entry the drawings can use. Keyed by the same name the
# renderer records, so the two cannot drift apart.
LEGEND_LIBRARY = {
    "meter":        ("Utility electrical meter", "meter"),
    "breaker":      ("Circuit breaker (CB)", "breaker"),
    "spd_ac":       ("AC rated surge arrester", "spd"),
    "spd_dc":       ("DC rated surge arrester", "spd_dc"),
    "phase1":       ("Single phase indicator", "phase1"),
    "phase3":       ("Three phase indicator (L1,L2,L3)", "phase3"),
    "phase4":       ("Four wire indicator (L1,L2,L3,N)", "phase4"),
    "earth_leakage": ("Earth leakage (EL / RCD)", "el"),
    "ct":           ("Current transformer", "ct"),
    "pole2":        ("2 (two) pole", "text:2P"),
    "pole3":        ("3 (three) pole", "text:3P"),
    "pole4":        ("4 (four) pole", "text:4P"),
    "pv_module":    ("PV module", "pv_module"),
    "fuse_dc":      ("DC rated fuse", "fuse"),
    "isolator_dc":  ("DC rated isolator", "isolator"),
    "earth":        ("Earth connected symbol", "earth"),
    "inverter":     ("Inverter symbol", "inverter"),
    "battery":      ("Battery", "battery"),
    "changeover":   ("Change over switch", "changeover"),
    "genset":       ("Back-up generator", "genset"),
    "transformer":  ("LV to MV step-up transformer", "transformer"),
    "mv_bus":       ("Medium voltage bus bar", "bus"),
    "rmu":          ("Ring main unit", "text:RMU"),
    "no_interconnect": ("No inter-connection", "nointer"),
    "wind":         ("Wind turbine generator", "wind"),
    "ev":           ("EV supply equipment", "ev"),
    "fuel_cell":    ("Fuel cell", "fuel_cell"),
    "hydrogen":     ("Hydrogen storage", "hydrogen"),
    "flywheel":     ("Flywheel storage", "flywheel"),
    "hydro":        ("Hydro turbine generator", "hydro"),
    "biomass":      ("Biomass generator", "biomass"),
    "geothermal":   ("Geothermal plant", "geothermal"),
    "csp":          ("Concentrating solar power", "csp"),
    "load":         ("Load", "load"),
    "combiner":     ("Combiner box", "box"),
}

DEFAULT_NOTES = [
    "Installation, earthing and bonding shall comply with the current "
    "national wiring rules and the listed normative references, e.g. "
    "IEC 60364-7-712 (PV), IEC 62548, IEC 60364-5-52 (cables).",
    "Systems with micro-inverters or power optimisers have different "
    "wiring to that indicated on this drawing.",
    "Ratings shown are calculated design values. Verify against "
    "manufacturers' data and the network operator's requirements before "
    "procurement or construction.",
]


class Sheet:
    """
    A drawing sheet under construction.

    Collects SVG fragments and the set of legend keys used, then renders
    the whole document with its furniture.
    """

    def __init__(self, title, subtitle=None, project=None, description=None,
                 sheet_no=1, sheet_of=1, revision="Rev 01", scale="N/A",
                 notes=None, width=SHEET_W, height=SHEET_H, language="en"):
        self.title = title
        self.subtitle = subtitle
        self.project = project or title
        self.description = description or ""
        self.sheet_no = sheet_no
        self.sheet_of = sheet_of
        self.revision = revision
        self.scale = scale
        self.notes = list(notes) if notes else list(DEFAULT_NOTES)
        self.width = width
        self.height = height
        self.language = language
        self._body = []
        self._legend = []          # ordered, de-duplicated
        self._legend_seen = set()

    # ---------------------------------------------------------- drawing

    def add(self, fragment):
        if fragment:
            self._body.append(fragment)

    def use(self, *keys):
        """Record that a legend symbol appears on this sheet."""
        for k in keys:
            if k and k not in self._legend_seen and k in LEGEND_LIBRARY:
                self._legend_seen.add(k)
                self._legend.append(k)

    # ------------------------------------------------------------ render

    def render(self):
        w, h = self.width, self.height
        legend_x = w - MARGIN - LEGEND_W
        parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="0 0 {w} {h}" width="100%" class="dg-root" '
            f'role="img" aria-label="{sym._esc(self.title)}">',
            f"<style>{THEME}{CSS}</style>",
            f'<rect width="{w}" height="{h}" fill="var(--dg-bg)"/>',
            # outer border
            f'<rect x="{MARGIN}" y="{MARGIN}" width="{w - 2 * MARGIN}" '
            f'height="{h - 2 * MARGIN}" class="dg-frame"/>',
        ]
        parts.extend(self._body)
        parts.append(self._notes_panel(legend_x, MARGIN))
        parts.append(self._legend_panel(legend_x, MARGIN + NOTES_H + 8))
        parts.append(self._title_block(MARGIN, h - MARGIN - TITLE_H, w - 2 * MARGIN))
        parts.append("</svg>")
        return "".join(parts)

    # ------------------------------------------------------------ notes

    def _notes_panel(self, x, y):
        wpx = LEGEND_W
        out = [
            f'<rect x="{x}" y="{y}" width="{wpx}" height="{NOTES_H}" '
            f'class="dg-frame"/>',
            f'<text x="{x + 7}" y="{y + 13}" class="dg-tiny-b">Notes:</text>',
        ]
        ty = y + 24
        for note in self.notes:
            lines = _wrap(note, 46)
            # Start a note only if it fits whole: half a sentence trailing
            # off the bottom of the panel reads as a printing fault.
            if ty + 9.2 * len(lines) > y + NOTES_H - 3:
                continue
            for line in lines:
                out.append(
                    f'<text x="{x + 7}" y="{ty}" class="dg-note">'
                    f'{sym._esc(line)}</text>'
                )
                ty += 9.2
            ty += 1.5
        return "".join(out)

    # ----------------------------------------------------------- legend

    def _legend_panel(self, x, y):
        rows = [(k,) + LEGEND_LIBRARY[k] for k in self._legend]
        row_h = 26
        height = 22 + row_h * len(rows)
        out = [
            f'<rect x="{x}" y="{y}" width="{LEGEND_W}" height="{height}" '
            f'class="dg-frame"/>',
            f'<text x="{x + 7}" y="{y + 14}" class="dg-tiny-b">Legend:</text>',
            f'<line x1="{x}" y1="{y + 20}" x2="{x + LEGEND_W}" '
            f'y2="{y + 20}" class="dg-rule"/>',
        ]
        col = x + LEGEND_W - 58
        ry = y + 20
        for _key, label, glyph in rows:
            out.append(
                f'<line x1="{x}" y1="{ry + row_h}" x2="{x + LEGEND_W}" '
                f'y2="{ry + row_h}" class="dg-rule"/>'
            )
            out.append(
                f'<line x1="{col}" y1="{ry}" x2="{col}" '
                f'y2="{ry + row_h}" class="dg-rule"/>'
            )
            for i, line in enumerate(_wrap(label, 24)[:2]):
                out.append(
                    f'<text x="{x + 6}" y="{ry + 15 + i * 9}" '
                    f'class="dg-tiny">{sym._esc(line)}</text>'
                )
            out.append(_legend_glyph(glyph, col + 29, ry + row_h / 2))
            ry += row_h
        return "".join(out)

    # ------------------------------------------------------ title block

    def _title_block(self, x, y, w):
        # Three columns: signoff fields | project | sheet number.
        c1 = w * 0.42
        c2 = w * 0.42
        c3 = w - c1 - c2
        out = [
            f'<rect x="{x}" y="{y}" width="{w}" height="{TITLE_H}" '
            f'class="dg-frame"/>',
            f'<line x1="{x + c1}" y1="{y}" x2="{x + c1}" '
            f'y2="{y + TITLE_H}" class="dg-frame"/>',
            f'<line x1="{x + c1 + c2}" y1="{y}" x2="{x + c1 + c2}" '
            f'y2="{y + TITLE_H}" class="dg-frame"/>',
        ]

        # --- column 1: revision + the fields a person signs
        rows = [("REVISION", self.revision), ("DATE", ""), ("EDITOR", ""),
                ("INSTALLER", ""), ("SIGNOFF", ""), ("PRINT NAME", "")]
        rh = TITLE_H / len(rows)
        for i, (field, value) in enumerate(rows):
            ry = y + i * rh
            if i:
                out.append(
                    f'<line x1="{x}" y1="{ry}" x2="{x + c1}" y2="{ry}" '
                    f'class="dg-rule"/>'
                )
            out.append(
                f'<text x="{x + 8}" y="{ry + rh / 2 + 3}" class="dg-field">'
                f'{field}:</text>'
            )
            out.append(
                f'<line x1="{x + 92}" y1="{ry}" x2="{x + 92}" '
                f'y2="{ry + rh}" class="dg-rule"/>'
            )
            if value:
                out.append(
                    f'<text x="{x + 100}" y="{ry + rh / 2 + 3.5}" '
                    f'class="dg-value">{sym._esc(value)}</text>'
                )

        # --- column 2: project and description
        px = x + c1 + 10
        out.append(
            f'<text x="{px}" y="{y + 15}" class="dg-field">PROJECT NAME:</text>'
        )
        for i, line in enumerate(_wrap(self.project, 44)[:2]):
            out.append(
                f'<text x="{px}" y="{y + 31 + i * 13}" class="dg-value">'
                f'{sym._esc(line)}</text>'
            )
        out.append(
            f'<line x1="{x + c1}" y1="{y + TITLE_H * 0.55}" '
            f'x2="{x + c1 + c2}" y2="{y + TITLE_H * 0.55}" class="dg-rule"/>'
        )
        out.append(
            f'<text x="{px}" y="{y + TITLE_H * 0.55 + 14}" '
            f'class="dg-field">DESCRIPTION:</text>'
        )
        for i, line in enumerate(_wrap(self.description, 44)[:2]):
            out.append(
                f'<text x="{px}" y="{y + TITLE_H * 0.55 + 29 + i * 12}" '
                f'class="dg-value">{sym._esc(line)}</text>'
            )
        out.append(
            f'<line x1="{x + c1 + c2 * 0.72}" y1="{y + TITLE_H * 0.55}" '
            f'x2="{x + c1 + c2 * 0.72}" y2="{y + TITLE_H}" class="dg-rule"/>'
        )
        out.append(
            f'<text x="{x + c1 + c2 * 0.72 + 8}" '
            f'y="{y + TITLE_H * 0.55 + 14}" class="dg-field">SCALE</text>'
        )
        out.append(
            f'<text x="{x + c1 + c2 * 0.72 + 8}" '
            f'y="{y + TITLE_H * 0.55 + 32}" class="dg-value">'
            f'{sym._esc(self.scale)}</text>'
        )

        # --- column 3: sheet number
        sx = x + c1 + c2
        out.append(
            f'<text x="{sx + 10}" y="{y + 15}" class="dg-field">SHEET</text>'
        )
        out.append(
            f'<text x="{sx + c3 / 2}" y="{y + 55}" '
            f'style="font-size:26px;font-weight:700;fill:var(--dg-text)" '
            f'text-anchor="middle">{self.sheet_no}</text>'
        )
        out.append(
            f'<line x1="{sx}" y1="{y + TITLE_H * 0.62}" x2="{x + w}" '
            f'y2="{y + TITLE_H * 0.62}" class="dg-rule"/>'
        )
        out.append(
            f'<text x="{sx + 10}" y="{y + TITLE_H * 0.62 + 14}" '
            f'class="dg-field">OF</text>'
        )
        out.append(
            f'<text x="{sx + c3 / 2}" y="{y + TITLE_H - 12}" '
            f'style="font-size:20px;font-weight:700;fill:var(--dg-text)" '
            f'text-anchor="middle">{self.sheet_of}</text>'
        )
        return "".join(out)


def _wrap(text, width):
    """Naive word wrap, adequate for the short strings on a drawing."""
    words = str(text or "").split()
    lines, cur = [], ""
    for word in words:
        trial = (cur + " " + word).strip()
        if len(trial) <= width:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines or [""]


def _legend_glyph(glyph, cx, cy):
    """Draw the miniature symbol shown in a legend row."""
    if glyph.startswith("text:"):
        return (
            f'<text x="{cx}" y="{cy + 4}" text-anchor="middle" '
            f'class="dg-tiny-b">{glyph[5:]}</text>'
        )
    fn = {
        "meter": lambda: sym.meter(cx, cy, letter="M"),
        "breaker": lambda: _mini(sym.breaker(cx, cy), 0.55, cx, cy),
        "spd": lambda: _mini(sym.spd(cx, cy), 0.55, cx, cy),
        "spd_dc": lambda: _mini(sym.spd(cx, cy), 0.55, cx, cy),
        "el": lambda: sym.earth_leakage(cx, cy, scale=0.55),
        "ct": lambda: sym.current_transformer(cx, cy, scale=0.6),
        "pv_module": lambda: _mini(sym.pv_array(cx, cy), 0.5, cx, cy),
        "fuse": lambda: _mini(sym.fuse(cx, cy), 0.55, cx, cy),
        "isolator": lambda: _mini(sym.isolator(cx, cy), 0.55, cx, cy),
        "earth": lambda: _mini(sym.earth(cx, cy), 0.6, cx, cy),
        "inverter": lambda: _mini(sym.inverter(cx, cy), 0.5, cx, cy),
        "battery": lambda: _mini(sym.battery(cx, cy), 0.5, cx, cy),
        "changeover": lambda: sym.changeover(cx, cy, scale=0.55),
        "genset": lambda: _mini(sym.generator(cx, cy), 0.5, cx, cy),
        "transformer": lambda: _mini(sym.transformer(cx, cy), 0.5, cx, cy),
        "bus": lambda: (
            f'<line x1="{cx - 20}" y1="{cy}" x2="{cx + 20}" y2="{cy}" '
            f'class="dg-bus"/>'
        ),
        "nointer": lambda: sym.no_interconnection(cx, cy),
        "phase1": lambda: sym.phase_ticks(cx, cy, 1, scale=1.3),
        "phase3": lambda: sym.phase_ticks(cx, cy, 3, scale=1.3),
        "phase4": lambda: sym.phase_ticks(cx, cy, 4, scale=1.3),
        "wind": lambda: _mini(sym.wind_turbine(cx, cy), 0.5, cx, cy),
        "ev": lambda: _mini(sym.ev_charger(cx, cy), 0.5, cx, cy),
        "fuel_cell": lambda: _mini(sym.fuel_cell(cx, cy), 0.5, cx, cy),
        "hydrogen": lambda: _mini(sym.hydrogen_tank(cx, cy), 0.5, cx, cy),
        "flywheel": lambda: _mini(sym.flywheel(cx, cy), 0.5, cx, cy),
        "hydro": lambda: _mini(sym.hydro_turbine(cx, cy), 0.5, cx, cy),
        "biomass": lambda: _mini(sym.biomass(cx, cy), 0.5, cx, cy),
        "geothermal": lambda: _mini(sym.geothermal(cx, cy), 0.5, cx, cy),
        "csp": lambda: _mini(sym.csp(cx, cy), 0.5, cx, cy),
        "load": lambda: _mini(sym.load(cx, cy), 0.55, cx, cy),
        "box": lambda: (
            f'<rect x="{cx - 16}" y="{cy - 9}" width="32" height="18" '
            f'class="dg-encl-box"/>'
        ),
    }.get(glyph)
    return fn() if fn else ""


def _mini(fragment, scale, cx, cy):
    """Shrink an already-placed symbol about its own centre."""
    return (
        f'<g transform="translate({cx},{cy}) scale({scale}) '
        f'translate({-cx},{-cy})">{fragment}</g>'
    )
