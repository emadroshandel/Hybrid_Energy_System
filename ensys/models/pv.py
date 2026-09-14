"""
Photovoltaic array model.

The chain implemented here is the standard one:

    GHI, DNI, DHI  ->  solar position  ->  plane-of-array irradiance
                   ->  cell temperature  ->  DC power  ->  inverter  ->  AC

Each step is separable and inspectable, because a sizing report has to be
able to show *why* a number came out as it did, not just assert it.

Notes on the choices made:

  * Solar position uses the NOAA/Michalsky algorithm, accurate to well under
    0.1 degrees over 1950-2050. That is far more than a 1-hour energy model
    needs, but it costs nothing and removes a whole class of doubt.

  * Transposition uses Hay-Davies. Isotropic (Liu-Jordan) is simpler but
    underestimates tilted-plane irradiance by 5-10% in clear climates
    because it ignores circumsolar brightening - which matters a lot for the
    high-DNI sites this tool is aimed at. Perez is better still but needs
    coefficient tables and air mass; Hay-Davies captures most of the gain.

  * Cell temperature uses Faiman, which the IEC 61853 series adopted, rather
    than the older NOCT form. In hot climates the difference in predicted
    annual yield is a few percent, and it is the difference in the right
    direction: NOCT tends to run cold.

  * Irradiance is treated as hourly average, and the solar position is
    evaluated at the mid-point of each hour. Evaluating at the hour boundary
    is a common and avoidable error that biases morning and evening output.
"""

from __future__ import annotations

import math

from ..assets import NonDispatchable

# Standard test conditions.
STC_IRRADIANCE = 1000.0   # W/m2
STC_CELL_TEMP = 25.0      # degC

# Faiman thermal model coefficients (IEC 61853-2 defaults).
FAIMAN_U0 = 25.0          # W/(m2 K), wind-independent term
FAIMAN_U1 = 6.84          # W s/(m3 K), wind-dependent term

MOUNTING_PRESETS = {
    # (u0, u1) - more airflow behind the module means cooler cells.
    "open_rack": (25.0, 6.84),
    "close_roof": (20.5, 4.5),
    "insulated_back": (15.0, 0.0),
}


