"""
Asset abstraction.

The engine's first version hard-coded four decision variables: PV, wind,
battery, generator. That does not survive contact with a real technology
list — hydro, biomass, CSP, fuel cells, pumped storage, flywheels and
hydrogen all behave differently, and bolting each one into a fixed vector
produces a dispatch loop nobody can read or verify.

Everything here reduces to four behaviours, and the dispatch only needs to
know which one an asset has:

  NonDispatchable   produces whatever the resource gives it this hour.
                    PV, wind, run-of-river hydro, tidal.
                    You cannot ask it for more; you can only curtail it.

  Dispatchable      produces on command, up to a rating, subject to a
                    minimum stable output and a fuel or resource cost.
                    Generators, biomass, fuel cells, CHP, geothermal, CSP
                    with storage.

  Storage           absorbs and releases energy with a state of charge and
                    a round-trip efficiency. Batteries, pumped hydro,
                    flywheels, supercapacitors, compressed air, hydrogen,
                    thermal.

  FlexibleLoad      a demand that can be shifted within constraints, and in
                    some cases reversed. EV fleets, and later any
                    controllable process load.

A technology is defined once, by which base class it inherits and what it
does in three or four methods. The dispatch never grows a new branch for a
new technology, which is what keeps the conservation guarantee intact as
the library expands.

The merit order below is the one piece of global policy: it decides who is
asked first when there is a deficit, and who is filled first when there is
a surplus. It is a property of the asset class rather than a chain of
if-statements in the dispatch, so a new technology declares its place
rather than being wedged into someone else's.
"""

from __future__ import annotations

# ---------------------------------------------------------------- roles
GENERATOR = "generator"
STORAGE = "storage"
FLEXIBLE_LOAD = "flexible_load"
CONVERTER = "converter"
GRID = "grid"

# Lower numbers are called first. The defaults encode the ordinary economic
# logic: use free energy before stored energy, stored before purchased,
# purchased before burnt fuel. A technology can override its own value, and
# the user can override any of them.
DEFAULT_MERIT = {
    # discharge / supply order in a deficit hour
    "storage_short": 10,      # flywheel, supercapacitor: fast, cheap cycles
    "storage_battery": 20,
    "flexible_v2x": 30,       # vehicle-to-grid, before buying or burning
    "storage_long": 40,       # pumped hydro, CAES, hydrogen
    "grid_import": 50,
    "dispatchable_cheap": 60,  # geothermal, biomass with cheap feedstock
    "dispatchable_fuel": 70,   # diesel, gas
    # charge / absorb order in a surplus hour
    "charge_battery": 20,
    "charge_flexible": 25,     # meet EV departure targets before exporting
    "charge_long": 40,
    "export": 60,
    "curtail": 99,
}


class Asset:
    """
    Common interface for anything the optimiser can size.

    An asset describes ONE unit. The optimiser chooses how many units, so
    every quantity here is per-unit and every cost is per-unit.
    """

    role = None
    technology = "generic"
    sizable = True          # False for the grid connection, which is fixed
    unit_label = "unit"

    def __init__(self, name=None, capital_cost=0.0, replacement_cost=0.0,
                 om_cost_per_year=0.0, lifetime_years=20, merit=None,
                 enabled=True, **kw):
        self.name = name or self.technology
        self.capital_cost = float(capital_cost)
        self.replacement_cost = float(replacement_cost)
        self.om_cost_per_year = float(om_cost_per_year)
        self.lifetime_years = float(lifetime_years)
        self.merit = merit
        self.enabled = bool(enabled)
        self.extra = kw

        if self.lifetime_years <= 0:
            raise ValueError(
                f"{self.name}: lifetime must be positive, got "
                f"{self.lifetime_years}"
            )

    # ------------------------------------------------------------ sizing

    def rated_power_kw(self):
        """Nameplate power of one unit. Used for converter and cable sizing."""
        return 0.0

    def rated_energy_kwh(self):
        """Nameplate energy of one unit, where the concept applies."""
        return 0.0

    def describe(self, n_units):
        """One-line description of n units, for reports and diagrams."""
        p = self.rated_power_kw() * n_units
        e = self.rated_energy_kwh() * n_units
        if e > 0 and p > 0:
            return f"{self.name}: {e:,.0f} kWh / {p:,.0f} kW"
        if p > 0:
            return f"{self.name}: {p:,.0f} kW"
        return f"{self.name}: {n_units} {self.unit_label}"

    def annual_cost(self, n_units, series, dt_h=1.0):
        """
        Recurring cost that escalates with time — fuel, feedstock, water.

        Returned separately from O&M because the two are discounted with
        different present-worth factors.
        """
        return 0.0

    def summary(self, n_units, series, dt_h=1.0):
        """Technology-specific performance figures for the report."""
        total = sum(series) * dt_h if series else 0.0
        cap = self.rated_power_kw() * n_units
        hours = len(series) if series else 0
        return {
            "technology": self.technology,
            "name": self.name,
            "units": n_units,
            "rated_kw": cap,
            "energy_kwh": total,
            "capacity_factor": (
                total / (cap * hours) if cap > 0 and hours else 0.0
            ),
        }

    def to_dict(self):
        return {
            "technology": self.technology,
            "name": self.name,
            "role": self.role,
            "capital_cost": self.capital_cost,
            "replacement_cost": self.replacement_cost,
            "om_cost_per_year": self.om_cost_per_year,
            "lifetime_years": self.lifetime_years,
            "rated_kw": self.rated_power_kw(),
            "rated_kwh": self.rated_energy_kwh(),
        }

    def __repr__(self):
        return f"{type(self).__name__}({self.name!r})"


