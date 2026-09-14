"""
Performance indicators.

These are the numbers the optimiser constrains on and the report is judged
by. Definitions matter here more than arithmetic, because several of these
terms are used loosely in the literature and two studies quoting "90%
renewable" can mean quite different things.

The definitions used:

  LPSP   loss of power supply probability, on an ENERGY basis:
         unmet energy / total demanded energy. The alternative
         hour-count basis (fraction of hours with any shortfall) is also
         reported as `lpsp_hours` because some standards use it, but the
         energy basis is the default since a one-minute shortfall and a
         day-long blackout should not score the same.

  Renewable fraction
         renewable energy actually SERVING demand, divided by total demand.
         Curtailed and exported renewable energy is excluded. Counting
         curtailed output, which some tools do, lets an absurdly oversized
         array claim 100%.

  Self-consumption
         share of on-site generation consumed on site rather than exported.
         A grid-export economics question.

  Self-sufficiency
         share of demand met without import. A resilience question.
         These two are routinely confused and move in opposite directions.
"""

from __future__ import annotations

import math


def compute(result, system=None, econ_result=None):
    """
    Build the full indicator set from a DispatchResult.

    Returns a flat dict, suitable for the optimiser's objective vector, the
    Pareto archive and the report table.
    """
    t = result.totals
    demand = t["total_demand_kwh"]
    unmet = t["unmet_kwh"]
    n_hours = len(result.load) or 1

    # ------------------------------------------------------- reliability
    lpsp = (unmet / demand) if demand > 0 else 0.0
    lpsp_hours = t["unmet_hours"] / n_hours

    # Longest continuous shortfall, which is what determines whether an
    # outage is an inconvenience or a process failure.
    longest = 0
    run = 0
    for u in result.unmet:
        if u > 1e-9:
            run += 1
            if run > longest:
                longest = run
        else:
            run = 0

    # -------------------------------------------------------- renewables
    renewable_served = t["renewable_kwh"] - t["curtailed_kwh"] - t["exported_kwh"]
    if renewable_served < 0:
        renewable_served = max(0.0, t["renewable_kwh"] - t["curtailed_kwh"])
    rf = (renewable_served / demand) if demand > 0 else 0.0
    rf = max(0.0, min(1.0, rf))

    onsite_gen = t["renewable_kwh"] + t["genset_kwh"]
    self_consumption = (
        (onsite_gen - t["exported_kwh"] - t["curtailed_kwh"]) / onsite_gen
        if onsite_gen > 0 else 0.0
    )
    self_sufficiency = (
        (demand - t["imported_kwh"]) / demand if demand > 0 else 0.0
    )
    curtailment_rate = (
        t["curtailed_kwh"] / t["renewable_kwh"] if t["renewable_kwh"] > 0 else 0.0
    )

    # ---------------------------------------------------------- emissions
    emissions = 0.0
    if system and system.grid:
        emissions += t["imported_kwh"] * system.grid.emission_factor_kg_per_kwh
    if system and system.genset and system.n_genset > 0:
        emissions += system.genset.annual_summary(
            system.n_genset, result.genset
        )["emissions_kg"]

    # ------------------------------------------------------------ storage
    cycles = 0.0
    if system and system.battery and system.n_battery > 0:
        cycles = system.battery.equivalent_full_cycles(
            system.n_battery, t["battery_discharge_kwh"]
        )
    rte_realised = (
        t["battery_discharge_kwh"] / t["battery_charge_kwh"]
        if t["battery_charge_kwh"] > 0 else 0.0
    )

    m = {
        # reliability
        "lpsp": lpsp,
        "lpsp_hours": lpsp_hours,
        "unmet_kwh": unmet,
        "unmet_hours": t["unmet_hours"],
        "longest_shortfall_hours": longest,
        "ev_unmet_departures": t["ev_unmet_departures"],
        "reliability": 1.0 - lpsp,
        # renewables
        "renewable_fraction": rf,
        "renewable_served_kwh": renewable_served,
        "self_consumption": max(0.0, min(1.0, self_consumption)),
        "self_sufficiency": max(0.0, min(1.0, self_sufficiency)),
        "curtailment_rate": curtailment_rate,
        "curtailed_kwh": t["curtailed_kwh"],
        # environment
        "emissions_kg": emissions,
        "emissions_kg_per_kwh": (emissions / demand) if demand > 0 else 0.0,
        # storage health
        "battery_cycles_per_year": cycles,
        "battery_rte_realised": rte_realised,
        "min_soc": t["min_soc"],
        # grid interaction
        "imported_kwh": t["imported_kwh"],
        "exported_kwh": t["exported_kwh"],
        "peak_import_kw": t["peak_import_kw"],
        "peak_export_kw": t["peak_export_kw"],
        "import_dependency": (
            t["imported_kwh"] / demand if demand > 0 else 0.0
        ),
        # demand
        "peak_load_kw": t["peak_load_kw"],
        "load_factor": (
            (t["load_kwh"] / n_hours) / t["peak_load_kw"]
            if t["peak_load_kw"] > 0 else 0.0
        ),
    }

    # -------------------------------------------------- capacity factors
    if system:
        if system.pv and system.n_pv > 0:
            cap = system.pv_capacity_kwp
            m["pv_capacity_factor"] = t["pv_kwh"] / (cap * n_hours) if cap else 0.0
            m["pv_specific_yield"] = t["pv_kwh"] / cap if cap else 0.0
        if system.wind and system.n_wind > 0:
            cap = system.wind_capacity_kw
            m["wind_capacity_factor"] = (
                t["wind_kwh"] / (cap * n_hours) if cap else 0.0
            )
        if system.genset and system.n_genset > 0:
            cap = system.genset_capacity_kw
            m["genset_capacity_factor"] = (
                t["genset_kwh"] / (cap * n_hours) if cap else 0.0
            )

    if econ_result:
        m["npc"] = econ_result["npc"]
        m["lcoe"] = econ_result["lcoe"]
        m["initial_capital"] = econ_result["initial_capital"]
        m["annualised_cost"] = econ_result["annualised_cost"]

    return m


