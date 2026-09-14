"""
IEC 60617 electrical symbols as SVG path fragments.

Each symbol is drawn in its own local coordinate system centred on (0, 0),
in a nominal 40 x 40 box, and placed by a transform. Keeping symbols
origin-centred is what makes the layout code able to place them without
knowing anything about their internal geometry.

Colours come from CSS custom properties so one stylesheet drives both the
light and dark rendering, and so a printed diagram can be forced to black
on white without touching this module.
"""

from __future__ import annotations

STROKE = 'stroke="var(--dg-line)" fill="none" stroke-width="1.6"'
FILL = 'fill="var(--dg-line)"'
THIN = 'stroke="var(--dg-line)" fill="none" stroke-width="1.1"'


def _g(body, x, y, scale=1.0, label=None, sublabel=None, title=None):
    """Wrap symbol geometry in a positioned group."""
    t = f'translate({x},{y})'
    if scale != 1.0:
        t += f' scale({scale})'
    parts = [f'<g transform="{t}">']
    if title:
        parts.append(f"<title>{_esc(title)}</title>")
    parts.append(body)
    parts.append("</g>")
    out = "".join(parts)
    if label:
        out += (
            f'<text x="{x}" y="{y + 34}" class="dg-label" '
            f'text-anchor="middle">{_esc(label)}</text>'
        )
    if sublabel:
        dy = 34 + (14 if label else 0)
        out += (
            f'<text x="{x}" y="{y + dy}" class="dg-sublabel" '
            f'text-anchor="middle">{_esc(sublabel)}</text>'
        )
    return out


def _esc(s):
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def pv_array(x, y, label=None, sublabel=None):
    """PV generator: a cell rectangle with the diagonal radiation arrows."""
    body = (
        f'<rect x="-18" y="-13" width="36" height="26" rx="2" {STROKE}/>'
        f'<line x1="-18" y1="13" x2="18" y2="-13" {STROKE}/>'
        f'<line x1="-11" y1="-20" x2="-5" y2="-15" {THIN}/>'
        f'<line x1="-3" y1="-20" x2="3" y2="-15" {THIN}/>'
        f'<polygon points="-5,-15 -8.5,-16 -7,-13" {FILL}/>'
        f'<polygon points="3,-15 -0.5,-16 1,-13" {FILL}/>'
    )
    return _g(body, x, y, label=label, sublabel=sublabel, title="PV array")


def wind_turbine(x, y, label=None, sublabel=None):
    """Wind generator: tower with three blades."""
    body = (
        f'<line x1="0" y1="20" x2="0" y2="-2" {STROKE}/>'
        f'<circle cx="0" cy="-4" r="2.5" {FILL}/>'
        f'<line x1="0" y1="-4" x2="0" y2="-20" {STROKE}/>'
        f'<line x1="0" y1="-4" x2="14" y2="5" {STROKE}/>'
        f'<line x1="0" y1="-4" x2="-14" y2="5" {STROKE}/>'
    )
    return _g(body, x, y, label=label, sublabel=sublabel, title="Wind turbine")


def battery(x, y, label=None, sublabel=None):
    """Battery: alternating long and short plates."""
    body = (
        f'<line x1="-14" y1="-14" x2="-14" y2="14" {STROKE}/>'
        f'<line x1="-6" y1="-8" x2="-6" y2="8" stroke="var(--dg-line)" stroke-width="3.2"/>'
        f'<line x1="2" y1="-14" x2="2" y2="14" {STROKE}/>'
        f'<line x1="10" y1="-8" x2="10" y2="8" stroke="var(--dg-line)" stroke-width="3.2"/>'
        f'<line x1="-20" y1="0" x2="-14" y2="0" {STROKE}/>'
        f'<line x1="10" y1="0" x2="20" y2="0" {STROKE}/>'
    )
    return _g(body, x, y, label=label, sublabel=sublabel, title="Battery bank")


