"""
Fast screening stage.

This is what replaces an MILP seed in the stdlib-only build. It exists to
hand the swarm a small set of already-good starting points, so that MOPSO
spends its evaluations refining a front rather than discovering where the
feasible region is.

Two layers, cheapest first:

  1. `analytic_filter` - an annual energy-balance check costing microseconds
     per candidate. It cannot prove a design works, because it ignores
     timing entirely, but it can prove one cannot: a system whose total
     annual generation plus maximum possible import falls short of annual
     demand will never meet the load, whatever the dispatch does. Rejecting
     those up front typically removes half of a naive lattice.

  2. `lattice_screen` - the real dispatch evaluated on a coarse grid. Using
     the true simulator rather than a simplified surrogate means the seeds
     are consistent with what the optimiser will later measure. A surrogate
     that disagrees with the real model seeds the swarm in the wrong place,
     which is worse than not seeding at all.

The output is deliberately a diverse set rather than the single best point:
seeding every particle at one location destroys the swarm's initial spread.
"""

from __future__ import annotations

import itertools

from .pareto import Solution, non_dominated_sort, crowding_distance


def analytic_filter(space, annual_demand_kwh, unit_yields, grid_import_limit_kw,
                    hours=8760, max_oversize=None, must_take=None):
    """
    Enumerate lattice points that pass an annual energy-balance sanity test.

    Parameters
    ----------
    unit_yields : annual kWh produced by ONE unit of each technology, in the
        same dimension order as the search space. Dispatchable technologies
        (genset) should carry their maximum possible annual output.
    max_oversize : reject designs generating more than this multiple of
        annual demand. Such designs are never on the cost frontier and only
        waste evaluations. Left as None it follows the same grid-connected /
        islanded distinction the bounds use: a site that can import has no
        reason to build past about twice its demand, an island does.

        The former fixed default of 6.0 was doing almost nothing - it sat
        above the search box's own ceiling for most studies, so the filter
        that was meant to remove absurd designs removed none of them.

    must_take : booleans, one per dimension, marking the technologies whose
        output arrives whether it is wanted or not. ONLY these count towards
        the oversize test.

        This distinction is not a refinement, it is the difference between a
        working filter and a broken one. `unit_yields` credits a dispatchable
        unit with its theoretical maximum - rated power for every hour of the
        year - because that is the right figure for the feasibility half of
        the test, which asks whether a design COULD meet the load. Reusing it
        in the oversize half asks whether a diesel that could run flat out
        all year would generate too much, and the answer for any generator
        worth installing is yes. With a loose threshold that mistake stayed
        hidden; tightening it deleted every design containing a generator.

    Returns a list of decision vectors.
    """
    if max_oversize is None:
        max_oversize = 2.0 if grid_import_limit_kw > 0 else 4.0
    max_import = grid_import_limit_kw * hours
    if must_take is None:
        must_take = [True] * len(unit_yields)
    keep = []

    ranges = [range(lo, hi + 1) for lo, hi in space.bounds]
    for point in itertools.product(*ranges):
        gen = 0.0
        firm = 0.0
        for n, y, mt in zip(point, unit_yields, must_take):
            e = n * y
            gen += e
            if mt:
                firm += e
        if gen + max_import < annual_demand_kwh * 0.999:
            continue                      # cannot possibly serve the load
        if annual_demand_kwh > 0 and firm > annual_demand_kwh * max_oversize:
            continue                      # absurdly oversized
        keep.append(list(point))
    return keep


# Technologies whose output is taken as it comes. Storage and flexible
# loads move energy rather than making it and appear in neither list.
MUST_TAKE_KEYS = ("pv", "wind")


def must_take_mask(keys):
    """Which decision dimensions produce output that cannot be turned down."""
    return [k in MUST_TAKE_KEYS for k in (keys or ())]


def coarse_points(space, levels=4):
    """
    A coarse lattice: `levels` evenly spaced values per active dimension,
    always including both bounds.

    Fixed dimensions contribute their single value and cost nothing.
    """
    per_dim = []
    for lo, hi in space.bounds:
        if hi <= lo:
            per_dim.append([lo])
            continue
        span = hi - lo
        k = min(levels, span + 1)
        if k <= 1:
            per_dim.append([lo])
            continue
        vals = sorted({lo + int(round(span * i / (k - 1))) for i in range(k)})
        per_dim.append(vals)
    return [list(p) for p in itertools.product(*per_dim)]


