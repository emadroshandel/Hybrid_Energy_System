"""
Single-line and three-line diagram generation.

The drawing is built from the sized design, so every rating printed on it is
the rating the calculation produced — there is no second document to fall
out of step with the numbers.

What is drawn depends on what the system *is*. A 5 kW rooftop array and a
5 MW plant are not the same diagram with different labels: they have
different connection points, different switchgear and different voltage
levels. `topology.classify` picks one of nine drawing classes and
`topology.build` computes every rating the drawing has to show; this module
only places things.

Layout, top to bottom:

    utility supply ─┐        ┌─ sources
    point of utility control │  DC isolation, fusing, DC surge protection
    meter, CT                │  converters
    main switch              │  feeder protection
        └──────── MAIN AC DISTRIBUTION BOARD ────────┘
                        outgoing ways
        change-over and sub-board        LV/MV step-up
        essential + non-essential loads  MV switchgear, utility

The three-line variant draws every AC conductor with its neutral and gangs
the poles of each device, which is what an installer needs for
terminations. Both views come from the same specification, so they cannot
disagree.
"""

from __future__ import annotations

from . import symbols as sym
from . import topology as tp
from .sheet import Sheet, DRAW_X0, DRAW_X1

DEV_H = 18          # half-height of a switchgear symbol
MIN_PITCH = 190     # closest two generation columns may sit
# Right of the board, kept clear for the board's own items - the surge
# arrester, the residual current device and the main earth bar. It has to
# hold their captions AND the rating text of the right-most load feeder,
# which sits at the same height: 300 left 48 units between them, so a
# three-line rating ran straight through the SPD caption.
RESERVED_W = 420


# Approximate character width per text class, for the background mask
# behind a caption. Exactness is not needed: the mask only has to cover the
# glyphs, and a little margin either side reads as deliberate.
_CHAR_W = {
    "dg-label": 6.2, "dg-encl": 6.4, "dg-sublabel": 4.9,
    "dg-tiny": 4.2, "dg-tiny-b": 4.4, "dg-rating": 5.0,
}


def _mask_text(x, y, text, cls="dg-label", anchor="middle", pad=5):
    """
    A caption that interrupts whatever it crosses.

    A single-line diagram is mostly vertical conductors, and every centred
    caption sits on one. Drawing the text over the line leaves it looking
    struck through; breaking the line behind the text is what a draughtsman
    does, and it costs one rectangle.
    """
    text = str(text)
    w = len(text) * _CHAR_W.get(cls, 5.2) + pad * 2
    if anchor == "middle":
        rx = x - w / 2
    elif anchor == "end":
        rx = x - w + pad
    else:
        rx = x - pad
    h = 13 if cls in ("dg-label", "dg-encl") else 11
    return (
        f'<rect x="{rx:.1f}" y="{y - h + 3:.1f}" width="{w:.1f}" '
        f'height="{h}" fill="var(--dg-bg)"/>'
        f'<text x="{x}" y="{y}" class="{cls}" text-anchor="{anchor}">'
        f'{sym._esc(text)}</text>'
    )


class _Bands:
    """
    Horizontal bands: the y of every row of equipment.

    Two profiles, because a drawing with a medium-voltage step-up has to
    fit a transformer, an MV bus, MV switchgear and the network below the
    board, and one that does not can give that space to the loads instead.
    """

    def __init__(self, mv=False):
        if mv:
            self.SRC = 110
            self.DC = 206
            self.CONV = 300
            self.PROT = 376
            self.BUS = 448
            self.OUT = 520
            self.SUB = 592
            self.LOAD = 676
            self.TX = 602
            self.MVBUS = 688
            self.MVSW = 750
            self.UTIL = 822
        else:
            self.SRC = 118
            self.DC = 222
            self.CONV = 330
            self.PROT = 412
            self.BUS = 490
            self.OUT = 564
            self.SUB = 666
            self.LOAD = 782
            self.TX = self.MVBUS = self.MVSW = self.UTIL = 0
        self.DB_TOP = self.BUS - 46
        self.DB_BOTTOM = self.OUT + 40
        self.SUB_BOTTOM = self.SUB + 34


# =====================================================================
# Canvas
# =====================================================================

