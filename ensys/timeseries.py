"""
Hourly time-series container.

The whole engine speaks in 8760-element lists of floats. This module gives
that convention a name, a few safe constructors and the statistics the
reports need, without pulling in pandas.

Leap years are handled by convention: a 8784-hour input is truncated to 8760
by dropping 29 February, because every downstream economic calculation is
expressed per standard year. The drop is recorded so the report can say so.
"""

from __future__ import annotations

import math

HOURS_PER_YEAR = 8760
HOURS_PER_DAY = 24
DAYS_PER_YEAR = 365

# Cumulative day-of-year at the start of each month, non-leap.
_MONTH_START_DAY = (0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334, 365)
MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


class TimeSeriesError(ValueError):
    """Raised when a series cannot be coerced to a usable 8760-hour year."""


def hour_of_day(t: int) -> int:
    """Hour of day (0-23) for absolute hour index t."""
    return t % HOURS_PER_DAY


def day_of_year(t: int) -> int:
    """Day of year (1-365) for absolute hour index t."""
    return t // HOURS_PER_DAY + 1


def month_of(t: int) -> int:
    """Month (1-12) for absolute hour index t."""
    d = t // HOURS_PER_DAY
    for m in range(12):
        if d < _MONTH_START_DAY[m + 1]:
            return m + 1
    return 12


def month_index() -> list:
    """Month number (1-12) for every hour of the year. Cached by the caller."""
    return [month_of(t) for t in range(HOURS_PER_YEAR)]


def coerce_year(values, name="series", fill=None):
    """
    Coerce an arbitrary sequence into exactly 8760 floats.

    Accepts:
      * 8760 values  - used as-is
      * 8784 values  - leap year, 29 February dropped
      * 365 values   - daily, expanded by repeating each value 24 times
      * 12 values    - monthly, expanded across the hours of each month
      * 1 value      - constant
    Anything else raises, unless `fill` is given, in which case the series is
    padded or truncated to length and a note is returned.

    Returns (values, note) where note is a human-readable string or None.
    """
    v = [float(x) for x in values]
    n = len(v)

    if n == HOURS_PER_YEAR:
        return v, None

    if n == 8784:
        # Drop 29 February: day 60 of a leap year, hours 1416..1439.
        out = v[:1416] + v[1440:]
        return out, f"{name}: leap year input, 29 February dropped"

    if n == DAYS_PER_YEAR:
        out = []
        for d in v:
            out.extend([d] * HOURS_PER_DAY)
        return out, f"{name}: daily input expanded to hourly (step-wise)"

    if n == 12:
        out = []
        for m in range(12):
            days = _MONTH_START_DAY[m + 1] - _MONTH_START_DAY[m]
            out.extend([v[m]] * days * HOURS_PER_DAY)
        return out, f"{name}: monthly input expanded to hourly (step-wise)"

    if n == 1:
        return v * HOURS_PER_YEAR, f"{name}: constant value applied to all hours"

    if fill is not None:
        if n > HOURS_PER_YEAR:
            return v[:HOURS_PER_YEAR], f"{name}: truncated from {n} to 8760 hours"
        out = v + [float(fill)] * (HOURS_PER_YEAR - n)
        return out, f"{name}: padded from {n} to 8760 hours with {fill}"

    raise TimeSeriesError(
        f"{name}: expected 8760, 8784, 365, 12 or 1 values, got {n}. "
        "Provide a full hourly year, or daily/monthly values to be expanded."
    )


def shift_utc_to_local(series, utc_offset_hours):
    """
    Re-index a UTC-timestamped hourly year onto local standard time.

    Every online provider returns hourly values stamped in UTC, while the
    solar-position model in `models.pv` works in local standard time (it
    subtracts the zone meridian to reach solar time). Feeding one to the
    other leaves the sun and the irradiance out of phase by the UTC offset,
    which silently destroys PV yield: the model computes a night-time zenith
    for hours that actually carry full irradiance and zeroes them. The error
    grows with the offset - a few per cent in western Europe, 30% in Iran,
    an order of magnitude in eastern Australia - so it is large enough to
    dominate a sizing study and small enough at the author's own longitude
    to go unnoticed.

    Local hour t holds the value the provider stamped at UTC hour
    t - offset. Fractional offsets (+3:30, +9:30, +5:45) are interpolated
    linearly between the two neighbouring UTC hours rather than rounded,
    because rounding half an hour reintroduces a fifth of the error this
    function exists to remove.

    The year is treated as circular, which is correct to within one day at
    the year boundary and is the standard convention for a typical year.
    """
    n = len(series)
    if n == 0:
        return []
    off = float(utc_offset_hours or 0.0)
    if abs(off) < 1e-9:
        return [float(v) for v in series]

    whole = int(math.floor(off))
    frac = off - whole
    out = []
    for t in range(n):
        a = float(series[(t - whole) % n])
        if frac > 1e-9:
            b = float(series[(t - whole - 1) % n])
            out.append(a * (1.0 - frac) + b * frac)
        else:
            out.append(a)
    return out


