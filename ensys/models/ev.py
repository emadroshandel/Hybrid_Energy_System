"""
Backwards-compatible EV interface.

The full model now lives in `evfleet.py`, with six charging scenarios and
five fleet archetypes. This module keeps the original constructor working —
explicit `(mu, sigma, lo, hi)` tuples for arrival, departure, initial state
of charge and vehicle count, as the MATLAB `Uncertainty_variable.m` defined
them — so existing studies and saved projects do not break.

Anything new should import from `evfleet` directly and choose an archetype
and a scenario, which is both clearer and better calibrated than four bare
distributions.
"""

from __future__ import annotations

from .evfleet import (  # noqa: F401  (re-exported for callers)
    ARCHETYPES, SCENARIOS, SCENARIO_LABELS, TruncatedNormal,
    UNCONTROLLED, V1G_SMART, V2G, V2H, PRICE_RESPONSIVE, SCHEDULED,
    compare_scenarios,
)
from .evfleet import EVFleet as _EVFleet


class EVFleet(_EVFleet):
    """
    EV fleet described by explicit distributions.

    Kept for compatibility with the original MATLAB formulation. The
    distributions replace the archetype's built-in ones; everything else —
    the overnight-wrapping connection window, the per-vehicle arrival
    spread, the departure obligation, and the scenario machinery — comes
    from the full model.
    """

    def __init__(self, name="EV fleet", battery_kwh=40.0, charger_kw=7.0,
                 efficiency=0.92, soc_min=0.10, soc_max=0.95,
                 soc_departure_target=0.80, v2g=False, v2g_soc_floor=0.50,
                 arrival=(15.0, 3.0, 14.0, 18.0),
                 departure=(8.0, 3.0, 7.0, 9.0),
                 initial_soc=(0.5, 0.3, 0.2, 0.85),
                 count=(5.0, 1.0, 4.0, 7.0),
                 spread_arrivals=False, seed=42,
                 capital_cost_per_charger=0.0, om_cost_per_year=0.0,
                 lifetime_years=15, scenario=None, **kw):
        super().__init__(
            name=name,
            archetype="residential",
            scenario=scenario or (V2G if v2g else V1G_SMART),
            charger_kw=charger_kw,
            battery_kwh=battery_kwh,
            efficiency=efficiency,
            soc_min=soc_min,
            soc_max=soc_max,
            soc_departure_target=soc_departure_target,
            v2g_soc_floor=v2g_soc_floor,
            spread_arrivals=spread_arrivals,
            seed=seed,
            capital_cost=capital_cost_per_charger,
            om_cost_per_year=om_cost_per_year,
            lifetime_years=lifetime_years,
            **kw
        )
        # Replace the archetype's distributions with the supplied ones.
        self.arrival_dist = TruncatedNormal(*arrival)
        self.departure_dist = TruncatedNormal(*departure)
        self.soc_dist = TruncatedNormal(*initial_soc)
        self.count_dist = TruncatedNormal(*count)
        self.weekend_factor = 1.0
        self.capital_cost_per_charger = float(capital_cost_per_charger)

    @property
    def v2g(self):
        return self.scenario == V2G

    def sample_year(self, seed=None, n_points=None):
        """
        Original method name.

        The vehicle count is drawn from `count` as it was in the MATLAB,
        rather than being fixed by the number of charge points, so results
        from the original formulation reproduce.
        """
        import random

        n = n_points
        if n is None:
            rng = random.Random(self.seed if seed is None else seed)
            n = max(1, int(self.count_dist.sample(rng)))
        prof = self.profile(seed=seed, n_points=n)
        # Legacy field names, so older reports still resolve.
        prof["soc_init"] = prof["arrival_soc"]
        return prof