class _Canvas:
    """
    Accumulates SVG and legend keys while the layout runs.

    Conductor multiplicity lives here, because it is the one thing that
    differs everywhere between the two views: in the single-line view a run
    is one line with hash marks stating how many conductors it stands for;
    in the three-line view the conductors are drawn.
    """

    def __init__(self, spec, three_line):
        self.spec = spec
        self.three_line = three_line
        self.phases = spec.phases
        self.side = "right"          # which side ratings are written on
        self.y = _Bands(spec.is_mv)
        self.parts = []
        self.legend = []

    # ---------------------------------------------------------- output

    def add(self, *frags):
        for f in frags:
            if f:
                self.parts.append(f)

    def use(self, *keys):
        self.legend.extend(k for k in keys if k)

    def svg(self):
        return "".join(self.parts)

    # ------------------------------------------------------ conductors

    def ac_offsets(self):
        """(dx, dashed) for each AC conductor drawn at a run."""
        if not self.three_line:
            return [(0.0, False)]
        if self.phases == 3:
            return [(-15.0, False), (0.0, False), (15.0, False), (28.0, True)]
        return [(-9.0, False), (9.0, True)]

    def dc_offsets(self):
        if not self.three_line:
            return [(0.0, False)]
        return [(-8.0, False), (8.0, False)]

    def half_width(self, dc=False):
        offs = self.dc_offsets() if dc else self.ac_offsets()
        return max(abs(o) for o, _d in offs)

    def run(self, x, y0, y1, dc=False, ticks=False):
        """A vertical conductor run between two bands."""
        if y1 <= y0:
            return ""
        cls = "dg-wire-dc" if dc else "dg-wire"
        offs = self.dc_offsets() if dc else self.ac_offsets()
        out = []
        for dx, dashed in offs:
            dash = ' stroke-dasharray="5 3"' if dashed else ""
            out.append(
                f'<line x1="{x + dx:.1f}" y1="{y0:.1f}" '
                f'x2="{x + dx:.1f}" y2="{y1:.1f}" class="{cls}"{dash}/>'
            )
        # In the single-line view the hash marks are the only statement of
        # how many conductors the line represents. Leaving them off is the
        # commonest reason a drawing comes back from a network operator.
        if ticks and not self.three_line and (y1 - y0) > 30:
            mid = (y0 + y1) / 2.0
            if dc:
                out.append(sym.phase_ticks(x, mid, 2, scale=0.9))
            else:
                out.append(sym.phase_ticks(
                    x, mid, self.phases, neutral=True, scale=0.95
                ))
                self.use("phase3" if self.phases == 3 else "phase1")
                self.use("pole4" if self.phases == 3 else "pole2")
        return "".join(out)

    def hrun(self, x0, x1, y, dc=False):
        cls = "dg-wire-dc" if dc else "dg-wire"
        return (
            f'<line x1="{x0:.1f}" y1="{y:.1f}" x2="{x1:.1f}" '
            f'y2="{y:.1f}" class="{cls}"/>'
        )

    # ---------------------------------------------------------- devices

    def device(self, fn, x, y, ratings=(), dc=False, legend=None,
               caption=None, side=None, rx=None):
        """
        Place a switchgear symbol on a run, with its ratings beside it.

        In the three-line view the device appears on every phase conductor,
        scaled down, with the dashed gang bar that says the poles operate
        together. The ratings are written once.

        `side` defaults to whichever side the canvas is currently writing
        on, so a run that would otherwise write its schedule into something
        else can be flipped for the length of one column.
        """
        side = side or self.side
        self.use(legend)
        out = []
        if self.three_line:
            offs = [o for o, d in
                    (self.dc_offsets() if dc else self.ac_offsets())
                    if not d]
            for dx in offs:
                frag = fn(x + dx, y)
                out.append(
                    f'<g transform="translate({x + dx:.1f},{y}) scale(0.52) '
                    f'translate({-(x + dx):.1f},{-y})">{frag}</g>'
                )
            if len(offs) > 1:
                out.append(
                    f'<line x1="{x + offs[0]:.1f}" y1="{y}" '
                    f'x2="{x + offs[-1]:.1f}" y2="{y}" class="dg-comms"/>'
                )
        else:
            out.append(fn(x, y))

        ratings = [r for r in ratings if r]
        if ratings:
            # Clear the widest symbol, not just the conductor bundle: a
            # converter is 36 units across and text at the conductor edge
            # lands on top of it.
            hw = max(self.half_width(dc), 20.0)
            dy = y - 5 - 5 * (len(ratings) - 1)
            if side == "right":
                out.append(sym.rating_text(rx if rx is not None
                                           else x + hw + 14, dy, ratings))
            else:
                out.append(sym.rating_text(rx if rx is not None
                                           else x - hw - 14, dy, ratings,
                                           anchor="end"))
        if caption:
            out.append(_mask_text(x, y + 32, caption, "dg-label"))
        return "".join(out)

    def source(self, fn, x, ratings=(), label=None, sublabel=None):
        """
        A generation or supply symbol at the top of a column.

        The caption goes above the symbol rather than below it, because
        below is where the conductor leaves and text sitting on a conductor
        is the thing that makes a drawing look drawn by a machine.
        """
        y = self.y.SRC
        out = [fn(x, y)]
        if label:
            out.append(
                f'<text x="{x}" y="{y - 48}" class="dg-label" '
                f'text-anchor="middle">{sym._esc(label)}</text>'
            )
        if sublabel:
            out.append(
                f'<text x="{x}" y="{y - 35}" class="dg-sublabel" '
                f'text-anchor="middle">{sym._esc(sublabel)}</text>'
            )
        ratings = [r for r in ratings if r]
        if ratings:
            out.append(sym.rating_text(
                x + 32, y - 5 - 5 * (len(ratings) - 1), ratings))
        return "".join(out)

    def tap(self, x, y):
        return f'<circle cx="{x:.1f}" cy="{y}" r="3.6" fill="var(--dg-bus)"/>'


class _Column:
    """One vertical branch: what to draw, and at which x."""

    def __init__(self, key, draw):
        self.key = key
        self.draw = draw
        self.x = 0.0


# =====================================================================
# Renderer
# =====================================================================

def render(design, title=None, subtitle=None, three_line=False, width=None,
           topology=None, project=None, sheet_no=None, sheet_of=2,
           revision="Rev 01", language="en", **kw):
    """
    Produce a complete standalone SVG drawing sheet.

    The SVG carries its own stylesheet and theme variables, so it renders
    the same embedded in the app, saved to a file, or printed.
    """
    spec = tp.build(design, topology=topology, **kw)
    c = _Canvas(spec, three_line)
    y = c.y

    view = "Three-line diagram" if three_line else "Single-line diagram"
    sheet = Sheet(
        title=title or view,
        subtitle=subtitle,
        project=project or title or "Hybrid energy system",
        description=f"{view} - {tp.describe(spec)}",
        sheet_no=sheet_no if sheet_no is not None else (2 if three_line else 1),
        sheet_of=sheet_of,
        revision=revision,
        notes=_notes(design, spec),
        language=language,
    )

    # ---------------------------------------------------- above the bus
    supply_col, gen_cols = _above_columns(design, spec)
    xs = []
    if supply_col:
        supply_col.x = DRAW_X0 + 96
        xs.append(supply_col.x)
    gx0 = DRAW_X0 + 258 if supply_col else DRAW_X0 + 90
    for col, gxx in zip(gen_cols,
                        _spread(gx0, DRAW_X1 - 96, len(gen_cols), "left")):
        col.x = gxx
        xs.append(gxx)

    # The board spans the sheet whatever the design, as the templates draw
    # it: a board that shrinks to fit two feeders leaves the drawing looking
    # like something has been cropped off it.
    bus_x0 = DRAW_X0 + 30
    bus_x1 = DRAW_X1 - 6

    for col in ([supply_col] if supply_col else []) + gen_cols:
        c.add(col.draw(c, col.x))

    # ----------------------------------------------------- the board
    c.add(_distribution_board(c, spec, bus_x0, bus_x1))
    for col in ([supply_col] if supply_col else []) + gen_cols:
        c.add(c.run(col.x, y.PROT + DEV_H, y.BUS, ticks=True))
        c.add(c.tap(col.x, y.BUS))

    # ---------------------------------------------------- below the bus
    below = _below_columns(design, spec)
    if spec.is_mv:
        # The MV chain takes the left of the sheet; the load feeders sit to
        # the right of it, so the two never cross.
        mv_x = bus_x0 + 190
        c.add(_mv_chain(c, spec, mv_x, bus_x0, bus_x1))
        load_x0, load_x1 = bus_x0 + 480, bus_x1 - RESERVED_W
    else:
        load_x0, load_x1 = bus_x0 + 30, bus_x1 - RESERVED_W

    for col, cx in zip(below, _spread(load_x0, load_x1, len(below))):
        col.x = cx
        c.add(c.tap(cx, y.BUS))
        c.add(col.draw(c, cx))

    # ------------------------------------------------------ main earth
    ex = bus_x1 - 45
    c.add(c.hrun(bus_x1 - 57, ex, y.BUS))
    c.add(f'<line x1="{ex}" y1="{y.BUS}" x2="{ex}" y2="{y.BUS + 22}" '
          f'class="dg-wire"/>')
    c.add(sym.earth(ex, y.BUS + 34))
    c.add(_stack(ex, y.BUS + 62,
                 ["MAIN EARTH BAR", spec.earthing_system]))
    c.use("earth")

    # ---------------------------------------------------- system stamp
    c.add(_system_stamp(spec, DRAW_X0 + 4, 30))
    if three_line:
        c.add(_phase_key(c, DRAW_X1 - 200, 32))

    sheet.add(c.svg())
    sheet.use(*c.legend)
    return sheet.render()


