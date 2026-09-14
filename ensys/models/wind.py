"""
Wind turbine model.

Two output models are provided:

  * `CUBIC` - the piecewise cubic approximation used in the original MATLAB
    `windt.m`. Kept bit-for-bit compatible so results can be validated
    against the existing study, and because it is the right choice when only
    nameplate figures (rated power, cut-in, rated, cut-out) are known.

  * `CURVE` - interpolation on a manufacturer power curve. More accurate and
    preferred whenever a real curve is available, since the cubic form
    systematically misestimates output in the 0.4-0.9 rated band where most
    annual energy is actually produced.

Wind speed measured at a reference height is translated to hub height by the
power law, v_hub = v_ref * (H_hub / H_ref) ** alpha, with alpha the terrain
shear exponent. The log law is offered as an alternative where a roughness
length is known, which is usually the better physical model over uniform
terrain.

Air density correction is applied to the power curve per IEC 61400-12,
because a curve is certified at 1.225 kg/m3 and sites at altitude produce
materially less. Ignoring it is the single most common overestimate in
wind resource assessment: at 2000 m the error is about 18%.
"""

from __future__ import annotations

import math

from ..assets import NonDispatchable

CUBIC = "cubic"
CURVE = "curve"

# Standard sea-level air density the certified power curve assumes (kg/m3).
RHO_STD = 1.225

# Terrain shear exponents (power law), typical values.
SHEAR_EXPONENTS = {
    "water": 0.10,
    "open_smooth": 0.14,      # the classic "1/7 power law" open terrain
    "open_farmland": 0.16,
    "crops_hedges": 0.20,
    "wooded": 0.24,
    "suburban": 0.28,
    "urban": 0.40,
}

# Surface roughness lengths z0 in metres (log law), per European Wind Atlas.
ROUGHNESS_LENGTHS = {
    "water": 0.0002,
    "open_smooth": 0.03,
    "open_farmland": 0.10,
    "crops_hedges": 0.20,
    "wooded": 0.50,
    "suburban": 1.00,
    "urban": 2.00,
}