def lattice_screen(space, evaluate_fn, levels=4, prefilter=None,
                   max_evaluations=400, progress_fn=None):
    """
    Evaluate a coarse lattice with the real objective function.

    Returns (solutions, seeds) where `seeds` is a diverse subset of the best
    points suitable for initialising a swarm.
    """
    points = coarse_points(space, levels)

    if prefilter is not None:
        allowed = {tuple(p) for p in prefilter}
        filtered = [p for p in points if tuple(p) in allowed]
        # If the prefilter eliminated the entire coarse lattice, fall back to
        # the unfiltered set rather than returning nothing - an empty seed
        # list is a silent failure the user would never see.
        if filtered:
            points = filtered

    if len(points) > max_evaluations:
        step = len(points) / float(max_evaluations)
        points = [points[int(i * step)] for i in range(max_evaluations)]

    solutions = []
    for i, p in enumerate(points):
        objectives, metrics, feasible, violation = evaluate_fn(p)
        solutions.append(Solution(p, objectives, metrics, feasible, violation))
        if progress_fn:
            progress_fn(i + 1, len(points))

    return solutions, select_seeds(solutions)


def select_seeds(solutions, n_seeds=12):
    """
    Choose a spread of good starting points.

    Takes the non-dominated front first, ordered by crowding distance so the
    extremes and the sparse regions come first. If the front is smaller than
    the requested count, the next fronts are added in order. This gives the
    swarm both quality and spread.
    """
    if not solutions:
        return []

    feasible = [s for s in solutions if s.feasible]
    pool = feasible if feasible else solutions

    fronts = non_dominated_sort(pool)
    seeds = []
    for front in fronts:
        crowding_distance(front)
        front_sorted = sorted(front, key=lambda s: s.crowding, reverse=True)
        for s in front_sorted:
            seeds.append(list(s.x))
            if len(seeds) >= n_seeds:
                return seeds
    return seeds


def estimate_unit_yields(resources, system, hours=8760, keys=None):
    """
    Annual energy produced by one unit of each technology, for the analytic
    filter. Dispatchable units report their theoretical maximum.

    `keys` is the decision-vector order to return them in. Without it the
    historical four-element order is used, which is right only when all
    four technologies are present: `analytic_filter` zips this list against
    the decision vector, and a zip against the wrong order credits one
    technology's output to another and rejects designs that are perfectly
    feasible. Storage and charge points yield nothing - storage moves
    energy rather than making it, and a fleet is a demand.
    """
    per_key = {
        "pv": sum(resources.get("pv_unit") or [0.0]),
        "wind": sum(resources.get("wind_unit") or [0.0]),
        "battery": 0.0,
        "genset": system.genset.rated_kw * hours if system.genset else 0.0,
        "ev_fleet": 0.0,
    }
    if keys is None:
        return [per_key["pv"], per_key["wind"],
                per_key["battery"], per_key["genset"]]
    return [per_key.get(k, 0.0) for k in keys]


# How far above annual demand one technology is allowed to generate on its
# own at the top of the search box.
#
# The old rule used 1.5 for every site. That is too loose for a
# grid-connected one and too tight for an island, and both errors are
# expensive in different ways.
#
#   Grid-connected. Export is normally paid a fraction of the import price,
#   often nothing, and is capped well below the import capacity. Generation
#   past roughly annual demand is therefore curtailed or given away, and no
#   design above about 1.5x annual has ever been on the cost frontier. A
#   looser ceiling does not find a better answer; it fills the box with
#   designs that only the saturated objectives can like.
#
#   Islanded. The opposite: with no import, the December deficit sets the
#   size, so annual-average thinking under-sizes badly. A solar-only island
#   at high latitude legitimately needs several times annual demand in
#   nameplate, most of it curtailed in summer. The ceiling has to allow it.
GRID_CONNECTED_HEADROOM = 1.5
ISLANDED_HEADROOM = 3.5

# A second, independent ceiling, in POWER rather than energy. Whatever the
# yield, a site does not install renewables at many times its own peak
# demand: the connection, the switchgear and the land all stop it first.
# This binds where the first rule does not - at a site with a poor resource,
# where "enough units to make 1.5x annual demand" can be an implausible
# amount of plant.
PEAK_POWER_HEADROOM = 6.0