def render_pair(design, title=None, subtitle=None, **kw):
    """Both views, for a report that needs each."""
    return {
        "single_line": render(design, title, subtitle, three_line=False,
                              sheet_no=1, sheet_of=2, **kw),
        "three_line": render(design, title, subtitle, three_line=True,
                             sheet_no=2, sheet_of=2, **kw),
    }


def _spread(x0, x1, n, align="center"):
    """
    Column centres across a span.

    Columns are never packed tighter than the pitch, and never flung to the
    far ends of an otherwise empty sheet either: a two-column drawing that
    puts its columns a metre apart reads as though something is missing.
    Generation columns are left-aligned so the drawing reads left to right;
    feeders below the board are spread across it.
    """
    if n <= 0:
        return []
    pitch = min(max((x1 - x0) / n, MIN_PITCH), 300.0)
    total = pitch * n
    if align == "center" and total < (x1 - x0):
        x0 += ((x1 - x0) - total) / 2.0
    return [x0 + pitch * (i + 0.5) for i in range(n)]


# =====================================================================
# Above-bus columns
# =====================================================================

def _above_columns(design, spec):
    supply = None
    if spec.get("supply") and not spec.is_mv:
        supply = _Column("supply", lambda c, x: _supply_column(c, spec, x))

    cols = []
    if spec.get("pv"):
        cols.append(_Column("pv", lambda c, x: _pv_column(c, spec, x)))
    if spec.get("wind"):
        cols.append(_Column("wind", lambda c, x: _wind_column(c, spec, x)))
    if spec.get("battery"):
        cols.append(_Column("battery", lambda c, x: _battery_column(c, spec, x)))
    if spec.get("genset") and not spec.has_sub_db:
        cols.append(_Column("genset", lambda c, x: _genset_column(c, spec, x)))

    # Anything else the design carries — hydro, biomass, fuel cells, CSP,
    # flywheels — draws from the same record the schedules read, so the
    # drawing cannot silently omit an asset the bill of materials lists.
    for key, rec in (design.get("extra_assets") or {}).items():
        if rec.get("class") == "flexible_load":
            continue
        cols.append(_Column(
            key, lambda c, x, r=rec, k=key: _generic_column(c, spec, x, k, r)
        ))
    return supply, cols


def _supply_column(c, spec, x):
    """
    The point of utility control: everything between the network and the
    customer's board, in the order the network operator inspects it.
    """
    y = c.y
    s = spec.supply
    out = [c.source(
        lambda px, py: sym.grid(px, py), x,
        label=f"UTILITY SUPPLY {s['kva']:,.0f} kVA",
        sublabel=f"{spec.system_voltage_v:,.0f} V, {spec.phases}-phase, "
                 f"{spec.frequency_hz} Hz",
    )]
    c.use("breaker", "meter")

    puc_a = s.get("puc_rating_a") or s["main_switch_a"]
    out.append(c.run(x, y.SRC + DEV_H, y.DC - DEV_H, ticks=True))
    out.append(c.device(
        lambda px, py: sym.fuse(px, py), x, y.DC,
        ratings=["POINT OF UTILITY CONTROL",
                 f"Service fuse {puc_a:,.0f} A",
                 f"{_poles(spec.phases)}, {spec.fault_level_ka:,.0f} kA"],
        legend="fuse_dc",
    ))

    out.append(c.run(x, y.DC + DEV_H, y.CONV - 12))
    out.append(c.device(
        lambda px, py: sym.meter(px, py, letter="kWh"), x, y.CONV,
        ratings=["REVENUE METER",
                 "Import and export registers"],
        legend="meter",
    ))

    yy = y.CONV + 12
    if s.get("ct_ratio"):
        mid = (yy + y.PROT - DEV_H) / 2
        out.append(c.run(x, yy, y.PROT - DEV_H))
        out.append(sym.current_transformer(x, mid, scale=0.9))
        out.append(sym.rating_text(
            x + 26, mid - 10,
            [f"CT {s['ct_ratio']}", "Export limit relay",
             f"Limit {s.get('export_limit_kw', 0):,.0f} kW"],
        ))
        c.use("ct")
    else:
        out.append(c.run(x, yy, y.PROT - DEV_H, ticks=True))

    out.append(c.device(
        lambda px, py: sym.breaker(px, py), x, y.PROT,
        ratings=[f"MAIN SWITCH {s['main_switch_a']:,.0f} A",
                 f"{_poles(spec.phases)} isolator",
                 _cable_note(s.get("cable"))],
        legend="breaker",
    ))
    return "".join(out)