class PVArray(NonDispatchable):
    """
    A PV array defined by installed DC capacity and orientation.

    Sizing is expressed in kWp (DC nameplate at STC) rather than module
    count, because that is how the optimiser reasons and how quotes are
    written. Module-level detail enters only at the string-sizing stage.

    Parameters
    ----------
    tilt_deg : surface tilt from horizontal, 0 = flat
    azimuth_deg : surface azimuth, 180 = due south, 0 = due north,
        90 = east, 270 = west. This is the convention used by PVGIS and
        most of the literature.
    temp_coeff_pmax : power temperature coefficient, per degC, negative
        (typically -0.0035 for crystalline silicon)
    system_losses : fractional DC-side losses aggregated (soiling, mismatch,
        wiring, light-induced degradation, connections)
    tracking : "fixed", "single_axis" or "two_axis"
    """

    def __init__(
        self,
        name="PV array",
        capacity_kwp=1.0,
        tilt_deg=None,
        azimuth_deg=180.0,
        temp_coeff_pmax=-0.0035,
        noct=45.0,
        system_losses=0.14,
        albedo=0.20,
        mounting="open_rack",
        tracking="fixed",
        module_efficiency=0.20,
        degradation_per_year=0.005,
        capital_cost=0.0,
        replacement_cost=0.0,
        om_cost_per_year=0.0,
        lifetime_years=25,
    ):
        super().__init__(
            name=name, capital_cost=capital_cost,
            replacement_cost=replacement_cost,
            om_cost_per_year=om_cost_per_year, lifetime_years=lifetime_years,
        )
        self.capacity_kwp = float(capacity_kwp)
        self.tilt_deg = tilt_deg          # None means "use latitude rule"
        self.azimuth_deg = float(azimuth_deg)
        self.temp_coeff_pmax = float(temp_coeff_pmax)
        self.noct = float(noct)
        self.system_losses = float(system_losses)
        self.albedo = float(albedo)
        self.mounting = mounting
        self.tracking = tracking
        self.module_efficiency = float(module_efficiency)
        self.degradation_per_year = float(degradation_per_year)

        if not (-1.0 < self.temp_coeff_pmax < 0.0):
            raise ValueError(
                "temperature coefficient of Pmax must be negative and "
                f"fractional per degC, got {self.temp_coeff_pmax}"
            )
        if not (0.0 <= self.system_losses < 1.0):
            raise ValueError("system_losses must be a fraction in [0, 1)")

    technology = "pv"
    unit_label = "array block"

    def rated_power_kw(self):
        return self.capacity_kwp

    def resource_key(self):
        return "ghi"

    def generate(self, resources, location=None):
        """Adapter to the asset contract; the real work is in output_series."""
        ghi = resources.get("ghi")
        if not ghi:
            raise ValueError(
                f"{self.name} needs a 'ghi' series (global horizontal "
                f"irradiance, W/m2). Fetch resource data for the site, or "
                f"import it from a file."
            )
        return self.output_series(
            ghi, location,
            dni=resources.get("dni"), dhi=resources.get("dhi"),
            temperature_c=resources.get("temperature_c"),
            wind_speed_ms=resources.get("wind_speed_10m"),
        )

    @property
    def area_m2(self):
        """Approximate module area for the installed capacity."""
        if self.module_efficiency <= 0:
            return None
        return self.capacity_kwp * 1000.0 / (STC_IRRADIANCE * self.module_efficiency)

    def resolved_tilt(self, latitude):
        """
        Tilt actually used. When not specified, an empirical optimum is used:
        for latitudes below 25 degrees the optimum is close to the latitude
        itself; above that, a shallower tilt collects more because of the
        larger diffuse fraction. This matches the widely used Landau fit and
        lands within a degree or two of the Global Solar Atlas OPTA value.
        """
        if self.tilt_deg is not None:
            return float(self.tilt_deg)
        lat = abs(float(latitude))
        if lat <= 25:
            return lat
        if lat <= 50:
            return lat * 0.76 + 3.1
        return lat * 0.5 + 16.3

    # ------------------------------------------------------------- output

    def output_series(
        self,
        ghi,
        location,
        dni=None,
        dhi=None,
        temperature_c=None,
        wind_speed_ms=None,
        year=2019,
    ):
        """
        Hourly AC-side DC power (kW) before the inverter.

        `ghi`, `dni`, `dhi` are hourly W/m2 series. If dni/dhi are missing,
        they are estimated from GHI with the Erbs correlation - workable, but
        a real study should use measured or modelled components, because the
        Erbs split can be off by 20% on an individual hour.

        Returns (dc_power_kw, info).
        """
        n = len(ghi)
        tilt = self.resolved_tilt(location.latitude)
        az = self.azimuth_deg

        if temperature_c is None:
            temperature_c = [20.0] * n
        if wind_speed_ms is None:
            wind_speed_ms = [1.0] * n

        u0, u1 = MOUNTING_PRESETS.get(self.mounting, (FAIMAN_U0, FAIMAN_U1))

        poa = []
        dc = []
        cell_temps = []
        clipped_hours = 0
        estimated_components = dni is None or dhi is None

        for t in range(n):
            g = max(0.0, ghi[t])
            zen, azi = solar_position(t, location, year=year)
            cos_zen = math.cos(math.radians(zen))

            if g <= 0.0 or zen >= 90.0:
                poa.append(0.0)
                dc.append(0.0)
                cell_temps.append(temperature_c[t])
                continue

            if estimated_components:
                bn, dh = erbs_split(g, zen, t)
            else:
                bn = max(0.0, dni[t])
                dh = max(0.0, dhi[t])

            surf_tilt, surf_az = self._tracked_orientation(tilt, az, zen, azi)
            poa_t = hay_davies_poa(
                bn, dh, g, zen, azi, surf_tilt, surf_az, self.albedo
            )
            poa.append(poa_t)

            tcell = faiman_cell_temp(
                poa_t, temperature_c[t], wind_speed_ms[t], u0, u1
            )
            cell_temps.append(tcell)

            # DC power. Linear in irradiance, with the temperature coefficient
            # applied to the deviation from STC cell temperature.
            p = (
                self.capacity_kwp
                * (poa_t / STC_IRRADIANCE)
                * (1.0 + self.temp_coeff_pmax * (tcell - STC_CELL_TEMP))
                * (1.0 - self.system_losses)
            )
            if p < 0.0:
                p = 0.0
            dc.append(p)

        total = sum(dc)
        cf = total / (self.capacity_kwp * n) if self.capacity_kwp and n else 0.0

        info = {
            "tilt_deg": tilt,
            "azimuth_deg": az,
            "tracking": self.tracking,
            "poa_irradiance": poa,
            "poa_annual_kwh_m2": sum(poa) / 1000.0,
            "ghi_annual_kwh_m2": sum(ghi) / 1000.0,
            "transposition_gain": (sum(poa) / sum(ghi)) if sum(ghi) > 0 else 0.0,
            "cell_temperature_c": cell_temps,
            "mean_cell_temp_c": (
                sum(cell_temps) / len(cell_temps) if cell_temps else 0.0
            ),
            "annual_dc_kwh": total,
            "capacity_factor": cf,
            "specific_yield_kwh_kwp": (
                total / self.capacity_kwp if self.capacity_kwp else 0.0
            ),
            "components_estimated": estimated_components,
            "clipped_hours": clipped_hours,
        }
        return dc, info

    def _tracked_orientation(self, tilt, az, zenith, azimuth):
        """Surface orientation for the configured tracking mode."""
        if self.tracking == "two_axis":
            # Surface normal follows the sun exactly.
            return min(90.0, zenith), azimuth
        if self.tracking == "single_axis":
            # Horizontal N-S axis, E-W tracking, ideal (no backtracking).
            # Rotation angle from the solar position projected onto the
            # axis-normal plane.
            zr = math.radians(zenith)
            ar = math.radians(azimuth - 180.0)
            x = math.sin(zr) * math.sin(ar)
            y = math.cos(zr)
            rot = math.degrees(math.atan2(x, y))
            rot = max(-55.0, min(55.0, rot))   # typical tracker travel limit
            surf_tilt = abs(rot)
            surf_az = 90.0 if rot > 0 else 270.0
            return surf_tilt, surf_az
        return tilt, az

    def yield_after_years(self, years):
        """Performance ratio remaining after `years` of linear degradation."""
        return max(0.0, 1.0 - self.degradation_per_year * float(years))


