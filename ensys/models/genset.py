"""
Diesel / gas generator model.

Fuel consumption follows the standard affine law used by HOMER and most of
the sizing literature:

    fuel(L/h) = F0 * P_rated + F1 * P_output

The intercept F0 is what makes a genset expensive to run lightly loaded: at
25% load a typical diesel burns roughly 40% of its full-load fuel, so the
specific consumption nearly doubles. That is why `min_load_ratio` exists and
why the dispatch should never be allowed to idle a large machine to serve a
small deficit.

Running a diesel below about 30-40% of rating for extended periods also
causes wet stacking and real engine damage, so the model treats the minimum
load ratio as a genuine operating constraint, not a cost preference.
"""

from __future__ import annotations

import math

from ..assets import Dispatchable

# Typical affine fuel-curve coefficients, litres per hour per kW.
# (F0 intercept coefficient, F1 slope coefficient)
FUEL_CURVE_PRESETS = {
    "diesel_small":  (0.0350, 0.2500),   # < 100 kW
    "diesel_medium": (0.0300, 0.2460),   # 100-500 kW
    "diesel_large":  (0.0250, 0.2360),   # > 500 kW
    "gas_natural":   (0.0400, 0.3000),
}

# Emission factors, kg CO2 per litre of fuel burned.
EMISSION_FACTORS = {
    "diesel": 2.68,
    "gasoline": 2.31,
    "gas_natural": 1.90,   # per m3
    "lpg": 1.51,
}