def _pv_column(c, spec, x):
    """PV: array, the DC combiner with its isolation and fusing, inverter."""
    y = c.y
    p = spec.pv
    strings, mods = p.get("strings"), p.get("modules_per_string")
    sub = None
    if strings and mods:
        sub = f"{strings:,.0f} strings x {mods:,.0f} modules"
        if p.get("module_w"):
            sub += f" x {p['module_w']:,.0f} Wp"

    arr = []
    if p.get("voc_cold_v"):
        arr.append(f"Voc cold {p['voc_cold_v']:,.0f} V")
    if p.get("vmp_hot_v"):
        arr.append(f"Vmp hot {p['vmp_hot_v']:,.0f} V")
    if p.get("isc_a"):
        arr.append(f"Isc {p['isc_a']:,.1f} A/string")
    if p.get("tilt_deg") is not None:
        arr.append(f"Tilt {p['tilt_deg']:,.0f}/Az {p.get('azimuth_deg', 0):,.0f}")

    out = [c.source(
        lambda px, py: sym.pv_array(px, py), x,
        label=f"PV ARRAY {p['capacity_kwp']:,.1f} kWp",
        sublabel=sub, ratings=arr,
    )]
    c.use("pv_module", "isolator_dc", "inverter", "combiner")

    # ---- DC combiner enclosure
    box_top, box_h = y.DC - 40, 96
    box_x0, box_x1 = x - 58, x + 34
    out.append(sym.combiner_box(box_x0, box_top, 92, box_h))
    caption = _enclosure_caption(
        x, box_top, "DC COMBINER BOX",
        f"{p.get('combiner_boxes', 1)} enclosure(s), IP65 minimum",
    )

    out.append(c.run(x, y.SRC + DEV_H, box_top, dc=True))
    fuse = p["dc_fuse"]
    if fuse.get("required") and fuse.get("rating_a"):
        out.append(c.run(x, box_top, y.DC - 26, dc=True))
        out.append(c.device(
            lambda px, py: sym.fuse(px, py), x, y.DC - 14, dc=True,
            ratings=[f"STRING FUSE {fuse['rating_a']:,.0f} A",
                     "gPV, IEC 60269-6"],
            legend="fuse_dc", rx=box_x1 + 14,
        ))
        out.append(c.run(x, y.DC - 2, y.DC + 16, dc=True))
    else:
        out.append(c.run(x, box_top, y.DC + 16, dc=True))
    iso = p["dc_isolator"]
    out.append(c.device(
        lambda px, py: sym.isolator(px, py, dc=True), x, y.DC + 30, dc=True,
        ratings=[f"DC ISOLATOR {iso['current_a']:,.0f} A",
                 f"{iso['voltage_class_v']:,.0f} V DC, {iso['poles']}-pole"],
        legend="isolator_dc", rx=box_x1 + 14,
    ))

    # DC surge protection, tapped off the string pair inside the enclosure
    spd = p.get("dc_spd") or {}
    if spd:
        sx = x - 38
        out.append(c.hrun(sx, x - c.half_width(dc=True), y.DC + 30, dc=True))
        out.append(f'<g transform="translate({sx},{y.DC + 30}) scale(0.72) '
                   f'translate({-sx},{-(y.DC + 30)})">'
                   f'{sym.spd(sx, y.DC + 30)}</g>')
        out.append(f'<line x1="{sx}" y1="{y.DC + 44}" x2="{sx}" '
                   f'y2="{y.DC + 54}" class="dg-wire"/>')
        out.append(f'<g transform="translate({sx},{y.DC + 62}) scale(0.7) '
                   f'translate({-sx},{-(y.DC + 62)})">'
                   f'{sym.earth(sx, y.DC + 62)}</g>')
        out.append(sym.rating_text(
            box_x0 - 6, y.DC + 22,
            [f"SPD {spd.get('class', 'Type 2 DC')}",
             f"Uc {spd['uc_v']:,.0f} V" if spd.get("uc_v") else None],
            anchor="end",
        ))
        c.use("spd_dc", "earth")

    # ---- inverter
    out.append(c.run(x, box_top + box_h, y.CONV - DEV_H, dc=True, ticks=True))
    inv = [f"{p['inverter_units']:,.0f} x {p['inverter_kw_each']:,.1f} kW AC",
           f"Total {p['inverter_kw_total']:,.1f} kW"]
    if p.get("dc_ac_ratio"):
        inv.append(f"DC/AC {p['dc_ac_ratio']:.2f}")
    if p.get("mppt_inputs"):
        inv.append(f"{p['mppt_inputs']:,.0f} MPPT inputs")
    inv.append(_cable_note(p.get("dc_cable"), "DC"))
    out.append(c.device(
        lambda px, py: sym.inverter(px, py), x, y.CONV,
        ratings=inv, legend="inverter", caption="PV INVERTER",
    ))

    out.append(c.run(x, y.CONV + DEV_H + 16, y.PROT - DEV_H, ticks=True))
    out.append(c.device(
        lambda px, py: sym.breaker(px, py), x, y.PROT,
        ratings=_prot_ratings(p.get("ac_protection"), p.get("ac_cable"),
                              p.get("inverter_ac_current_a"), spec),
        legend="breaker",
    ))
    out.append(caption)
    return "".join(out)


def _wind_column(c, spec, x):
    y = c.y
    w = spec.wind
    sub = f"{w['units']:,.0f} x {w['unit_kw']:,.0f} kW"
    if w.get("hub_height_m"):
        sub += f", hub {w['hub_height_m']:,.0f} m"
    out = [c.source(
        lambda px, py: sym.wind_turbine(px, py), x,
        label=f"WTG {w['capacity_kw']:,.0f} kW", sublabel=sub,
    )]
    c.use("wind", "inverter", "breaker")
    out.append(c.run(x, y.SRC + DEV_H, y.CONV - DEV_H, ticks=True))
    out.append(c.device(
        lambda px, py: sym.inverter(px, py), x, y.CONV,
        ratings=[f"CONVERTER {w['converter_kw']:,.0f} kW",
                 "Full-power back-to-back",
                 _cable_note(w.get("cable"))],
        legend="inverter", caption="WTG CONVERTER",
    ))
    out.append(c.run(x, y.CONV + DEV_H + 16, y.PROT - DEV_H, ticks=True))
    out.append(c.device(
        lambda px, py: sym.breaker(px, py), x, y.PROT,
        ratings=_prot_ratings(w.get("protection"), w.get("cable"), None, spec),
        legend="breaker",
    ))
    return "".join(out)


def _battery_column(c, spec, x):
    """
    Battery: bank, DC protection at the terminals, then the converter.

    The DC fuse and isolator are drawn at the battery, not at the converter,
    because that is where they have to be: a bank can deliver thousands of
    amps into a short and the protection has to sit between the cells and
    the fault.
    """
    y = c.y
    b = spec.battery
    extra = []
    if b.get("soc_window"):
        lo, hi = b["soc_window"]
        extra.append(f"SOC {lo * 100:,.0f}-{hi * 100:,.0f}%")
    if b.get("cycles_per_year"):
        extra.append(f"{b['cycles_per_year']:,.0f} cycles/yr")
    if b.get("expected_life_years"):
        extra.append(f"Life {b['expected_life_years']:,.1f} yr")

    sub = f"{b['power_kw']:,.0f} kW"
    if b.get("chemistry"):
        sub += f", {b['chemistry']}"
    out = [c.source(
        lambda px, py: sym.battery(px, py), x,
        label=f"BESS {b['capacity_kwh']:,.0f} kWh", sublabel=sub,
        ratings=extra,
    )]
    c.use("battery", "fuse_dc", "isolator_dc", "inverter", "breaker",
          "combiner")

    box_top, box_h = y.DC - 40, 96
    box_x0, box_x1 = x - 46, x + 46
    out.append(sym.combiner_box(box_x0, box_top, 92, box_h))
    caption = _enclosure_caption(
        x, box_top, "BATTERY DC ISOLATION",
        f"{b['dc_bus_v']:,.0f} V DC bus",
    )
    out.append(c.run(x, y.SRC + DEV_H, box_top, dc=True))
    out.append(c.run(x, box_top, y.DC - 26, dc=True))
    out.append(c.device(
        lambda px, py: sym.fuse(px, py), x, y.DC - 14, dc=True,
        ratings=[f"DC FUSE {b['dc_fuse_a']:,.0f} A",
                 f"{b['dc_bus_v']:,.0f} V DC"],
        legend="fuse_dc", rx=box_x1 + 14,
    ))
    out.append(c.run(x, y.DC - 2, y.DC + 16, dc=True))
    out.append(c.device(
        lambda px, py: sym.isolator(px, py, dc=True), x, y.DC + 30, dc=True,
        ratings=[f"DC ISOLATOR {b['dc_isolator_a']:,.0f} A",
                 f"In {b['dc_current_a']:,.0f} A nominal"],
        legend="isolator_dc", rx=box_x1 + 14,
    ))
    out.append(c.run(x, box_top + box_h, y.CONV - DEV_H, dc=True, ticks=True))
    out.append(c.device(
        lambda px, py: sym.inverter(px, py, bidirectional=True), x, y.CONV,
        ratings=[f"PCS {b['pcs_kw']:,.0f} kW", b["pcs_quadrants"],
                 _cable_note(b.get("cable"))],
        legend="inverter", caption="BATTERY CONVERTER",
    ))
    out.append(c.run(x, y.CONV + DEV_H + 16, y.PROT - DEV_H, ticks=True))
    out.append(c.device(
        lambda px, py: sym.breaker(px, py), x, y.PROT,
        ratings=_prot_ratings(b.get("protection"), b.get("cable"), None, spec),
        legend="breaker",
    ))
    out.append(caption)
    return "".join(out)


