"""
Storage technologies.

All of these share the Storage contract — a state of charge, a round-trip
efficiency, power and energy limits — so what distinguishes them is the
behaviour the contract does not capture:

  Pumped hydro      Very long life, poor energy density, and its energy
                    capacity depends on head and reservoir volume rather
                    than on a nameplate. Charging and discharging
                    efficiencies genuinely differ (pumps and turbines are
                    not the same machine), so it is the one technology here
                    with asymmetric efficiency.

  Flywheel          Enormous power, tiny energy, and self-discharge
                    measured in percent per HOUR rather than per month.
                    It cannot do daily shifting and should never be sized
                    for it; it exists for ride-through and frequency work.

  Supercapacitor    The same shape as a flywheel, more extreme still:
                    effectively unlimited cycles, seconds of energy.

  Compressed air    Poor round-trip efficiency unless the heat of
                    compression is recovered. Diabatic CAES burns gas on
                    expansion, so it is not a pure store at all and its
                    emissions belong in the accounting.

  Thermal           Stores heat, not electricity. Only useful where the
                    demand is heat, or paired with a power block as in CSP.
                    Modelling it as an electrical store is a category error
                    the class refuses to make.

  Hydrogen          A three-part chain — electrolyser, tank, fuel cell —
                    with a round trip near 35%. That number decides where
                    hydrogen makes sense: never for daily shifting, only
                    for seasonal storage or where the hydrogen itself has
                    value.
"""

from __future__ import annotations

import math

from ..assets import Storage, DEFAULT_MERIT

WATER_DENSITY = 1000.0
GRAVITY = 9.80665


class BatteryStorage(Storage):
    """
    Electrochemical battery.

    Chemistry presets set efficiency, depth of discharge, cycle life and
    calendar life together, because those four are not independent — an LFP
    cell's deep discharge window is part of the same design that gives it
    its cycle life.
    """

    technology = "battery"
    unit_label = "module"
    default_merit = DEFAULT_MERIT["storage_battery"]

    PRESETS = {
        # (one-way eff, soc_min, soc_max, cycles, calendar yrs, self-disch/h)
        "lithium_nmc": (0.96, 0.10, 1.00, 5000, 15, 0.0000042),
        "lithium_lfp": (0.97, 0.05, 1.00, 7000, 20, 0.0000035),
        "lithium_titanate": (0.96, 0.00, 1.00, 20000, 20, 0.0000042),
        "sodium_ion": (0.94, 0.05, 1.00, 5000, 15, 0.0000050),
        "lead_acid_flooded": (0.90, 0.40, 1.00, 1200, 8, 0.0000420),
        "lead_acid_agm": (0.92, 0.40, 1.00, 1500, 10, 0.0000280),
        "nickel_iron": (0.80, 0.20, 1.00, 11000, 25, 0.0001200),
        "flow_vanadium": (0.87, 0.00, 1.00, 15000, 20, 0.0000000),
        "flow_zinc_bromine": (0.80, 0.00, 1.00, 10000, 20, 0.0000000),
        "sodium_sulphur": (0.90, 0.10, 1.00, 4500, 15, 0.0000000),
    }

    def __init__(self, name=None, chemistry="lithium_lfp",
                 nominal_energy_kwh=10.0, nominal_power_kw=5.0, **kw):
        if chemistry in self.PRESETS:
            eff, lo, hi, cyc, cal, sd = self.PRESETS[chemistry]
            kw.setdefault("efficiency", eff)
            kw.setdefault("soc_min", lo)
            kw.setdefault("soc_max", hi)
            kw.setdefault("cycles_to_eol", cyc)
            kw.setdefault("calendar_life_years", cal)
            kw.setdefault("self_discharge_per_hour", sd)
            kw.setdefault("lifetime_years", cal)
            kw.setdefault("soc_initial", (lo + hi) / 2.0)
        super().__init__(
            name=name or f"Battery ({chemistry.replace('_', ' ')})",
            nominal_energy_kwh=nominal_energy_kwh,
            nominal_power_kw=nominal_power_kw, **kw
        )
        self.chemistry = chemistry

    def summary(self, n_units, series, dt_h=1.0):
        s = super().summary(n_units, series, dt_h)
        s["chemistry"] = self.chemistry
        s["round_trip_efficiency"] = self.round_trip_efficiency
        s["c_rate"] = self.c_rate()
        return s