def generator(x, y, label=None, sublabel=None, letter="G"):
    """Rotating machine: circle with a letter."""
    body = (
        f'<circle cx="0" cy="0" r="17" {STROKE}/>'
        f'<text x="0" y="6" class="dg-symbol" text-anchor="middle">{letter}</text>'
    )
    return _g(body, x, y, label=label, sublabel=sublabel, title="Generator")


def grid(x, y, label=None, sublabel=None):
    """Utility supply: pylon-style symbol in a circle."""
    body = (
        f'<circle cx="0" cy="0" r="17" {STROKE}/>'
        f'<path d="M -8 6 L 0 -8 L 8 6 M -5 1 L 5 1" {STROKE}/>'
    )
    return _g(body, x, y, label=label, sublabel=sublabel, title="Utility grid")


def inverter(x, y, label=None, sublabel=None, bidirectional=False):
    """
    Converter: a square split by a diagonal, DC side marked with a dashed
    line and AC side with a sine. Bidirectional units get arrows both ways.
    """
    body = (
        f'<rect x="-18" y="-18" width="36" height="36" rx="2" {STROKE}/>'
        f'<line x1="18" y1="-18" x2="-18" y2="18" {STROKE}/>'
        f'<line x1="-13" y1="-8" x2="-3" y2="-8" {THIN}/>'
        f'<line x1="-13" y1="-4" x2="-11" y2="-4" {THIN}/>'
        f'<line x1="-9" y1="-4" x2="-7" y2="-4" {THIN}/>'
        f'<line x1="-5" y1="-4" x2="-3" y2="-4" {THIN}/>'
        f'<path d="M 3 8 q 2.5 -6 5 0 q 2.5 6 5 0" {THIN}/>'
    )
    if bidirectional:
        body += (
            f'<polygon points="-22,-2 -18,0 -22,2" {FILL}/>'
            f'<polygon points="22,-2 18,0 22,2" {FILL}/>'
        )
    return _g(
        body, x, y, label=label, sublabel=sublabel,
        title="Bidirectional converter" if bidirectional else "Inverter",
    )


def transformer(x, y, label=None, sublabel=None):
    """Two-winding transformer: two overlapping circles."""
    body = (
        f'<circle cx="0" cy="-7" r="11" {STROKE}/>'
        f'<circle cx="0" cy="7" r="11" {STROKE}/>'
    )
    return _g(body, x, y, label=label, sublabel=sublabel, title="Transformer")


def breaker(x, y, label=None, closed=True, dc=False):
    """Circuit breaker: a switch with the square contact marker."""
    if closed:
        arm = f'<line x1="0" y1="-11" x2="0" y2="11" {STROKE}/>'
    else:
        arm = f'<line x1="0" y1="-11" x2="9" y2="9" {STROKE}/>'
    body = (
        f'<line x1="0" y1="-18" x2="0" y2="-11" {STROKE}/>'
        f'{arm}'
        f'<line x1="0" y1="11" x2="0" y2="18" {STROKE}/>'
        f'<rect x="-5" y="-5" width="10" height="10" {STROKE}/>'
    )
    if dc:
        body += f'<text x="11" y="4" class="dg-tiny">DC</text>'
    return _g(body, x, y, label=label, title="Circuit breaker")


def fuse(x, y, label=None):
    """Fuse: rectangle with a through line."""
    body = (
        f'<line x1="0" y1="-18" x2="0" y2="-9" {STROKE}/>'
        f'<rect x="-6" y="-9" width="12" height="18" {STROKE}/>'
        f'<line x1="0" y1="-9" x2="0" y2="9" {STROKE}/>'
        f'<line x1="0" y1="9" x2="0" y2="18" {STROKE}/>'
    )
    return _g(body, x, y, label=label, title="Fuse")


