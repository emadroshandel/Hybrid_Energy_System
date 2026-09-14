"""
Input plausibility checks.

Every number in a sizing study is dimensionally ambiguous to a computer and
obvious to an engineer. A load series in watts is a perfectly valid list of
floats; monthly irradiation given as a daily average is a perfectly valid
list of twelve numbers; a 25 kWp PV block is a perfectly reasonable unit
right up until the site is a house. The engine cannot tell, and until now it
did not try - it accepted whatever it was given, sized against it correctly,
and reported an answer three orders of magnitude away from what the user
meant, with no indication that anything had gone wrong.

That is the failure mode this module exists to prevent. It does not stop a
study; it says, in words, what the engine believes it has been asked, and it
says so loudly when the belief is implausible. "Peak demand 292 kW, annual
1.18 GWh - a light-industrial site or a small campus" is the sentence that
turns a silent 1000x unit error into a five-second fix.

Every check returns a finding:

    {"level": "info" | "warning" | "error", "code": ..., "message": ...}

`error` means the study will produce a number that is almost certainly wrong
and the user should stop. `warning` means the input is unusual and may be
deliberate. `info` states an interpretation so it can be contradicted.

Nothing here raises. A plausibility check that blocks a legitimate but
unusual study is worse than the silence it replaced.
"""

from __future__ import annotations

from .timeseries import HOURS_PER_YEAR

# ---------------------------------------------------------------------
# Reference ranges
# ---------------------------------------------------------------------

# Annual specific yield of a fixed, well-oriented PV array, kWh per kWp.
# Roughly 700 in northern Scandinavia, 2100 in the Atacama. Anything outside
# this band is a data or configuration fault, not a site.
PV_YIELD_MIN = 450.0
PV_YIELD_MAX = 2400.0
PV_YIELD_TYPICAL = (900.0, 2000.0)

# Annual capacity factor of a wind turbine at a real site.
WIND_CF_MIN = 0.02
WIND_CF_MAX = 0.60

# Monthly global horizontal irradiation, kWh/m2 PER MONTH. A month's total
# is 40-260 almost everywhere on land. Values of 2-9 are the DAILY average
# that the Global Solar Atlas and most met stations quote, which is the
# single most common unit error in this input.
MONTHLY_GHI_MIN = 20.0
MONTHLY_GHI_MAX = 320.0
DAILY_GHI_PLAUSIBLE = (0.4, 10.0)

_DAYS_IN_MONTH = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)

# Site archetypes by annual demand, used to state an interpretation the user
# can immediately recognise or reject.
SITE_SCALES = (
    (0.0,        1_200.0,    "a single room, a telecom cabinet or a small "
                             "off-grid cabin"),
    (1_200.0,    25_000.0,   "a house or small apartment"),
    (25_000.0,   150_000.0,  "a large villa, a farm, a shop or a small "
                             "office"),
    (150_000.0,  2_000_000.0, "a light-industrial site, a small factory, a "
                              "hotel or a campus building"),
    (2_000_000.0, 50_000_000.0, "a factory, a hospital or a university "
                                "campus"),
    (50_000_000.0, float("inf"), "a heavy-industrial plant, a data centre or "
                                 "a small town"),
)


def _finding(level, code, message):
    return {"level": level, "code": code, "message": message}


def describe_scale(annual_kwh):
    for lo, hi, label in SITE_SCALES:
        if lo <= annual_kwh < hi:
            return label
    return "a site of unusual size"


def _fmt_energy(kwh):
    if kwh >= 1e9:
        return f"{kwh / 1e9:.2f} TWh"
    if kwh >= 1e6:
        return f"{kwh / 1e6:.2f} GWh"
    if kwh >= 1e3:
        return f"{kwh / 1e3:.1f} MWh"
    return f"{kwh:.0f} kWh"


def _fmt_power(kw):
    if kw >= 1000:
        return f"{kw / 1000:.2f} MW"
    if kw >= 1:
        return f"{kw:.1f} kW"
    return f"{kw * 1000:.0f} W"


# ---------------------------------------------------------------------
# Load profile
# ---------------------------------------------------------------------