class PumpedHydroStorage(Storage):
    """
    Pumped hydro.

    Energy capacity comes from geography, not a catalogue: E = rho g V H eta
    over 3.6e6 to get kWh. That is why this class takes a reservoir volume
    and a head rather than a kWh rating, and derives the rest.

    Pump and turbine efficiencies differ, so the base class's symmetric
    one-way efficiency is replaced with an explicit pair. The geometric mean
    is handed to the base class so the inherited limit calculations stay
    consistent, and the true asymmetric values are used in `step`.
    """

    technology = "pumped_hydro"
    unit_label = "scheme"
    default_merit = DEFAULT_MERIT["storage_long"]

    def __init__(self, name="Pumped hydro", reservoir_m3=100000.0,
                 head_m=100.0, rated_power_kw_=1000.0,
                 pump_efficiency=0.88, turbine_efficiency=0.90,
                 evaporation_per_hour=0.0, **kw):
        energy = (
            WATER_DENSITY * GRAVITY * float(reservoir_m3) * float(head_m)
            / 3.6e6
        )
        combined = math.sqrt(
            float(pump_efficiency) * float(turbine_efficiency)
        )
        kw.setdefault("lifetime_years", 60)
        kw.setdefault("soc_min", 0.05)
        kw.setdefault("soc_max", 0.98)
        kw.setdefault("soc_initial", 0.5)
        kw.setdefault("calendar_life_years", 60)
        super().__init__(
            name=name, nominal_energy_kwh=energy,
            nominal_power_kw=float(rated_power_kw_),
            efficiency=combined,
            self_discharge_per_hour=float(evaporation_per_hour), **kw
        )
        self.reservoir_m3 = float(reservoir_m3)
        self.head_m = float(head_m)
        self.pump_efficiency = float(pump_efficiency)
        self.turbine_efficiency = float(turbine_efficiency)

    def step(self, n_units, soc, charge_kw, discharge_kw, dt_h=1.0):
        """Uses the true asymmetric efficiencies rather than the geometric mean."""
        if n_units <= 0:
            return 0.0
        cap = n_units * self.nominal_energy_kwh
        if cap <= 0:
            return 0.0
        delta = (
            charge_kw * self.pump_efficiency
            - discharge_kw / self.turbine_efficiency
        ) * dt_h
        soc = soc + delta / cap
        if self.self_discharge_per_hour:
            soc -= self.self_discharge_per_hour * dt_h
        return max(0.0, min(1.0, soc))

    def summary(self, n_units, series, dt_h=1.0):
        s = super().summary(n_units, series, dt_h)
        s.update({
            "reservoir_m3": self.reservoir_m3 * n_units,
            "head_m": self.head_m,
            "pump_efficiency": self.pump_efficiency,
            "turbine_efficiency": self.turbine_efficiency,
            "round_trip_efficiency": (
                self.pump_efficiency * self.turbine_efficiency
            ),
            "hours_at_rating": (
                self.nominal_energy_kwh / self.nominal_power_kw
                if self.nominal_power_kw else 0.0
            ),
        })
        return s


class Flywheel(Storage):
    """
    Flywheel energy storage.

    Self-discharge is 1-3% per HOUR, so a flywheel loses most of its charge
    overnight. It is a power device, not an energy device: the right jobs
    are ride-through, frequency response and smoothing a fast-varying load.
    Sizing one for daily solar shifting wastes the asset and the money, and
    the summary says so when the observed duty looks like that.
    """

    technology = "flywheel"
    unit_label = "unit"
    default_merit = DEFAULT_MERIT["storage_short"]

    def __init__(self, name="Flywheel", nominal_energy_kwh=5.0,
                 nominal_power_kw=100.0, **kw):
        kw.setdefault("efficiency", 0.95)
        kw.setdefault("soc_min", 0.05)
        kw.setdefault("soc_max", 1.00)
        kw.setdefault("soc_initial", 0.5)
        kw.setdefault("self_discharge_per_hour", 0.02)
        kw.setdefault("lifetime_years", 20)
        kw.setdefault("cycles_to_eol", 1000000)
        kw.setdefault("calendar_life_years", 20)
        super().__init__(
            name=name, nominal_energy_kwh=nominal_energy_kwh,
            nominal_power_kw=nominal_power_kw, **kw
        )

    def summary(self, n_units, series, dt_h=1.0):
        s = super().summary(n_units, series, dt_h)
        duration = (
            self.nominal_energy_kwh / self.nominal_power_kw
            if self.nominal_power_kw else 0.0
        )
        s["duration_hours"] = duration
        s["notes"] = []
        cycles = s.get("energy_kwh", 0.0)
        if duration < 0.5:
            s["notes"].append(
                f"This flywheel holds {duration * 60:.0f} minutes at its "
                f"rating and self-discharges at "
                f"{self.self_discharge_per_hour * 100:.1f}% per hour. It "
                f"cannot shift energy between day and night; if the design "
                f"is relying on it for that, use a battery instead."
            )
        return s