def _genset_column(c, spec, x):
    y = c.y
    g = spec.genset
    extra = [f"Full load {g['current_a']:,.0f} A"]
    if g.get("run_hours"):
        extra.append(f"{g['run_hours']:,.0f} h/yr expected")
    out = [c.source(
        lambda px, py: sym.generator(px, py), x,
        label=f"GENSET {g['capacity_kw']:,.0f} kW",
        sublabel=f"{g['units']:,.0f} x {g['unit_kw']:,.0f} kW, "
                 f"{g['kva']:,.0f} kVA at 0.8 pf",
        ratings=extra,
    )]
    c.use("genset", "breaker", "earth", "isolator_dc")

    # The set's own neutral earth: the earthing arrangement changes when the
    # set is the source, so the drawing has to show where it is made.
    out.append(c.hrun(x - 48, x - 17, y.SRC))
    out.append(f'<line x1="{x - 48}" y1="{y.SRC}" x2="{x - 48}" '
               f'y2="{y.SRC + 14}" class="dg-wire"/>')
    out.append(f'<g transform="translate({x - 48},{y.SRC + 26}) scale(0.7) '
               f'translate({-(x - 48)},{-(y.SRC + 26)})">'
               f'{sym.earth(x - 48, y.SRC + 26)}</g>')

    out.append(c.run(x, y.SRC + DEV_H, y.CONV - DEV_H, ticks=True))
    out.append(c.device(
        lambda px, py: sym.isolator(px, py), x, y.CONV,
        ratings=["GENERATOR ISOLATOR",
                 f"{tp._standard_switch(g['current_a']):,.0f} A, "
                 f"{_poles(spec.phases)}"],
        legend="isolator_dc", caption="LOCKABLE ISOLATOR",
    ))
    out.append(c.run(x, y.CONV + DEV_H + 16, y.PROT - DEV_H, ticks=True))
    out.append(c.device(
        lambda px, py: sym.breaker(px, py), x, y.PROT,
        ratings=_prot_ratings(g.get("protection"), g.get("cable"), None, spec),
        legend="breaker",
    ))
    return "".join(out)


def _generic_column(c, spec, x, key, rec):
    """Any other technology the design carries."""
    y = c.y
    tech = rec.get("technology", key)
    fn = sym.for_technology(tech)
    out = [c.source(
        lambda px, py: fn(px, py), x,
        label=rec.get("label") or key.replace("_", " ").upper(),
        sublabel=rec.get("sublabel"),
    )]
    c.use({"fuel_cell": "fuel_cell", "hydrogen": "hydrogen",
           "flywheel": "flywheel", "biomass": "biomass",
           "geothermal": "geothermal", "csp": "csp",
           "run_of_river_hydro": "hydro", "reservoir_hydro": "hydro",
           }.get(tech), "breaker")

    dc = bool(rec.get("dc"))
    conv = rec.get("converter") or {}
    yy = y.SRC + DEV_H
    if conv:
        out.append(c.run(x, yy, y.CONV - DEV_H, dc=dc, ticks=True))
        out.append(c.device(
            lambda px, py: sym.inverter(
                px, py, bidirectional=conv.get("bidirectional", False)
            ), x, y.CONV,
            ratings=[conv.get("label", "CONVERTER"), conv.get("sub")],
            legend="inverter",
        ))
        yy = y.CONV + DEV_H + 16
    out.append(c.run(x, yy, y.PROT - DEV_H, ticks=True))
    out.append(c.device(
        lambda px, py: sym.breaker(px, py), x, y.PROT,
        ratings=_prot_ratings(rec.get("protection"), rec.get("cable"),
                              rec.get("rated_kw"), spec),
        legend="breaker",
    ))
    return "".join(out)


# =====================================================================
# The distribution board
# =====================================================================

def _distribution_board(c, spec, x0, x1):
    y = c.y
    out = [
        f'<rect x="{x0:.1f}" y="{y.DB_TOP}" width="{x1 - x0:.1f}" '
        f'height="{y.DB_BOTTOM - y.DB_TOP}" class="dg-encl-box"/>',
        sym.vertical_enclosure_label(
            x0 - 13, y.DB_TOP, y.DB_BOTTOM - y.DB_TOP,
            "MAIN AC DISTRIBUTION BOARD",
        ),
        f'<line x1="{x0 + 16:.1f}" y1="{y.BUS}" x2="{x1 - 16:.1f}" '
        f'y2="{y.BUS}" class="dg-bus"/>',
        f'<text x="{x1 - 18:.1f}" y="{y.BUS - 26}" class="dg-label" '
        f'text-anchor="end">LV BUSBAR {spec.system_voltage_v:,.0f} V, '
        f'{spec.phases}-phase, {spec.frequency_hz} Hz</text>',
        f'<text x="{x1 - 18:.1f}" y="{y.BUS - 13}" class="dg-sublabel" '
        f'text-anchor="end">{spec.load["ways"]:,.0f} way, '
        f'{_poles(spec.phases)}, {spec.fault_level_ka:,.0f} kA Icu</text>',
    ]

    # Surge protection and the board residual current device belong to the
    # board rather than to any one feeder, so they hang off the bus itself.
    spd = (spec.protection_scheme or {}).get("spd_ac") or {}
    if spd:
        sx = x1 - 210
        out.append(f'<line x1="{sx}" y1="{y.BUS}" x2="{sx}" '
                   f'y2="{y.BUS + 16}" class="dg-wire"/>')
        out.append(sym.spd(sx, y.BUS + 32))
        out.append(_stack(
            sx, y.BUS + 62,
            [f"SPD {spd.get('class', 'Type 2')}",
             f"Uc {spd['uc_v']:,.0f} V" if spd.get("uc_v") else None,
             spd.get("discharge_current")],
        ))
        c.use("spd_ac")

    rcd = (spec.protection_scheme or {}).get("rcd") or {}
    if rcd.get("rating_ma"):
        rx = x1 - 120
        out.append(f'<line x1="{rx}" y1="{y.BUS}" x2="{rx}" '
                   f'y2="{y.BUS + 16}" class="dg-wire"/>')
        out.append(sym.earth_leakage(rx, y.BUS + 34, scale=0.78))
        out.append(_stack(
            rx, y.BUS + 62,
            [f"EL {rcd['rating_ma']:,.0f} mA type {rcd.get('type', 'A')}",
             "Residual current"],
        ))
        c.use("earth_leakage")
    return "".join(out)