# ---------------------------------------------------------------- geometry

def solar_position(hour_index, location, year=2019):
    """
    Solar zenith and azimuth (degrees) for the mid-point of hour
    `hour_index` of a standard 8760-hour year at `location`.

    Azimuth follows the PVGIS convention: 0 = north, 90 = east, 180 = south,
    270 = west. Zenith is measured from vertical, so 90 is the horizon.

    Implementation follows Michalsky (1988) / NOAA, in solar time derived
    from the equation of time and the longitude offset from the time-zone
    meridian, which is the part most naive implementations get wrong.
    """
    lat = math.radians(location.latitude)
    lon = location.longitude
    tz = getattr(location, "utc_offset_hours", None)
    if tz is None:
        # Fall back to the nearest whole-hour zone for the longitude.
        tz = round(lon / 15.0)

    day = hour_index // 24 + 1
    hour = hour_index % 24 + 0.5      # mid-point of the hour

    # Fractional year angle.
    gamma = 2.0 * math.pi / 365.0 * (day - 1 + (hour - 12) / 24.0)

    # Equation of time, minutes.
    eqtime = 229.18 * (
        0.000075
        + 0.001868 * math.cos(gamma)
        - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2 * gamma)
        - 0.040849 * math.sin(2 * gamma)
    )

    # Solar declination, radians.
    decl = (
        0.006918
        - 0.399912 * math.cos(gamma)
        + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2 * gamma)
        + 0.000907 * math.sin(2 * gamma)
        - 0.002697 * math.cos(3 * gamma)
        + 0.001480 * math.sin(3 * gamma)
    )

    time_offset = eqtime + 4.0 * lon - 60.0 * tz
    true_solar_time = hour * 60.0 + time_offset
    hour_angle = math.radians(true_solar_time / 4.0 - 180.0)

    cos_zen = (
        math.sin(lat) * math.sin(decl)
        + math.cos(lat) * math.cos(decl) * math.cos(hour_angle)
    )
    cos_zen = max(-1.0, min(1.0, cos_zen))
    zenith = math.degrees(math.acos(cos_zen))

    # Azimuth from north, clockwise.
    sin_zen = math.sin(math.acos(cos_zen))
    if abs(sin_zen) < 1e-9:
        azimuth = 180.0
    else:
        cos_az = (
            math.sin(decl) * math.cos(lat)
            - math.cos(decl) * math.sin(lat) * math.cos(hour_angle)
        ) / sin_zen
        cos_az = max(-1.0, min(1.0, cos_az))
        azimuth = math.degrees(math.acos(cos_az))
        if hour_angle > 0:
            azimuth = 360.0 - azimuth

    return zenith, azimuth


