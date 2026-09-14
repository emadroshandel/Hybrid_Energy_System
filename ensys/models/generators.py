"""
Generation technologies beyond PV and wind.

Each one is a few dozen lines because the asset base classes carry the
dispatch contract. What matters in each is the physics that makes it
behave differently from a diesel engine with a different label.

  Micro-hydro       Power is rho*g*Q*H*eta. Run-of-river follows the flow
                    duration curve and is non-dispatchable; with a pondage
                    reservoir it becomes dispatchable within a daily volume.

  Biomass gasifier  Dispatchable, but limited by feedstock AVAILABILITY over
                    the year, not just by rating. A 500 kW gasifier with
                    200 tonnes of husk a year cannot run 8760 hours, and a
                    model that ignores the fuel budget will happily size one
                    that does.

  CSP with storage  A solar field charges a thermal store; the power block
                    draws from the store. That is what makes CSP
                    dispatchable when PV is not, and it is why solar
                    multiple and storage hours are the two real design
                    variables.

  Geothermal        Near-constant output, high capital, very low marginal
                    cost, and slow to ramp. It sits at the bottom of the
                    merit order because once built it should run.

  Tidal             Entirely predictable, unlike wind — driven by lunar
                    harmonics, so a synthetic series from constituents is
                    legitimate where a synthetic wind year is not.

  Fuel cell         Efficiency RISES at part load, the opposite of a
                    combustion engine. Applying engine intuition to a fuel
                    cell gets the dispatch backwards.

  CHP               Sized by heat demand in most real installations, with
                    electricity as the by-product. Modelling it
                    electricity-first inverts the actual constraint, so the
                    heat side is explicit here.
"""

from __future__ import annotations

import math

from ..assets import Dispatchable, NonDispatchable, DEFAULT_MERIT

WATER_DENSITY = 1000.0      # kg/m3
GRAVITY = 9.80665           # m/s2


# =====================================================================
# Hydro
# =====================================================================

class RunOfRiverHydro(NonDispatchable):
    """
    Run-of-river hydro with no storage.

    Output follows the river flow. Below the minimum technical flow the
    turbine cannot run at all, and above the design flow the excess simply
    passes the intake — both are hard limits, not efficiency curves.

    The environmental compensation flow is subtracted before anything else.
    It is a licence condition in most jurisdictions and ignoring it
    overstates output by whatever the regulator will actually require.
    """

    technology = "run_of_river_hydro"
    unit_label = "turbine"

    def __init__(self, name="Run-of-river hydro", design_flow_m3s=1.0,
                 head_m=20.0, efficiency=0.85, min_flow_ratio=0.20,
                 compensation_flow_m3s=0.0, **kw):
        kw.setdefault("lifetime_years", 40)
        super().__init__(name=name, **kw)
        self.design_flow_m3s = float(design_flow_m3s)
        self.head_m = float(head_m)
        self.efficiency = float(efficiency)
        self.min_flow_ratio = float(min_flow_ratio)
        self.compensation_flow_m3s = float(compensation_flow_m3s)

        if self.head_m <= 0 or self.design_flow_m3s <= 0:
            raise ValueError(f"{name}: head and design flow must be positive")

    def rated_power_kw(self):
        return (
            WATER_DENSITY * GRAVITY * self.design_flow_m3s * self.head_m
            * self.efficiency / 1000.0
        )

    def resource_key(self):
        return "river_flow_m3s"

    def power_at(self, flow_m3s):
        usable = max(0.0, flow_m3s - self.compensation_flow_m3s)
        if usable < self.design_flow_m3s * self.min_flow_ratio:
            return 0.0
        q = min(usable, self.design_flow_m3s)
        return WATER_DENSITY * GRAVITY * q * self.head_m * self.efficiency / 1000.0

    def generate(self, resources, location=None):
        flow = resources.get("river_flow_m3s")
        if not flow:
            raise ValueError(
                f"{self.name} needs a 'river_flow_m3s' series. Supply gauged "
                f"flow data, or a flow duration curve the engine can expand."
            )
        out = [self.power_at(q) for q in flow]
        rated = self.rated_power_kw()
        hours = len(out)
        below = sum(
            1 for q in flow
            if max(0.0, q - self.compensation_flow_m3s)
            < self.design_flow_m3s * self.min_flow_ratio
        )
        spilled = sum(
            max(0.0, q - self.compensation_flow_m3s - self.design_flow_m3s)
            for q in flow
        )
        return out, {
            "rated_kw": rated,
            "annual_kwh": sum(out),
            "capacity_factor": sum(out) / (rated * hours) if rated else 0.0,
            "hours_below_minimum_flow": below,
            "spilled_volume_m3": spilled * 3600.0,
            "mean_flow_m3s": sum(flow) / hours if hours else 0.0,
        }