class Supercapacitor(Storage):
    """
    Supercapacitor bank.

    Seconds of energy, effectively unlimited cycles, and very high cost per
    kWh. It earns its place only where the duty is many shallow cycles a
    day — smoothing a crane, a lift, or a fast-cycling process — never
    where the duty is measured in hours.
    """

    technology = "supercapacitor"
    unit_label = "bank"
    default_merit = DEFAULT_MERIT["storage_short"] - 5

    def __init__(self, name="Supercapacitor", nominal_energy_kwh=0.5,
                 nominal_power_kw=50.0, **kw):
        kw.setdefault("efficiency", 0.98)
        kw.setdefault("soc_min", 0.10)
        kw.setdefault("soc_max", 1.00)
        kw.setdefault("soc_initial", 0.5)
        kw.setdefault("self_discharge_per_hour", 0.05)
        kw.setdefault("lifetime_years", 15)
        kw.setdefault("cycles_to_eol", 1000000)
        kw.setdefault("calendar_life_years", 15)
        super().__init__(
            name=name, nominal_energy_kwh=nominal_energy_kwh,
            nominal_power_kw=nominal_power_kw, **kw
        )


class CompressedAirStorage(Storage):
    """
    Compressed air energy storage.

    Adiabatic CAES recovers the heat of compression and reaches about 70%
    round trip. Diabatic CAES throws that heat away and burns natural gas
    to reheat the air on expansion — which means it is not a storage
    technology at all in the emissions accounting, and its gas burn is
    reported here rather than hidden inside a round-trip number.
    """

    technology = "caes"
    unit_label = "cavern"
    default_merit = DEFAULT_MERIT["storage_long"]

    def __init__(self, name="Compressed air storage", nominal_energy_kwh=10000.0,
                 nominal_power_kw=1000.0, adiabatic=True,
                 gas_kwh_per_kwh_out=0.0, gas_price_per_kwh=0.035, **kw):
        kw.setdefault("efficiency", 0.84 if adiabatic else 0.75)
        kw.setdefault("soc_min", 0.10)
        kw.setdefault("soc_max", 1.00)
        kw.setdefault("soc_initial", 0.5)
        kw.setdefault("lifetime_years", 30)
        kw.setdefault("calendar_life_years", 30)
        super().__init__(
            name=name, nominal_energy_kwh=nominal_energy_kwh,
            nominal_power_kw=nominal_power_kw, **kw
        )
        self.adiabatic = bool(adiabatic)
        # Diabatic plant needs roughly 1.2 kWh of gas per kWh delivered.
        self.gas_kwh_per_kwh_out = (
            0.0 if adiabatic else (gas_kwh_per_kwh_out or 1.2)
        )
        self.gas_price_per_kwh = float(gas_price_per_kwh)

    def annual_cost(self, n_units, series, dt_h=1.0):
        if self.adiabatic:
            return 0.0
        delivered = sum(x for x in series if x > 0) * dt_h
        return delivered * self.gas_kwh_per_kwh_out * self.gas_price_per_kwh

    def summary(self, n_units, series, dt_h=1.0):
        s = super().summary(n_units, series, dt_h)
        delivered = sum(x for x in series if x > 0) * dt_h
        gas = delivered * self.gas_kwh_per_kwh_out
        s.update({
            "adiabatic": self.adiabatic,
            "gas_kwh": gas,
            "emissions_kg": gas * 0.202,
            "round_trip_efficiency": self.round_trip_efficiency,
        })
        if not self.adiabatic and gas > 0:
            s.setdefault("notes", []).append(
                "This is diabatic CAES: it burns natural gas on expansion, "
                f"{gas:,.0f} kWh a year. Its emissions are real and are "
                "counted; do not present it as zero-carbon storage."
            )
        return s


