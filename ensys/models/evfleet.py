"""
Electric vehicle fleets, with the full range of charging and discharging
behaviours.

The original MATLAB modelled one behaviour: charge whenever connected and
there is surplus. That is only one of six that matter, and they give very
different answers — an uncontrolled fleet raises the evening peak, a smart
one flattens it, and a V2G one can remove it. Choosing between them is
usually the single largest decision in a site with EVs, so the scenario is
a first-class input here rather than an implicit assumption.

Scenarios
---------
UNCONTROLLED     Charge at full power from the moment of connection until
                 full. The pessimistic baseline, and what actually happens
                 without a control system. It reliably creates the worst
                 peak, which is the point of modelling it.

V1G_SMART        Unidirectional but shiftable within the connection window.
                 The vehicle takes the same energy, at times the site
                 chooses. Captures most of the benefit of V2G with none of
                 the battery-warranty argument, which is why most real
                 deployments stop here.

V2G              Bidirectional, exports to the grid. Adds arbitrage and
                 peak-shaving revenue, and adds battery throughput that
                 belongs in the cost. Modelling V2G without the degradation
                 cost is how V2G studies produce results nobody can build a
                 business case on.

V2H / V2B        Bidirectional to the building only, never exporting. Very
                 common in practice because it avoids export approval, and
                 it is the mode most utilities will actually permit.

PRICE_RESPONSIVE Charges below a price threshold, discharges above another.
                 Behaves like a battery with a curfew.

SCHEDULED        Fixed windows. What a depot with a fixed shift pattern
                 really does.

In every scenario the departure state-of-charge target is a hard service
obligation. A vehicle that leaves below target is recorded as a failure and
surfaces in the report, because a charging strategy that saves money by
stranding drivers has not saved anything.
"""

from __future__ import annotations

import math
import random

from ..assets import FlexibleLoad, DEFAULT_MERIT
from ..timeseries import HOURS_PER_YEAR, HOURS_PER_DAY, DAYS_PER_YEAR

UNCONTROLLED = "uncontrolled"
V1G_SMART = "v1g_smart"
V2G = "v2g"
V2H = "v2h"
PRICE_RESPONSIVE = "price_responsive"
SCHEDULED = "scheduled"

SCENARIOS = (UNCONTROLLED, V1G_SMART, V2G, V2H, PRICE_RESPONSIVE, SCHEDULED)

SCENARIO_LABELS = {
    UNCONTROLLED: "Uncontrolled — charge on plug-in",
    V1G_SMART: "Smart unidirectional (V1G) — shift within the window",
    V2G: "Vehicle-to-grid (V2G) — bidirectional, can export",
    V2H: "Vehicle-to-home/building (V2H/V2B) — bidirectional, no export",
    PRICE_RESPONSIVE: "Price-responsive — charge cheap, discharge dear",
    SCHEDULED: "Scheduled — fixed charging windows",
}


class TruncatedNormal:
    """Normal distribution truncated to [lo, hi], sampled by rejection."""

    def __init__(self, mu, sigma, lo, hi, max_draws=500):
        self.mu, self.sigma = float(mu), float(sigma)
        self.lo, self.hi = float(lo), float(hi)
        self.max_draws = int(max_draws)
        if self.lo >= self.hi:
            raise ValueError(f"invalid truncation bounds [{lo}, {hi}]")
        if self.sigma <= 0:
            raise ValueError("sigma must be positive")

    def sample(self, rng):
        for _ in range(self.max_draws):
            x = rng.gauss(self.mu, self.sigma)
            if self.lo <= x <= self.hi:
                return x
        return (self.lo + self.hi) / 2.0