class ReservoirHydro(Dispatchable):
    """
    Hydro with pondage: dispatchable within a daily energy budget.

    The reservoir is modelled as a volume that refills from inflow and
    empties as generation. It is a Dispatchable rather than a Storage
    because it has an external inflow — you cannot decide to charge it.
    """

    technology = "reservoir_hydro"
    unit_label = "turbine"
    default_merit = DEFAULT_MERIT["dispatchable_cheap"]

    def __init__(self, name="Reservoir hydro", rated_kw=500.0, head_m=40.0,
                 efficiency=0.88, reservoir_m3=50000.0, **kw):
        kw.setdefault("lifetime_years", 40)
        kw.setdefault("min_load_ratio", 0.15)
        super().__init__(name=name, **kw)
        self.rated_kw = float(rated_kw)
        self.head_m = float(head_m)
        self.efficiency = float(efficiency)
        self.reservoir_m3 = float(reservoir_m3)

    def rated_power_kw(self):
        return self.rated_kw

    def rated_energy_kwh(self):
        """Energy stored in a full reservoir at the working head."""
        return (
            WATER_DENSITY * GRAVITY * self.reservoir_m3 * self.head_m
            * self.efficiency / 3.6e6
        )

    def available_kw(self, n_units, t, state):
        stored = state.get("hydro_volume_m3", self.reservoir_m3 * n_units)
        energy_kwh = (
            WATER_DENSITY * GRAVITY * stored * self.head_m
            * self.efficiency / 3.6e6
        )
        return max(0.0, min(n_units * self.rated_kw, energy_kwh))

    def flow_for_power(self, kw):
        denom = WATER_DENSITY * GRAVITY * self.head_m * self.efficiency
        return kw * 1000.0 / denom if denom > 0 else 0.0


# =====================================================================
# Biomass
# =====================================================================

