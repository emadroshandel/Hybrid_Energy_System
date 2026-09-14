"""
Grid connection and tariffs.

Three tariff structures are supported, matching the columns already present
in the project's Data.xlsx:

  * single      - one flat import price and one flat export price
  * time_of_use - price by hour-of-day band, optionally by season
  * dynamic     - a full 8760-hour price series

Import and export limits are enforced separately, because they usually are:
an export cap set by the network operator is commonly far below the import
capacity of the same connection.

Sign convention throughout the engine: import (purchase) is positive cost,
export (sale) is negative cost. Energy series are always non-negative and
kept in separate import/export vectors, never netted, so that the report can
show both and so that asymmetric prices are applied correctly.
"""

from __future__ import annotations

from ..assets import Asset, GRID
from ..timeseries import HOURS_PER_YEAR, coerce_year

SINGLE = "single"
TIME_OF_USE = "time_of_use"
DYNAMIC = "dynamic"

# Default time-of-use bands (hour-of-day -> band name), a common 3-band shape.
DEFAULT_TOU_BANDS = {
    "off_peak": list(range(0, 7)) + [23],
    "shoulder": list(range(7, 17)) + [22],
    "peak": list(range(17, 22)),
}


class GridConnection(Asset):
    """
    A point of common coupling with an electricity network.

    Parameters
    ----------
    import_limit_kw : maximum purchase power; 0 means islanded
    export_limit_kw : maximum sale power; 0 means export not permitted
    import_price / export_price : scalar, dict of bands, or 8760-series
    tariff_type : SINGLE, TIME_OF_USE or DYNAMIC
    demand_charge_per_kw_month : charge on monthly peak import, if any
    standing_charge_per_year : fixed annual connection charge
    emission_factor_kg_per_kwh : grid carbon intensity, for the CO2 metric
    availability : fraction of hours the grid is actually present. Below 1.0
        the connection is treated as unreliable and outage hours are drawn
        deterministically so results stay reproducible.
    mean_outage_hours : mean duration of one interruption. Together with the
        availability this sets how often the supply fails as well as for how
        long; a network that fails rarely for a long time needs a very
        different design from one that blinks constantly, though the two can
        share an availability figure exactly.
    """

    technology = "grid"
    role = GRID
    sizable = False
    unit_label = "connection"

    def __init__(
        self,
        name="Grid",
        import_limit_kw=1e6,
        export_limit_kw=0.0,
        import_price=0.10,
        export_price=0.05,
        tariff_type=SINGLE,
        tou_bands=None,
        demand_charge_per_kw_month=0.0,
        standing_charge_per_year=0.0,
        emission_factor_kg_per_kwh=0.4,
        price_escalation_per_year=0.02,
        availability=1.0,
        mean_outage_hours=4.0,
        outage_seed=12345,
    ):
        super().__init__(name=name, lifetime_years=40)
        self.import_limit_kw = float(import_limit_kw)
        self.export_limit_kw = float(export_limit_kw)
        self.tariff_type = tariff_type
        self.tou_bands = tou_bands or DEFAULT_TOU_BANDS
        self.demand_charge_per_kw_month = float(demand_charge_per_kw_month)
        self.standing_charge_per_year = float(standing_charge_per_year)
        self.emission_factor_kg_per_kwh = float(emission_factor_kg_per_kwh)
        self.price_escalation_per_year = float(price_escalation_per_year)
        self.availability = float(availability)
        # Mean time to repair, hours. With the availability it fixes how
        # often the supply fails as well as for how long, and the second is
        # what sizes the backup.
        self.mean_outage_hours = max(1.0, float(mean_outage_hours))
        self.outage_seed = int(outage_seed)

        self.import_price = self._build_price(import_price, "import price")
        self.export_price = self._build_price(export_price, "export price")

        if self.import_limit_kw < 0 or self.export_limit_kw < 0:
            raise ValueError("grid power limits cannot be negative")

    def rated_power_kw(self):
        return max(self.import_limit_kw, self.export_limit_kw)

    @property
    def is_islanded(self):
        return self.import_limit_kw <= 0.0 and self.export_limit_kw <= 0.0

    def _build_price(self, price, label):
        """Normalise any accepted price specification to an 8760-hour series."""
        if isinstance(price, (int, float)):
            return [float(price)] * HOURS_PER_YEAR

        if isinstance(price, dict):
            # Band name -> price. Expand via the configured hour bands.
            series = [0.0] * HOURS_PER_YEAR
            hour_price = {}
            for band, hours in self.tou_bands.items():
                if band not in price:
                    raise ValueError(
                        f"{label}: time-of-use band '{band}' has no price. "
                        f"Bands defined: {sorted(self.tou_bands)}"
                    )
                for h in hours:
                    hour_price[h] = float(price[band])
            missing = set(range(24)) - set(hour_price)
            if missing:
                raise ValueError(
                    f"{label}: hours {sorted(missing)} are not covered by any "
                    "time-of-use band"
                )
            for t in range(HOURS_PER_YEAR):
                series[t] = hour_price[t % 24]
            return series

        series, _note = coerce_year(price, label)
        return series

    def outage_mask(self):
        """
        Boolean series: True where the grid is available.

        Outages are placed deterministically from a seeded generator so that
        two runs of the same design give the same answer - a study that
        cannot be reproduced cannot be defended.

        They are also placed in CLUSTERS, which is the part that matters for
        sizing. Drawing each hour independently at 95% availability gives
        about 440 separate one-hour interruptions spread evenly through the
        year. Real networks do not fail like that: they fail perhaps twenty
        times a year for several hours at a time, and once in a while for a
        day. The two profiles have identical availability and completely
        different consequences - an hour of storage rides out the first and
        is irrelevant to the second - so the independent draw understates
        the backup a site needs by roughly the mean repair time.

        A two-state Markov chain fixes it with one extra parameter. The user
        gives the availability and the mean time to repair; the failure rate
        follows, because for a two-state chain

            availability = MTBF / (MTBF + MTTR)

        so MTBF = MTTR * A / (1 - A), and the per-hour probability of
        entering an outage is 1/MTBF. Over a long year this reproduces the
        requested availability while placing the lost hours where they hurt.
        """
        if self.availability >= 1.0:
            return [True] * HOURS_PER_YEAR
        import random

        rng = random.Random(self.outage_seed)
        a = max(0.0, min(1.0, self.availability))
        mttr = max(1.0, float(self.mean_outage_hours))

        if a <= 0.0:
            return [False] * HOURS_PER_YEAR

        mtbf = mttr * a / (1.0 - a)
        p_fail = 1.0 / mtbf if mtbf > 0 else 1.0
        p_repair = 1.0 / mttr

        mask = []
        up = True
        for _ in range(HOURS_PER_YEAR):
            if up:
                if rng.random() < p_fail:
                    up = False
            else:
                if rng.random() < p_repair:
                    up = True
            mask.append(up)
        return mask

    def outage_statistics(self):
        """
        What the outage model actually produced, for the report.

        Availability alone does not describe an unreliable supply; the
        number and length of the interruptions do, and they are what a
        client recognises from their own experience of the network.
        """
        mask = self.outage_mask()
        events = []
        run = 0
        for up in mask:
            if up:
                if run:
                    events.append(run)
                run = 0
            else:
                run += 1
        if run:
            events.append(run)
        total = sum(events)
        return {
            "availability": 1.0 - total / float(len(mask) or 1),
            "outage_events_per_year": len(events),
            "outage_hours_per_year": total,
            "mean_outage_hours": (total / len(events)) if events else 0.0,
            "longest_outage_hours": max(events) if events else 0,
        }

    def import_cost(self, energy_kwh_series):
        """Total purchase cost for an hourly import series."""
        return sum(e * p for e, p in zip(energy_kwh_series, self.import_price))

    def export_revenue(self, energy_kwh_series):
        """Total sale revenue for an hourly export series."""
        return sum(e * p for e, p in zip(energy_kwh_series, self.export_price))

    def demand_charges(self, import_series, months):
        """
        Annual demand charge from the peak import in each calendar month.

        Demand charges are frequently the largest single line on a commercial
        bill and are the main thing peak shaving is bought to reduce, so they
        are modelled explicitly rather than folded into the energy price.
        """
        if self.demand_charge_per_kw_month <= 0:
            return 0.0, [0.0] * 12
        peaks = [0.0] * 12
        for t, p in enumerate(import_series):
            m = months[t] - 1
            if p > peaks[m]:
                peaks[m] = p
        return sum(peaks) * self.demand_charge_per_kw_month, peaks

    def annual_cost(self, import_series, export_series, months=None):
        """Full annual grid bill, broken out so the report can itemise it."""
        energy_in = self.import_cost(import_series)
        energy_out = self.export_revenue(export_series)
        demand = 0.0
        peaks = [0.0] * 12
        if months is not None:
            demand, peaks = self.demand_charges(import_series, months)
        total = energy_in - energy_out + demand + self.standing_charge_per_year
        return {
            "import_cost": energy_in,
            "export_revenue": energy_out,
            "demand_charge": demand,
            "monthly_peaks_kw": peaks,
            "standing_charge": self.standing_charge_per_year,
            "net_cost": total,
            "imported_kwh": sum(import_series),
            "exported_kwh": sum(export_series),
            "emissions_kg": sum(import_series) * self.emission_factor_kg_per_kwh,
        }

    def describe_tariff(self):
        """Human-readable tariff summary for the report."""
        imp = self.import_price
        exp = self.export_price
        uniq_i = len(set(round(x, 6) for x in imp))
        uniq_e = len(set(round(x, 6) for x in exp))
        return {
            "type": self.tariff_type,
            "import_min": min(imp),
            "import_max": max(imp),
            "import_mean": sum(imp) / len(imp),
            "import_distinct_prices": uniq_i,
            "export_min": min(exp),
            "export_max": max(exp),
            "export_mean": sum(exp) / len(exp),
            "export_distinct_prices": uniq_e,
            "import_limit_kw": self.import_limit_kw,
            "export_limit_kw": self.export_limit_kw,
            "spread": (sum(imp) / len(imp)) - (sum(exp) / len(exp)),
        }
