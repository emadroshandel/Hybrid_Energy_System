"""
Power conversion equipment sizing.

Covers the three converters a hybrid system needs:

  * PV inverter          DC array -> AC bus
  * Battery PCS          bidirectional, AC-coupled storage
  * Wind converter       variable-frequency AC -> AC bus

and the string design that sits behind the PV inverter.

The design decisions this module makes explicit:

DC/AC ratio. A PV inverter is deliberately undersized relative to the array,
because the array reaches nameplate output for only a few hours a year.
Ratios of 1.15-1.35 are normal; the losses from clipping the peak are less
than the saving on the inverter. This module computes the ACTUAL clipping
loss from the hourly series rather than assuming a rule of thumb, which is
the only way to know whether a given ratio is right for a particular site.
A high-latitude site and a desert site want different ratios.

String voltage limits. The two binding cases are opposites and both must be
checked:
  - coldest expected temperature at open circuit sets the MAXIMUM string
    voltage, because Voc rises as temperature falls. Exceeding the inverter
    or module rating here is a safety failure, not a performance one.
  - hottest expected cell temperature at maximum power sets the MINIMUM
    string voltage, which must stay above the inverter's MPPT window or the
    array stops producing on exactly the days it should produce most.
"""

from __future__ import annotations

import math

# Standard IEC/commercial inverter sizes, kW. Rounding a calculated rating
# up to a real product is what turns a number into a specification.
STANDARD_INVERTER_KW = [
    1, 1.5, 2, 2.5, 3, 3.6, 4, 5, 6, 8, 10, 12, 15, 17, 20, 25, 30, 33, 36,
    40, 50, 60, 75, 100, 110, 125, 150, 175, 200, 250, 300, 333, 400, 500,
    630, 800, 1000, 1250, 1500, 2000, 2500, 3000,
]


def standard_size(kw, sizes=None, allow_below=False):
    """Round a computed rating up to the next standard product size."""
    sizes = sizes or STANDARD_INVERTER_KW
    for s in sizes:
        if s >= kw - 1e-9:
            return float(s)
    if allow_below:
        return float(sizes[-1])
    # Above the largest standard unit: use multiple units.
    return float(math.ceil(kw / sizes[-1]) * sizes[-1])


def size_pv_inverter(dc_series, array_kwp, target_dc_ac=1.20,
                     inverter_efficiency=0.97, max_clipping_loss=0.02,
                     ambient_derate=1.0):
    """
    Size the PV inverter from the actual DC production series.

    Rather than applying a fixed DC/AC ratio, this sweeps candidate ratings
    and reports the clipping loss each would cause, then picks the smallest
    standard size whose clipping loss stays within `max_clipping_loss`.

    Returns a specification dict including the clipping curve, so the report
    can show the trade-off rather than assert a single answer.
    """
    if array_kwp <= 0:
        return None

    annual_dc = sum(dc_series)
    peak_dc = max(dc_series) if dc_series else 0.0

    def clipping_at(ac_kw):
        dc_limit = ac_kw / inverter_efficiency
        clipped = sum(max(0.0, p - dc_limit) for p in dc_series)
        return clipped

    # Sweep from a very undersized inverter up to no clipping at all.
    curve = []
    lo = max(0.05, array_kwp * 0.4)
    hi = max(peak_dc, array_kwp) * 1.05
    steps = 40
    for i in range(steps + 1):
        ac = lo + (hi - lo) * i / steps
        clipped = clipping_at(ac)
        curve.append(
            {
                "ac_kw": ac,
                "dc_ac_ratio": array_kwp / ac if ac > 0 else None,
                "clipped_kwh": clipped,
                "clipping_loss": clipped / annual_dc if annual_dc > 0 else 0.0,
            }
        )

    # Smallest rating meeting the clipping budget.
    chosen = None
    for point in curve:
        if point["clipping_loss"] <= max_clipping_loss:
            chosen = point
            break
    if chosen is None:
        chosen = curve[-1]

    # Also report what the user's requested ratio would do.
    requested_ac = array_kwp / target_dc_ac if target_dc_ac > 0 else array_kwp
    requested_clip = clipping_at(requested_ac)

    rating = standard_size(chosen["ac_kw"] * ambient_derate)
    n_units = 1
    if rating > STANDARD_INVERTER_KW[-1]:
        n_units = int(math.ceil(rating / STANDARD_INVERTER_KW[-1]))
        rating = STANDARD_INVERTER_KW[-1]

    total_ac = rating * n_units
    final_clip = clipping_at(total_ac)

    return {
        "component": "PV inverter",
        "array_kwp": array_kwp,
        "rated_ac_kw": rating,
        "units": n_units,
        "total_ac_kw": total_ac,
        "dc_ac_ratio": array_kwp / total_ac if total_ac > 0 else None,
        "requested_dc_ac_ratio": target_dc_ac,
        "requested_ac_kw": requested_ac,
        "requested_clipping_loss": (
            requested_clip / annual_dc if annual_dc > 0 else 0.0
        ),
        "clipping_kwh_per_year": final_clip,
        "clipping_loss": final_clip / annual_dc if annual_dc > 0 else 0.0,
        "peak_dc_kw": peak_dc,
        "annual_dc_kwh": annual_dc,
        "annual_ac_kwh": (annual_dc - final_clip) * inverter_efficiency,
        "efficiency": inverter_efficiency,
        "clipping_curve": curve,
        "hours_clipped": sum(
            1 for p in dc_series if p > total_ac / inverter_efficiency
        ),
        "notes": _inverter_notes(array_kwp, total_ac, final_clip, annual_dc),
    }