class BiomassGenerator(Dispatchable):
    """
    Biomass or biogas generator with an annual feedstock budget.

    The budget is the point. Rating alone says a 500 kW unit can produce
    4.4 GWh a year; 200 tonnes of rice husk at 13 GJ/t and 25% efficiency
    says it can produce about 180 MWh. Sizing against the rating and
    ignoring the fuel is the standard way to design a biomass plant that
    sits idle nine months of the year.
    """

    technology = "biomass"
    unit_label = "genset"
    default_merit = DEFAULT_MERIT["dispatchable_cheap"]

    def __init__(self, name="Biomass gasifier", rated_kw=100.0,
                 electrical_efficiency=0.25, feedstock_lhv_mj_per_kg=13.0,
                 feedstock_price_per_tonne=40.0,
                 annual_feedstock_tonnes=None, carbon_neutral=True, **kw):
        kw.setdefault("min_load_ratio", 0.40)
        kw.setdefault("lifetime_years", 20)
        super().__init__(name=name, **kw)
        self.rated_kw = float(rated_kw)
        self.electrical_efficiency = float(electrical_efficiency)
        self.feedstock_lhv_mj_per_kg = float(feedstock_lhv_mj_per_kg)
        self.feedstock_price_per_tonne = float(feedstock_price_per_tonne)
        self.annual_feedstock_tonnes = annual_feedstock_tonnes
        self.carbon_neutral = bool(carbon_neutral)

        if not (0.0 < self.electrical_efficiency < 1.0):
            raise ValueError(f"{name}: electrical efficiency must be in (0, 1)")

    def rated_power_kw(self):
        return self.rated_kw

    def feedstock_kg_per_kwh(self):
        energy_per_kg = self.feedstock_lhv_mj_per_kg / 3.6   # kWh thermal
        return 1.0 / (energy_per_kg * self.electrical_efficiency)

    def annual_energy_limit_kwh(self, n_units):
        """Energy the available feedstock can actually produce."""
        if self.annual_feedstock_tonnes is None:
            return float("inf")
        kg = self.annual_feedstock_tonnes * 1000.0
        return kg / self.feedstock_kg_per_kwh()

    def available_kw(self, n_units, t, state):
        limit = self.annual_energy_limit_kwh(n_units)
        if limit == float("inf"):
            return n_units * self.rated_kw
        used = state.get(f"biomass_used_{id(self)}", 0.0)
        if used >= limit:
            return 0.0
        return n_units * self.rated_kw

    def marginal_cost(self, n_units, output_kw):
        return self.feedstock_kg_per_kwh() * self.feedstock_price_per_tonne / 1000.0

    def annual_cost(self, n_units, series, dt_h=1.0):
        energy = sum(series) * dt_h
        tonnes = energy * self.feedstock_kg_per_kwh() / 1000.0
        return tonnes * self.feedstock_price_per_tonne

    def summary(self, n_units, series, dt_h=1.0):
        s = super().summary(n_units, series, dt_h)
        energy = s["energy_kwh"]
        tonnes = energy * self.feedstock_kg_per_kwh() / 1000.0
        s.update({
            "feedstock_tonnes": tonnes,
            "feedstock_cost": tonnes * self.feedstock_price_per_tonne,
            "feedstock_budget_tonnes": self.annual_feedstock_tonnes,
            "budget_exceeded": (
                self.annual_feedstock_tonnes is not None
                and tonnes > self.annual_feedstock_tonnes * 1.001
            ),
            # Biomass is counted carbon-neutral only when the feedstock is
            # genuinely a residue and regrows. Purpose-grown fuel is not.
            "emissions_kg": 0.0 if self.carbon_neutral else energy * 0.39,
        })
        return s


# =====================================================================
# Concentrating solar power
# =====================================================================

class CSPWithStorage(Dispatchable):
    """
    Concentrating solar power with a molten-salt thermal store.

    Two design numbers govern everything:

      solar multiple   the field's thermal rating divided by the power
                       block's thermal demand. Above 1.0 the field
                       oversupplies at midday, and that excess is what
                       fills the store.
      storage hours    how long the block can run at rating from a full
                       store.

    A solar multiple of 1.0 with storage is incoherent — there is never any
    excess to store — and the model says so rather than silently producing
    a store that never charges.

    CSP uses DNI only. Diffuse light cannot be concentrated, which is why
    CSP suits deserts and PV suits everywhere.
    """

    technology = "csp"
    unit_label = "block"
    default_merit = DEFAULT_MERIT["dispatchable_cheap"]

    def __init__(self, name="CSP with thermal storage", rated_kw=1000.0,
                 solar_multiple=2.0, storage_hours=6.0,
                 field_efficiency=0.55, block_efficiency=0.40,
                 storage_efficiency=0.98, dni_design_w_m2=800.0,
                 parasitic_ratio=0.10, **kw):
        kw.setdefault("min_load_ratio", 0.20)
        kw.setdefault("lifetime_years", 30)
        super().__init__(name=name, **kw)
        self.rated_kw = float(rated_kw)
        self.solar_multiple = float(solar_multiple)
        self.storage_hours = float(storage_hours)
        self.field_efficiency = float(field_efficiency)
        self.block_efficiency = float(block_efficiency)
        self.storage_efficiency = float(storage_efficiency)
        self.dni_design_w_m2 = float(dni_design_w_m2)
        self.parasitic_ratio = float(parasitic_ratio)

        if self.storage_hours > 0 and self.solar_multiple <= 1.05:
            raise ValueError(
                f"{name}: {self.storage_hours:g} hours of storage with a solar "
                f"multiple of {self.solar_multiple:g} cannot work — the field "
                f"never produces more than the power block consumes, so the "
                f"store can never charge. Raise the solar multiple above about "
                f"1.3, or set storage_hours to 0."
            )

    def rated_power_kw(self):
        return self.rated_kw

    def rated_energy_kwh(self):
        return self.rated_kw * self.storage_hours

    def resource_key(self):
        return "dni"

    def aperture_area_m2(self):
        """Solar field aperture for the chosen solar multiple."""
        thermal_demand_kw = self.rated_kw / self.block_efficiency
        field_thermal_kw = thermal_demand_kw * self.solar_multiple
        return field_thermal_kw * 1000.0 / (
            self.dni_design_w_m2 * self.field_efficiency
        )

    def field_thermal_series(self, dni, n_units=1):
        """Thermal power collected each hour, in kW."""
        area = self.aperture_area_m2() * n_units
        return [
            max(0.0, d) * area * self.field_efficiency / 1000.0 for d in dni
        ]

    def available_kw(self, n_units, t, state):
        """
        Electrical output available: whatever the store holds, capped by the
        block rating, net of parasitic consumption.
        """
        thermal = state.get("csp_thermal_kw", [])
        stored = state.get("csp_store_kwh", 0.0)
        direct = thermal[t] if t < len(thermal) else 0.0
        thermal_available = direct + stored
        gross = min(
            n_units * self.rated_kw, thermal_available * self.block_efficiency
        )
        return max(0.0, gross * (1.0 - self.parasitic_ratio))


