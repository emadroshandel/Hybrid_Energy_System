"""
Battery energy storage model.

The dispatch loop needs three things from a battery each hour: how much
power it can absorb, how much it can deliver, and what that does to its
state of charge. Everything else here exists to make the sizing report
defensible - degradation, cycle counting and replacement scheduling.

The SOC update matches the original MATLAB `sim4.m`:

    SOC(t+1) = SOC(t) + (P_in * eff - P_out / eff) / (N * E_nom)

which places the round-trip efficiency symmetrically on both directions, so
`eff` here is the one-way efficiency and the round-trip figure is eff^2.
This is worth stating explicitly because the two conventions are routinely
confused, and mixing them up is a 10% error in stored energy.

Degradation is modelled by the rainflow-free "throughput plus calendar"
approach: whichever of cycle life or calendar life is consumed first drives
replacement. That is the same logic HOMER uses and it is adequate for
sizing; it is not adequate for warranty modelling.
"""

from __future__ import annotations

import math

from ..assets import Storage

CHEMISTRY_PRESETS = {
    # (one-way eff, SOC min, SOC max, cycles to EOL @ rated DoD, calendar yrs)
    "lithium_nmc":    (0.96, 0.10, 1.00, 5000, 15),
    "lithium_lfp":    (0.97, 0.05, 1.00, 7000, 20),
    "lead_acid_flooded": (0.90, 0.40, 1.00, 1200, 8),
    "lead_acid_agm":  (0.92, 0.40, 1.00, 1500, 10),
    "flow_vanadium":  (0.87, 0.00, 1.00, 15000, 20),
}


class Battery(Storage):
    """
    A battery bank made of `n` identical units.

    Parameters
    ----------
    nominal_energy_kwh : usable-at-100%-DoD energy of ONE unit
    nominal_power_kw : continuous power rating of ONE unit
    efficiency : ONE-WAY efficiency; round trip is this squared
    soc_min, soc_max : operating window as a fraction of nominal energy
    soc_initial : starting state of charge
    cycles_to_eol : equivalent full cycles before end of life
    calendar_life_years : shelf life regardless of use
    """

    technology = "battery"
    unit_label = "module"

    def __init__(
        self,
        name="Battery bank",
        nominal_energy_kwh=10.0,
        nominal_power_kw=5.0,
        efficiency=0.95,
        soc_min=0.20,
        soc_max=1.00,
        soc_initial=0.60,
        cycles_to_eol=5000,
        calendar_life_years=15,
        chemistry=None,
        self_discharge_per_hour=0.0,
        capital_cost=0.0,
        replacement_cost=0.0,
        om_cost_per_year=0.0,
    ):
        if chemistry and chemistry in CHEMISTRY_PRESETS:
            eff, smin, smax, cyc, cal = CHEMISTRY_PRESETS[chemistry]
            efficiency = eff
            soc_min = smin
            soc_max = smax
            cycles_to_eol = cyc
            calendar_life_years = cal

        super().__init__(
            name=name,
            nominal_energy_kwh=nominal_energy_kwh,
            nominal_power_kw=nominal_power_kw,
            efficiency=efficiency, soc_min=soc_min, soc_max=soc_max,
            soc_initial=soc_initial,
            self_discharge_per_hour=self_discharge_per_hour,
            cycles_to_eol=int(cycles_to_eol),
            calendar_life_years=float(calendar_life_years),
            lifetime_years=float(calendar_life_years),
            capital_cost=capital_cost, replacement_cost=replacement_cost,
            om_cost_per_year=om_cost_per_year,
        )
        self.chemistry = chemistry or "generic"

    @property
    def usable_fraction(self):
        return self.soc_max - self.soc_min

    # The per-hour limits, the SOC update and the degradation accounting are
    # all inherited from Storage now. They used to be duplicated here, which
    # meant the efficiency-scaling fix had to be made in two places — and
    # was, at first, made in only one.

    def depth_of_discharge_stats(self, soc_series):
        """
        Simple cycle statistics from an SOC trace.

        Counts a cycle each time the trace turns from falling to rising, and
        records the depth of that excursion. This is a half-cycle counter,
        not rainflow; it is enough to spot a design that is cycling the
        battery far harder than its rating assumes.
        """
        if len(soc_series) < 3:
            return {"cycles": 0, "mean_dod": 0.0, "max_dod": 0.0}

        depths = []
        peak = soc_series[0]
        trough = soc_series[0]
        falling = False
        for s in soc_series[1:]:
            if s < trough:
                trough = s
                falling = True
            elif falling and s > trough + 1e-6:
                depths.append(peak - trough)
                peak = s
                trough = s
                falling = False
            elif not falling and s > peak:
                peak = s
        if falling and peak > trough:
            depths.append(peak - trough)

        if not depths:
            return {"cycles": 0, "mean_dod": 0.0, "max_dod": 0.0}
        return {
            "cycles": len(depths),
            "mean_dod": sum(depths) / len(depths),
            "max_dod": max(depths),
        }


def autonomy_hours(battery, n_units, mean_load_kw):
    """
    How long the bank alone could carry the average load. The classic
    off-grid "days of autonomy" figure, in hours.
    """
    if mean_load_kw <= 0:
        return float("inf")
    return battery.usable_energy_kwh(n_units) / mean_load_kw