class ThermalStorage(Storage):
    """
    Sensible-heat thermal storage.

    Stores HEAT. It can only serve a heat demand, or feed a power block as
    in CSP. The `serves` attribute makes that explicit, and the dispatch
    refuses to let a heat store supply an electrical deficit, because that
    is a category error a kWh-based model would otherwise permit silently.

    Standing loss is per hour and is the dominant design constraint: a tank
    losing 1% an hour has lost a fifth of its charge in a day.
    """

    technology = "thermal_storage"
    unit_label = "tank"
    default_merit = DEFAULT_MERIT["storage_long"]

    def __init__(self, name="Thermal storage", volume_m3=50.0,
                 delta_t_k=50.0, medium="water", standing_loss_per_hour=0.005,
                 serves="heat", charge_power_kw=100.0, **kw):
        # Specific heat capacity in kJ/(kg K) and density in kg/m3.
        media = {
            "water": (4.186, 1000.0),
            "molten_salt": (1.53, 1800.0),
            "concrete": (0.88, 2400.0),
            "rock": (0.84, 1600.0),
            "oil": (2.20, 850.0),
        }
        cp, rho = media.get(medium, media["water"])
        energy_kwh = volume_m3 * rho * cp * delta_t_k / 3600.0

        kw.setdefault("efficiency", 0.99)
        kw.setdefault("soc_min", 0.05)
        kw.setdefault("soc_max", 1.00)
        kw.setdefault("soc_initial", 0.5)
        kw.setdefault("lifetime_years", 25)
        kw.setdefault("calendar_life_years", 25)
        super().__init__(
            name=name, nominal_energy_kwh=energy_kwh,
            nominal_power_kw=float(charge_power_kw),
            self_discharge_per_hour=float(standing_loss_per_hour), **kw
        )
        self.volume_m3 = float(volume_m3)
        self.delta_t_k = float(delta_t_k)
        self.medium = medium
        self.serves = serves       # "heat" or "power_block"

    def summary(self, n_units, series, dt_h=1.0):
        s = super().summary(n_units, series, dt_h)
        s.update({
            "medium": self.medium,
            "volume_m3": self.volume_m3 * n_units,
            "delta_t_k": self.delta_t_k,
            "serves": self.serves,
            "standing_loss_per_day": 1.0 - (
                1.0 - self.self_discharge_per_hour
            ) ** 24,
        })
        return s