# =====================================================================
# Geothermal
# =====================================================================

class GeothermalPlant(Dispatchable):
    """
    Binary-cycle geothermal.

    Near-constant output, very low marginal cost, high capital, and slow to
    change output — so it belongs at the cheap end of the merit order and
    should be sized to base load rather than to peak.

    Output falls slightly in hot weather because the binary cycle rejects
    heat to ambient. It is a small effect and it happens exactly when demand
    peaks in hot climates, which is why it is modelled rather than ignored.
    """

    technology = "geothermal"
    unit_label = "unit"
    default_merit = DEFAULT_MERIT["dispatchable_cheap"] - 5

    def __init__(self, name="Geothermal", rated_kw=1000.0,
                 availability=0.95, ambient_derate_per_k=0.005,
                 design_ambient_c=20.0, resource_decline_per_year=0.005, **kw):
        kw.setdefault("min_load_ratio", 0.50)
        kw.setdefault("lifetime_years", 30)
        kw.setdefault("ramp_rate_per_hour", 0.15)
        super().__init__(name=name, **kw)
        self.rated_kw = float(rated_kw)
        self.availability = float(availability)
        self.ambient_derate_per_k = float(ambient_derate_per_k)
        self.design_ambient_c = float(design_ambient_c)
        self.resource_decline_per_year = float(resource_decline_per_year)

    def rated_power_kw(self):
        return self.rated_kw

    def available_kw(self, n_units, t, state):
        temps = state.get("temperature_c")
        derate = 1.0
        if temps and t < len(temps):
            excess = temps[t] - self.design_ambient_c
            if excess > 0:
                derate = max(0.5, 1.0 - self.ambient_derate_per_k * excess)
        return n_units * self.rated_kw * self.availability * derate


# =====================================================================
# Marine
# =====================================================================