class WindTurbine(NonDispatchable):
    """
    A single wind turbine unit.

    Parameters
    ----------
    rated_kw : nameplate electrical output (kW)
    v_cutin, v_rated, v_cutout : m/s at hub height
    hub_height_m : hub height above ground
    rotor_diameter_m : optional, used for swept area and Cp reporting
    efficiency : drivetrain/electrical efficiency applied to the cubic model,
        matching WTeff in the original MATLAB
    power_curve : optional list of (wind_speed_ms, power_kw) pairs
    model : CUBIC or CURVE
    """

    def __init__(
        self,
        name="Generic 2 MW",
        rated_kw=2000.0,
        v_cutin=3.0,
        v_rated=12.0,
        v_cutout=25.0,
        hub_height_m=80.0,
        rotor_diameter_m=90.0,
        efficiency=1.0,
        power_curve=None,
        model=None,
        reference_height_m=10.0,
        capital_cost=0.0,
        replacement_cost=0.0,
        om_cost_per_year=0.0,
        lifetime_years=20,
    ):
        super().__init__(
            name=name, capital_cost=capital_cost,
            replacement_cost=replacement_cost,
            om_cost_per_year=om_cost_per_year, lifetime_years=lifetime_years,
        )
        self.rated_kw = float(rated_kw)
        self.v_cutin = float(v_cutin)
        self.v_rated = float(v_rated)
        self.v_cutout = float(v_cutout)
        self.hub_height_m = float(hub_height_m)
        self.rotor_diameter_m = float(rotor_diameter_m) if rotor_diameter_m else None
        self.efficiency = float(efficiency)
        self.power_curve = sorted(power_curve) if power_curve else None
        self.model = model or (CURVE if power_curve else CUBIC)
        # Height the supplied wind-speed data was measured or modelled at.
        # Not the hub height: the two are usually different, and confusing
        # them is a 10-20% error in annual energy.
        self.reference_height_m = float(reference_height_m)

        if self.v_rated <= self.v_cutin:
            raise ValueError(
                f"{name}: rated wind speed ({self.v_rated}) must exceed "
                f"cut-in ({self.v_cutin})"
            )
        if self.v_cutout <= self.v_rated:
            raise ValueError(
                f"{name}: cut-out wind speed ({self.v_cutout}) must exceed "
                f"rated ({self.v_rated})"
            )

    technology = "wind"
    unit_label = "turbine"

    def rated_power_kw(self):
        return self.rated_kw

    def resource_key(self):
        return "wind_speed"

    def generate(self, resources, location=None):
        """Adapter to the asset contract."""
        ws = None
        ref = self.reference_height_m
        for key, height in (("wind_speed", self.reference_height_m),
                            ("wind_speed_100m", 100.0),
                            ("wind_speed_50m", 50.0),
                            ("wind_speed_10m", 10.0)):
            if resources.get(key):
                ws, ref = resources[key], height
                break
        if not ws:
            raise ValueError(
                f"{self.name} needs a wind-speed series. Fetch resource data "
                f"for the site, or import measured data."
            )
        return self.output_series(
            ws, ref_height_m=ref,
            shear_exponent=(location.shear_exponent if location else 0.14),
            elevation_m=(location.elevation_m if location else 0.0),
            temperature_c=resources.get("temperature_c"),
        )

    @property
    def swept_area_m2(self):
        if not self.rotor_diameter_m:
            return None
        return math.pi * (self.rotor_diameter_m / 2.0) ** 2

    @property
    def specific_power_w_m2(self):
        """
        Rated power per swept area. A key classification number: below about
        300 W/m2 is a low-specific-power machine that reaches rated output
        more often, which suits low-wind sites.
        """
        a = self.swept_area_m2
        if not a:
            return None
        return self.rated_kw * 1000.0 / a

    def power_at(self, v):
        """
        Electrical output (kW) at hub-height wind speed v (m/s), before any
        air-density correction.
        """
        if v < self.v_cutin or v > self.v_cutout:
            return 0.0

        if self.model == CURVE and self.power_curve:
            return self._interp_curve(v)

        # Piecewise cubic, identical in form to the original windt.m.
        if v < self.v_rated:
            frac = (v - self.v_cutin) / (self.v_rated - self.v_cutin)
            return self.efficiency * self.rated_kw * frac ** 3
        return self.efficiency * self.rated_kw

    def _interp_curve(self, v):
        """Linear interpolation on the manufacturer curve."""
        pc = self.power_curve
        if v <= pc[0][0]:
            return 0.0
        if v >= pc[-1][0]:
            return float(pc[-1][1])
        lo, hi = 0, len(pc) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if pc[mid][0] <= v:
                lo = mid
            else:
                hi = mid
        v0, p0 = pc[lo]
        v1, p1 = pc[hi]
        if v1 == v0:
            return float(p0)
        return float(p0 + (p1 - p0) * (v - v0) / (v1 - v0))

    # ---------------------------------------------------------------- output

    def output_series(
        self,
        wind_speed,
        ref_height_m=10.0,
        shear_exponent=0.14,
        roughness_length_m=None,
        air_density=None,
        elevation_m=None,
        temperature_c=None,
        availability=1.0,
        wake_loss=0.0,
    ):
        """
        Convert a measured wind-speed series into an hourly output series (kW).

        Returns (power_kw, info) where info carries the derived quantities a
        report has to justify: hub-height speeds, density ratio, capacity
        factor and full-load hours.

        wake_loss applies only when several turbines are grouped; for a single
        machine leave it at zero. It is applied as a flat energy derate, which
        is a simplification - a real wake model needs layout and wind rose.
        """
        vhub = self.shift_to_hub_height(
            wind_speed, ref_height_m, shear_exponent, roughness_length_m
        )

        rho = air_density
        if rho is None:
            rho = air_density_at(elevation_m or 0.0, temperature_c)
        density_ratio = rho / RHO_STD

        derate = float(availability) * (1.0 - float(wake_loss))

        out = []
        hours_at_rated = 0
        hours_below_cutin = 0
        hours_above_cutout = 0
        for v in vhub:
            if v < self.v_cutin:
                hours_below_cutin += 1
                out.append(0.0)
                continue
            if v > self.v_cutout:
                hours_above_cutout += 1
                out.append(0.0)
                continue
            # IEC 61400-12 density correction: for a pitch-regulated machine
            # the curve is shifted in speed by the cube root of the density
            # ratio, which is more faithful than scaling power directly.
            v_eff = v * (density_ratio ** (1.0 / 3.0))
            p = self.power_at(v_eff) * derate
            if p >= self.rated_kw * derate * 0.999:
                hours_at_rated += 1
            out.append(p)

        total = sum(out)
        n = len(out) or 1
        cf = total / (self.rated_kw * n) if self.rated_kw else 0.0

        info = {
            "hub_wind_speed": vhub,
            "mean_wind_speed_hub_ms": sum(vhub) / len(vhub) if vhub else 0.0,
            "mean_wind_speed_ref_ms": (
                sum(wind_speed) / len(wind_speed) if wind_speed else 0.0
            ),
            "air_density_kg_m3": rho,
            "density_ratio": density_ratio,
            "annual_energy_kwh": total,
            "capacity_factor": cf,
            "full_load_hours": total / self.rated_kw if self.rated_kw else 0.0,
            "hours_at_rated": hours_at_rated,
            "hours_below_cutin": hours_below_cutin,
            "hours_above_cutout": hours_above_cutout,
            "model": self.model,
        }
        return out, info

    def shift_to_hub_height(
        self, wind_speed, ref_height_m, shear_exponent=0.14, roughness_length_m=None
    ):
        """
        Translate measured wind speed to hub height.

        Uses the log law when a roughness length is supplied (physically
        better over uniform terrain), otherwise the power law, which is what
        the original MATLAB used.
        """
        ref = float(ref_height_m)
        hub = self.hub_height_m
        if ref <= 0:
            raise ValueError("reference height must be positive")

        if roughness_length_m:
            z0 = float(roughness_length_m)
            if z0 <= 0 or ref <= z0 or hub <= z0:
                raise ValueError(
                    "roughness length must be positive and below both heights"
                )
            factor = math.log(hub / z0) / math.log(ref / z0)
        else:
            factor = (hub / ref) ** float(shear_exponent)

        return [v * factor for v in wind_speed]