# =====================================================================
# Below-bus columns
# =====================================================================

def _below_columns(design, spec):
    cols = []
    if spec.has_sub_db:
        cols.append(_Column("sub", lambda c, x: _sub_board(c, spec, x)))
    if not spec.has_sub_db or spec.split_loads:
        cols.append(_Column("load", lambda c, x: _load_feeder(c, spec, x)))
    if spec.get("ev"):
        cols.append(_Column("ev", lambda c, x: _ev_feeder(c, spec, x)))
    for key, rec in (design.get("extra_assets") or {}).items():
        if rec.get("class") == "flexible_load":
            cols.append(_Column(
                key, lambda c, x, r=rec, k=key: _generic_load(c, spec, x, k, r)
            ))
    return cols


def _sub_board(c, spec, x):
    """
    The backed-up board, reached through a change-over.

    Two sources can feed it and they must never be paralleled, which is why
    the change-over and the no-inter-connection mark are both drawn: the
    mark is what a network operator looks for to satisfy itself that the
    standby source cannot back-feed the network.
    """
    y = c.y
    out = [c.run(x, y.BUS, y.OUT - DEV_H, ticks=True)]
    c.use("changeover", "breaker", "load")
    out.append(c.device(
        lambda px, py: sym.breaker(px, py), x, y.OUT,
        ratings=["ESSENTIAL SUPPLY", _poles(spec.phases)],
        legend="breaker",
    ))
    out.append(c.run(x, y.OUT + DEV_H, y.SUB - 44))
    out.append(c.hrun(x, x + 16, y.SUB - 44))
    out.append(c.run(x + 16, y.SUB - 44, y.SUB - 20))
    out.append(sym.changeover(x, y.SUB))
    out.append(sym.rating_text(
        x + 34, y.SUB + 2,
        ["CHANGE-OVER SWITCH", f"{_poles(spec.phases)}, break before make",
         "Mechanically interlocked"],
    ))

    # The alternative source. It is drawn as a labelled stub rather than a
    # routed line because the two are on opposite sides of the sheet, and
    # the no-inter-connection mark is what the network operator looks for
    # to satisfy itself the standby source cannot back-feed the network.
    c.use("no_interconnect")
    if spec.get("genset"):
        out.append(_genset_branch(c, spec, x - 170, x))
    else:
        out.append(c.hrun(x - 108, x - 16, y.SUB - 20))
        out.append(sym.no_interconnection(x - 68, y.SUB - 20))
        out.append(sym.rating_text(
            x - 112, y.SUB - 30,
            ["From inverter back-up output", "NO INTER-CONNECTION"],
            anchor="end",
        ))

    backed = (spec.load["essential_kw"] if spec.split_loads
              else spec.load["peak_kw"])
    name = "ESSENTIAL LOADS" if spec.split_loads else "BACKED-UP LOADS"
    out.append(c.run(x, y.SUB + 20, y.SUB_BOTTOM + 18))
    out.append(sym.combiner_box(x - 98, y.SUB_BOTTOM + 18, 196, 46))
    out.append(_mask_text(x, y.SUB_BOTTOM + 40, "SUB DISTRIBUTION BOARD",
                          "dg-encl"))
    out.append(_mask_text(x, y.SUB_BOTTOM + 55,
                          f"{name.capitalize()} {backed:,.1f} kW",
                          "dg-sublabel"))
    out.append(c.run(x, y.SUB_BOTTOM + 64, y.LOAD - DEV_H, ticks=True))
    out.append(sym.load(x, y.LOAD, name, f"{backed:,.1f} kW"))
    return "".join(out)


def _genset_branch(c, spec, gx, sub_x):
    """
    The standby set, feeding the change-over and nothing else.

    It is deliberately not connected to the busbar. A set that can be
    paralleled with the utility is a different scheme with different
    protection requirements, and drawing it that way by accident is the
    error the no-inter-connection mark exists to rule out.
    """
    y = c.y
    g = spec.genset
    c.use("genset", "breaker", "earth")
    out = [
        sym.generator(gx, y.SUB + 96),
        _mask_text(gx, y.SUB + 60, "STANDBY GENSET", "dg-label"),
        _mask_text(gx, y.SUB + 73,
                   f'{g["units"]:,.0f} x {g["unit_kw"]:,.0f} kW, '
                   f'{g["kva"]:,.0f} kVA', "dg-sublabel"),
    ]
    # the set's own neutral earth
    out.append(c.hrun(gx - 46, gx - 17, y.SUB + 96))
    out.append(f'<line x1="{gx - 46}" y1="{y.SUB + 96}" x2="{gx - 46}" '
               f'y2="{y.SUB + 110}" class="dg-wire"/>')
    out.append(f'<g transform="translate({gx - 46},{y.SUB + 122}) scale(0.7) '
               f'translate({-(gx - 46)},{-(y.SUB + 122)})">'
               f'{sym.earth(gx - 46, y.SUB + 122)}</g>')

    out.append(c.run(gx, y.SUB + 18 + DEV_H, y.SUB + 96 - DEV_H, ticks=True))
    out.append(c.device(
        lambda px, py: sym.breaker(px, py), gx, y.SUB + 18,
        ratings=_prot_ratings(g.get("protection"), g.get("cable"),
                              None, spec),
        legend="breaker", side="left",
    ))
    out.append(c.run(gx, y.SUB - 20, y.SUB + 18 - DEV_H))
    out.append(c.hrun(gx, sub_x - 16, y.SUB - 20))
    out.append(sym.no_interconnection((gx + sub_x) / 2, y.SUB - 20))
    out.append(_mask_text((gx + sub_x) / 2, y.SUB - 30,
                          "NO INTER-CONNECTION", "dg-rating"))
    return "".join(out)


def _load_feeder(c, spec, x):
    y = c.y
    ld = spec.load
    kw = ld["non_essential_kw"] if spec.split_loads else ld["peak_kw"]
    name = "NON-ESSENTIAL LOADS" if spec.split_loads else "SITE LOAD"
    out = [c.run(x, y.BUS, y.OUT - DEV_H, ticks=True)]
    out.append(c.device(
        lambda px, py: sym.breaker(px, py), x, y.OUT,
        ratings=_prot_ratings(ld.get("protection"), ld.get("cable"),
                              None, spec),
        legend="breaker",
    ))
    out.append(c.run(x, y.OUT + DEV_H, y.LOAD - DEV_H, ticks=True))
    out.append(sym.load(
        x, y.LOAD, f"{name} {kw:,.1f} kW",
        f"{ld['annual_mwh']:,.0f} MWh/yr" if ld.get("annual_mwh") else None,
    ))
    c.use("load")
    return "".join(out)