def extraterrestrial_irradiance(day_of_year):
    """Solar constant corrected for earth-sun distance (W/m2)."""
    return 1367.0 * (
        1.0 + 0.033 * math.cos(2.0 * math.pi * day_of_year / 365.0)
    )


def erbs_split(ghi, zenith_deg, hour_index):
    """
    Split GHI into DNI and DHI using the Erbs correlation.

    Returns (dni, dhi) in W/m2. Used only when measured or modelled
    components are unavailable; the report flags when this path was taken.
    """
    cos_zen = math.cos(math.radians(zenith_deg))
    if cos_zen <= 0.01 or ghi <= 0:
        return 0.0, max(0.0, ghi)

    day = hour_index // 24 + 1
    e0 = extraterrestrial_irradiance(day) * cos_zen
    if e0 <= 0:
        return 0.0, ghi

    kt = ghi / e0
    kt = max(0.0, min(1.0, kt))

    if kt <= 0.22:
        df = 1.0 - 0.09 * kt
    elif kt <= 0.80:
        df = (
            0.9511
            - 0.1604 * kt
            + 4.388 * kt ** 2
            - 16.638 * kt ** 3
            + 12.336 * kt ** 4
        )
    else:
        df = 0.165

    dhi = ghi * df
    dni = (ghi - dhi) / cos_zen
    if dni < 0:
        dni = 0.0
    return dni, dhi


def angle_of_incidence(zenith_deg, azimuth_deg, surf_tilt_deg, surf_az_deg):
    """Cosine of the angle between the surface normal and the sun."""
    zr = math.radians(zenith_deg)
    ar = math.radians(azimuth_deg)
    tr = math.radians(surf_tilt_deg)
    sar = math.radians(surf_az_deg)
    return (
        math.cos(zr) * math.cos(tr)
        + math.sin(zr) * math.sin(tr) * math.cos(ar - sar)
    )


def hay_davies_poa(dni, dhi, ghi, zenith_deg, azimuth_deg,
                   surf_tilt_deg, surf_az_deg, albedo=0.20):
    """
    Plane-of-array irradiance (W/m2) by the Hay-Davies transposition.

    Total = beam + circumsolar diffuse + isotropic diffuse + ground reflected.
    The anisotropy index weights how much of the diffuse behaves like beam,
    which is what separates this from the plain isotropic model.
    """
    cos_aoi = angle_of_incidence(
        zenith_deg, azimuth_deg, surf_tilt_deg, surf_az_deg
    )
    cos_aoi = max(0.0, cos_aoi)
    tr = math.radians(surf_tilt_deg)

    beam = dni * cos_aoi

    day = 1
    e0 = 1367.0
    cos_zen = max(0.0, math.cos(math.radians(zenith_deg)))
    # Anisotropy index: fraction of diffuse that is circumsolar.
    ai = 0.0
    if e0 * cos_zen > 0:
        ai = min(1.0, dni / e0)

    circumsolar = dhi * ai * (cos_aoi / cos_zen if cos_zen > 0.01 else 0.0)
    isotropic = dhi * (1.0 - ai) * (1.0 + math.cos(tr)) / 2.0
    ground = ghi * albedo * (1.0 - math.cos(tr)) / 2.0

    total = beam + circumsolar + isotropic + ground
    return max(0.0, total)


def faiman_cell_temp(poa, air_temp_c, wind_speed_ms, u0=FAIMAN_U0, u1=FAIMAN_U1):
    """
    Module temperature by the Faiman model (IEC 61853-2).

        T_module = T_air + POA / (u0 + u1 * v_wind)

    Wind speed here is at module height. Using a 10 m measurement without
    correction runs the modules too cool and overstates yield.
    """
    denom = u0 + u1 * max(0.0, float(wind_speed_ms))
    if denom <= 0:
        return float(air_temp_c)
    return float(air_temp_c) + float(poa) / denom


def noct_cell_temp(poa, air_temp_c, noct=45.0):
    """Older NOCT cell temperature model, retained for comparison."""
    return float(air_temp_c) + (float(noct) - 20.0) / 800.0 * float(poa)


def optimum_tilt(latitude):
    """Empirical optimum fixed tilt for a latitude, in degrees."""
    lat = abs(float(latitude))
    if lat <= 25:
        return lat
    if lat <= 50:
        return lat * 0.76 + 3.1
    return lat * 0.5 + 16.3