def objective_vector(metrics, objectives):
    """
    Extract the objective values for the optimiser, all as MINIMISATION
    targets.

    Objectives that are naturally maximised (renewable fraction,
    reliability) are negated here, once, so that every downstream comparison
    can assume "smaller is better". Doing this conversion in one place is
    what stops sign errors from silently inverting a Pareto front.
    """
    out = []
    for name in objectives:
        if name in ("renewable_fraction", "reliability", "self_sufficiency",
                    "self_consumption"):
            out.append(-float(metrics.get(name, 0.0)))
        else:
            v = metrics.get(name, float("inf"))
            out.append(float(v) if v is not None else float("inf"))
    return out


def check_constraints(metrics, constraints):
    """
    Test a design against hard constraints.

    `constraints` is a dict of {metric: (op, value)} with op in
    '<=', '>=', '<', '>', '=='.

    Returns (feasible, violations, total_violation) where total_violation is
    a normalised magnitude used to rank infeasible designs against each
    other. Ranking infeasible solutions rather than discarding them keeps
    the swarm informed when the feasible region is small - discarding them
    is why constrained PSO often fails to find any feasible point at all.
    """
    violations = []
    total = 0.0
    ops = {
        "<=": lambda a, b: a <= b + 1e-9,
        ">=": lambda a, b: a >= b - 1e-9,
        "<": lambda a, b: a < b,
        ">": lambda a, b: a > b,
        "==": lambda a, b: abs(a - b) < 1e-9,
    }
    for key, (op, target) in (constraints or {}).items():
        value = metrics.get(key)
        if value is None:
            continue
        if op not in ops:
            raise ValueError(f"unknown constraint operator '{op}'")
        if not ops[op](value, target):
            scale = abs(target) if abs(target) > 1e-9 else 1.0
            magnitude = abs(value - target) / scale
            violations.append(
                {
                    "metric": key,
                    "operator": op,
                    "target": target,
                    "actual": value,
                    "magnitude": magnitude,
                }
            )
            total += magnitude
    return (len(violations) == 0), violations, total


def summarise_for_report(metrics, currency="USD"):
    """Format the headline indicators as display strings."""
    def pct(x):
        return f"{100.0 * x:.1f}%" if x is not None else "-"

    def money(x):
        if x is None or (isinstance(x, float) and math.isinf(x)):
            return "-"
        return f"{currency} {x:,.0f}"

    return {
        "Net present cost": money(metrics.get("npc")),
        "Levelised cost of energy": (
            f"{currency} {metrics['lcoe']:.4f}/kWh"
            if metrics.get("lcoe") not in (None, float("inf")) else "-"
        ),
        "Initial capital": money(metrics.get("initial_capital")),
        "Renewable fraction": pct(metrics.get("renewable_fraction")),
        "Loss of power supply probability": pct(metrics.get("lpsp")),
        "Unmet load": f"{metrics.get('unmet_kwh', 0):,.0f} kWh/yr",
        "Longest shortfall": f"{metrics.get('longest_shortfall_hours', 0)} h",
        "Self-sufficiency": pct(metrics.get("self_sufficiency")),
        "Self-consumption": pct(metrics.get("self_consumption")),
        "Curtailment": pct(metrics.get("curtailment_rate")),
        "Annual emissions": f"{metrics.get('emissions_kg', 0) / 1000.0:,.1f} t CO2",
        "Battery cycles": f"{metrics.get('battery_cycles_per_year', 0):.0f}/yr",
    }