def check_load(series, name="load"):
    """
    State what the engine thinks the load is, and flag the shapes that are
    almost always a unit error.

    The interpretation line is the important part and is always emitted.
    A user who meant a house and reads "peak 292 kW - a light-industrial
    site" has found their mistake; a user who meant a factory reads a
    confirmation and moves on.
    """
    out = []
    if not series:
        return [_finding("error", "load_empty", "The load series is empty.")]

    n = len(series)
    peak = max(series)
    mean = sum(series) / n
    annual = sum(series) * (HOURS_PER_YEAR / n if n else 1.0)
    base = min(series)

    if peak <= 0:
        return [_finding("error", "load_zero",
                         "Every value in the load series is zero or negative. "
                         "Nothing can be sized against it.")]

    out.append(_finding(
        "info", "load_interpretation",
        f"Read as {_fmt_power(peak)} peak, {_fmt_power(mean)} average, "
        f"{_fmt_energy(annual)} a year - the demand of "
        f"{describe_scale(annual)}. Every capacity in the result follows "
        f"from this number, so if it is not the site you meant, correct the "
        f"units or the peak before reading anything else."
    ))

    # The classic 1000x: a house profile entered in watts, or a large site
    # entered in MW.
    #
    # This cannot be proven from the numbers - 3 MW is a perfectly real
    # factory and also exactly what a 3 kW house looks like in watts - so it
    # is raised as a question, at a threshold low enough to catch the case
    # that matters. A genuine multi-megawatt site loses one line of text; a
    # house read as a factory loses the whole study.
    if peak >= 1_000:
        out.append(_finding(
            "warning", "load_maybe_watts",
            f"A peak of {_fmt_power(peak)} is a large industrial supply. If "
            f"that is the site, ignore this. If you meant something smaller, "
            f"the file is almost certainly in watts: the engine reads every "
            f"load value as kW and nothing in the data distinguishes the "
            f"two. Set the scale factor to 0.001."
        ))
    if 0 < peak < 0.2:
        out.append(_finding(
            "warning", "load_maybe_megawatts",
            f"A peak of {_fmt_power(peak)} is smaller than a domestic "
            f"kettle. If the source file was in megawatts, set the scale "
            f"factor to 1000."
        ))

    lf = mean / peak if peak else 0.0
    if lf < 0.08:
        out.append(_finding(
            "warning", "load_factor_low",
            f"The load factor is {100 * lf:.0f}% - the site is idle almost "
            f"all year and sized by a few short peaks. Check that the peak "
            f"is real and not a metering spike; one bad hour in 8760 sets "
            f"the whole design."
        ))
    elif lf > 0.95:
        out.append(_finding(
            "warning", "load_factor_flat",
            f"The load factor is {100 * lf:.0f}%, which is almost perfectly "
            f"flat. Real demand rarely is. If this came from a monthly or "
            f"annual figure spread evenly over the year, the storage this "
            f"study recommends will be far too small, because a flat profile "
            f"has none of the day-night swing that sizes a battery."
        ))

    if base <= 0 and peak > 0:
        zeros = sum(1 for v in series if v <= 0)
        if zeros > n * 0.02:
            out.append(_finding(
                "warning", "load_zero_hours",
                f"{zeros} hours ({100.0 * zeros / n:.0f}%) have zero demand. "
                f"If those are gaps in the metering rather than genuine "
                f"shutdowns, the annual demand - and everything sized from "
                f"it - is understated."
            ))
    return out


# ---------------------------------------------------------------------
# Solar resource
# ---------------------------------------------------------------------

