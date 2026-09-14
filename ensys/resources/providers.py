"""
Geographic resource data providers.

Each provider turns a Location into resource series or summary statistics.
All of them are free and none needs an API key except Renewables.ninja,
which needs a free registration token.

Provider notes, because choosing the wrong one is the largest error source
in a sizing study:

  GLOBAL SOLAR ATLAS - long-term averages only (annual and 12 monthly
    values for GHI, DNI, DIF, GTI, PVOUT, optimum tilt, temperature,
    elevation). No hourly data. Excellent for screening and for validating
    an hourly dataset's annual total. Solargis data under the World Bank
    programme, CC BY 4.0.

  PVGIS - hourly time series (2005-2023 depending on the database) and TMY,
    from SARAH-3 for Europe/Africa/most of Asia and ERA5 elsewhere. This is
    the best free hourly solar source for most of the world and the one to
    prefer for a real study.

  NASA POWER - global hourly and daily, 1981-present, from MERRA-2. Coarser
    resolution (0.5 x 0.625 degrees) so it misses local terrain effects,
    but it covers everywhere and includes wind at 10 and 50 m.

  OPEN-METEO - ERA5 reanalysis, hourly, 1940-present, no key, generous
    limits. Good for wind at multiple heights and for long historical runs
    needed for inter-annual variability.

  RENEWABLES.NINJA - hourly PV and wind POWER (not just resource) from
    MERRA-2 with a turbine power curve applied. Free key, 50 requests/hour.
    This is the source of the project's existing NINJA.csv.

Transport: the engine must run both under CPython with urllib and inside
Pyodide where only the browser's fetch is available. `set_fetcher` lets the
host inject a transport; the default uses urllib and simply raises a clear
error where that is unavailable, rather than failing obscurely.
"""

from __future__ import annotations

import json

from .cache import ResourceCache

_FETCHER = None
DEFAULT_TIMEOUT = 45


class ProviderError(RuntimeError):
    """A provider could not supply usable data."""


# ---------------------------------------------------------------------
# Time reference
# ---------------------------------------------------------------------
#
# Every online provider here returns hourly values stamped in UTC. The
# solar-position model works in LOCAL STANDARD TIME - it subtracts the zone
# meridian to reach solar time - so a UTC-indexed series handed straight to
# it leaves the sun and the irradiance out of phase by the whole UTC offset.
# The symptom is not an obvious crash but a quietly low PV yield: hours that
# carry full irradiance are assigned a night-time zenith and zeroed, and the
# transposition runs on the wrong incidence angle for the rest. The size of
# the error is the size of the offset, so it is invisible in London and
# ruinous in Tehran or Adelaide.
#
# Every provider therefore converts to local standard time HERE, at the
# boundary, and stamps the result. Nothing downstream has to remember.

SOLAR_KEYS = ("ghi", "dni", "dhi", "temperature_c")


def _localise(out, location, keys):
    """
    Move a provider's UTC-stamped series onto local standard time, in place.

    Also records the shift so the report can state it. Wind-speed series are
    included: they are used with temperature for air density and with the
    load profile for dispatch, both of which are local-clock quantities.
    """
    from ..timeseries import shift_utc_to_local

    off = float(getattr(location, "utc_offset_hours", 0.0) or 0.0)
    for k in keys:
        if out.get(k):
            out[k] = shift_utc_to_local(out[k], off)
    out["time_reference"] = "local_standard_time"
    out["utc_offset_applied_hours"] = off
    if getattr(location, "utc_offset_estimated", False):
        out.setdefault("warnings", []).append(
            f"The site's UTC offset was not given, so {off:+.1f} h was "
            f"estimated from longitude and used to align the resource data "
            f"with local time. In a country spanning several longitudes on "
            f"one legal time zone this estimate can be an hour or more out; "
            f"set the offset explicitly for a final study."
        )
    return out


def _tz_key(location):
    """Cache-key fragment, so two sites at one point but different offsets
    cannot serve each other a wrongly shifted year."""
    return f"tz{float(getattr(location, 'utc_offset_hours', 0.0) or 0.0):+.2f}"


def set_fetcher(fn):
    """
    Install a transport: fn(url, timeout) -> text.

    Under Pyodide the host passes a wrapper around the browser fetch. Under
    CPython the default urllib implementation is used.
    """
    global _FETCHER
    _FETCHER = fn