def _ev_feeder(c, spec, x):
    y = c.y
    ev = spec.ev
    out = [c.run(x, y.BUS, y.OUT - DEV_H, ticks=True)]
    out.append(c.device(
        lambda px, py: sym.breaker(px, py), x, y.OUT,
        ratings=_prot_ratings(ev.get("protection"), ev.get("cable"),
                              None, spec),
        legend="breaker",
    ))
    rcd = ev.get("rcd") or {}
    yy = y.OUT + DEV_H
    if rcd.get("rating_ma"):
        out.append(c.run(x, yy, y.SUB - DEV_H))
        out.append(c.device(
            lambda px, py: sym.earth_leakage(px, py), x, y.SUB,
            ratings=[f"EL {rcd['rating_ma']:,.0f} mA",
                     f"Type {rcd.get('type', 'B')}",
                     "DC fault current detection"],
            legend="earth_leakage",
        ))
        yy = y.SUB + DEV_H
    out.append(c.run(x, yy, y.LOAD - DEV_H, ticks=True))
    out.append(sym.ev_charger(
        x, y.LOAD,
        f"EVSE {ev['chargers']:,.0f} x {ev['charger_kw']:,.0f} kW",
        f"{ev['design_kw']:,.0f} kW at {ev['diversity']:.2f} diversity"
        + (", V2G" if ev.get("v2g") else ""),
    ))
    c.use("ev")
    return "".join(out)


def _generic_load(c, spec, x, key, rec):
    y = c.y
    out = [c.run(x, y.BUS, y.OUT - DEV_H, ticks=True)]
    out.append(c.device(
        lambda px, py: sym.breaker(px, py), x, y.OUT,
        ratings=_prot_ratings(rec.get("protection"), rec.get("cable"),
                              None, spec),
        legend="breaker",
    ))
    out.append(c.run(x, y.OUT + DEV_H, y.LOAD - DEV_H, ticks=True))
    fn = sym.for_technology(rec.get("technology", key))
    out.append(fn(x, y.LOAD, rec.get("label") or key.upper(),
                  rec.get("sublabel")))
    return "".join(out)


# =====================================================================
# Medium voltage
# =====================================================================

def _mv_chain(c, spec, x, bus_x0, bus_x1):
    """
    LV to MV step-up, the MV bus, and the connection to the network.

    Drawn below the LV board because that is the direction power flows in
    an exporting plant, and because it keeps the network operator's
    equipment together at one end of the sheet.
    """
    y = c.y
    mv = spec.mv
    out = [c.run(x, y.BUS, y.OUT - DEV_H, ticks=True)]
    c.use("transformer", "mv_bus", "breaker", "earth")

    out.append(c.device(
        lambda px, py: sym.breaker(px, py), x, y.OUT,
        ratings=[f"LV CB {mv['lv_current_a']:,.0f} A", _poles(spec.phases)],
        legend="breaker",
    ))
    out.append(c.run(x, y.OUT + DEV_H, y.TX - 20, ticks=True))
    out.append(sym.transformer(x, y.TX))
    out.append(sym.rating_text(
        x + 26, y.TX - 22,
        [f"{mv['transformers']:,.0f} x {mv['transformer_kva']:,.0f} kVA",
         f"{mv['lv_voltage_v']:,.0f} V / {mv['mv_voltage_v'] / 1000:,.0f} kV",
         f"{mv['vector_group']}, {mv['impedance_pct']:.1f}% Uk",
         f"LV {mv['lv_current_a']:,.0f} A / MV {mv['mv_current_a']:,.0f} A"],
    ))
    out.append(_mask_text(x, y.TX + 34, "STEP-UP TRANSFORMER", "dg-label"))
    # transformer star point earth
    out.append(c.hrun(x - 46, x - 12, y.TX + 7))
    out.append(f'<line x1="{x - 46}" y1="{y.TX + 7}" x2="{x - 46}" '
               f'y2="{y.TX + 20}" class="dg-wire"/>')
    out.append(f'<g transform="translate({x - 46},{y.TX + 32}) scale(0.7) '
               f'translate({-(x - 46)},{-(y.TX + 32)})">'
               f'{sym.earth(x - 46, y.TX + 32)}</g>')

    out.append(c.run(x, y.TX + 40, y.MVBUS, ticks=True))
    out.append(c.tap(x, y.MVBUS))

    mv_x0, mv_x1 = x - 160, x + 160
    out.append(
        f'<line x1="{mv_x0:.1f}" y1="{y.MVBUS}" x2="{mv_x1:.1f}" '
        f'y2="{y.MVBUS}" class="dg-bus"/>'
        + _mask_text(mv_x0, y.MVBUS - 11,
                     f'MV BUSBAR - {mv["mv_switchgear"]}',
                     "dg-label", anchor="start")
    )

    # ring main units, where the site is an existing MV customer
    if mv.get("ring_main_units"):
        rx = mv_x0 + 30
        out.append(c.tap(rx, y.MVBUS))
        out.append(f'<line x1="{rx}" y1="{y.MVBUS}" x2="{rx}" '
                   f'y2="{y.MVBUS + 20}" class="dg-wire"/>')
        out.append(
            f'<rect x="{rx - 32}" y="{y.MVBUS + 20}" width="64" height="34" '
            f'class="dg-encl-box"/>'
            f'<text x="{rx}" y="{y.MVBUS + 41}" class="dg-tiny-b" '
            f'text-anchor="middle">RMU x {mv["ring_main_units"]:,.0f}</text>'
            f'<text x="{rx}" y="{y.MVBUS + 68}" class="dg-sublabel" '
            f'text-anchor="middle">Ring main units</text>'
        )
        c.use("rmu")

    out.append(c.run(x, y.MVBUS, y.MVSW - DEV_H, ticks=True))
    out.append(c.device(
        lambda px, py: sym.breaker(px, py), x, y.MVSW,
        ratings=["MV CIRCUIT BREAKER", mv["mv_switchgear"],
                 "Settings by the network operator"],
        legend="breaker",
    ))
    out.append(c.run(x, y.MVSW + DEV_H, y.UTIL - DEV_H, ticks=True))

    s = spec.get("supply") or {}
    out.append(sym.grid(
        x, y.UTIL, "UTILITY NETWORK",
        f"{mv['mv_voltage_v'] / 1000:,.0f} kV, "
        f"{s.get('kva', 0):,.0f} kVA connection",
    ))
    if s.get("ct_ratio"):
        cy = (y.MVSW + y.UTIL) / 2
        out.append(sym.current_transformer(x, cy, scale=0.85))
        out.append(sym.rating_text(
            x + 26, cy - 15,
            [f"CT {s['ct_ratio']}", "Revenue metering",
             f"Export limit {s.get('export_limit_kw', 0):,.0f} kW"],
        ))
        c.use("ct")
    return "".join(out)