def check_monthly_ghi(values):
    """
    Twelve monthly GHI totals, or twelve daily averages wearing a disguise.

    Returns (corrected_values, findings). The correction is applied rather
    than merely reported, because a study run on daily averages is wrong by
    a factor of thirty and no one reads a warning attached to a result that
    otherwise looks plausible.
    """
    out = []
    if not values or len(values) != 12:
        return values, [_finding(
            "error", "ghi_count",
            f"Twelve monthly values are needed; {len(values) if values else 0} "
            f"were given."
        )]

    v = [float(x) for x in values]
    if any(x < 0 for x in v):
        out.append(_finding("error", "ghi_negative",
                            "Monthly irradiation cannot be negative."))
        return v, out

    hi = max(v)
    lo = min(x for x in v if x > 0) if any(x > 0 for x in v) else 0.0

    if DAILY_GHI_PLAUSIBLE[0] <= hi <= DAILY_GHI_PLAUSIBLE[1]:
        # These are daily averages, in kWh/m2/day. Convert to monthly totals.
        converted = [x * _DAYS_IN_MONTH[m] for m, x in enumerate(v)]
        out.append(_finding(
            "warning", "ghi_daily_averages",
            f"The twelve values peak at {hi:.1f} kWh/m2, which is a DAILY "
            f"average, not a monthly total - this is how the Global Solar "
            f"Atlas and most met stations report irradiation. They have been "
            f"multiplied by the days in each month, giving "
            f"{sum(converted):.0f} kWh/m2 a year. Left uncorrected this "
            f"understates the solar resource about thirtyfold, and the "
            f"study would answer by recommending an absurd amount of PV."
        ))
        return converted, out

    annual = sum(v)
    if hi > MONTHLY_GHI_MAX:
        out.append(_finding(
            "warning", "ghi_high",
            f"A monthly total of {hi:.0f} kWh/m2 is above anything recorded "
            f"on Earth (about 300). Check the units - values in MJ/m2 need "
            f"dividing by 3.6."
        ))
    elif lo and lo < MONTHLY_GHI_MIN and annual < 700:
        out.append(_finding(
            "warning", "ghi_low",
            f"The twelve values total {annual:.0f} kWh/m2 a year, which is "
            f"below the cloudiest inhabited places (about 700). The PV yield "
            f"will be understated and the recommended array correspondingly "
            f"oversized."
        ))
    else:
        out.append(_finding(
            "info", "ghi_annual",
            f"Annual global horizontal irradiation {annual:.0f} kWh/m2."
        ))
    return v, out


def check_pv_yield(unit_series, capacity_kwp, location=None,
                   time_reference=None):
    """
    The single most diagnostic number in a solar study: kWh per kWp per year.

    An engineer knows this figure for their own region to within ten per
    cent, so reporting it turns a whole class of silent faults - misaligned
    time stamps, wrong hemisphere, irradiance in the wrong unit, a tilt
    typed as 90 - into something visible at a glance.
    """
    out = []
    if not unit_series or not capacity_kwp:
        return out
    y = sum(unit_series) / float(capacity_kwp)

    out.append(_finding(
        "info", "pv_yield",
        f"Modelled PV yield {y:,.0f} kWh per kWp per year"
        + (f" at {location.name}" if location is not None else "")
        + ". Compare this with the Global Solar Atlas figure for the site "
          "before trusting anything downstream: it is the number every "
          "capacity in the result is divided by."
    ))

    if y < PV_YIELD_MIN:
        causes = [
            "the resource data may be stamped in UTC while the model works "
            "in local time (check the reported UTC offset)",
            "the irradiance may be in the wrong unit",
            "the tilt or azimuth may be pointing the array away from the sun",
            "the DC system losses may have been entered as a percentage "
            "rather than a fraction",
        ]
        out.append(_finding(
            "error", "pv_yield_low",
            f"A yield of {y:,.0f} kWh/kWp is below anything achievable at a "
            f"real site (the cloudiest inhabited places manage about "
            f"{PV_YIELD_MIN:.0f}). The study will compensate by recommending "
            f"several times the PV a real design needs. Likely causes: "
            + "; ".join(causes) + "."
        ))
    elif y > PV_YIELD_MAX:
        out.append(_finding(
            "error", "pv_yield_high",
            f"A yield of {y:,.0f} kWh/kWp exceeds the best sites on Earth "
            f"(about {PV_YIELD_MAX:.0f} for a fixed array). The irradiance "
            f"series or the array capacity is wrong, and the study will "
            f"under-size the system."
        ))
    if time_reference and time_reference != "local_standard_time":
        out.append(_finding(
            "warning", "pv_time_reference",
            "The resource series is not stamped as local standard time. The "
            "solar-position model works in local time, so a UTC-stamped "
            "series puts the sun and the irradiance out of phase by the "
            "whole UTC offset."
        ))
    return out


def check_wind_yield(unit_series, rated_kw, hours=HOURS_PER_YEAR):
    out = []
    if not unit_series or not rated_kw:
        return out
    cf = sum(unit_series) / (float(rated_kw) * len(unit_series))
    out.append(_finding(
        "info", "wind_cf",
        f"Modelled wind capacity factor {100 * cf:.1f}%."
    ))
    if cf < WIND_CF_MIN:
        out.append(_finding(
            "warning", "wind_cf_low",
            f"A capacity factor of {100 * cf:.1f}% means the turbine barely "
            f"turns. Check the hub height against the height the wind data "
            f"was measured at - extrapolating a 10 m series down from an "
            f"assumed 80 m is a common way to lose most of the resource - "
            f"and check the cut-in speed against the site's mean wind."
        ))
    elif cf > WIND_CF_MAX:
        out.append(_finding(
            "warning", "wind_cf_high",
            f"A capacity factor of {100 * cf:.1f}% is above the best "
            f"offshore sites. The wind series may be in km/h rather than "
            f"m/s, or the power curve may not match the machine."
        ))
    return out