class TidalStream(NonDispatchable):
    """
    Tidal stream turbine.

    Power goes with the cube of current speed, like wind, but the resource
    is deterministic: it comes from lunar and solar harmonics, so a
    synthetic series built from tidal constituents is a legitimate input
    where a synthetic wind year would not be.

    The M2 and S2 constituents alone reproduce the spring-neap cycle well
    enough for sizing, which is why they are the default.
    """

    technology = "tidal"
    unit_label = "turbine"

    def __init__(self, name="Tidal stream turbine", rated_kw=100.0,
                 rotor_diameter_m=10.0, cut_in_ms=0.7, rated_ms=2.5,
                 cp=0.40, **kw):
        kw.setdefault("lifetime_years", 25)
        super().__init__(name=name, **kw)
        self.rated_kw = float(rated_kw)
        self.rotor_diameter_m = float(rotor_diameter_m)
        self.cut_in_ms = float(cut_in_ms)
        self.rated_ms = float(rated_ms)
        self.cp = float(cp)

    def rated_power_kw(self):
        return self.rated_kw

    def resource_key(self):
        return "tidal_velocity_ms"

    def swept_area_m2(self):
        return math.pi * (self.rotor_diameter_m / 2.0) ** 2

    def power_at(self, v):
        if v < self.cut_in_ms:
            return 0.0
        if v >= self.rated_ms:
            return self.rated_kw
        p = (
            0.5 * WATER_DENSITY * self.swept_area_m2() * v ** 3
            * self.cp / 1000.0
        )
        return min(self.rated_kw, p)

    def generate(self, resources, location=None):
        v = resources.get("tidal_velocity_ms")
        if not v:
            v = self.synthetic_velocity()
        out = [self.power_at(x) for x in v]
        hours = len(out)
        return out, {
            "rated_kw": self.rated_kw,
            "annual_kwh": sum(out),
            "capacity_factor": (
                sum(out) / (self.rated_kw * hours) if self.rated_kw else 0.0
            ),
            "mean_velocity_ms": sum(v) / hours if hours else 0.0,
            "synthetic": "tidal_velocity_ms" not in resources,
        }

    def synthetic_velocity(self, peak_spring_ms=2.5, hours=8760):
        """
        Tidal current from the two dominant semidiurnal constituents.

        M2 has a period of 12.4206 h and S2 exactly 12 h; their beat is the
        14.77-day spring-neap cycle. Amplitude ratio 0.46 is a typical
        open-coast value.
        """
        m2_period = 12.4206
        s2_period = 12.0000
        s2_ratio = 0.46
        scale = peak_spring_ms / (1.0 + s2_ratio)
        out = []
        for t in range(hours):
            m2 = math.sin(2 * math.pi * t / m2_period)
            s2 = s2_ratio * math.sin(2 * math.pi * t / s2_period)
            out.append(abs(scale * (m2 + s2)))
        return out


class WaveEnergyConverter(NonDispatchable):
    """
    Wave energy converter.

    Wave power per metre of crest is proportional to H^2 * T, so the
    resource is described by significant wave height and energy period
    rather than a single speed. Capture width ratio is the device-specific
    efficiency and is where most of the uncertainty in wave energy sits.
    """

    technology = "wave"
    unit_label = "device"

    def __init__(self, name="Wave energy converter", rated_kw=250.0,
                 capture_width_m=20.0, capture_width_ratio=0.25, **kw):
        kw.setdefault("lifetime_years", 20)
        super().__init__(name=name, **kw)
        self.rated_kw = float(rated_kw)
        self.capture_width_m = float(capture_width_m)
        self.capture_width_ratio = float(capture_width_ratio)

    def rated_power_kw(self):
        return self.rated_kw

    def resource_key(self):
        return "wave_height_m"

    def wave_power_kw_per_m(self, hs, te):
        """Deep-water wave power flux: about 0.49 * Hs^2 * Te kW/m."""
        return 0.49 * hs * hs * te

    def generate(self, resources, location=None):
        hs = resources.get("wave_height_m")
        te = resources.get("wave_period_s")
        if not hs:
            raise ValueError(
                f"{self.name} needs a 'wave_height_m' series, and ideally "
                f"'wave_period_s' as well."
            )
        if not te:
            # Without a measured period, the common empirical relation
            # Te ~ 4.5 * sqrt(Hs) is used, and flagged.
            te = [4.5 * math.sqrt(max(0.01, h)) for h in hs]

        out = []
        for h, p in zip(hs, te):
            flux = self.wave_power_kw_per_m(h, p)
            captured = flux * self.capture_width_m * self.capture_width_ratio
            out.append(min(self.rated_kw, max(0.0, captured)))
        hours = len(out)
        return out, {
            "rated_kw": self.rated_kw,
            "annual_kwh": sum(out),
            "capacity_factor": (
                sum(out) / (self.rated_kw * hours) if self.rated_kw else 0.0
            ),
            "period_estimated": "wave_period_s" not in resources,
        }


# =====================================================================
# Fuel cell and CHP
# =====================================================================