def isolator(x, y, label=None, dc=False):
    """Disconnector / isolating switch: open-blade switch."""
    body = (
        f'<line x1="0" y1="-18" x2="0" y2="-9" {STROKE}/>'
        f'<line x1="0" y1="-9" x2="9" y2="8" {STROKE}/>'
        f'<circle cx="0" cy="-9" r="2" {FILL}/>'
        f'<circle cx="0" cy="9" r="2" {FILL}/>'
        f'<line x1="0" y1="9" x2="0" y2="18" {STROKE}/>'
    )
    if dc:
        body += f'<text x="12" y="3" class="dg-tiny">DC</text>'
    return _g(body, x, y, label=label, title="Isolator")


def meter(x, y, label=None, letter="kWh"):
    """Energy meter."""
    body = (
        f'<rect x="-16" y="-11" width="32" height="22" rx="2" {STROKE}/>'
        f'<text x="0" y="4" class="dg-tiny" text-anchor="middle">{letter}</text>'
    )
    return _g(body, x, y, label=label, title="Meter")


def load(x, y, label=None, sublabel=None):
    """Load: downward arrow into a bar."""
    body = (
        f'<line x1="0" y1="-18" x2="0" y2="6" {STROKE}/>'
        f'<polygon points="-7,6 7,6 0,19" {FILL}/>'
    )
    return _g(body, x, y, label=label, sublabel=sublabel, title="Load")


def ev_charger(x, y, label=None, sublabel=None):
    """EV supply equipment: a pillar with a plug."""
    body = (
        f'<rect x="-11" y="-16" width="22" height="28" rx="3" {STROKE}/>'
        f'<circle cx="0" cy="-7" r="4.5" {THIN}/>'
        f'<line x1="0" y1="-2.5" x2="0" y2="6" {THIN}/>'
        f'<path d="M 11 4 q 8 0 8 8 l 0 4" {THIN}/>'
    )
    return _g(body, x, y, label=label, sublabel=sublabel, title="EV charger")


def earth(x, y, label=None):
    """Protective earth."""
    body = (
        f'<line x1="0" y1="-12" x2="0" y2="0" {STROKE}/>'
        f'<line x1="-11" y1="0" x2="11" y2="0" {STROKE}/>'
        f'<line x1="-7" y1="5" x2="7" y2="5" {STROKE}/>'
        f'<line x1="-3" y1="10" x2="3" y2="10" {STROKE}/>'
    )
    return _g(body, x, y, label=label, title="Protective earth")


def spd(x, y, label=None):
    """Surge protective device: varistor symbol."""
    body = (
        f'<line x1="0" y1="-18" x2="0" y2="-10" {STROKE}/>'
        f'<rect x="-7" y="-10" width="14" height="20" {STROKE}/>'
        f'<line x1="-7" y1="6" x2="7" y2="-6" {STROKE}/>'
        f'<line x1="0" y1="10" x2="0" y2="18" {STROKE}/>'
    )
    return _g(body, x, y, label=label, title="Surge protective device")


# ===================================================================
# Extended technology symbols
# ===================================================================
# Added with the wider technology set. Each keeps the same 40x40
# origin-centred convention so the layout code places them without knowing
# anything about their internal geometry.

def hydro_turbine(x, y, label=None, sublabel=None):
    """Hydro generator: circle with a water-wheel mark and penstock."""
    body = (
        f'<circle cx="0" cy="0" r="17" {STROKE}/>'
        f'<path d="M -8 4 q 4 -6 8 0 q 4 6 8 0" {THIN}/>'
        f'<path d="M -8 -3 q 4 -6 8 0 q 4 6 8 0" {THIN}/>'
        f'<text x="0" y="-7" class="dg-tiny" text-anchor="middle">H</text>'
    )
    return _g(body, x, y, label=label, sublabel=sublabel, title="Hydro turbine")


def biomass(x, y, label=None, sublabel=None):
    """Biomass generator: machine circle with a leaf."""
    body = (
        f'<circle cx="0" cy="0" r="17" {STROKE}/>'
        f'<path d="M -6 6 q 0 -12 12 -12 q 0 12 -12 12 z" {THIN}/>'
        f'<line x1="-6" y1="6" x2="4" y2="-4" {THIN}/>'
    )
    return _g(body, x, y, label=label, sublabel=sublabel, title="Biomass generator")