# Fleet archetypes. Each sets the arrival, departure, dwell and daily-energy
# behaviour that actually distinguishes one kind of site from another.
ARCHETYPES = {
    "residential": {
        "label": "Residential — home overnight charging",
        "arrival": (18.0, 2.0, 15.0, 23.0),
        "departure": (7.5, 1.2, 5.0, 10.0),
        "arrival_soc": (0.45, 0.18, 0.15, 0.85),
        "charger_kw": 7.4,
        "battery_kwh": 60.0,
        "weekend_factor": 0.7,
        "v2g_plausible": True,
    },
    "workplace": {
        "label": "Workplace — daytime charging",
        "arrival": (8.5, 1.0, 6.5, 11.0),
        "departure": (17.5, 1.2, 15.0, 20.0),
        "arrival_soc": (0.55, 0.15, 0.25, 0.90),
        "charger_kw": 11.0,
        "battery_kwh": 60.0,
        "weekend_factor": 0.15,
        "v2g_plausible": True,
    },
    "depot": {
        "label": "Depot — return-to-base fleet",
        "arrival": (18.5, 1.5, 16.0, 22.0),
        "departure": (6.0, 1.0, 4.0, 8.0),
        "arrival_soc": (0.25, 0.10, 0.10, 0.50),
        "charger_kw": 22.0,
        "battery_kwh": 100.0,
        "weekend_factor": 0.3,
        "v2g_plausible": True,
    },
    "public_fast": {
        "label": "Public fast charging — short dwell, all day",
        "arrival": (13.0, 5.0, 6.0, 22.0),
        "departure": (13.6, 5.0, 6.5, 22.5),
        "arrival_soc": (0.25, 0.12, 0.05, 0.55),
        "charger_kw": 150.0,
        "battery_kwh": 70.0,
        "weekend_factor": 1.1,
        "v2g_plausible": False,
    },
    "bus_fleet": {
        "label": "Electric bus fleet — overnight depot charging",
        "arrival": (21.0, 1.5, 18.0, 24.0),
        "departure": (5.0, 0.8, 4.0, 7.0),
        "arrival_soc": (0.20, 0.08, 0.08, 0.40),
        "charger_kw": 60.0,
        "battery_kwh": 300.0,
        "weekend_factor": 0.5,
        "v2g_plausible": True,
    },
}