class NonDispatchable(Asset):
    """
    A generator whose output is set by the resource, not by demand.

    The engine asks it once, before the dispatch loop, for a per-unit hourly
    series. During dispatch it can only be curtailed.
    """

    role = GENERATOR
    dispatchable = False

    def generate(self, resources, location=None):
        """
        Per-unit hourly output in kW, plus an info dict.

        Must return (list_of_8760_floats, info). Named `generate` rather
        than `output_series` so that technologies which already expose a
        richer, technology-specific `output_series` signature (PV takes
        irradiance components, wind takes a reference height) can keep it
        and adapt here, instead of having their public API flattened to fit
        a common shape.
        """
        raise NotImplementedError(
            f"{type(self).__name__} must implement generate()"
        )

    def resource_key(self):
        """
        Name of the resource series this technology needs, so the UI can
        tell the user what to supply and the engine can fail with a useful
        message rather than a KeyError.
        """
        return None


class Dispatchable(Asset):
    """
    A generator that produces on command.

    `min_load_ratio` is a real operating constraint for combustion plant and
    is why a large unit serving a small deficit is a design error rather
    than merely inefficient.
    """

    role = GENERATOR
    dispatchable = True
    default_merit = DEFAULT_MERIT["dispatchable_fuel"]

    def __init__(self, min_load_ratio=0.0, ramp_rate_per_hour=None,
                 min_runtime_hours=1, startup_cost=0.0, **kw):
        super().__init__(**kw)
        self.min_load_ratio = float(min_load_ratio)
        self.ramp_rate_per_hour = ramp_rate_per_hour
        self.min_runtime_hours = int(min_runtime_hours)
        self.startup_cost = float(startup_cost)
        if not (0.0 <= self.min_load_ratio < 1.0):
            raise ValueError(
                f"{self.name}: min_load_ratio must be in [0, 1), got "
                f"{self.min_load_ratio}"
            )

    def available_kw(self, n_units, t, state):
        """Maximum output available this hour. Resource-limited plant overrides."""
        return n_units * self.rated_power_kw()

    def min_output_kw(self, n_units, t, state):
        return n_units * self.rated_power_kw() * self.min_load_ratio

    def dispatch(self, n_units, demand_kw, t, state):
        """
        Decide output for a requested demand.

        Returns (output_kw, forced_surplus_kw). Forced surplus is what the
        minimum load ratio produces above what was asked for — energy that
        must be stored or curtailed, and a clear signal of oversizing.
        """
        if n_units <= 0 or demand_kw <= 0:
            return 0.0, 0.0
        hi = self.available_kw(n_units, t, state)
        if hi <= 0:
            return 0.0, 0.0
        lo = min(self.min_output_kw(n_units, t, state), hi)
        if demand_kw >= hi:
            return hi, 0.0
        if demand_kw >= lo:
            return demand_kw, 0.0
        return lo, lo - demand_kw

    def marginal_cost(self, n_units, output_kw):
        """Cost per kWh at this output, used to order dispatchable plant."""
        return 0.0