# ---------------------------------------------------------------------
# Component sizing against the site
# ---------------------------------------------------------------------

def check_component_scale(load, components, grid=None):
    """
    Are the unit sizes the optimiser must buy in sensible for this site?

    The optimiser buys whole units. If the unit is a quarter of the site's
    peak demand, the search has four usable settings and the answer is
    quantised into uselessness; if the unit is larger than the peak, the
    only choices are nothing and far too much. Neither is visible in the
    result - it just looks like a coarse or extreme recommendation.
    """
    out = []
    if not load:
        return out
    peak = max(load)
    mean = sum(load) / len(load)
    daily = mean * 24.0
    if peak <= 0:
        return out

    pv = components.get("pv")
    if pv is not None and getattr(pv, "capacity_kwp", 0) > 0:
        unit = pv.capacity_kwp
        if unit > peak:
            out.append(_finding(
                "warning", "pv_unit_oversized",
                f"The PV block is {unit:g} kWp but the site peaks at "
                f"{_fmt_power(peak)}. The optimiser can only choose whole "
                f"blocks, so its options are nothing, one block already "
                f"larger than the whole site, or multiples of that. Use a "
                f"block of roughly {max(0.5, round(peak / 10, 1)):g} kWp "
                f"and scale the per-unit costs to match."
            ))
        elif unit > peak * 0.25:
            out.append(_finding(
                "info", "pv_unit_coarse",
                f"The PV block ({unit:g} kWp) is {100 * unit / peak:.0f}% of "
                f"peak demand, so the array can only be sized in steps that "
                f"large. A smaller block gives a finer answer at some cost "
                f"in search time."
            ))

    bat = components.get("battery")
    if bat is not None and getattr(bat, "nominal_energy_kwh", 0) > 0:
        unit_kwh = bat.nominal_energy_kwh
        unit_kw = getattr(bat, "nominal_power_kw", 0.0) or 0.0
        if daily > 0 and unit_kwh > daily:
            out.append(_finding(
                "warning", "battery_unit_oversized",
                f"The battery block is {unit_kwh:g} kWh, more than the "
                f"site's entire daily consumption ({daily:,.0f} kWh). One "
                f"block is already more storage than the load can cycle, so "
                f"the search cannot express a sensible size. Try about "
                f"{max(1.0, round(daily / 8)):g} kWh."
            ))
        if unit_kwh > 0 and unit_kw > 0:
            c_rate = unit_kw / unit_kwh
            if c_rate > 2.0:
                out.append(_finding(
                    "warning", "battery_c_rate_high",
                    f"The block delivers {unit_kw:g} kW from {unit_kwh:g} kWh "
                    f"- a {c_rate:.1f} C discharge, which only specialised "
                    f"power cells sustain. Check the two figures are not "
                    f"swapped."
                ))
            elif c_rate < 0.1:
                out.append(_finding(
                    "info", "battery_c_rate_low",
                    f"The block is rated {unit_kw:g} kW on {unit_kwh:g} kWh "
                    f"({c_rate:.2f} C, a {1 / c_rate:.0f}-hour battery). That "
                    f"is unusually slow; it will limit how much of the store "
                    f"the evening peak can reach."
                ))

    wind = components.get("wind")
    if wind is not None and getattr(wind, "rated_kw", 0) > 0:
        if wind.rated_kw > peak * 2.0:
            out.append(_finding(
                "warning", "wind_unit_oversized",
                f"One turbine is rated {_fmt_power(wind.rated_kw)} against a "
                f"site peak of {_fmt_power(peak)}. A single machine already "
                f"exceeds anything the site can use, so most of its output "
                f"is exported or curtailed."
            ))

    gen = components.get("genset")
    if gen is not None and getattr(gen, "rated_kw", 0) > 0:
        if gen.rated_kw > peak * 2.0:
            out.append(_finding(
                "warning", "genset_oversized",
                f"The generator is rated {_fmt_power(gen.rated_kw)} against a "
                f"peak of {_fmt_power(peak)}. It will spend its life below "
                f"its minimum load ratio, which wet-stacks a diesel and "
                f"wrecks it."
            ))

    if grid is not None and not getattr(grid, "is_islanded", False):
        limit = float(getattr(grid, "import_limit_kw", 0.0) or 0.0)
        if limit and limit < peak:
            out.append(_finding(
                "warning", "grid_below_peak",
                f"The import limit ({_fmt_power(limit)}) is below the site "
                f"peak ({_fmt_power(peak)}). The design must cover the "
                f"difference itself, which is a legitimate brief but a very "
                f"different one from a firm supply - say so deliberately."
            ))
        elif limit > peak * 20:
            out.append(_finding(
                "warning", "grid_far_above_peak",
                f"The import limit ({_fmt_power(limit)}) is {limit / peak:.0f} "
                f"times the site peak ({_fmt_power(peak)}). If the site is "
                f"smaller than intended, the connection was probably left at "
                f"a default from a larger example."
            ))
    return out