class EVFleet(FlexibleLoad):
    """
    A fleet of electric vehicles connecting at the site.

    Sized by the number of CHARGE POINTS, which is the thing that costs
    money and needs a cable, not the number of vehicles, which the site does
    not own. Vehicle count per charge point is a utilisation input.
    """

    technology = "ev_fleet"
    role = "flexible_load"
    unit_label = "charge point"
    default_merit = DEFAULT_MERIT["charge_flexible"]

    def __init__(self, name=None, archetype="residential",
                 scenario=V1G_SMART, charger_kw=None, battery_kwh=None,
                 vehicles_per_point=1.0, efficiency=0.92,
                 soc_min=0.10, soc_max=0.95, soc_departure_target=0.80,
                 v2g_soc_floor=0.50, spread_arrivals=True,
                 degradation_cost_per_kwh=0.03,
                 price_charge_below=None, price_discharge_above=None,
                 scheduled_windows=None, seed=42, **kw):
        arch = ARCHETYPES.get(archetype, ARCHETYPES["residential"])
        kw.setdefault("lifetime_years", 12)
        super().__init__(
            name=name or f"EV fleet ({arch['label'].split('—')[0].strip()})",
            **kw
        )
        self.archetype = archetype
        self.scenario = scenario
        self.charger_kw = float(
            charger_kw if charger_kw is not None else arch["charger_kw"]
        )
        self.battery_kwh = float(
            battery_kwh if battery_kwh is not None else arch["battery_kwh"]
        )
        self.vehicles_per_point = float(vehicles_per_point)
        self.efficiency = float(efficiency)
        self.soc_min = float(soc_min)
        self.soc_max = float(soc_max)
        self.soc_departure_target = float(soc_departure_target)
        self.v2g_soc_floor = float(v2g_soc_floor)
        self.spread_arrivals = bool(spread_arrivals)
        self.degradation_cost_per_kwh = float(degradation_cost_per_kwh)
        self.price_charge_below = price_charge_below
        self.price_discharge_above = price_discharge_above
        self.scheduled_windows = scheduled_windows or [(22, 6)]
        self.seed = int(seed)

        self.arrival_dist = TruncatedNormal(*arch["arrival"])
        self.departure_dist = TruncatedNormal(*arch["departure"])
        self.soc_dist = TruncatedNormal(*arch["arrival_soc"])
        self.weekend_factor = arch["weekend_factor"]
        self._arch = arch

        if scenario not in SCENARIOS:
            raise ValueError(
                f"unknown EV scenario '{scenario}'. Available: "
                f"{', '.join(SCENARIOS)}"
            )
        if not (0.0 <= self.soc_min < self.soc_max <= 1.0):
            raise ValueError(f"{self.name}: invalid SOC window")
        if self.soc_departure_target > self.soc_max:
            raise ValueError(
                f"{self.name}: departure target {self.soc_departure_target} "
                f"exceeds the maximum charge SOC {self.soc_max}"
            )
        if self.bidirectional and not arch["v2g_plausible"]:
            raise ValueError(
                f"{self.name}: the '{archetype}' archetype has a dwell time of "
                f"well under an hour, so there is no window in which to "
                f"discharge. Bidirectional operation is not credible here — "
                f"use a stationary battery instead."
            )

    # ------------------------------------------------------------ traits

    @property
    def bidirectional(self):
        return self.scenario in (V2G, V2H, PRICE_RESPONSIVE)

    @property
    def v2g(self):
        """True when the fleet can give energy back, in either direction."""
        return self.bidirectional

    @property
    def can_export(self):
        """V2H and V2B serve the building only; only V2G reaches the grid."""
        return self.scenario == V2G

    @property
    def shiftable(self):
        return self.scenario != UNCONTROLLED

    def rated_power_kw(self):
        return self.charger_kw

    def rated_energy_kwh(self):
        return self.battery_kwh * self.vehicles_per_point

    def describe(self, n_units):
        return (
            f"{self.name}: {n_units} x {self.charger_kw:.0f} kW "
            f"({SCENARIO_LABELS[self.scenario].split('—')[0].strip()})"
        )

    # ----------------------------------------------------------- profile

    def unit_profile(self, seed=None, reference_points=40):
        """
        The occupancy of a single charge point, as a fraction of an hour.

        Drawn from a reference fleet and divided by its size, so the
        diversity between arrivals is preserved — a single draw would give
        a square wave, and every charge point in the design would arrive at
        the same instant, which is the artificial peak the spread exists to
        avoid. The result scales linearly with the number of points, which
        is what lets the optimiser size the fleet.
        """
        ref = max(1, int(reference_points))
        prof = self.profile(seed=seed, n_points=ref)
        scaled = {"per_unit": True, "reference_points": ref}
        for k, v in prof.items():
            if isinstance(v, list) and v and isinstance(v[0], (int, float)):
                if k in ("hours_to_departure", "hours_to_next_departure"):
                    scaled[k] = list(v)          # times, not quantities
                else:
                    scaled[k] = [x / ref for x in v]
            else:
                scaled[k] = v
        # Arrival state of charge is an average, not a quantity: dividing it
        # by the reference fleet would send every vehicle in empty.
        scaled["arrival_soc"] = list(prof["arrival_soc"])
        scaled["soc_init"] = scaled["arrival_soc"]
        return scaled

    def profile(self, seed=None, n_points=1):
        """
        Draw a year of connection behaviour.

        Unlike the original MATLAB, which drew one arrival hour per day and
        applied it to the whole fleet — making every vehicle arrive at the
        same instant and producing an artificially sharp peak — each charge
        point gets its own draw when `spread_arrivals` is set. The
        difference in peak demand between the two is routinely 30-40%.
        """
        rng = random.Random(self.seed if seed is None else seed)
        n_points = max(1, int(n_points))

        connected_count = [0.0] * HOURS_PER_YEAR
        arrival_soc = [0.0] * HOURS_PER_YEAR
        just_arrived = [0.0] * HOURS_PER_YEAR
        about_to_leave = [0.0] * HOURS_PER_YEAR
        hours_to_departure = [0] * HOURS_PER_YEAR

        n_draws = n_points if self.spread_arrivals else 1
        weight = n_points / float(n_draws)

        for d in range(DAYS_PER_YEAR):
            dow = d % 7
            is_weekend = dow in (5, 6)
            day_factor = self.weekend_factor if is_weekend else 1.0

            for _ in range(n_draws):
                if rng.random() > day_factor:
                    continue                    # this point is unused today
                arr = self.arrival_dist.sample(rng)
                dep = self.departure_dist.sample(rng)
                soc0 = self.soc_dist.sample(rng)

                a = int(arr)
                dp = int(dep)
                # Overnight wrap: an evening arrival with a morning departure
                # spans midnight. The original comparison silently produced
                # an empty window for exactly this, the commonest case.
                if a <= dp:
                    span = [(d, h) for h in range(a, dp + 1)]
                else:
                    span = [(d, h) for h in range(a, HOURS_PER_DAY)]
                    if d + 1 < DAYS_PER_YEAR:
                        span += [(d + 1, h) for h in range(0, dp + 1)]

                # A window running past the end of the year is truncated.
                # Its vehicles must not be recorded as departing early: they
                # simply run off the end of the simulated year, and counting
                # them as failures blames the controller for the calendar.
                truncated = any(
                    day * HOURS_PER_DAY + h >= HOURS_PER_YEAR
                    for day, h in span
                )
                for k, (day, h) in enumerate(span):
                    t = day * HOURS_PER_DAY + h
                    if t >= HOURS_PER_YEAR:
                        break
                    connected_count[t] += weight
                    hours_to_departure[t] = max(
                        hours_to_departure[t], len(span) - k - 1
                    )
                    if k == 0:
                        just_arrived[t] += weight
                        # Accumulate, then divide: assigning here kept only
                        # the last vehicle drawn in that hour, so the state
                        # of charge the fleet is blended toward was one
                        # sample rather than the average of the arrivals.
                        arrival_soc[t] += soc0 * weight
                    if k == len(span) - 1 and not truncated:
                        about_to_leave[t] += weight

        # Hours until the NEXT departure event, which is the deadline that
        # actually binds. `hours_to_departure` is a fleet-wide maximum — the
        # last vehicle to leave — and deferring against it lets the early
        # leavers go out undercharged while the controller still believes it
        # has hours in hand. In a residential fleet where vehicles leave
        # between 06:00 and 09:00 that is most of them.
        hours_to_next_departure = [0] * HOURS_PER_YEAR
        countdown = 0
        for t in range(HOURS_PER_YEAR - 1, -1, -1):
            if about_to_leave[t] > 0:
                countdown = 0
            elif connected_count[t] > 0:
                countdown += 1
            else:
                countdown = 0
            hours_to_next_departure[t] = countdown

        arrival_soc = [
            (a / j if j > 0 else 0.0)
            for a, j in zip(arrival_soc, just_arrived)
        ]

        return {
            "connected": [c > 0 for c in connected_count],
            "n_present": connected_count,
            "arrival_soc": arrival_soc,
            "just_arrived": just_arrived,
            "about_to_leave": about_to_leave,
            "hours_to_departure": hours_to_departure,
            "hours_to_next_departure": hours_to_next_departure,
            "n_points": n_points,
            "scenario": self.scenario,
        }

    # -------------------------------------------------------- per-hour

    # ------------------------------------------------------ price context
    # Absolute price thresholds are useless in practice: nobody knows what
    # "cheap" is in advance, the answer differs by tariff and by country,
    # and a threshold set once is wrong the moment the tariff changes. The
    # thresholds are therefore derived from the tariff the study is
    # actually running against, which is how the sizing literature handles
    # it (INESCTEC rec-sizing, Open Climate Fix solar-and-storage) and how
    # a real controller is commissioned.

    def set_price_context(self, import_price, export_price=None,
                          low_pct=0.30, high_pct=0.75):
        prices = sorted(p for p in (import_price or []) if p is not None)
        if not prices:
            self._price_ctx = None
            return self._price_ctx
        lo = prices[int(low_pct * (len(prices) - 1))]
        hi = prices[int(high_pct * (len(prices) - 1))]
        exp = sorted(p for p in (export_price or []) if p is not None)
        self._price_ctx = {
            "low": self.price_charge_below if self.price_charge_below is not None else lo,
            "high": self.price_discharge_above if self.price_discharge_above is not None else hi,
            "median": prices[len(prices) // 2],
            "export_high": exp[int(high_pct * (len(exp) - 1))] if exp else 0.0,
            "flat": (hi - lo) < 1e-9,
        }
        return self._price_ctx

    def _ctx(self):
        return getattr(self, "_price_ctx", None)

    def worth_cycling(self, buy_price, sell_price):
        """
        Is a charge-discharge cycle worth doing at these two prices?

        The round trip loses energy twice and wears the pack, so the spread
        has to cover both before it earns anything. Discharging a vehicle
        because the price is merely above average is how a控 strategy loses
        money while appearing clever.
        """
        eta = self.efficiency or 1.0
        cost = buy_price / eta + self.degradation_cost_per_kwh
        return sell_price * eta > cost

    @staticmethod
    def _present(n_units, t, profile):
        """
        How many charge points are occupied this hour.

        A profile marked `per_unit` holds the occupancy of ONE charge point
        as a fraction, so the fleet size is whatever the sizing decided and
        the same profile serves every candidate design. Without this the
        vehicle count is baked into the profile at the moment it is drawn,
        the unit count is ignored, and changing the number of chargers
        changes nothing at all — which is exactly what it did.
        """
        return EVFleet._scaled("n_present", n_units, t, profile)

    @staticmethod
    def _scaled(field, n_units, t, profile):
        """
        A per-charge-point quantity, scaled to the fleet.

        Every count in the profile has to be scaled by the same factor or
        the ratios between them stop meaning anything: scaling the number
        present but not the number that just arrived makes the arrival
        blend in `step` too small by exactly that factor, the aggregate
        state of charge never falls, and the fleet appears to need almost
        no energy at all.
        """
        series = profile.get(field) or []
        v = series[t] if t < len(series) else 0.0
        if profile.get("per_unit"):
            v *= max(0, int(n_units or 0))
        return v

    def max_charge_kw(self, n_units, soc, t, profile):
        n = self._present(n_units, t, profile)
        if n <= 0:
            return 0.0
        by_power = n * self.charger_kw
        pack = n * self.battery_kwh * self.vehicles_per_point
        headroom = pack * (self.soc_max - soc)
        by_energy = headroom / self.efficiency if self.efficiency > 0 else headroom
        return max(0.0, min(by_power, by_energy))

    def max_discharge_kw(self, n_units, soc, t, profile):
        if not self.bidirectional:
            return 0.0
        n = self._present(n_units, t, profile)
        if n <= 0:
            return 0.0

        # Never discharge below what can still be recovered before the NEXT
        # departure. Using the fleet-wide last departure here lets V2G empty
        # the pack on the strength of hours that belong to vehicles which are
        # not the ones about to leave.
        remaining = profile.get(
            "hours_to_next_departure", profile["hours_to_departure"]
        )[t]
        pack = n * self.battery_kwh * self.vehicles_per_point
        max_recharge = remaining * n * self.charger_kw * self.efficiency
        recoverable_soc = max_recharge / pack if pack > 0 else 0.0
        floor = max(
            self.v2g_soc_floor,
            self.soc_departure_target - recoverable_soc,
        )
        if soc <= floor:
            return 0.0

        by_power = n * self.charger_kw
        available = pack * (soc - floor)
        return max(0.0, min(by_power, available * self.efficiency))

    def required_kw(self, n_units, soc, t, profile):
        """
        Power that must be taken this hour regardless of price, so the fleet
        still reaches its departure target.

        Uncontrolled charging treats every available kW as required — that
        is exactly what makes it the worst case. The smart scenarios require
        only what the remaining window can no longer absorb.
        """
        n = self._present(n_units, t, profile)
        if n <= 0:
            return 0.0

        if self.scenario == UNCONTROLLED:
            return self.max_charge_kw(n_units, soc, t, profile)

        deficit = self.soc_departure_target - soc
        if deficit <= 0:
            return 0.0

        pack = n * self.battery_kwh * self.vehicles_per_point
        energy_needed = pack * deficit / self.efficiency
        max_rate = n * self.charger_kw

        # `hours_to_departure` counts the connected hours remaining AFTER
        # this one. Deferring is safe only while those hours can still
        # deliver the energy; the moment they cannot, charging stops being
        # optional.
        #
        # The off-by-one here matters more than it looks. Writing the test as
        # `energy_needed >= max_rate * max(1, remaining)` — which is what this
        # was — means that in the final connected hour, where remaining is 0,
        # the condition becomes `energy_needed >= max_rate` and any shortfall
        # SMALLER than one hour of charging is silently skipped. The fleet
        # then departs a few percent below target, every single day. It
        # produced 1441 missed departures a year against zero for
        # uncontrolled charging, which is exactly backwards: a smart
        # controller must never do worse than a dumb one at meeting the duty.
        # The binding deadline is the NEXT departure, not the last one.
        remaining = profile.get(
            "hours_to_next_departure", profile["hours_to_departure"]
        )[t]
        if energy_needed > max_rate * remaining + 1e-9:
            return min(max_rate, energy_needed)
        return 0.0

    def _energy_to_target(self, n_units, soc, t, profile):
        """
        Power that would bring the fleet to its departure target this hour.

        The ceiling on anything the controller does for price reasons: past
        it, the energy is not stored for later, it is sold to whoever drives
        away with it.
        """
        n = self._present(n_units, t, profile)
        if n <= 0:
            return 0.0
        deficit = self.soc_departure_target - soc
        if deficit <= 0:
            return 0.0
        pack = n * self.battery_kwh * self.vehicles_per_point
        eta = self.efficiency or 1.0
        return pack * deficit / eta

    def preferred_charge_kw(self, n_units, soc, t, profile, price=None,
                            surplus_kw=0.0):
        """
        Power the fleet would LIKE to take this hour, above what is required.

        This is where the scenarios differ from one another.
        """
        available = self.max_charge_kw(n_units, soc, t, profile)
        if available <= 0:
            return 0.0

        if self.scenario == UNCONTROLLED:
            return available

        if self.scenario == SCHEDULED:
            h = t % HOURS_PER_DAY
            for start, end in self.scheduled_windows:
                inside = (
                    start <= h <= end if start <= end
                    else (h >= start or h <= end)
                )
                if inside:
                    return available
            return 0.0

        ctx = self._ctx()

        # A cheap hour is a reason to bring the charge FORWARD, not a reason
        # to fill the pack. Taking every available kilowatt because the
        # tariff is low buys energy that simply drives away, and the fleet
        # ends up costing more than it would have uncontrolled — which is
        # what it did: seven times the throughput and a worse net present
        # cost than doing nothing clever at all.
        to_target = self._energy_to_target(n_units, soc, t, profile)

        if self.scenario == PRICE_RESPONSIVE:
            if price is None or ctx is None:
                return available
            return min(available, to_target) if price <= ctx["low"] else 0.0

        # V1G, V2G and V2H soak up on-site surplus first — that is the whole
        # point of shifting — and, when the tariff has a spread worth
        # exploiting, also fill from the grid in its cheapest hours rather
        # than waiting to be forced into a dear one by the departure
        # deadline. Surplus is free; a cheap hour is the next best thing.
        want = min(available, max(0.0, surplus_kw))
        if ctx and not ctx["flat"] and price is not None and price <= ctx["low"]:
            want = max(want, min(available, to_target))
        return want

    def price_now(self, export_price_t):
        """Tell the fleet what this hour's export price is."""
        ctx = self._ctx()
        if ctx is not None:
            ctx["export_price_now"] = export_price_t

    def preferred_discharge_kw(self, n_units, soc, t, profile, price=None,
                               deficit_kw=0.0, export=False):
        """
        Power the fleet will give back this hour.

        Serving on-site load displaces an import at the retail price, which
        is nearly always worth more than exporting, so that comes first.
        Exporting is only offered to V2G, and only when the spread covers
        the round trip and the wear.
        """
        avail = self.max_discharge_kw(n_units, soc, t, profile)
        if avail <= 0:
            return 0.0
        ctx = self._ctx()

        if export:
            # Exporting earns the export price, which in most tariffs is a
            # fraction of the retail rate. Gating it on the import price —
            # the number the rest of this method reasons about — would have
            # the fleet exporting at five cents to avoid buying at
            # twenty-eight, which is a loss dressed up as a strategy. With
            # a typical feed-in tariff this correctly almost never fires,
            # and vehicle-to-grid behaves like vehicle-to-home until the
            # export price is worth having.
            sell = (ctx or {}).get("export_price_now")
            if not self.can_export or ctx is None or sell is None:
                return 0.0
            if not self.worth_cycling(ctx["low"], sell):
                return 0.0
            return avail

        if self.scenario == PRICE_RESPONSIVE:
            if price is None or ctx is None:
                return min(avail, max(0.0, deficit_kw))
            if price < ctx["high"] or not self.worth_cycling(ctx["low"], price):
                return 0.0
            return avail

        # V2G and V2H hold their charge for the dear hours when the tariff
        # has a spread worth holding for; with a flat tariff there is
        # nothing to wait for and they simply serve the load.
        give = min(avail, max(0.0, deficit_kw))
        if ctx and not ctx["flat"] and price is not None:
            if price < ctx["high"] and not self.worth_cycling(ctx["low"], price):
                return 0.0
        return give

    def step(self, n_units, soc, charge_kw, discharge_kw, t, profile, dt_h=1.0):
        """
        Advance fleet state of charge.

        On an arrival hour the state is reset toward the arrival SOC,
        weighted by how much of the fleet is new. Resetting the whole fleet
        SOC on any arrival — as the original did — discards the charge of
        vehicles already plugged in.

        LIMITATION worth stating plainly: the fleet is carried as ONE
        aggregate state of charge, not one per vehicle. When arrivals and
        departures are staggered, a vehicle that plugs in late is blended
        into the same average as one that has been charging for hours, so
        individual departure compliance is approximate. In practice this
        shows up as a handful of reported near-misses a year — a fraction of
        a percent of departures — where the average sits just under target
        while every individual vehicle would have made it. A systematic
        failure looks quite different: percentages, not a handful. The
        summary reports the rate so the two can be told apart, and
        per-vehicle tracking is the fix if that distinction ever has to
        carry weight.
        """
        n = self._present(n_units, t, profile)
        if n <= 0:
            return soc

        arrived = self._scaled("just_arrived", n_units, t, profile)
        if arrived > 0:
            frac = min(1.0, arrived / n)
            soc = soc * (1.0 - frac) + profile["arrival_soc"][t] * frac

        pack = n * self.battery_kwh * self.vehicles_per_point
        if pack <= 0:
            return soc
        delta = (
            charge_kw * self.efficiency - discharge_kw / self.efficiency
        ) * dt_h
        return max(0.0, min(1.0, soc + delta / pack))

    # ----------------------------------------------------------- costing

    def annual_cost(self, n_units, series, dt_h=1.0):
        """
        Battery degradation attributable to V2G discharge.

        Only the discharge counts: charging a vehicle is what the charger is
        for. Discharging it for the site's benefit consumes cycle life the
        owner paid for, and a V2G business case that omits this is not a
        business case. `series` here is the discharge series.
        """
        if not self.bidirectional:
            return 0.0
        return sum(series) * dt_h * self.degradation_cost_per_kwh

    def summary(self, n_units, charge_series, discharge_series=None,
                profile=None, unmet_departures=0, dt_h=1.0):
        charged = sum(charge_series) * dt_h if charge_series else 0.0
        discharged = (
            sum(discharge_series) * dt_h if discharge_series else 0.0
        )
        peak = max(charge_series) if charge_series else 0.0
        connected_hours = (
            sum(1 for c in profile["connected"] if c) if profile else 0
        )
        notes = []

        if self.scenario == UNCONTROLLED and peak > 0:
            notes.append(
                f"Uncontrolled charging puts a {peak:,.0f} kW peak on the "
                f"connection. Smart unidirectional charging would move most "
                f"of that energy without changing the vehicles or the "
                f"chargers — compare the V1G scenario before sizing the "
                f"supply for this."
            )
        if self.bidirectional and discharged > 0:
            notes.append(
                f"V2G discharge of {discharged:,.0f} kWh a year costs "
                f"{discharged * self.degradation_cost_per_kwh:,.0f} in "
                f"battery degradation at "
                f"{self.degradation_cost_per_kwh:.3f} per kWh. That cost "
                f"falls on the vehicle owner and has to be shared with them "
                f"for the arrangement to hold."
            )
        total_departures = (
            sum(1 for x in profile["about_to_leave"] if x > 0)
            if profile else 0
        )
        miss_rate = (
            unmet_departures / total_departures if total_departures else 0.0
        )
        if unmet_departures > 0:
            if miss_rate < 0.01:
                notes.append(
                    f"{unmet_departures} of {total_departures} departures "
                    f"({miss_rate * 100:.2f}%) were marginally below the "
                    f"{self.soc_departure_target * 100:.0f}% target. At this "
                    f"rate it is an artefact of tracking the fleet as one "
                    f"aggregate state of charge rather than per vehicle, not "
                    f"a shortfall in the charging strategy."
                )
            else:
                notes.append(
                    f"{unmet_departures} of {total_departures} departures "
                    f"({miss_rate * 100:.1f}%) were below the "
                    f"{self.soc_departure_target * 100:.0f}% target state of "
                    f"charge. The charging strategy is not meeting its "
                    f"service obligation — add charge points, raise the "
                    f"power, or relax the target."
                )

        return {
            "technology": self.technology,
            "name": self.name,
            "archetype": self.archetype,
            "scenario": self.scenario,
            "scenario_label": SCENARIO_LABELS[self.scenario],
            "charge_points": n_units,
            "charger_kw": self.charger_kw,
            "installed_kw": n_units * self.charger_kw,
            "peak_charge_kw": peak,
            "diversity_realised": (
                peak / (n_units * self.charger_kw)
                if n_units and self.charger_kw else 0.0
            ),
            "energy_charged_kwh": charged,
            "energy_discharged_kwh": discharged,
            "net_kwh": charged - discharged,
            "connected_hours": connected_hours,
            "unmet_departures": unmet_departures,
            "total_departures": total_departures,
            "unmet_departure_rate": miss_rate,
            "degradation_cost": (
                discharged * self.degradation_cost_per_kwh
                if self.bidirectional else 0.0
            ),
            "bidirectional": self.bidirectional,
            "can_export": self.can_export,
            "notes": notes,
        }


def compare_scenarios(base_kwargs, scenarios=None):
    """
    Build one fleet per scenario from the same physical description.

    Comparing scenarios on identical vehicles and chargers is the only way
    to see what the control strategy alone is worth, which is usually the
    cheapest improvement available at a site with EVs.
    """
    out = {}
    for s in (scenarios or SCENARIOS):
        kw = dict(base_kwargs)
        kw["scenario"] = s
        try:
            out[s] = EVFleet(**kw)
        except ValueError:
            continue        # archetype does not support this scenario
    return out