def _inverter_notes(array_kwp, ac_kw, clipped, annual_dc):
    notes = []
    ratio = array_kwp / ac_kw if ac_kw > 0 else 0
    if ratio < 1.0:
        notes.append(
            f"The inverter ({ac_kw:.0f} kW AC) is larger than the array "
            f"({array_kwp:.0f} kWp DC). This costs money for no yield: the "
            f"array can never reach the inverter's rating. Consider a "
            f"smaller inverter or a larger array."
        )
    elif ratio > 1.5:
        notes.append(
            f"A DC/AC ratio of {ratio:.2f} is aggressive. Check that the "
            f"inverter's maximum DC input current and voltage are not "
            f"exceeded, and that its warranty permits this ratio."
        )
    loss = clipped / annual_dc if annual_dc > 0 else 0
    if loss > 0.03:
        notes.append(
            f"Clipping discards {100 * loss:.1f}% of annual DC production "
            f"({clipped:,.0f} kWh/yr). A larger inverter would recover most "
            f"of it - compare against the extra capital cost."
        )
    return notes


def size_battery_pcs(charge_series, discharge_series, battery_power_kw,
                     efficiency=0.97, headroom=1.10):
    """
    Size the bidirectional power conversion system for the battery.

    Sized on the observed peak flow rather than the battery's nameplate
    power, because a battery that is never dispatched at full rate does not
    need a full-rate converter. `headroom` covers future operation outside
    the simulated year.
    """
    peak_charge = max(charge_series) if charge_series else 0.0
    peak_discharge = max(discharge_series) if discharge_series else 0.0
    peak = max(peak_charge, peak_discharge)
    required = peak * headroom
    rating = standard_size(required)

    utilisation = (peak / battery_power_kw) if battery_power_kw > 0 else 0.0

    notes = []
    if battery_power_kw > 0 and utilisation < 0.6:
        notes.append(
            f"The battery's nameplate power is {battery_power_kw:.0f} kW but "
            f"the dispatch never exceeds {peak:.0f} kW ({100 * utilisation:.0f}% "
            f"of it). Either the energy-to-power ratio is larger than this "
            f"application needs, or the control strategy is not using the "
            f"available power."
        )
    if rating > battery_power_kw > 0:
        notes.append(
            f"The converter ({rating:.0f} kW) is rated above the battery's "
            f"own power limit ({battery_power_kw:.0f} kW); the battery, not "
            f"the converter, will be the binding constraint."
        )

    return {
        "component": "Battery PCS (bidirectional)",
        "rated_kw": rating,
        "peak_charge_kw": peak_charge,
        "peak_discharge_kw": peak_discharge,
        "battery_nameplate_kw": battery_power_kw,
        "utilisation": utilisation,
        "efficiency": efficiency,
        "headroom": headroom,
        "four_quadrant": True,
        "notes": notes,
    }