class HydrogenStorage(Storage):
    """
    The hydrogen chain: electrolyser, tank, fuel cell.

    Modelled as one Storage asset because that is how it behaves from the
    electrical bus, but the three components are sized and costed
    separately, because they are — and because the electrolyser and the
    fuel cell are usually very differently rated.

    The round trip is about 35%: roughly 65% for the electrolyser, 95% for
    compression and storage, and 55% for the fuel cell. That number is the
    whole story. Two-thirds of the input is lost, so hydrogen is never the
    right answer for daily shifting where a battery returns 90%. It earns
    its place only for seasonal storage — where a battery large enough
    would be absurd — or where the hydrogen has value of its own.

    Compensating for that, the energy capacity is nearly free once the tank
    exists, and there is no self-discharge to speak of, which is exactly
    what seasonal storage needs.
    """

    technology = "hydrogen"
    unit_label = "system"
    default_merit = DEFAULT_MERIT["storage_long"] + 5

    def __init__(self, name="Hydrogen storage", tank_kg=100.0,
                 electrolyser_kw=100.0, fuel_cell_kw=50.0,
                 electrolyser_efficiency=0.65, fuel_cell_efficiency=0.55,
                 compression_efficiency=0.95, lhv_kwh_per_kg=33.3,
                 boil_off_per_hour=0.0,
                 electrolyser_capital=0.0, fuel_cell_capital=0.0,
                 tank_capital=0.0, **kw):
        energy_kwh = float(tank_kg) * float(lhv_kwh_per_kg)
        combined = math.sqrt(
            float(electrolyser_efficiency) * float(compression_efficiency)
            * float(fuel_cell_efficiency)
        )
        kw.setdefault("soc_min", 0.05)
        kw.setdefault("soc_max", 0.98)
        kw.setdefault("soc_initial", 0.5)
        kw.setdefault("lifetime_years", 20)
        kw.setdefault("calendar_life_years", 20)
        kw.setdefault(
            "capital_cost",
            float(electrolyser_capital) + float(fuel_cell_capital)
            + float(tank_capital),
        )
        super().__init__(
            name=name, nominal_energy_kwh=energy_kwh,
            nominal_power_kw=float(electrolyser_kw),
            efficiency=combined,
            self_discharge_per_hour=float(boil_off_per_hour), **kw
        )
        self.tank_kg = float(tank_kg)
        self.electrolyser_kw = float(electrolyser_kw)
        self.fuel_cell_kw = float(fuel_cell_kw)
        self.electrolyser_efficiency = float(electrolyser_efficiency)
        self.fuel_cell_efficiency = float(fuel_cell_efficiency)
        self.compression_efficiency = float(compression_efficiency)
        self.lhv_kwh_per_kg = float(lhv_kwh_per_kg)
        self.electrolyser_capital = float(electrolyser_capital)
        self.fuel_cell_capital = float(fuel_cell_capital)
        self.tank_capital = float(tank_capital)

    @property
    def round_trip_efficiency(self):
        return (
            self.electrolyser_efficiency * self.compression_efficiency
            * self.fuel_cell_efficiency
        )

    def max_charge_kw(self, n_units, soc, t=None):
        """Charging is limited by the ELECTROLYSER rating."""
        if n_units <= 0:
            return 0.0
        by_power = n_units * self.electrolyser_kw
        headroom = n_units * self.nominal_energy_kwh * (self.soc_max - soc)
        charge_eff = self.electrolyser_efficiency * self.compression_efficiency
        by_energy = headroom / charge_eff if charge_eff > 0 else headroom
        return max(0.0, min(by_power, by_energy))

    def max_discharge_kw(self, n_units, soc, t=None):
        """Discharging is limited by the FUEL CELL rating, usually much smaller."""
        if n_units <= 0:
            return 0.0
        by_power = n_units * self.fuel_cell_kw
        stored = n_units * self.nominal_energy_kwh * (soc - self.soc_min)
        return max(0.0, min(by_power, stored * self.fuel_cell_efficiency))

    def step(self, n_units, soc, charge_kw, discharge_kw, dt_h=1.0):
        if n_units <= 0:
            return 0.0
        cap = n_units * self.nominal_energy_kwh
        if cap <= 0:
            return 0.0
        charge_eff = self.electrolyser_efficiency * self.compression_efficiency
        delta = (
            charge_kw * charge_eff - discharge_kw / self.fuel_cell_efficiency
        ) * dt_h
        soc = soc + delta / cap
        if self.self_discharge_per_hour:
            soc -= self.self_discharge_per_hour * dt_h
        return max(0.0, min(1.0, soc))

    def hydrogen_kg(self, energy_in_kwh):
        """Hydrogen produced from a given electrical input."""
        return (
            energy_in_kwh * self.electrolyser_efficiency
            * self.compression_efficiency / self.lhv_kwh_per_kg
        )

    def summary(self, n_units, series, dt_h=1.0):
        s = super().summary(n_units, series, dt_h)
        s.update({
            "tank_kg": self.tank_kg * n_units,
            "electrolyser_kw": self.electrolyser_kw * n_units,
            "fuel_cell_kw": self.fuel_cell_kw * n_units,
            "round_trip_efficiency": self.round_trip_efficiency,
            "storage_kwh": self.nominal_energy_kwh * n_units,
            "seasonal_capable": (
                self.nominal_energy_kwh * n_units
                / max(1.0, self.fuel_cell_kw * n_units) > 100
            ),
            "notes": [
                f"Round-trip efficiency is "
                f"{self.round_trip_efficiency * 100:.0f}%. About two-thirds "
                f"of the electricity put in is lost. This is only the right "
                f"technology for seasonal storage, or where the hydrogen "
                f"itself has a use — for daily cycling a battery returns "
                f"roughly 90%."
            ],
        })
        return s


LIBRARY = {
    "battery": BatteryStorage,
    "pumped_hydro": PumpedHydroStorage,
    "flywheel": Flywheel,
    "supercapacitor": Supercapacitor,
    "caes": CompressedAirStorage,
    "thermal_storage": ThermalStorage,
    "hydrogen": HydrogenStorage,
}