def geothermal(x, y, label=None, sublabel=None):
    """Geothermal: machine circle over ground hatching."""
    body = (
        f'<circle cx="0" cy="-3" r="15" {STROKE}/>'
        f'<text x="0" y="2" class="dg-symbol" text-anchor="middle">G</text>'
        f'<line x1="-16" y1="15" x2="16" y2="15" {STROKE}/>'
        f'<line x1="-10" y1="15" x2="-14" y2="20" {THIN}/>'
        f'<line x1="0" y1="15" x2="-4" y2="20" {THIN}/>'
        f'<line x1="10" y1="15" x2="6" y2="20" {THIN}/>'
    )
    return _g(body, x, y, label=label, sublabel=sublabel, title="Geothermal plant")


def csp(x, y, label=None, sublabel=None):
    """CSP: heliostat field converging on a tower."""
    body = (
        f'<line x1="0" y1="18" x2="0" y2="-8" {STROKE}/>'
        f'<circle cx="0" cy="-12" r="5" {STROKE}/>'
        f'<line x1="-18" y1="14" x2="-10" y2="8" {THIN}/>'
        f'<line x1="18" y1="14" x2="10" y2="8" {THIN}/>'
        f'<line x1="-13" y1="6" x2="-4" y2="-8" {THIN}/>'
        f'<line x1="13" y1="6" x2="4" y2="-8" {THIN}/>'
    )
    return _g(body, x, y, label=label, sublabel=sublabel,
              title="Concentrating solar power")


def tidal(x, y, label=None, sublabel=None):
    """Tidal turbine: rotor below a wave line."""
    body = (
        f'<path d="M -18 -14 q 4.5 -4 9 0 q 4.5 4 9 0 q 4.5 -4 9 0" {THIN}/>'
        f'<line x1="0" y1="18" x2="0" y2="-2" {STROKE}/>'
        f'<circle cx="0" cy="-2" r="2.5" {FILL}/>'
        f'<line x1="-12" y1="-8" x2="12" y2="4" {STROKE}/>'
        f'<line x1="-12" y1="4" x2="12" y2="-8" {STROKE}/>'
    )
    return _g(body, x, y, label=label, sublabel=sublabel, title="Tidal turbine")


def wave_device(x, y, label=None, sublabel=None):
    """Wave energy converter: float on a wave train."""
    body = (
        f'<path d="M -18 6 q 4.5 -7 9 0 q 4.5 7 9 0 q 4.5 -7 9 0" {STROKE}/>'
        f'<path d="M -18 14 q 4.5 -7 9 0 q 4.5 7 9 0 q 4.5 -7 9 0" {THIN}/>'
        f'<rect x="-7" y="-14" width="14" height="12" rx="3" {STROKE}/>'
        f'<line x1="0" y1="-2" x2="0" y2="4" {THIN}/>'
    )
    return _g(body, x, y, label=label, sublabel=sublabel,
              title="Wave energy converter")


def fuel_cell(x, y, label=None, sublabel=None):
    """Fuel cell: stacked plates with H2 in."""
    body = (
        f'<rect x="-15" y="-13" width="30" height="26" rx="2" {STROKE}/>'
        f'<line x1="-6" y1="-13" x2="-6" y2="13" {THIN}/>'
        f'<line x1="2" y1="-13" x2="2" y2="13" {THIN}/>'
        f'<text x="-11" y="4" class="dg-tiny">H₂</text>'
    )
    return _g(body, x, y, label=label, sublabel=sublabel, title="Fuel cell")