def air_density_at(elevation_m, temperature_c=None):
    """
    Air density (kg/m3) from elevation and optionally temperature.

    Uses the ISA barometric formula for pressure, then the ideal gas law.
    When temperature is not given, the ISA lapse rate of 6.5 K/km from 15 C
    at sea level is assumed.
    """
    z = float(elevation_m or 0.0)
    t0 = 288.15          # ISA sea-level temperature, K
    p0 = 101325.0        # ISA sea-level pressure, Pa
    lapse = 0.0065       # K/m
    g = 9.80665
    r_specific = 287.058  # J/(kg K) for dry air
    r_universal = 8.31447
    m_air = 0.0289644

    t_isa = t0 - lapse * z
    pressure = p0 * (t_isa / t0) ** (g * m_air / (r_universal * lapse))

    if temperature_c is None:
        t_k = t_isa
    elif isinstance(temperature_c, (list, tuple)):
        mean_c = sum(temperature_c) / len(temperature_c) if temperature_c else 15.0
        t_k = mean_c + 273.15
    else:
        t_k = float(temperature_c) + 273.15

    return pressure / (r_specific * t_k)


def weibull_fit(wind_speed):
    """
    Fit Weibull shape (k) and scale (c) to a wind-speed series.

    Uses the empirical method of moments, which is stable and needs no
    iteration. Returned so reports can show the resource distribution and so
    a synthetic year can be generated when only summary statistics are known.
    """
    v = [x for x in wind_speed if x > 0]
    n = len(v)
    if n < 2:
        return {"k": None, "c": None, "mean": 0.0, "std": 0.0}

    mean = sum(v) / n
    std = math.sqrt(sum((x - mean) ** 2 for x in v) / (n - 1))
    if mean <= 0 or std <= 0:
        return {"k": None, "c": None, "mean": mean, "std": std}

    # Justus approximation, valid for 1 <= k <= 10.
    k = (std / mean) ** -1.086
    k = max(1.0, min(10.0, k))
    c = mean / math.gamma(1.0 + 1.0 / k)
    return {
        "k": k,
        "c": c,
        "mean": mean,
        "std": std,
        "power_density_w_m2": 0.5 * RHO_STD * c ** 3 * math.gamma(1.0 + 3.0 / k),
    }


# ------------------------------------------------------------------ library

def library():
    """
    A small library of representative machines spanning the sizes this tool
    is aimed at. Power curves are approximate and intended for screening;
    a real project should load the manufacturer's certified curve.
    """
    return {
        "vestas_v90_2000": WindTurbine(
            name="Vestas V90 2.0 MW",
            rated_kw=2000.0, v_cutin=3.5, v_rated=12.0, v_cutout=25.0,
            hub_height_m=80.0, rotor_diameter_m=90.0,
            power_curve=[
                (3.5, 0), (4, 55), (5, 168), (6, 331), (7, 564), (8, 866),
                (9, 1223), (10, 1590), (11, 1900), (12, 1990), (13, 2000),
                (20, 2000), (25, 2000), (25.5, 0),
            ],
            lifetime_years=20,
        ),
        "generic_10kw": WindTurbine(
            name="Generic small 10 kW",
            rated_kw=10.0, v_cutin=2.5, v_rated=11.0, v_cutout=25.0,
            hub_height_m=18.0, rotor_diameter_m=7.0, lifetime_years=20,
        ),
        "generic_100kw": WindTurbine(
            name="Generic 100 kW",
            rated_kw=100.0, v_cutin=3.0, v_rated=12.0, v_cutout=25.0,
            hub_height_m=30.0, rotor_diameter_m=21.0, lifetime_years=20,
        ),
        "generic_1500kw": WindTurbine(
            name="Generic 1.5 MW",
            rated_kw=1500.0, v_cutin=3.5, v_rated=12.5, v_cutout=25.0,
            hub_height_m=65.0, rotor_diameter_m=77.0, lifetime_years=20,
        ),
    }