class FuelCell(Dispatchable):
    """
    Hydrogen or natural-gas fuel cell.

    Efficiency RISES at part load — the opposite of a combustion engine —
    because the activation and ohmic losses that dominate at high current
    density fall away. So a fuel cell is a good choice for a varying load,
    and running one at full rating to "be efficient" is precisely wrong.

    Stack life is counted in operating hours and degrades with cycling, so
    frequent starts cost more than continuous running.
    """

    technology = "fuel_cell"
    unit_label = "stack"
    default_merit = DEFAULT_MERIT["dispatchable_fuel"] - 5

    def __init__(self, name="Fuel cell", rated_kw=100.0,
                 nominal_efficiency=0.50, part_load_bonus=0.12,
                 fuel="hydrogen", fuel_price_per_kg=5.0,
                 lhv_kwh_per_kg=33.3, stack_life_hours=40000,
                 degradation_per_1000h=0.01, **kw):
        kw.setdefault("min_load_ratio", 0.10)
        kw.setdefault("lifetime_years", 15)
        super().__init__(name=name, **kw)
        self.rated_kw = float(rated_kw)
        self.nominal_efficiency = float(nominal_efficiency)
        self.part_load_bonus = float(part_load_bonus)
        self.fuel = fuel
        self.fuel_price_per_kg = float(fuel_price_per_kg)
        self.lhv_kwh_per_kg = float(lhv_kwh_per_kg)
        self.stack_life_hours = float(stack_life_hours)
        self.degradation_per_1000h = float(degradation_per_1000h)

    def rated_power_kw(self):
        return self.rated_kw

    def efficiency_at(self, load_ratio):
        """
        Efficiency against load ratio.

        Peaks around 30-40% load and falls toward the rating. The curve
        below is a simple quadratic fit to that shape, which is enough for
        an energy model and honest about not being a polarisation curve.
        """
        r = max(0.05, min(1.0, load_ratio))
        bonus = self.part_load_bonus * (1.0 - r) * (r / 0.35 if r < 0.35 else 1.0)
        return min(0.70, self.nominal_efficiency + bonus)

    def fuel_kg_per_hour(self, n_units, output_kw):
        if output_kw <= 0 or n_units <= 0:
            return 0.0
        ratio = output_kw / (n_units * self.rated_kw)
        eff = self.efficiency_at(ratio)
        thermal_kwh = output_kw / eff
        return thermal_kwh / self.lhv_kwh_per_kg

    def marginal_cost(self, n_units, output_kw):
        if output_kw <= 0:
            return 0.0
        return (
            self.fuel_kg_per_hour(n_units, output_kw)
            * self.fuel_price_per_kg / output_kw
        )

    def annual_cost(self, n_units, series, dt_h=1.0):
        # Only charged when the fuel is bought. Hydrogen produced on site by
        # an electrolyser in the same system is already paid for by the
        # electricity that made it, and charging again would double-count.
        if self.fuel == "hydrogen_onsite":
            return 0.0
        total = 0.0
        for p in series:
            total += self.fuel_kg_per_hour(n_units, p) * dt_h
        return total * self.fuel_price_per_kg

    def summary(self, n_units, series, dt_h=1.0):
        s = super().summary(n_units, series, dt_h)
        fuel = sum(self.fuel_kg_per_hour(n_units, p) * dt_h for p in series)
        run_h = sum(1 for p in series if p > 1e-9)
        ratios = [
            p / (n_units * self.rated_kw)
            for p in series if p > 1e-9 and n_units > 0
        ]
        s.update({
            "fuel_kg": fuel,
            "run_hours": run_h,
            "mean_efficiency": (
                sum(self.efficiency_at(r) for r in ratios) / len(ratios)
                if ratios else 0.0
            ),
            "stack_life_years": (
                self.stack_life_hours / run_h if run_h > 0 else None
            ),
            "emissions_kg": 0.0 if self.fuel.startswith("hydrogen") else fuel * 2.75,
        })
        return s