# =====================================================================
# Annotations
# =====================================================================

def _system_stamp(spec, x, y):
    """The parameters every rating on the sheet was derived from."""
    rows = [
        f"System: {spec.system_voltage_v:,.0f} V, {spec.phases}-phase, "
        f"{spec.frequency_hz} Hz, pf {spec.power_factor:.2f}",
        f"Earthing: {spec.earthing_system}    "
        f"Prospective fault level: {spec.fault_level_ka:,.0f} kA",
        # The drawing class used to be a third line here. On a sheet whose
        # first column heading sits at the same height, it ran straight
        # through it. It is in the title block and in the notes, where
        # there is room for the whole sentence.
    ]
    return "".join(
        f'<text x="{x}" y="{y + i * 12}" class="dg-sub">{sym._esc(r)}</text>'
        for i, r in enumerate(rows)
    )


def _phase_key(c, x, y):
    lbl = "L1  L2  L3  N (dashed)" if c.phases == 3 else "L  N (dashed)"
    return (
        f'<text x="{x}" y="{y}" class="dg-tiny-b">Three-line view</text>'
        f'<text x="{x}" y="{y + 12}" class="dg-tiny">{lbl}</text>'
        f'<text x="{x}" y="{y + 24}" class="dg-tiny">'
        f'Dashed link = ganged poles</text>'
    )


def _notes(design, spec):
    notes = []
    if spec.meta.get("scope"):
        notes.append(
            f"Drawing class: {spec.meta['label']} - applicable to "
            f"{spec.meta['scope']}."
        )
    notes.append(
        "Installation, earthing and bonding shall comply with the national "
        "wiring rules and IEC 60364 (-4-41, -4-43, -5-52, -5-54, -7-712), "
        "IEC 62548 and IEC 62109."
    )
    notes.append(
        "Ratings shown are calculated design values. Verify against "
        "manufacturers' data and the network operator's requirements before "
        "procurement."
    )
    notes += list(design.get("diagram_notes") or [])
    notes += list(spec.get("warnings") or [])
    return notes


def _stack(x, y, lines):
    """Short centred caption lines beneath a symbol."""
    out = []
    for i, line in enumerate([ln for ln in lines if ln]):
        out.append(
            f'<text x="{x}" y="{y + i * 10}" class="dg-tiny" '
            f'text-anchor="middle">{sym._esc(line)}</text>'
        )
    return "".join(out)


def _enclosure_caption(x, top, title, sub=None):
    """
    Two-line caption above an enclosure, centred on its column.

    One mask covers both lines: two separate ones leave a sliver of the
    conductor showing between them, which reads as a printing artefact.
    """
    w = max(len(str(title)) * _CHAR_W["dg-encl"],
            len(str(sub or "")) * _CHAR_W["dg-sublabel"]) + 12
    h = 27 if sub else 15
    return (
        f'<rect x="{x - w / 2:.1f}" y="{top - 32:.1f}" width="{w:.1f}" '
        f'height="{h}" fill="var(--dg-bg)"/>'
        f'<text x="{x}" y="{top - 20}" class="dg-encl" '
        f'text-anchor="middle">{sym._esc(title)}</text>'
        + (f'<text x="{x}" y="{top - 8}" class="dg-sublabel" '
           f'text-anchor="middle">{sym._esc(sub)}</text>' if sub else "")
    )


def _poles(phases):
    return "4P (3P+N)" if phases == 3 else "2P (P+N)"


def _prot_ratings(prot, cable, current_a, spec):
    """The three things a protective device on a drawing has to state."""
    out = []
    label = _prot_label(prot)
    if label:
        out.append(label)
        out.append(f"{_poles(spec.phases)}, {spec.fault_level_ka:,.0f} kA Icu")
    elif current_a:
        out.append(f"{current_a:,.0f} A")
    out.append(_cable_note(cable))
    return [r for r in out if r]


_MATERIAL = {"copper": "Cu", "aluminium": "Al", "aluminum": "Al",
             "cu": "Cu", "al": "Al"}


def _cable_note(cab, prefix=""):
    if not cab or not cab.get("csa_mm2"):
        return None
    runs = cab.get("parallel_runs", 1) or 1
    txt = f"{runs} x " if runs > 1 else ""
    txt += f"{cab['csa_mm2']:,.0f} mm2 "
    txt += _MATERIAL.get(str(cab.get("material", "")).lower(), "Cu")
    ins = str(cab.get("insulation", "")).upper()
    if ins:
        txt += f"/{ins}"
    if cab.get("length_m"):
        txt += f", {cab['length_m']:,.0f} m"
    return f"{prefix} {txt}" if prefix else txt


def _prot_label(prot):
    if not prot:
        return None
    r = prot.get("rating_a")
    if r is None:
        return None
    name = {"mcb": "MCB", "mccb": "MCCB", "acb": "ACB",
            "fuse_gg": "Fuse gG", "fuse_gpv": "Fuse gPV"}.get(
        prot.get("device", "mcb"), str(prot.get("device", "")).upper())
    curve = prot.get("curve")
    return f"{name} {r:,.0f} A" + (f" curve {curve}" if curve else "")


# ---------------------------------------------------------------------
# Backwards compatibility
# ---------------------------------------------------------------------
# The earlier renderer exposed a feeder model. It is kept so a caller that
# built feeders itself still works, but the drawings no longer go through
# it: the topology specification carries far more than a feeder list can.

class Feeder:
    def __init__(self, kind, label, sublabel=None, protection=None,
                 converter=None, dc=False, isolator=True, extra_notes=None):
        self.kind = kind
        self.label = label
        self.sublabel = sublabel
        self.protection = protection
        self.converter = converter
        self.dc = dc
        self.isolator = isolator
        self.extra_notes = extra_notes or []


def build_feeders(design):
    """Ordered sources and loads, for callers that want the model only."""
    spec = tp.build(design)
    sources, loads = [], []
    if spec.get("supply"):
        sources.append(Feeder("grid", f"GRID {spec.supply['kva']:,.0f} kVA"))
    for key in ("pv", "wind", "battery", "genset"):
        rec = spec.get(key)
        if rec:
            cap = (rec.get("capacity_kwp") or rec.get("capacity_kw")
                   or rec.get("capacity_kwh") or 0)
            sources.append(Feeder(key, f"{key.upper()} {cap:,.0f}"))
    loads.append(Feeder("load", f"LOAD {spec.load['peak_kw']:,.0f} kW"))
    if spec.get("ev"):
        loads.append(Feeder("ev", f"EVSE {spec.ev['chargers']:,.0f}"))
    return sources, loads