def _fetch(url, timeout=DEFAULT_TIMEOUT):
    if _FETCHER is not None:
        return _FETCHER(url, timeout)
    try:
        from urllib.request import urlopen, Request
        from urllib.error import URLError, HTTPError
    except ImportError:  # pragma: no cover - Pyodide without a fetcher
        raise ProviderError(
            "No network transport available. In a browser build, call "
            "providers.set_fetcher() with a fetch wrapper before requesting "
            "online data, or import the data from a file instead."
        )
    req = Request(url, headers={"User-Agent": "HES/0.1 (sizing tool)"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except HTTPError as e:
        raise ProviderError(
            f"{url.split('?')[0]} returned HTTP {e.code}. "
            "The service may be rate-limiting or temporarily unavailable."
        ) from e
    except URLError as e:
        raise ProviderError(
            f"Could not reach {url.split('?')[0]}: {e.reason}. "
            "Check the network connection, or supply the data from a file."
        ) from e


# =====================================================================
# Global Solar Atlas
# =====================================================================

GSA_URL = "https://api.globalsolaratlas.info/data/lta?loc={lat},{lon}"


def global_solar_atlas(location, cache=None, timeout=DEFAULT_TIMEOUT):
    """
    Long-term average solar resource and PV yield at a point.

    Returns a dict with annual and monthly values. Keys follow the Atlas:
      GHI, DNI, DIF  kWh/m2/year
      GTI_opta       kWh/m2/year at the optimum tilt
      PVOUT_csi      kWh/kWp/year for a crystalline-silicon system
      OPTA           optimum tilt, degrees
      TEMP           mean air temperature, degC
      ELE            elevation, metres
    """
    key = f"gsa:{location.latitude:.4f},{location.longitude:.4f}"
    if cache:
        hit = cache.get(key)
        if hit is not None:
            return hit

    url = GSA_URL.format(lat=location.latitude, lon=location.longitude)
    raw = _fetch(url, timeout)
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise ProviderError(
            "Global Solar Atlas returned a response that is not JSON. "
            "The endpoint may have changed."
        ) from e

    annual = data.get("annual", {}).get("data", {})
    monthly = data.get("monthly", {}).get("data", {})
    if not annual:
        raise ProviderError(
            f"Global Solar Atlas has no data for "
            f"{location.latitude:.4f}, {location.longitude:.4f}. "
            "The point may be over open ocean or outside coverage."
        )

    out = {
        "provider": "Global Solar Atlas (Solargis / World Bank)",
        "licence": "CC BY 4.0",
        "latitude": location.latitude,
        "longitude": location.longitude,
        "annual": annual,
        "monthly": monthly,
        "metadata": data.get("annual", {}).get("metadata", {}),
        "ghi_kwh_m2_year": annual.get("GHI"),
        "dni_kwh_m2_year": annual.get("DNI"),
        "dif_kwh_m2_year": annual.get("DIF"),
        "gti_opt_kwh_m2_year": annual.get("GTI_opta"),
        "pvout_kwh_kwp_year": annual.get("PVOUT_csi"),
        "optimum_tilt_deg": annual.get("OPTA"),
        "mean_temperature_c": annual.get("TEMP"),
        "elevation_m": annual.get("ELE"),
        "monthly_ghi": monthly.get("GHI"),
        "monthly_dni": monthly.get("DNI"),
        "monthly_dif": monthly.get("DIF"),
        "monthly_temperature": monthly.get("TEMP"),
        "monthly_pvout": monthly.get("PVOUT_csi"),
        "hourly": False,
    }
    if cache:
        cache.put(key, out)
    return out


# =====================================================================
# PVGIS
# =====================================================================

PVGIS_BASE = "https://re.jrc.ec.europa.eu/api/v5_3"


def pvgis_hourly(location, start_year=2020, end_year=2020, cache=None,
                 timeout=90, database=None):
    """
    Hourly irradiance and temperature from PVGIS.

    Returns GHI (reconstructed from components), DNI, DHI, temperature and
    10 m wind speed as 8760-hour series.

    PVGIS returns beam normal (Gb(n)), diffuse horizontal (Gd(h)) and
    reflected (Gr(h)) on the *horizontal* plane when angles are zero, so GHI
    is rebuilt as Gb(n)*cos(zenith) + Gd(h). Taking Gb(n) directly as GHI is
    a common and serious error.
    """
    key = (
        f"pvgis:{location.latitude:.4f},{location.longitude:.4f}:"
        f"{start_year}-{end_year}:{_tz_key(location)}"
    )
    if cache:
        hit = cache.get(key)
        if hit is not None:
            return hit

    url = (
        f"{PVGIS_BASE}/seriescalc?lat={location.latitude}"
        f"&lon={location.longitude}&startyear={start_year}"
        f"&endyear={end_year}&outputformat=json"
        f"&angle=0&aspect=0&pvcalculation=0"
    )
    if database:
        url += f"&raddatabase={database}"

    raw = _fetch(url, timeout)
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise ProviderError(
            "PVGIS returned a response that is not JSON. The coordinates may "
            "be outside its coverage (it excludes some high latitudes)."
        ) from e

    records = data.get("outputs", {}).get("hourly", [])
    if not records:
        raise ProviderError(
            f"PVGIS returned no hourly data for {location.latitude:.4f}, "
            f"{location.longitude:.4f}."
        )

    from ..models.pv import solar_position
    from ..timeseries import coerce_year
    import math

    # PVGIS timestamps are UTC. The horizontal beam component is rebuilt
    # with cos(zenith), so the components must be moved onto local time
    # FIRST - reconstructing GHI against a zenith from a different instant
    # is the same misalignment twice over, and it removes far more energy
    # than the shift alone.
    dni, dhi, temp, wind = [], [], [], []
    for rec in records:
        dni.append(float(rec.get("Gb(n)", 0.0) or 0.0))
        dhi.append(float(rec.get("Gd(h)", 0.0) or 0.0))
        temp.append(float(rec.get("T2m", 20.0) or 20.0))
        wind.append(float(rec.get("WS10m", 1.0) or 1.0))

    dni, _ = coerce_year(dni, "PVGIS DNI")
    dhi, _ = coerce_year(dhi, "PVGIS DHI")
    temp, _ = coerce_year(temp, "PVGIS temperature")
    wind, _ = coerce_year(wind, "PVGIS wind")

    staged = _localise(
        {"dni": dni, "dhi": dhi, "temperature_c": temp,
         "wind_speed_10m": wind},
        location, ("dni", "dhi", "temperature_c", "wind_speed_10m"),
    )
    dni, dhi = staged["dni"], staged["dhi"]
    temp, wind = staged["temperature_c"], staged["wind_speed_10m"]

    ghi = []
    for i in range(len(dni)):
        zen, _az = solar_position(i % 8760, location)
        cz = math.cos(math.radians(zen))
        ghi.append(dni[i] * max(0.0, cz) + dhi[i])

    out = {
        "provider": "PVGIS (European Commission JRC)",
        "licence": "Free reuse with attribution",
        "database": data.get("inputs", {}).get("meteo_data", {}).get(
            "radiation_db", database or "default"
        ),
        "year": start_year,
        "ghi": ghi,
        "dni": dni,
        "dhi": dhi,
        "temperature_c": temp,
        "wind_speed_10m": wind,
        "annual_ghi_kwh_m2": sum(ghi) / 1000.0,
        "hourly": True,
        "time_reference": staged["time_reference"],
        "utc_offset_applied_hours": staged["utc_offset_applied_hours"],
        "warnings": staged.get("warnings", []),
    }
    if cache:
        cache.put(key, out)
    return out


def pvgis_tmy(location, cache=None, timeout=90):
    """
    Typical Meteorological Year from PVGIS.

    A TMY is the right input for a design study: it is a synthetic year
    assembled from the most representative months, so it captures typical
    conditions rather than one year's weather. Use a real year only when
    studying inter-annual variability.
    """
    key = (
        f"pvgis_tmy:{location.latitude:.4f},{location.longitude:.4f}:"
        f"{_tz_key(location)}"
    )
    if cache:
        hit = cache.get(key)
        if hit is not None:
            return hit

    url = (
        f"{PVGIS_BASE}/tmy?lat={location.latitude}&lon={location.longitude}"
        f"&outputformat=json"
    )
    raw = _fetch(url, timeout)
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise ProviderError("PVGIS TMY returned a non-JSON response.") from e

    records = data.get("outputs", {}).get("tmy_hourly", [])
    if not records:
        raise ProviderError("PVGIS returned no TMY data for this point.")

    from ..models.pv import solar_position
    from ..timeseries import coerce_year
    import math

    # As in pvgis_hourly: shift to local standard time before rebuilding
    # GHI, so the zenith and the irradiance describe the same instant.
    dni, dhi, temp, wind = [], [], [], []
    for rec in records:
        dni.append(float(rec.get("Gb(n)", 0.0) or 0.0))
        dhi.append(float(rec.get("Gd(h)", 0.0) or 0.0))
        temp.append(float(rec.get("T2m", 20.0) or 20.0))
        wind.append(float(rec.get("WS10m", 1.0) or 1.0))

    dni, _ = coerce_year(dni, "TMY DNI")
    dhi, _ = coerce_year(dhi, "TMY DHI")
    temp, _ = coerce_year(temp, "TMY temperature")
    wind, _ = coerce_year(wind, "TMY wind")

    staged = _localise(
        {"dni": dni, "dhi": dhi, "temperature_c": temp,
         "wind_speed_10m": wind},
        location, ("dni", "dhi", "temperature_c", "wind_speed_10m"),
    )
    dni, dhi = staged["dni"], staged["dhi"]
    temp, wind = staged["temperature_c"], staged["wind_speed_10m"]

    ghi = []
    for i in range(len(dni)):
        zen, _ = solar_position(i % 8760, location)
        cz = math.cos(math.radians(zen))
        ghi.append(dni[i] * max(0.0, cz) + dhi[i])

    out = {
        "provider": "PVGIS TMY (European Commission JRC)",
        "licence": "Free reuse with attribution",
        "ghi": ghi, "dni": dni, "dhi": dhi,
        "temperature_c": temp, "wind_speed_10m": wind,
        "annual_ghi_kwh_m2": sum(ghi) / 1000.0,
        "hourly": True, "tmy": True,
        "time_reference": staged["time_reference"],
        "utc_offset_applied_hours": staged["utc_offset_applied_hours"],
        "warnings": staged.get("warnings", []),
    }
    if cache:
        cache.put(key, out)
    return out


# =====================================================================
# NASA POWER
# =====================================================================

POWER_BASE = "https://power.larc.nasa.gov/api/temporal"


def nasa_power_hourly(location, year=2020, cache=None, timeout=120):
    """
    Hourly solar and wind from NASA POWER (MERRA-2).

    Global coverage including ocean, which makes it the fallback when PVGIS
    has no data. Spatial resolution is coarse, so for a site in complex
    terrain treat the wind speed with caution.
    """
    key = (
        f"power:{location.latitude:.4f},{location.longitude:.4f}:{year}:"
        f"{_tz_key(location)}"
    )
    if cache:
        hit = cache.get(key)
        if hit is not None:
            return hit

    params = "ALLSKY_SFC_SW_DWN,ALLSKY_SFC_SW_DNI,ALLSKY_SFC_SW_DIFF,T2M,WS10M,WS50M"
    url = (
        f"{POWER_BASE}/hourly/point?parameters={params}&community=RE"
        f"&longitude={location.longitude}&latitude={location.latitude}"
        f"&start={year}0101&end={year}1231&format=JSON"
    )
    raw = _fetch(url, timeout)
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise ProviderError("NASA POWER returned a non-JSON response.") from e

    props = data.get("properties", {}).get("parameter", {})
    if not props:
        raise ProviderError(
            "NASA POWER returned no parameters. The date range may be "
            "outside the available record."
        )

    from ..timeseries import coerce_year

    def series(name, default=0.0):
        d = props.get(name, {})
        keys = sorted(d.keys())
        vals = []
        for k in keys:
            v = d[k]
            # POWER uses -999 as its fill value; treating it as data
            # produces spectacular nonsense.
            vals.append(default if v is None or v <= -900 else float(v))
        return vals

    ghi = series("ALLSKY_SFC_SW_DWN")
    dni = series("ALLSKY_SFC_SW_DNI")
    dhi = series("ALLSKY_SFC_SW_DIFF")
    temp = series("T2M", 20.0)
    ws10 = series("WS10M", 1.0)
    ws50 = series("WS50M", 1.0)

    ghi, _ = coerce_year(ghi, "POWER GHI", fill=0.0)
    dni, _ = coerce_year(dni, "POWER DNI", fill=0.0)
    dhi, _ = coerce_year(dhi, "POWER DHI", fill=0.0)
    temp, _ = coerce_year(temp, "POWER temperature", fill=20.0)
    ws10, _ = coerce_year(ws10, "POWER wind 10 m", fill=1.0)
    ws50, _ = coerce_year(ws50, "POWER wind 50 m", fill=1.0)

    out = {
        "provider": "NASA POWER (MERRA-2)",
        "licence": "Public domain",
        "year": year,
        "ghi": ghi, "dni": dni, "dhi": dhi,
        "temperature_c": temp,
        "wind_speed_10m": ws10,
        "wind_speed_50m": ws50,
        "hourly": True,
    }

    # POWER hourly values are stamped in UTC.
    _localise(
        out, location,
        SOLAR_KEYS + ("wind_speed_10m", "wind_speed_50m"),
    )
    out["annual_ghi_kwh_m2"] = sum(out["ghi"]) / 1000.0

    if cache:
        cache.put(key, out)
    return out


# =====================================================================
# Open-Meteo (ERA5 archive)
# =====================================================================

OPENMETEO_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"


def open_meteo(location, year=2020, cache=None, timeout=90,
               wind_heights=(10, 100)):
    """
    Hourly ERA5 reanalysis from Open-Meteo: irradiance, temperature and wind
    at several heights. No key, and the archive reaches back to 1940, which
    makes it the practical source for multi-year variability studies.
    """
    key = (
        f"openmeteo:{location.latitude:.4f},{location.longitude:.4f}:{year}:"
        f"{_tz_key(location)}"
    )
    if cache:
        hit = cache.get(key)
        if hit is not None:
            return hit

    wind_vars = ",".join(f"wind_speed_{h}m" for h in wind_heights)
    url = (
        f"{OPENMETEO_ARCHIVE}?latitude={location.latitude}"
        f"&longitude={location.longitude}"
        f"&start_date={year}-01-01&end_date={year}-12-31"
        f"&hourly=shortwave_radiation,direct_normal_irradiance,"
        f"diffuse_radiation,temperature_2m,{wind_vars}"
        f"&timezone=UTC"
    )
    raw = _fetch(url, timeout)
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise ProviderError("Open-Meteo returned a non-JSON response.") from e

    h = data.get("hourly")
    if not h:
        raise ProviderError("Open-Meteo returned no hourly block.")

    from ..timeseries import coerce_year

    def pick(name, default=0.0):
        vals = h.get(name) or []
        return [default if v is None else float(v) for v in vals]

    ghi, _ = coerce_year(pick("shortwave_radiation"), "GHI", fill=0.0)
    dni, _ = coerce_year(pick("direct_normal_irradiance"), "DNI", fill=0.0)
    dhi, _ = coerce_year(pick("diffuse_radiation"), "DHI", fill=0.0)
    temp, _ = coerce_year(pick("temperature_2m", 20.0), "temperature", fill=20.0)

    out = {
        "provider": "Open-Meteo (ERA5 reanalysis)",
        "licence": "CC BY 4.0",
        "year": year,
        "ghi": ghi, "dni": dni, "dhi": dhi,
        "temperature_c": temp,
        "annual_ghi_kwh_m2": sum(ghi) / 1000.0,
        "hourly": True,
    }
    for hh in wind_heights:
        s, _ = coerce_year(
            pick(f"wind_speed_{hh}m", 1.0), f"wind {hh} m", fill=1.0
        )
        # Open-Meteo reports wind in km/h by default.
        out[f"wind_speed_{hh}m"] = [v / 3.6 for v in s]

    # The request asks for timezone=UTC, so every series is UTC-stamped.
    _localise(
        out, location,
        SOLAR_KEYS + tuple(f"wind_speed_{hh}m" for hh in wind_heights),
    )
    out["annual_ghi_kwh_m2"] = sum(out["ghi"]) / 1000.0

    if cache:
        cache.put(key, out)
    return out


# =====================================================================
# Renewables.ninja
# =====================================================================

NINJA_BASE = "https://www.renewables.ninja/api"


def renewables_ninja_wind(location, token, turbine="Vestas V90 2000",
                          height=80, year=2019, capacity=1.0,
                          cache=None, timeout=120):
    """
    Hourly wind power and speed from Renewables.ninja.

    Needs a free API token. Rate-limited to about 50 requests per hour, so
    results are always cached.
    """
    key = (
        f"ninja_wind:{location.latitude:.4f},{location.longitude:.4f}:"
        f"{year}:{turbine}:{height}"
    )
    if cache:
        hit = cache.get(key)
        if hit is not None:
            return hit

    from urllib.parse import quote

    url = (
        f"{NINJA_BASE}/data/wind?lat={location.latitude}"
        f"&lon={location.longitude}&date_from={year}-01-01"
        f"&date_to={year}-12-31&capacity={capacity}&height={height}"
        f"&turbine={quote(turbine)}&format=json&raw=true"
        f"&header=true&local_time=false"
    )

    def fetch_with_token(u, t):
        if _FETCHER is not None:
            return _FETCHER(u, t)
        from urllib.request import urlopen, Request

        req = Request(u, headers={"Authorization": f"Token {token}"})
        with urlopen(req, timeout=t) as resp:
            return resp.read().decode("utf-8")

    raw = fetch_with_token(url, timeout)
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise ProviderError(
            "Renewables.ninja returned a non-JSON response. The token may be "
            "invalid or the hourly rate limit reached."
        ) from e

    rows = data.get("data", {})
    keys = sorted(rows.keys())
    speed = [float(rows[k].get("wind_speed", 0.0)) for k in keys]
    power = [float(rows[k].get("electricity", 0.0)) for k in keys]

    from ..timeseries import coerce_year

    speed, _ = coerce_year(speed, "ninja wind speed", fill=0.0)
    power, _ = coerce_year(power, "ninja power", fill=0.0)

    out = {
        "provider": "Renewables.ninja (MERRA-2)",
        "licence": "CC BY-NC 4.0 - non-commercial use only",
        "turbine": turbine,
        "height_m": height,
        "year": year,
        "wind_speed": speed,
        "power_per_kw": power,
        "hourly": True,
    }
    if cache:
        cache.put(key, out)
    return out


# =====================================================================
# Orchestration
# =====================================================================

PROVIDERS = {
    "global_solar_atlas": global_solar_atlas,
    "pvgis_tmy": pvgis_tmy,
    "pvgis_hourly": pvgis_hourly,
    "nasa_power": nasa_power_hourly,
    "open_meteo": open_meteo,
}


def fetch_resources(location, prefer=("pvgis_tmy", "open_meteo", "nasa_power"),
                    cache_dir=None, year=2020, verbose=False):
    """
    Get an hourly resource year, trying providers in order until one works.

    Always also fetches the Global Solar Atlas long-term averages, because
    they cost one small request and give an independent check on the hourly
    dataset's annual total. A disagreement above about 10% means the hourly
    source is not representative of the site, and the report says so rather
    than quietly proceeding.

    Returns a dict with the hourly series plus a `provenance` block naming
    every source used, which is what makes the study auditable.
    """
    cache = ResourceCache(cache_dir) if cache_dir else None
    provenance = {"attempts": [], "location": location.to_dict()}

    lta = None
    try:
        lta = global_solar_atlas(location, cache=cache)
        provenance["attempts"].append(
            {"provider": "global_solar_atlas", "status": "ok"}
        )
    except ProviderError as e:
        provenance["attempts"].append(
            {"provider": "global_solar_atlas", "status": "failed", "error": str(e)}
        )

    hourly = None
    for name in prefer:
        fn = PROVIDERS.get(name)
        if not fn:
            continue
        try:
            if name in ("pvgis_hourly", "nasa_power", "open_meteo"):
                hourly = fn(location, year=year, cache=cache)
            else:
                hourly = fn(location, cache=cache)
            provenance["attempts"].append({"provider": name, "status": "ok"})
            provenance["hourly_source"] = hourly["provider"]
            break
        except Exception as e:
            provenance["attempts"].append(
                {"provider": name, "status": "failed", "error": str(e)}
            )

    if hourly is None:
        if lta is None:
            raise ProviderError(
                "No resource provider could be reached and no long-term "
                "averages were retrieved. Supply the resource data from a "
                "file instead - the import template accepts hourly GHI, wind "
                "speed and temperature."
            )
        from .synth import synthesise_year

        hourly = synthesise_year(location, lta)
        provenance["attempts"].append(
            {"provider": "synthetic", "status": "ok",
             "note": "Hourly series synthesised from Global Solar Atlas "
                     "monthly averages; suitable for screening only."}
        )
        provenance["hourly_source"] = "Synthetic (from monthly averages)"

    out = dict(hourly)
    out["long_term_average"] = lta
    out["provenance"] = provenance

    # Cross-check the hourly annual total against the long-term average.
    if lta and lta.get("ghi_kwh_m2_year") and out.get("annual_ghi_kwh_m2"):
        ref = lta["ghi_kwh_m2_year"]
        got = out["annual_ghi_kwh_m2"]
        dev = (got - ref) / ref if ref else 0.0
        out["ghi_deviation_from_lta"] = dev
        if abs(dev) > 0.10:
            out.setdefault("warnings", []).append(
                f"The hourly dataset's annual GHI ({got:.0f} kWh/m2) differs "
                f"from the Global Solar Atlas long-term average "
                f"({ref:.0f} kWh/m2) by {100 * dev:+.1f}%. Either the chosen "
                f"year was unrepresentative, or the datasets disagree at this "
                f"location. Consider using a TMY."
            )
    return out