class Storage(Asset):
    """
    Anything with a state of charge.

    The efficiency convention is fixed engine-wide and stated here because
    getting it wrong is a silent 10% error: `efficiency` is the ONE-WAY
    figure, so round trip is its square, and the available charge and
    discharge limits are scaled so that draining the full available power
    lands exactly on the floor rather than below it.
    """

    role = STORAGE
    default_merit = DEFAULT_MERIT["storage_battery"]

    def __init__(self, nominal_energy_kwh=1.0, nominal_power_kw=1.0,
                 efficiency=0.95, soc_min=0.0, soc_max=1.0, soc_initial=0.5,
                 self_discharge_per_hour=0.0, cycles_to_eol=None,
                 calendar_life_years=None, **kw):
        super().__init__(**kw)
        self.nominal_energy_kwh = float(nominal_energy_kwh)
        self.nominal_power_kw = float(nominal_power_kw)
        self.efficiency = float(efficiency)
        self.soc_min = float(soc_min)
        self.soc_max = float(soc_max)
        self.soc_initial = float(soc_initial)
        self.self_discharge_per_hour = float(self_discharge_per_hour)
        self.cycles_to_eol = cycles_to_eol
        self.calendar_life_years = (
            calendar_life_years if calendar_life_years is not None
            else self.lifetime_years
        )

        if not (0.0 < self.efficiency <= 1.0):
            raise ValueError(
                f"{self.name}: one-way efficiency must be in (0, 1], got "
                f"{self.efficiency}"
            )
        if not (0.0 <= self.soc_min < self.soc_max <= 1.0):
            raise ValueError(
                f"{self.name}: invalid SOC window "
                f"[{self.soc_min}, {self.soc_max}]"
            )
        if not (self.soc_min <= self.soc_initial <= self.soc_max):
            raise ValueError(
                f"{self.name}: initial SOC {self.soc_initial} lies outside "
                f"the operating window [{self.soc_min}, {self.soc_max}]"
            )

    @property
    def round_trip_efficiency(self):
        return self.efficiency ** 2

    def rated_power_kw(self):
        return self.nominal_power_kw

    def rated_energy_kwh(self):
        return self.nominal_energy_kwh

    def c_rate(self):
        if self.nominal_energy_kwh <= 0:
            return 0.0
        return self.nominal_power_kw / self.nominal_energy_kwh

    def usable_energy_kwh(self, n_units):
        return n_units * self.nominal_energy_kwh * (self.soc_max - self.soc_min)

    def max_charge_kw(self, n_units, soc, t=None):
        """
        Power the store can absorb at its terminals.

        `step` adds charge * efficiency / capacity, so reaching soc_max
        requires capacity * (soc_max - soc) / efficiency at the terminals.
        """
        if n_units <= 0:
            return 0.0
        by_power = n_units * self.nominal_power_kw
        headroom = n_units * self.nominal_energy_kwh * (self.soc_max - soc)
        by_energy = headroom / self.efficiency if self.efficiency > 0 else headroom
        return max(0.0, min(by_power, by_energy))

    def max_discharge_kw(self, n_units, soc, t=None):
        """
        Power the store can deliver at its terminals.

        Scaled by the efficiency for the same reason, in the other
        direction: without it the store is drained past soc_min by 1/eff.
        """
        if n_units <= 0:
            return 0.0
        by_power = n_units * self.nominal_power_kw
        available = n_units * self.nominal_energy_kwh * (soc - self.soc_min)
        return max(0.0, min(by_power, available * self.efficiency))

    def step(self, n_units, soc, charge_kw, discharge_kw, dt_h=1.0):
        if n_units <= 0:
            return 0.0
        cap = n_units * self.nominal_energy_kwh
        if cap <= 0:
            return 0.0
        delta = (charge_kw * self.efficiency - discharge_kw / self.efficiency) * dt_h
        soc = soc + delta / cap
        if self.self_discharge_per_hour:
            soc -= self.self_discharge_per_hour * dt_h
        # Clamped to the physical range [0, 1], deliberately NOT to
        # [soc_min, soc_max]. The operating window constrains what the
        # dispatch may draw; it does not stop a store from losing charge on
        # its own. A flywheel at 2% an hour is empty after a quiet day, and
        # clamping it at soc_min would invent energy that is not there. The
        # discharge limit above already refuses to supply from below the
        # floor, so nothing is drawn out of that region.
        return max(0.0, min(1.0, soc))

    def equivalent_full_cycles(self, n_units, discharge_kwh):
        cap = n_units * self.nominal_energy_kwh
        return discharge_kwh / cap if cap > 0 else 0.0

    def expected_life_years(self, n_units, annual_discharge_kwh):
        """Whichever of cycle life and calendar life runs out first."""
        if not self.cycles_to_eol:
            return self.calendar_life_years
        cpy = self.equivalent_full_cycles(n_units, annual_discharge_kwh)
        if cpy <= 0:
            return self.calendar_life_years
        return min(self.cycles_to_eol / cpy, self.calendar_life_years)