def electrolyser(x, y, label=None, sublabel=None):
    """Electrolyser: vessel with two electrodes and bubbles."""
    body = (
        f'<rect x="-15" y="-13" width="30" height="26" rx="2" {STROKE}/>'
        f'<line x1="-7" y1="-8" x2="-7" y2="8" {STROKE}/>'
        f'<line x1="7" y1="-8" x2="7" y2="8" {STROKE}/>'
        f'<circle cx="-4" cy="0" r="1.6" {THIN}/>'
        f'<circle cx="4" cy="-4" r="1.6" {THIN}/>'
        f'<circle cx="4" cy="4" r="1.6" {THIN}/>'
    )
    return _g(body, x, y, label=label, sublabel=sublabel, title="Electrolyser")


def hydrogen_tank(x, y, label=None, sublabel=None):
    """Hydrogen storage vessel."""
    body = (
        f'<rect x="-12" y="-15" width="24" height="30" rx="12" {STROKE}/>'
        f'<text x="0" y="4" class="dg-tiny" text-anchor="middle">H₂</text>'
    )
    return _g(body, x, y, label=label, sublabel=sublabel,
              title="Hydrogen storage")


def pumped_hydro(x, y, label=None, sublabel=None):
    """Pumped storage: two reservoirs and a reversible machine."""
    body = (
        f'<path d="M -18 -14 l 12 0 l 0 5 l -12 0 z" {STROKE}/>'
        f'<path d="M 6 12 l 12 0 l 0 5 l -12 0 z" {STROKE}/>'
        f'<line x1="-12" y1="-9" x2="0" y2="0" {STROKE}/>'
        f'<line x1="0" y1="0" x2="12" y2="12" {STROKE}/>'
        f'<circle cx="0" cy="0" r="6" {STROKE}/>'
        f'<polygon points="-3,-2 3,0 -3,2" {FILL}/>'
    )
    return _g(body, x, y, label=label, sublabel=sublabel,
              title="Pumped hydro storage")


def flywheel(x, y, label=None, sublabel=None):
    """Flywheel: heavy rim on a shaft."""
    body = (
        f'<circle cx="0" cy="0" r="15" stroke="var(--dg-line)" fill="none" '
        f'stroke-width="4"/>'
        f'<circle cx="0" cy="0" r="4" {FILL}/>'
        f'<line x1="-15" y1="0" x2="15" y2="0" {THIN}/>'
        f'<line x1="0" y1="-15" x2="0" y2="15" {THIN}/>'
    )
    return _g(body, x, y, label=label, sublabel=sublabel, title="Flywheel")


def supercapacitor(x, y, label=None, sublabel=None):
    """Supercapacitor: capacitor plates with a double bar."""
    body = (
        f'<line x1="-16" y1="0" x2="-4" y2="0" {STROKE}/>'
        f'<line x1="-4" y1="-13" x2="-4" y2="13" stroke="var(--dg-line)" '
        f'stroke-width="3"/>'
        f'<line x1="2" y1="-13" x2="2" y2="13" stroke="var(--dg-line)" '
        f'stroke-width="3"/>'
        f'<line x1="2" y1="0" x2="16" y2="0" {STROKE}/>'
        f'<text x="-1" y="-17" class="dg-tiny" text-anchor="middle">C</text>'
    )
    return _g(body, x, y, label=label, sublabel=sublabel,
              title="Supercapacitor")


def caes(x, y, label=None, sublabel=None):
    """Compressed air: pressure vessel with a compressor arrow."""
    body = (
        f'<rect x="-15" y="-11" width="30" height="22" rx="11" {STROKE}/>'
        f'<path d="M -7 3 l 7 -8 l 7 8" {THIN}/>'
        f'<text x="0" y="9" class="dg-tiny" text-anchor="middle">air</text>'
    )
    return _g(body, x, y, label=label, sublabel=sublabel,
              title="Compressed air storage")


def thermal_store(x, y, label=None, sublabel=None):
    """Thermal store: insulated tank with heat waves."""
    body = (
        f'<rect x="-13" y="-15" width="26" height="30" rx="4" {STROKE}/>'
        f'<path d="M -7 8 q 3 -5 6 0 q 3 5 6 0" {THIN}/>'
        f'<path d="M -7 1 q 3 -5 6 0 q 3 5 6 0" {THIN}/>'
        f'<path d="M -7 -6 q 3 -5 6 0 q 3 5 6 0" {THIN}/>'
    )
    return _g(body, x, y, label=label, sublabel=sublabel,
              title="Thermal storage")