def size_wind_converter(wind_series, rated_kw, efficiency=0.96):
    """Full-power converter for a variable-speed turbine."""
    peak = max(wind_series) if wind_series else 0.0
    rating = standard_size(max(peak, rated_kw) * 1.05)
    return {
        "component": "Wind converter (full power)",
        "rated_kw": rating,
        "turbine_rated_kw": rated_kw,
        "peak_output_kw": peak,
        "efficiency": efficiency,
        "notes": [],
    }


def size_grid_interface(import_series, export_series, voltage_v=400,
                        power_factor=0.95, headroom=1.15):
    """
    Point of common coupling rating.

    Reported in both kW and kVA, because the connection agreement and the
    switchgear are specified in kVA while the energy model works in kW.
    Confusing the two undersizes the equipment by whatever the power factor
    is - typically 5%.
    """
    peak_import = max(import_series) if import_series else 0.0
    peak_export = max(export_series) if export_series else 0.0
    peak = max(peak_import, peak_export) * headroom
    kva = peak / power_factor if power_factor > 0 else peak
    current = (
        kva * 1000.0 / (math.sqrt(3) * voltage_v) if voltage_v > 0 else 0.0
    )
    return {
        "component": "Point of common coupling",
        "peak_import_kw": peak_import,
        "peak_export_kw": peak_export,
        "rated_kw": peak,
        "rated_kva": kva,
        "voltage_v": voltage_v,
        "power_factor": power_factor,
        "line_current_a": current,
        "notes": [],
    }


# ------------------------------------------------------------ PV strings

def mppt_config(rating_kw):
    """
    Realistic MPPT input count and per-input current limit for an inverter of
    a given rating.

    Sizing an array against a fixed "2 MPPT inputs at 26 A" assumption is
    only valid for a small string inverter. A 630 kW central inverter is a
    different machine: its DC side is fed from combiner boxes and accepts
    thousands of amps across a handful of inputs. Applying the string-
    inverter limit to it produces an impossible design, and applying the
    central-inverter limit to a residential unit produces an unsafe one.
    """
    if rating_kw <= 6:
        return {"n_mppt": 2, "i_max_per_mppt": 13.0, "class": "residential string"}
    if rating_kw <= 30:
        return {"n_mppt": 3, "i_max_per_mppt": 26.0, "class": "commercial string"}
    if rating_kw <= 150:
        return {"n_mppt": 12, "i_max_per_mppt": 40.0, "class": "large string"}
    if rating_kw <= 400:
        return {"n_mppt": 12, "i_max_per_mppt": 60.0, "class": "modular central"}
    return {"n_mppt": 16, "i_max_per_mppt": 120.0, "class": "central"}


class PVModule:
    """Electrical parameters of one PV module, at STC."""

    def __init__(self, name="Generic 550 W mono", pmax_w=550.0, vmp=41.8,
                 imp=13.16, voc=49.9, isc=13.95,
                 temp_coeff_voc=-0.0027, temp_coeff_pmax=-0.0035,
                 temp_coeff_isc=0.0005):
        self.name = name
        self.pmax_w = float(pmax_w)
        self.vmp = float(vmp)
        self.imp = float(imp)
        self.voc = float(voc)
        self.isc = float(isc)
        self.temp_coeff_voc = float(temp_coeff_voc)
        self.temp_coeff_pmax = float(temp_coeff_pmax)
        self.temp_coeff_isc = float(temp_coeff_isc)