class Generator(Dispatchable):
    """
    A dispatchable fuel generator.

    Parameters
    ----------
    rated_kw : nameplate continuous output of ONE unit
    min_load_ratio : lowest permitted output as a fraction of rating
    fuel_curve : (F0, F1) or a preset name
    fuel_price : cost per litre (or per m3 for gas)
    lifetime_hours : operating hours to overhaul/replacement
    """

    technology = "genset"
    unit_label = "genset"

    def __init__(
        self,
        name="Diesel generator",
        rated_kw=100.0,
        min_load_ratio=0.30,
        fuel_curve="diesel_medium",
        fuel_price=1.0,
        fuel_type="diesel",
        lifetime_hours=15000,
        min_runtime_hours=1,
        startup_cost=0.0,
        capital_cost=0.0,
        replacement_cost=0.0,
        om_cost_per_hour=0.0,
        site_derate=1.0,
    ):
        super().__init__(
            name=name, min_load_ratio=min_load_ratio,
            min_runtime_hours=min_runtime_hours, startup_cost=startup_cost,
            capital_cost=capital_cost, replacement_cost=replacement_cost,
            lifetime_years=20,
        )
        self.rated_kw = float(rated_kw)
        if isinstance(fuel_curve, str):
            if fuel_curve not in FUEL_CURVE_PRESETS:
                raise ValueError(
                    f"unknown fuel curve preset '{fuel_curve}'. "
                    f"Available: {sorted(FUEL_CURVE_PRESETS)}"
                )
            self.f0, self.f1 = FUEL_CURVE_PRESETS[fuel_curve]
        else:
            self.f0, self.f1 = (float(fuel_curve[0]), float(fuel_curve[1]))
        self.fuel_price = float(fuel_price)
        self.fuel_type = fuel_type
        self.lifetime_hours = float(lifetime_hours)
        self.om_cost_per_hour = float(om_cost_per_hour)
        # IEC TS 62257-7-3 5.2.4: output available at the site, as a
        # fraction of the ISO 8528-1 nameplate. Set by the study from the
        # site's altitude, design temperature and humidity.
        self.site_derate = max(0.1, min(1.0, float(site_derate or 1.0)))

    def rated_power_kw(self):
        return self.rated_kw

    # ---- unit commitment -------------------------------------------------
    # A bank of N sets is not one machine N times the size. Only as many
    # units run as the demand needs, and the minimum-load floor and the
    # fuel-curve intercept apply to the RUNNING units only. Treating the
    # bank as one machine forced every unit to idle at its minimum whenever
    # any of them was needed: a four-set bank asked for 10 kW produced
    # 4 x 30 % of rating and burnt four intercepts of fuel to do it.

    def unit_available_kw(self):
        return self.rated_kw * self.site_derate

    def units_running(self, n_units, output_kw):
        if n_units <= 0 or output_kw <= 0:
            return 0
        per = self.unit_available_kw()
        if per <= 0:
            return 0
        return max(1, min(int(n_units), int(math.ceil(output_kw / per - 1e-9))))

    def min_output_kw(self, n_units, t=None, state=None):
        """Lowest output with one unit running."""
        if n_units <= 0:
            return 0.0
        return self.rated_kw * self.min_load_ratio

    def max_output_kw(self, n_units):
        return n_units * self.unit_available_kw()

    def available_kw(self, n_units, t=None, state=None):
        return n_units * self.unit_available_kw()

    def marginal_cost(self, n_units, output_kw):
        if output_kw <= 0:
            return 0.0
        return self.fuel_rate(n_units, output_kw) * self.fuel_price / output_kw

    def annual_cost(self, n_units, series, dt_h=1.0):
        s = self.annual_summary(n_units, series, dt_h)
        return s["fuel_cost"] + s["om_cost"] + s["startup_cost"]

    def fuel_rate(self, n_units, output_kw):
        """Fuel consumption per hour at a given output, running units only."""
        if output_kw <= 0 or n_units <= 0:
            return 0.0
        on = self.units_running(n_units, output_kw)
        return self.f0 * on * self.rated_kw + self.f1 * output_kw

    def specific_consumption(self, n_units, output_kw):
        """Litres per kWh produced - the number that exposes light loading."""
        if output_kw <= 0:
            return None
        return self.fuel_rate(n_units, output_kw) / output_kw

    def efficiency(self, n_units, output_kw, lhv_kwh_per_litre=10.0):
        """Electrical efficiency at a given output."""
        fuel = self.fuel_rate(n_units, output_kw)
        if fuel <= 0:
            return 0.0
        return output_kw / (fuel * lhv_kwh_per_litre)

    def dispatch(self, n_units, demand_kw, t=None, state=None):
        return self.dispatch_output(n_units, demand_kw)

    def dispatch_output(self, n_units, demand_kw):
        """
        Output the machine will actually produce for a requested demand,
        honouring the minimum load ratio.

        Returns (output_kw, surplus_kw). Surplus is non-zero when the minimum
        load forces the genset to produce more than is needed - energy that
        must be dumped or stored, and a strong signal the unit is oversized.
        """
        if n_units <= 0 or demand_kw <= 0:
            return 0.0, 0.0
        hi = self.max_output_kw(n_units)
        if demand_kw >= hi:
            return hi, 0.0
        on = self.units_running(n_units, demand_kw)
        lo = on * self.rated_kw * self.min_load_ratio
        if demand_kw >= lo:
            return demand_kw, 0.0
        return lo, lo - demand_kw

    def annual_summary(self, n_units, output_series, dt_h=1.0):
        """Fuel, cost, emissions, run hours and starts for the year."""
        fuel = 0.0
        run_hours = 0
        starts = 0
        prev_on = False
        energy = 0.0
        low_load_hours = 0
        unit_hours = 0
        per = self.unit_available_kw()

        for p in output_series:
            on = p > 1e-9
            if on:
                fuel += self.fuel_rate(n_units, p) * dt_h
                run_hours += 1
                energy += p * dt_h
                k = self.units_running(n_units, p)
                unit_hours += k
                # IEC TS 62257-7-3 5.2.2: below 50 % of the running units'
                # rating is light loading. The old test compared output with
                # the minimum the dispatch itself enforces, so it could never
                # count anything.
                if per > 0 and p < 0.5 * k * per - 1e-9:
                    low_load_hours += 1
                if not prev_on:
                    starts += 1
            prev_on = on

        ef = EMISSION_FACTORS.get(self.fuel_type, 2.68)
        return {
            "fuel_units": fuel,
            "fuel_cost": fuel * self.fuel_price,
            "run_hours": run_hours,
            "starts": starts,
            "startup_cost": starts * self.startup_cost,
            "om_cost": unit_hours * self.om_cost_per_hour,
            "energy_kwh": energy,
            "emissions_kg": fuel * ef,
            "capacity_factor": (
                energy / (self.max_output_kw(n_units) * len(output_series))
                if n_units > 0 and output_series else 0.0
            ),
            "mean_load_ratio": (
                energy / (unit_hours * per)
                if unit_hours and per > 0 else 0.0
            ),
            "unit_run_hours": unit_hours,
            "site_derate": self.site_derate,
            "specific_consumption": (fuel / energy) if energy > 0 else None,
            "low_load_hours": low_load_hours,
            # Overhaul life is consumed per running UNIT hour.
            "years_to_overhaul": (
                self.lifetime_hours * n_units / unit_hours
                if unit_hours > 0 else None
            ),
        }