class CHPUnit(Dispatchable):
    """
    Combined heat and power.

    In most real installations the heat demand sizes the unit and
    electricity is the by-product, so `heat_led` is the default. Sizing a
    CHP on electrical demand and treating heat as a bonus inverts the actual
    constraint and produces units that dump heat all summer.

    The heat credit is the value of fuel displaced in the boiler that would
    otherwise have met the same demand. Counting heat as free revenue,
    rather than as avoided boiler fuel, systematically overstates CHP.
    """

    technology = "chp"
    unit_label = "unit"

    def __init__(self, name="CHP unit", rated_kw=100.0,
                 electrical_efficiency=0.35, thermal_efficiency=0.50,
                 fuel_price_per_kwh=0.035, boiler_efficiency=0.85,
                 heat_led=True, emission_factor_kg_per_kwh_fuel=0.202, **kw):
        kw.setdefault("min_load_ratio", 0.50)
        kw.setdefault("lifetime_years", 15)
        super().__init__(name=name, **kw)
        self.rated_kw = float(rated_kw)
        self.electrical_efficiency = float(electrical_efficiency)
        self.thermal_efficiency = float(thermal_efficiency)
        self.fuel_price_per_kwh = float(fuel_price_per_kwh)
        self.boiler_efficiency = float(boiler_efficiency)
        self.heat_led = bool(heat_led)
        self.emission_factor = float(emission_factor_kg_per_kwh_fuel)

        total = self.electrical_efficiency + self.thermal_efficiency
        if total > 1.0:
            raise ValueError(
                f"{name}: electrical + thermal efficiency is {total:.2f}, "
                f"which exceeds unity. Check the figures — they should be on "
                f"the same basis, usually LHV."
            )

    def rated_power_kw(self):
        return self.rated_kw

    @property
    def heat_to_power_ratio(self):
        return self.thermal_efficiency / self.electrical_efficiency

    def rated_heat_kw(self):
        return self.rated_kw * self.heat_to_power_ratio

    def available_kw(self, n_units, t, state):
        """
        In heat-led mode the electrical output is whatever the heat demand
        implies, not whatever the electrical demand wants.
        """
        cap = n_units * self.rated_kw
        if not self.heat_led:
            return cap
        heat = state.get("heat_demand_kw")
        if not heat or t >= len(heat):
            return cap
        implied = heat[t] / self.heat_to_power_ratio
        return max(0.0, min(cap, implied))

    def fuel_kwh(self, output_kw):
        if self.electrical_efficiency <= 0:
            return 0.0
        return output_kw / self.electrical_efficiency

    def heat_kw(self, output_kw):
        return output_kw * self.heat_to_power_ratio

    def marginal_cost(self, n_units, output_kw):
        """
        Net cost per kWh electrical, after crediting displaced boiler fuel.

        This is what makes CHP competitive and why a model that omits the
        credit will never build one.
        """
        if output_kw <= 0:
            return 0.0
        fuel = self.fuel_kwh(output_kw) * self.fuel_price_per_kwh
        heat = self.heat_kw(output_kw)
        credit = (
            heat / self.boiler_efficiency * self.fuel_price_per_kwh
            if self.boiler_efficiency > 0 else 0.0
        )
        return (fuel - credit) / output_kw

    def annual_cost(self, n_units, series, dt_h=1.0):
        energy = sum(series) * dt_h
        fuel = self.fuel_kwh(energy) * self.fuel_price_per_kwh
        heat = self.heat_kw(energy)
        credit = (
            heat / self.boiler_efficiency * self.fuel_price_per_kwh
            if self.boiler_efficiency > 0 else 0.0
        )
        return fuel - credit

    def summary(self, n_units, series, dt_h=1.0):
        s = super().summary(n_units, series, dt_h)
        energy = s["energy_kwh"]
        fuel = self.fuel_kwh(energy)
        heat = self.heat_kw(energy)
        s.update({
            "heat_kwh": heat,
            "fuel_kwh": fuel,
            "heat_to_power_ratio": self.heat_to_power_ratio,
            "total_efficiency": (
                (energy + heat) / fuel if fuel > 0 else 0.0
            ),
            "boiler_fuel_displaced_kwh": (
                heat / self.boiler_efficiency if self.boiler_efficiency else 0.0
            ),
            "emissions_kg": fuel * self.emission_factor,
            "heat_led": self.heat_led,
        })
        return s