def bounds_from_load(load, pv_unit=None, wind_unit=None, battery=None,
                     genset=None, headroom=None, grid=None):
    """
    Suggest search bounds from the load profile and the grid connection.

    A sensible upper bound matters more than it looks. Too low and the
    optimum sits on the boundary, which the report should flag rather than
    present as an answer. Too high is the more insidious failure: the extra
    space is not merely wasted search, it is populated by designs that are
    genuinely non-dominated on any objective that saturates - reliability
    and renewable fraction both do - so an oversized box does not just cost
    time, it puts absurd designs on the reported front.

    Two ceilings are applied to each generating technology and the tighter
    one wins: an ENERGY ceiling (enough units to make `headroom` times the
    annual demand alone) and a POWER ceiling (installed capacity no more
    than `PEAK_POWER_HEADROOM` times the peak load). Storage is bounded by
    the energy it can actually deliver, not its nameplate.

    `grid` is the GridConnection, or None. Passing it is what lets the
    ceiling distinguish a site that can import from one that cannot; the
    parameter is optional so older callers keep working, but omitting it
    gives an island's bounds to a grid-connected site.
    """
    annual = sum(load)
    peak = max(load) if load else 0.0
    mean = annual / len(load) if load else 0.0

    islanded = (grid is None) or getattr(grid, "is_islanded", False)
    firm_import_kw = 0.0 if islanded else float(
        getattr(grid, "import_limit_kw", 0.0) or 0.0
    )

    # How much of the site's own peak the connection can carry. Treating
    # this as a yes/no - "firm if the limit reaches the peak" - puts a site
    # whose connection covers 85% of its peak in the same class as a desert
    # island, and hands it an island's search box. The reality is a
    # continuum, so the headroom is interpolated along it: a connection that
    # covers the peak gets the grid-connected ceiling, one that covers
    # nothing gets the island's, and a partial supply gets the blend.
    firmness = 0.0
    if peak > 0 and firm_import_kw > 0:
        firmness = min(1.0, firm_import_kw / peak)
    elif peak <= 0 and firm_import_kw > 0:
        firmness = 1.0
    grid_is_firm = firmness >= 0.99

    if headroom is None:
        headroom = (
            ISLANDED_HEADROOM
            + (GRID_CONNECTED_HEADROOM - ISLANDED_HEADROOM) * firmness
        )

    def cap(unit_annual, unit_kw):
        """Units allowed, as the tighter of the energy and power ceilings."""
        if not unit_annual or unit_annual <= 0:
            return 0
        by_energy = annual * headroom / unit_annual
        limit = by_energy
        if unit_kw and unit_kw > 0 and peak > 0:
            by_power = peak * PEAK_POWER_HEADROOM / unit_kw
            limit = min(limit, by_power)
        return max(1, int(round(limit)))

    pv_unit_kw = (max(pv_unit) if pv_unit else 0.0)
    wind_unit_kw = (max(wind_unit) if wind_unit else 0.0)
    n_pv_max = cap(sum(pv_unit) if pv_unit else 0.0, pv_unit_kw)
    n_wt_max = cap(sum(wind_unit) if wind_unit else 0.0, wind_unit_kw)

    n_bat_max = 0
    if battery and battery.nominal_energy_kwh > 0:
        # Bound on USABLE energy, not nameplate. A store operated between
        # 10% and 90% state of charge delivers 80% of what it is sold as,
        # and a bound written against nameplate is a fifth too tight - which
        # is the direction that truncates the search silently.
        usable_fraction = 1.0
        try:
            window = float(battery.soc_max) - float(battery.soc_min)
            if 0.0 < window <= 1.0:
                usable_fraction = window
        except (AttributeError, TypeError, ValueError):
            pass
        usable_kwh = battery.nominal_energy_kwh * usable_fraction

        # One day of average demand where the grid can cover a bad day, two
        # where it cannot - an island has to ride through the night AND the
        # cloudy morning after it - interpolated along the same firmness
        # measure the generation ceiling uses.
        days = 2.0 - firmness
        n_bat_max = max(1, int(round(mean * 24.0 * days / usable_kwh)))

    n_gen_max = 0
    if genset and genset.rated_kw > 0:
        n_gen_max = max(1, int(round(peak / genset.rated_kw)) + 1)

    return {
        "n_pv": (0, n_pv_max),
        "n_wind": (0, n_wt_max),
        "n_battery": (0, n_bat_max),
        "n_genset": (0, n_gen_max),
        "basis": {
            "annual_demand_kwh": annual,
            "peak_load_kw": peak,
            "mean_load_kw": mean,
            "headroom": headroom,
            "peak_power_headroom": PEAK_POWER_HEADROOM,
            "grid_import_limit_kw": firm_import_kw,
            "grid_is_firm": grid_is_firm,
            "grid_firmness": firmness,
            "islanded": islanded,
        },
    }