def chp(x, y, label=None, sublabel=None):
    """CHP: machine circle with a heat output arrow."""
    body = (
        f'<circle cx="0" cy="0" r="16" {STROKE}/>'
        f'<text x="0" y="5" class="dg-symbol" text-anchor="middle">M</text>'
        f'<line x1="16" y1="-8" x2="24" y2="-8" {THIN}/>'
        f'<polygon points="24,-11 29,-8 24,-5" {FILL}/>'
        f'<text x="21" y="-13" class="dg-tiny">Q</text>'
    )
    return _g(body, x, y, label=label, sublabel=sublabel,
              title="Combined heat and power")


# Technology key -> symbol function, so the diagram builder can look one up
# instead of carrying a chain of if-statements that has to be extended for
# every technology added.
SYMBOL_FOR = {
    "pv": pv_array,
    "csp": csp,
    "wind": wind_turbine,
    "run_of_river_hydro": hydro_turbine,
    "reservoir_hydro": hydro_turbine,
    "tidal": tidal,
    "wave": wave_device,
    "genset": generator,
    "biomass": biomass,
    "geothermal": geothermal,
    "chp": chp,
    "fuel_cell": fuel_cell,
    "battery": battery,
    "pumped_hydro": pumped_hydro,
    "flywheel": flywheel,
    "supercapacitor": supercapacitor,
    "caes": caes,
    "thermal_storage": thermal_store,
    "hydrogen": hydrogen_tank,
    "ev_fleet": ev_charger,
    "ev": ev_charger,          # the load-side feeder uses the short key
    "grid": grid,
    "load": load,
}


def for_technology(tech):
    """Symbol function for a technology, falling back to a generic machine."""
    return SYMBOL_FOR.get(tech, generator)


# ===================================================================
# Switchgear and metering detail
# ===================================================================
# Added to match the embedded-generator SLD templates, which require the
# point-of-utility-control furniture that a bus-and-feeder sketch omits.

def earth_leakage(x, y, label=None, scale=1.0, poles=2):
    """
    Earth leakage / residual current device.

    Drawn as the template does: a dashed enclosure around the pole group
    with the test/trip element beside it, which is what distinguishes it
    from a plain circuit breaker on the drawing.
    """
    body = (
        f'<rect x="-19" y="-13" width="38" height="26" rx="1.5" '
        f'stroke="var(--dg-line)" fill="none" stroke-width="1.1" '
        f'stroke-dasharray="4 2.5"/>'
        f'<line x1="-13" y1="-13" x2="-13" y2="13" '
        f'stroke="var(--dg-line)" stroke-width="1.4"/>'
        f'<line x1="-9" y1="-13" x2="-9" y2="13" '
        f'stroke="var(--dg-line)" stroke-width="1.4"/>'
        f'<rect x="-4" y="-8" width="18" height="16" {STROKE}/>'
        f'<text x="5" y="4" class="dg-tiny-b" text-anchor="middle">EL</text>'
        f'<line x1="0" y1="-18" x2="0" y2="-13" {STROKE}/>'
        f'<line x1="0" y1="13" x2="0" y2="18" {STROKE}/>'
    )
    return _g(body, x, y, scale=scale, label=label,
              title="Earth leakage / residual current device")


def current_transformer(x, y, label=None, scale=1.0):
    """Current transformer, as used for export limiting."""
    body = (
        f'<path d="M -6 -11 a 11 11 0 0 0 0 22" {STROKE}/>'
        f'<rect x="-2" y="-9" width="20" height="18" rx="2" {STROKE}/>'
        f'<text x="8" y="4" class="dg-tiny" text-anchor="middle">CT</text>'
    )
    return _g(body, x, y, scale=scale, label=label,
              title="Current transformer")