def design_strings(array_kwp, module, mppt_v_min=200.0, mppt_v_max=800.0,
                   inverter_v_max=1000.0, inverter_i_max_per_mppt=26.0,
                   t_min_c=-10.0, t_max_cell_c=70.0, n_mppt=2):
    """
    Work out modules per string and number of strings.

    The two temperature limits are applied as described in the module
    docstring. `t_min_c` should be the record low ambient at the site, not
    the average winter minimum - the limit is a safety rating and one cold
    morning is enough to exceed it.
    """
    if array_kwp <= 0 or module.pmax_w <= 0:
        return None

    n_modules_total = int(math.ceil(array_kwp * 1000.0 / module.pmax_w))

    # Voc rises as temperature falls below STC (25 C).
    voc_cold = module.voc * (1.0 + module.temp_coeff_voc * (t_min_c - 25.0))
    # Vmp falls as cell temperature rises above STC.
    vmp_hot = module.vmp * (1.0 + module.temp_coeff_pmax * (t_max_cell_c - 25.0))

    max_per_string = int(math.floor(inverter_v_max / voc_cold)) if voc_cold > 0 else 0
    max_per_string_mppt = (
        int(math.floor(mppt_v_max / voc_cold)) if voc_cold > 0 else 0
    )
    min_per_string = int(math.ceil(mppt_v_min / vmp_hot)) if vmp_hot > 0 else 0

    upper = min(max_per_string, max_per_string_mppt)

    errors = []
    if upper < min_per_string:
        errors.append(
            f"No valid string length exists: the coldest-day open-circuit "
            f"limit allows at most {upper} modules per string, but the "
            f"hottest-day MPPT minimum requires at least {min_per_string}. "
            f"Use a module with a different voltage, or an inverter with a "
            f"wider MPPT window."
        )
        modules_per_string = max(1, min_per_string)
    else:
        # Longest permitted string minimises the number of strings, the DC
        # cabling and the combiner count.
        modules_per_string = upper

    n_strings = int(math.ceil(n_modules_total / modules_per_string))
    strings_per_mppt = int(math.ceil(n_strings / max(1, n_mppt)))
    current_per_mppt = strings_per_mppt * module.isc * 1.25   # IEC 60364 factor

    if current_per_mppt > inverter_i_max_per_mppt:
        strings_per_input = max(
            1, int(math.floor(inverter_i_max_per_mppt / (module.isc * 1.25)))
        )
        needed_mppt = int(math.ceil(n_strings / strings_per_input))
        errors.append(
            f"Each MPPT input would carry {current_per_mppt:.1f} A "
            f"(after the 1.25 safety factor) against a "
            f"{inverter_i_max_per_mppt:.1f} A limit. This array needs at "
            f"least {needed_mppt} MPPT inputs at {strings_per_input} string(s) "
            f"each - use an inverter with more inputs, split the array across "
            f"more inverters, or add combiner boxes."
        )

    actual_kwp = n_strings * modules_per_string * module.pmax_w / 1000.0

    return {
        "module": module.name,
        "module_pmax_w": module.pmax_w,
        "modules_total": n_strings * modules_per_string,
        "modules_per_string": modules_per_string,
        "strings": n_strings,
        "strings_per_mppt": strings_per_mppt,
        "mppt_inputs": n_mppt,
        "actual_capacity_kwp": actual_kwp,
        "requested_capacity_kwp": array_kwp,
        "string_voc_cold_v": voc_cold * modules_per_string,
        "string_vmp_hot_v": vmp_hot * modules_per_string,
        "string_vmp_stc_v": module.vmp * modules_per_string,
        "string_isc_a": module.isc,
        "current_per_mppt_a": current_per_mppt,
        "limits": {
            "min_modules_per_string": min_per_string,
            "max_modules_per_string": upper,
            "design_t_min_c": t_min_c,
            "design_t_max_cell_c": t_max_cell_c,
            "inverter_v_max": inverter_v_max,
            "mppt_window_v": [mppt_v_min, mppt_v_max],
        },
        "errors": errors,
        "valid": not errors,
    }