def constant(value):
    """A flat 8760-hour series."""
    return [float(value)] * HOURS_PER_YEAR


def scale(series, factor):
    """Element-wise multiply by a scalar."""
    f = float(factor)
    return [x * f for x in series]


def add(a, b):
    """Element-wise sum of two equal-length series."""
    return [x + y for x, y in zip(a, b)]


def subtract(a, b):
    """Element-wise difference."""
    return [x - y for x, y in zip(a, b)]


def clip(series, lo=None, hi=None):
    """Clamp every element into [lo, hi]."""
    out = []
    for x in series:
        if lo is not None and x < lo:
            x = lo
        if hi is not None and x > hi:
            x = hi
        out.append(x)
    return out


def energy_kwh(series_kw, dt_h=1.0):
    """Total energy in kWh for a power series in kW."""
    return sum(series_kw) * dt_h


def stats(series):
    """Descriptive statistics used throughout the reports."""
    n = len(series)
    if n == 0:
        return {"n": 0}
    s = sorted(series)
    total = sum(series)
    mean = total / n

    def pct(p):
        if n == 1:
            return s[0]
        k = (n - 1) * p
        lo = int(math.floor(k))
        hi = int(math.ceil(k))
        if lo == hi:
            return s[lo]
        return s[lo] * (hi - k) + s[hi] * (k - lo)

    var = sum((x - mean) ** 2 for x in series) / n
    return {
        "n": n,
        "sum": total,
        "mean": mean,
        "min": s[0],
        "max": s[-1],
        "std": math.sqrt(var),
        "p05": pct(0.05),
        "median": pct(0.50),
        "p95": pct(0.95),
        "load_factor": (mean / s[-1]) if s[-1] > 0 else 0.0,
    }


def monthly_totals(series, months=None):
    """Sum a series into 12 monthly totals."""
    if months is None:
        months = month_index()
    out = [0.0] * 12
    for t, x in enumerate(series):
        out[months[t] - 1] += x
    return out


def monthly_means(series, months=None):
    """Mean of a series within each of the 12 months."""
    if months is None:
        months = month_index()
    tot = [0.0] * 12
    cnt = [0] * 12
    for t, x in enumerate(series):
        m = months[t] - 1
        tot[m] += x
        cnt[m] += 1
    return [tot[i] / cnt[i] if cnt[i] else 0.0 for i in range(12)]


def daily_profile(series):
    """Average value for each hour of the day, across the whole year."""
    tot = [0.0] * HOURS_PER_DAY
    cnt = [0] * HOURS_PER_DAY
    for t, x in enumerate(series):
        h = t % HOURS_PER_DAY
        tot[h] += x
        cnt[h] += 1
    return [tot[h] / cnt[h] if cnt[h] else 0.0 for h in range(HOURS_PER_DAY)]


def daily_totals(series):
    """365 daily sums."""
    return [
        sum(series[d * HOURS_PER_DAY:(d + 1) * HOURS_PER_DAY])
        for d in range(DAYS_PER_YEAR)
    ]


def duration_curve(series, points=200):
    """
    Load-duration style curve: values sorted descending, downsampled to
    `points` samples so a chart stays light.
    """
    s = sorted(series, reverse=True)
    n = len(s)
    if n <= points:
        return list(range(n)), s
    step = n / float(points)
    idx = [int(i * step) for i in range(points)]
    return idx, [s[i] for i in idx]


def peak_hour(series):
    """(index, value) of the maximum."""
    if not series:
        return None, None
    i = max(range(len(series)), key=lambda k: series[k])
    return i, series[i]


def rolling_max(series, window):
    """
    Maximum over a trailing window, used for sizing checks that must survive
    a sustained condition rather than a single spike.
    """
    if window <= 1:
        return list(series)
    out = []
    from collections import deque

    dq = deque()
    for i, x in enumerate(series):
        while dq and series[dq[-1]] <= x:
            dq.pop()
        dq.append(i)
        while dq[0] <= i - window:
            dq.popleft()
        out.append(series[dq[0]])
    return out