class FlexibleLoad(Asset):
    """
    A demand that can be shifted, and sometimes reversed.

    Distinct from Storage because it has a service obligation: an EV must
    reach its departure state of charge whether or not that is cheap. A
    battery has no such duty, and conflating the two is how V2G studies end
    up reporting savings that would strand the vehicles.
    """

    role = FLEXIBLE_LOAD
    default_merit = DEFAULT_MERIT["charge_flexible"]

    def profile(self, seed=None):
        """Per-hour availability and requirement, as a dict of series."""
        raise NotImplementedError

    def max_charge_kw(self, n_units, soc, t, profile):
        return 0.0

    def max_discharge_kw(self, n_units, soc, t, profile):
        return 0.0

    def required_kw(self, n_units, soc, t, profile):
        """
        Power that must be taken this hour to meet the service obligation,
        regardless of price. Returning more than zero here is what makes the
        dispatch buy from the grid at a bad time rather than fail a duty.
        """
        return 0.0

    def step(self, n_units, soc, charge_kw, discharge_kw, t, profile, dt_h=1.0):
        raise NotImplementedError


class AssetRegistry:
    """
    The ordered set of assets in a study.

    Order is the decision-vector order, fixed at construction, so a saved
    project and a re-run produce the same vector meaning. Adding a
    technology later appends rather than inserts, for the same reason.
    """

    def __init__(self, assets=None):
        self._assets = []
        self._by_key = {}
        for a in assets or []:
            self.add(a)

    def add(self, asset, key=None):
        key = key or asset.technology
        if key in self._by_key:
            n = 2
            while f"{key}_{n}" in self._by_key:
                n += 1
            key = f"{key}_{n}"
        self._by_key[key] = asset
        self._assets.append((key, asset))
        return key

    def get(self, key):
        return self._by_key.get(key)

    def keys(self):
        return [k for k, _ in self._assets]

    def items(self):
        return list(self._assets)

    def sizable(self):
        """Assets the optimiser chooses a count for, in vector order."""
        return [(k, a) for k, a in self._assets if a.sizable and a.enabled]

    def by_role(self, role):
        return [(k, a) for k, a in self._assets if a.role == role and a.enabled]

    def non_dispatchable(self):
        return [
            (k, a) for k, a in self._assets
            if isinstance(a, NonDispatchable) and a.enabled
        ]

    def dispatchable(self):
        """Dispatchable generators in merit order, cheapest first."""
        items = [
            (k, a) for k, a in self._assets
            if isinstance(a, Dispatchable) and a.enabled
        ]
        return sorted(
            items,
            key=lambda ka: (
                ka[1].merit if ka[1].merit is not None else ka[1].default_merit
            ),
        )

    def storage(self):
        """Storage in merit order: fast and cheap-cycling first."""
        items = [
            (k, a) for k, a in self._assets
            if isinstance(a, Storage) and a.enabled
        ]
        return sorted(
            items,
            key=lambda ka: (
                ka[1].merit if ka[1].merit is not None else ka[1].default_merit
            ),
        )

    def flexible_loads(self):
        return [
            (k, a) for k, a in self._assets
            if isinstance(a, FlexibleLoad) and a.enabled
        ]

    def __len__(self):
        return len(self._assets)

    def __iter__(self):
        return iter(self._assets)

    def __repr__(self):
        return f"AssetRegistry({[k for k, _ in self._assets]})"