def changeover(x, y, label=None, scale=1.0):
    """
    Change-over / bypass switch: one pole selecting between two sources.
    """
    body = (
        f'<line x1="-16" y1="-14" x2="-16" y2="-6" {STROKE}/>'
        f'<line x1="16" y1="-14" x2="16" y2="-6" {STROKE}/>'
        f'<circle cx="-16" cy="-6" r="2" {FILL}/>'
        f'<circle cx="16" cy="-6" r="2" {FILL}/>'
        f'<line x1="0" y1="10" x2="-15" y2="-5" {STROKE}/>'
        f'<circle cx="0" cy="10" r="2" {FILL}/>'
        f'<line x1="0" y1="10" x2="0" y2="18" {STROKE}/>'
        f'<text x="9" y="16" class="dg-tiny">C/O</text>'
    )
    return _g(body, x, y, scale=scale, label=label,
              title="Change over switch")


def no_interconnection(x, y, label=None, scale=1.0):
    """
    The 'no inter-connection' mark: two supplies that must never be
    paralleled. It appears wherever a generator and the utility feed the
    same board through a change-over.
    """
    body = (
        f'<line x1="-14" y1="0" x2="-4" y2="0" {STROKE}/>'
        f'<line x1="4" y1="0" x2="14" y2="0" {STROKE}/>'
        f'<line x1="-4" y1="-8" x2="4" y2="8" {STROKE}/>'
    )
    return _g(body, x, y, scale=scale, label=label,
              title="No inter-connection")


def phase_ticks(x, y, n=3, angle=-52, scale=1.0, neutral=False):
    """
    The conductor-count hash marks that cross a line on an SLD.

    Three strokes for a three-phase circuit, plus a longer fourth for the
    neutral. This is how a single-line drawing states how many conductors
    the single line represents, and leaving it off is the commonest reason
    a drawing is returned by a network operator.
    """
    n = int(n)
    out = []
    spacing = 4.2
    start = -(n - 1) * spacing / 2.0
    for i in range(n):
        off = start + i * spacing
        out.append(
            f'<line x1="{off - 5}" y1="5" x2="{off + 5}" y2="-5" '
            f'stroke="var(--dg-line)" stroke-width="1.2"/>'
        )
    if neutral:
        off = start + n * spacing
        out.append(
            f'<line x1="{off - 6.5}" y1="6.5" x2="{off + 6.5}" y2="-6.5" '
            f'stroke="var(--dg-line)" stroke-width="1.2"/>'
        )
    body = "".join(out)
    return (
        f'<g transform="translate({x},{y}) rotate({angle}) '
        f'scale({scale})">{body}</g>'
    )


def combiner_box(x, y, w, h, title=None, sub=None):
    """A labelled enclosure: DC combiner, AC combiner, distribution board."""
    out = [
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" '
        f'class="dg-encl-box"/>'
    ]
    if title:
        out.append(
            f'<text x="{x + w / 2}" y="{y - 7}" class="dg-encl" '
            f'text-anchor="middle">{_esc(title)}</text>'
        )
    if sub:
        out.append(
            f'<text x="{x + w / 2}" y="{y + h + 13}" class="dg-sublabel" '
            f'text-anchor="middle">{_esc(sub)}</text>'
        )
    return "".join(out)


def vertical_enclosure_label(x, y, h, text):
    """Rotated enclosure caption, as the templates use down the left edge."""
    cy = y + h / 2
    return (
        f'<text x="{x}" y="{cy}" class="dg-encl" text-anchor="middle" '
        f'transform="rotate(-90 {x} {cy})">{_esc(text)}</text>'
    )


def rating_text(x, y, lines, anchor="start", cls="dg-rating"):
    """A stack of rating annotations beside a device."""
    out = []
    for i, line in enumerate(lines):
        if not line:
            continue
        out.append(
            f'<text x="{x}" y="{y + i * 10}" class="{cls}" '
            f'text-anchor="{anchor}">{_esc(line)}</text>'
        )
    return "".join(out)
