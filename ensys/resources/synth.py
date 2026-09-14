"""
Synthetic hourly year from summary statistics.

When no hourly provider can be reached, a usable year can still be built
from twelve monthly means - which is exactly what the Global Solar Atlas
gives, and what a client can usually supply from a local met station.

This is a screening tool, and the code says so loudly. A synthetic year
has no cloud persistence: real weather clusters bad days together, and it is
those multi-day lulls that size a battery. A synthetic year will therefore
UNDERSIZE storage, often badly. The generated data carries a `synthetic`
flag so that every report built on it can carry the warning.

Method:
  * daily clear-sky profile from the solar geometry already in models.pv
  * scaled so each month's total matches the target monthly mean
  * day-to-day variability from a seeded lognormal clearness index, with a
    first-order autocorrelation so cloudy days at least cluster in pairs
  * wind from a Weibull distribution fitted to the target mean, with a
    diurnal shape and the same autocorrelation treatment
"""

from __future__ import annotations

import math
import random

from ..timeseries import HOURS_PER_YEAR, month_of, MONTH_NAMES
from ..models.pv import solar_position, extraterrestrial_irradiance, erbs_split

_DAYS_IN_MONTH = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def synthesise_year(location, lta=None, monthly_ghi=None,
                    monthly_temperature=None, mean_wind_speed=None,
                    weibull_k=2.0, seed=7):
    """
    Build an hourly resource year.

    `lta` is a Global Solar Atlas record; monthly values are taken from it
    unless overridden. `monthly_ghi` is in kWh/m2 per month.
    """
    rng = random.Random(seed)

    if monthly_ghi is None and lta:
        monthly_ghi = lta.get("monthly_ghi")
    if monthly_temperature is None and lta:
        monthly_temperature = lta.get("monthly_temperature")

    if not monthly_ghi or len(monthly_ghi) != 12:
        raise ValueError(
            "Twelve monthly GHI totals (kWh/m2) are required to synthesise a "
            "year. Supply them directly or provide a Global Solar Atlas record."
        )
    if not monthly_temperature or len(monthly_temperature) != 12:
        monthly_temperature = [20.0] * 12

    # ---- clear-sky envelope -------------------------------------------
    clear = []
    for t in range(HOURS_PER_YEAR):
        zen, _az = solar_position(t, location)
        cz = math.cos(math.radians(zen))
        if cz <= 0:
            clear.append(0.0)
            continue
        day = t // 24 + 1
        e0 = extraterrestrial_irradiance(day)
        # Simple Haurwitz clear-sky model: adequate as a shape, since the
        # monthly scaling below fixes the magnitude anyway.
        clear.append(max(0.0, 1098.0 * cz * math.exp(-0.059 / cz)))

    # ---- daily clearness with persistence ------------------------------
    # AR(1) in the normal domain, mapped to a bounded clearness index.
    # The innovation is scaled by sqrt(1 - phi^2) so the process has unit
    # stationary variance. Without that scaling an AR(1) with phi = 0.4 has
    # variance 1/(1-0.4^2) = 1.19, and the day-to-day spread comes out wider
    # than intended - the same mistake, more severely, would distort the
    # wind distribution below.
    kt_day = []
    phi_kt = 0.4
    sigma_kt = math.sqrt(1.0 - phi_kt * phi_kt)
    prev = rng.gauss(0.0, 1.0)
    for d in range(365):
        prev = phi_kt * prev + sigma_kt * rng.gauss(0.0, 1.0)
        kt = 0.68 + 0.16 * prev
        kt_day.append(max(0.20, min(0.85, kt)))

    raw = []
    for t in range(HOURS_PER_YEAR):
        raw.append(clear[t] * kt_day[t // 24])

    # ---- scale each month to its target total --------------------------
    ghi = list(raw)
    month_scale = []
    idx = 0
    for m in range(12):
        n_days = _DAYS_IN_MONTH[m]
        start = idx * 24
        end = (idx + n_days) * 24
        idx += n_days
        got = sum(raw[start:end]) / 1000.0        # kWh/m2 for the month
        target = float(monthly_ghi[m])
        s = (target / got) if got > 1e-9 else 0.0
        month_scale.append(s)
        for t in range(start, end):
            ghi[t] = raw[t] * s

    # ---- components ----------------------------------------------------
    dni, dhi = [], []
    for t in range(HOURS_PER_YEAR):
        zen, _ = solar_position(t, location)
        b, d = erbs_split(ghi[t], zen, t)
        dni.append(b)
        dhi.append(d)

    # ---- temperature ---------------------------------------------------
    temp = []
    for t in range(HOURS_PER_YEAR):
        m = month_of(t) - 1
        base = float(monthly_temperature[m])
        h = t % 24
        # Diurnal swing peaking mid-afternoon, amplitude scaled with the
        # clearness of the day: clear days swing more.
        amp = 6.0 * kt_day[t // 24] / 0.68
        temp.append(base + amp * math.sin(2 * math.pi * (h - 9) / 24.0))

    # ---- wind ----------------------------------------------------------
    wind = []
    if mean_wind_speed and mean_wind_speed > 0:
        k = float(weibull_k)
        c = float(mean_wind_speed) / math.gamma(1.0 + 1.0 / k)

        # Gaussian-copula sampling: an AR(1) latent series is mapped through
        # its own normal CDF to uniforms, then through the Weibull inverse
        # CDF. The innovation must be scaled by sqrt(1 - phi^2) so the latent
        # process has unit variance - otherwise the uniforms pile up at 0 and
        # 1, the right tail of the Weibull is over-sampled, and the mean wind
        # speed comes out well above the target.
        phi = 0.85
        sigma = math.sqrt(1.0 - phi * phi)
        prev = rng.gauss(0.0, 1.0)
        for t in range(HOURS_PER_YEAR):
            prev = phi * prev + sigma * rng.gauss(0.0, 1.0)
            u = 0.5 * (1.0 + math.erf(prev / math.sqrt(2.0)))
            u = min(0.999999, max(0.000001, u))
            v = c * (-math.log(1.0 - u)) ** (1.0 / k)
            # Mild diurnal shape: windier in the afternoon over land. This
            # has zero mean over the day, so it reshapes without biasing.
            h = t % 24
            v *= 1.0 + 0.15 * math.sin(2 * math.pi * (h - 10) / 24.0)
            wind.append(max(0.0, v))

        # Correct the residual sampling error so the series hits the stated
        # mean exactly. The user gave us a mean; returning a different one
        # would be silently substituting our sampling noise for their data.
        got = sum(wind) / len(wind)
        if got > 1e-9:
            adj = float(mean_wind_speed) / got
            wind = [v * adj for v in wind]
    else:
        wind = [0.0] * HOURS_PER_YEAR

    return {
        "provider": "Synthetic (generated from monthly averages)",
        "licence": "n/a",
        "synthetic": True,
        "hourly": True,
        # Built FROM the solar-position model, so it is already indexed on
        # local standard time and must not be shifted again.
        "time_reference": "local_standard_time",
        "utc_offset_applied_hours": 0.0,
        "ghi": ghi,
        "dni": dni,
        "dhi": dhi,
        "temperature_c": temp,
        "wind_speed_10m": wind,
        "annual_ghi_kwh_m2": sum(ghi) / 1000.0,
        "monthly_scale_factors": dict(zip(MONTH_NAMES, month_scale)),
        "warnings": [
            "This resource year is synthetic. It reproduces the correct "
            "monthly energy totals but not the real sequence of weather. "
            "Multi-day cloudy or calm periods are under-represented, so "
            "battery and generator sizes derived from it will be optimistic. "
            "Use it for screening and replace it with measured or reanalysis "
            "data before committing to a design.",
        ],
    }