# ---------------------------------------------------------------------
# Economics, and the result
# ---------------------------------------------------------------------

def check_economics(econ, grid=None):
    out = []
    if econ is None:
        return out
    ir = econ.interest_rate
    er = econ.escalation_rate
    if ir <= er:
        out.append(_finding(
            "warning", "discount_below_escalation",
            f"The discount rate ({100 * ir:.1f}%) is not above the price "
            f"escalation rate ({100 * er:.1f}%). Future energy costs then "
            f"grow at least as fast as they are discounted, so the present "
            f"worth of the energy bill is dominated by the last years of the "
            f"project and the result is extremely sensitive to the project "
            f"life."
        ))
    if ir > 0.35:
        out.append(_finding(
            "warning", "discount_very_high",
            f"A discount rate of {100 * ir:.0f}% makes anything beyond about "
            f"five years worthless in present terms, so the study will "
            f"reject capital-intensive options almost regardless of their "
            f"merit. If this is a high-inflation economy, work in real terms "
            f"instead: a real discount rate with real (unescalated) prices."
        ))
    return out


def check_result(metrics, system, load, grid=None):
    """
    Post-run sanity. Cheap, and it catches the cases where every input was
    individually plausible and the answer is still not a design anyone would
    build.
    """
    out = []
    if not metrics:
        return out
    annual = sum(load) if load else 0.0
    peak = max(load) if load else 0.0

    lcoe = metrics.get("lcoe")
    if lcoe and lcoe not in (float("inf"),) and grid is not None:
        try:
            tariff = sum(grid.import_price) / len(grid.import_price)
        except TypeError:
            tariff = float(getattr(grid, "import_price", 0.0) or 0.0)
        if tariff > 0 and lcoe > tariff * 4:
            out.append(_finding(
                "warning", "lcoe_far_above_tariff",
                f"The levelised cost ({lcoe:.2f}/kWh) is more than four "
                f"times the import tariff ({tariff:.2f}/kWh). A design this "
                f"far from the grid's own price is being driven by a "
                f"constraint or an objective rather than by economics - "
                f"usually a renewable-fraction target, or a reliability "
                f"target that storage alone has to meet."
            ))

    gen_kw = getattr(system, "generation_capacity_kw", 0.0)
    if peak > 0 and gen_kw > peak * 8:
        out.append(_finding(
            "warning", "generation_far_above_peak",
            f"The design installs {_fmt_power(gen_kw)} of generation against "
            f"a peak demand of {_fmt_power(peak)} - {gen_kw / peak:.0f} times "
            f"over. Unless this is a deliberate export project, check the "
            f"modelled resource yield: a generation-to-peak ratio like this "
            f"is what an understated yield looks like from the outside."
        ))

    curt = metrics.get("curtailment_rate")
    if curt is not None and curt > 0.35:
        out.append(_finding(
            "warning", "curtailment_high",
            f"{100 * curt:.0f}% of the renewable output is curtailed. The "
            f"design is paying for generation it throws away; either "
            f"storage, export capacity or a smaller array is the better "
            f"answer, and if the optimiser still prefers this one, an "
            f"objective is pushing it there."
        ))
    return out


# ---------------------------------------------------------------------

def worst_level(findings):
    """The highest severity present, for a caller deciding whether to stop."""
    order = {"info": 0, "warning": 1, "error": 2}
    worst = "info"
    for f in findings or []:
        if order.get(f.get("level"), 0) > order[worst]:
            worst = f["level"]
    return worst
