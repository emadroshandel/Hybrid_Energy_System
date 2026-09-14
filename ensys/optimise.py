"""
Top-level sizing driver.

Ties the pieces together: build an objective function from a base system,
a year of resource data and the financial parameters; screen a coarse
lattice for seeds; run the multi-objective swarm; return the Pareto front
with a recommended design.

The whole pipeline is deterministic given a seed. That is not a nicety - a
sizing study that returns a different answer each time cannot be defended
to a client or reproduced by a reviewer.
"""

from __future__ import annotations

import time

from . import dispatch as dispatch_mod
from . import economics as econ_mod
from . import metrics as metrics_mod
from .optim import algorithms as alg_mod
from .optim import screen as screen_mod
from .system import SearchSpace

DEFAULT_OBJECTIVES = ("npc", "lpsp", "renewable_fraction")

DEFAULT_ALGORITHM = alg_mod.DEFAULT

DEFAULT_CONSTRAINTS = {
    # A design that cannot keep the lights on is not a cheap design, it is a
    # different product. 5% is a common planning threshold; tighten it for
    # critical loads.
    "lpsp": ("<=", 0.05),
}


class SizingStudy:
    """
    One complete sizing problem.

    Parameters
    ----------
    base_system : SystemConfig carrying the component models. Its unit counts
        are ignored and replaced by the search.
    resources : dict with 'load' and the per-unit generation series
    econ : EconomicParameters
    objectives : names from the metrics dict, minimised (with the sign flip
        for naturally-maximised ones handled in metrics.objective_vector)
    constraints : {metric: (operator, value)}
    space : SearchSpace, or None to derive bounds from the load
    algorithm : which search to run - a key from `optim.algorithms`. The
        choice changes how the front is found, never how a design is
        evaluated: every algorithm calls the same `evaluate`, so two
        algorithms scoring the same design differently is a bug, not a
        difference of opinion.
    """

    def __init__(
        self,
        base_system,
        resources,
        econ=None,
        objectives=DEFAULT_OBJECTIVES,
        constraints=None,
        space=None,
        strategy=dispatch_mod.LOAD_FOLLOWING,
        seed=1234,
        size_ev_fleet=False,
        algorithm=DEFAULT_ALGORITHM,
    ):
        # Validate the algorithm name here rather than at run time, so a
        # typo fails before a study has spent a minute simulating.
        alg_mod.get(algorithm)
        self.algorithm = algorithm or DEFAULT_ALGORITHM
        self.base_system = base_system
        self.resources = resources
        self.econ = econ or econ_mod.EconomicParameters()
        self.objectives = tuple(objectives)
        self.constraints = (
            DEFAULT_CONSTRAINTS if constraints is None else constraints
        )
        self.strategy = strategy
        self.seed = int(seed)
        self.size_ev_fleet = bool(size_ev_fleet)

        if space is None:
            space = self._derive_space()
        self.space = space

        self._eval_count = 0
        self._eval_time = 0.0

    def _derive_space(self):
        b = screen_mod.bounds_from_load(
            self.resources["load"],
            pv_unit=self.resources.get("pv_unit"),
            wind_unit=self.resources.get("wind_unit"),
            battery=self.base_system.battery,
            genset=self.base_system.genset,
            # Without the connection the bounds cannot tell an island from a
            # site with a firm supply, and have to assume the island - which
            # is how a grid-connected study ends up searching designs that
            # generate several times what it uses.
            grid=self.base_system.grid,
        )
        self.bounds_basis = b["basis"]

        # The charge points are a decision, not a given. Leaving them out
        # was the same defect the MATLAB had: `mainWT.m` searched an EV
        # dimension that `sim4.m` never read, so a third of the swarm's
        # effort explored a variable that could not change the answer. Here
        # the variable was absent altogether, which is the same thing with
        # less wasted effort — the fleet was whatever the form said, and
        # nothing could trade it against anything.
        # By default the charge points are FIXED at what the site has.
        # An EV fleet is a demand, not an option: letting the optimiser
        # reduce it to zero does not save money, it deletes the vehicles,
        # and every objective improves because the hardest part of the
        # problem has been deleted with them. The count becomes a free
        # variable only when the study explicitly asks for the number of
        # charge points to be sized, and then the departure obligation is
        # what stops it going to zero.
        ev = self.base_system.ev
        n_ev = int(self.base_system.n_chargers or 0)
        if ev is None or n_ev <= 0:
            ev_bounds = (0, 0)
        elif getattr(self, "size_ev_fleet", False):
            ev_bounds = (1, max(1, int(round(n_ev * 1.5))))
        else:
            ev_bounds = (n_ev, n_ev)

        # Build the box from the system's own decision keys, in its own
        # order. Constructing it from a fixed list of four names and hoping
        # the two line up is how a dimension ends up driving the wrong
        # asset: with only PV and a fleet present the vector is two long,
        # the box was four, and the fleet silently took the wind bound.
        per_key = {
            "pv": b["n_pv"],
            "wind": b["n_wind"],
            "battery": b["n_battery"],
            "genset": b["n_genset"],
            "ev_fleet": ev_bounds,
        }
        keys = self.base_system.decision_keys()
        bounds = []
        for k in keys:
            if k in per_key:
                bounds.append(per_key[k])
                continue
            # Anything else in the catalogue - CSP, biomass, a flywheel, a
            # hydrogen store - gets bounds from what KIND of asset it is.
            # Falling back to (0, 0) here, as this did, is not a neutral
            # default: it pins the technology at zero units for the whole
            # search, so the study reports "the optimiser did not choose
            # one" when the optimiser was never allowed to.
            bounds.append(self._bounds_for(k, b))
        if not bounds:
            bounds, keys = [(0, 0)], ["none"]
        return SearchSpace(bounds=bounds, keys=keys)

    def _bounds_for(self, key, basis):
        """
        Unit bounds for a technology with no hand-written rule, from its
        asset class.

        The same two ceilings the named technologies get - energy and power
        for generation, a day or two of demand for storage - applied through
        the generic asset interface rather than by name.
        """
        from .assets import Dispatchable, FlexibleLoad, NonDispatchable, Storage

        asset = self.base_system.asset(key)
        if asset is None:
            return (0, 0)

        b = basis["basis"]
        annual = b["annual_demand_kwh"]
        peak = b["peak_load_kw"]
        mean = b["mean_load_kw"]
        headroom = b["headroom"]

        if isinstance(asset, NonDispatchable):
            unit = self.resources.get(f"unit::{key}") or []
            unit_annual = sum(unit)
            if unit_annual <= 0:
                return (0, 0)
            limit = annual * headroom / unit_annual
            unit_kw = max(unit) if unit else 0.0
            if unit_kw > 0 and peak > 0:
                limit = min(
                    limit, peak * screen_mod.PEAK_POWER_HEADROOM / unit_kw
                )
            return (0, max(1, int(round(limit))))

        if isinstance(asset, Dispatchable):
            rated = asset.rated_power_kw()
            if rated <= 0 or peak <= 0:
                return (0, 0)
            return (0, max(1, int(round(peak / rated)) + 1))

        if isinstance(asset, Storage):
            usable = asset.rated_energy_kwh()
            try:
                window = float(asset.soc_max) - float(asset.soc_min)
                if 0.0 < window <= 1.0:
                    usable *= window
            except (AttributeError, TypeError, ValueError):
                pass
            if usable <= 0:
                return (0, 0)
            days = 2.0 - b.get("grid_firmness", 0.0)
            return (0, max(1, int(round(mean * 24.0 * days / usable))))

        if isinstance(asset, FlexibleLoad):
            # A demand, not an option - see the EV note above.
            return (0, 0)

        return (0, 0)

    # ---------------------------------------------------------- evaluation

    def evaluate(self, x):
        """
        Evaluate one decision vector.

        Returns (objectives, metrics, feasible, violation) - the contract the
        optimiser expects.
        """
        t0 = time.perf_counter()
        system = self.base_system.with_decision(x)

        if system.is_empty:
            # No generation and no grid: infinitely bad, but scored rather
            # than rejected so the swarm learns to move away from it.
            inf = float("inf")
            return (
                [inf] * len(self.objectives),
                {"lpsp": 1.0, "npc": inf, "renewable_fraction": 0.0},
                False,
                1e6,
            )

        result = dispatch_mod.simulate(system, self.resources, self.strategy)
        econ_result = econ_mod.evaluate(system, result, self.econ)
        m = metrics_mod.compute(result, system, econ_result)

        feasible, violations, total = metrics_mod.check_constraints(
            m, self.constraints
        )
        obj = metrics_mod.objective_vector(m, self.objectives)

        m["_violations"] = violations
        self._eval_count += 1
        self._eval_time += time.perf_counter() - t0
        return obj, m, feasible, total

    # ---------------------------------------------------------------- run

    def run(
        self,
        n_particles=30,
        n_iterations=60,
        screen_levels=4,
        use_screening=True,
        progress_fn=None,
        time_budget_s=None,
        algorithm=None,
    ):
        """
        Execute the full pipeline and return a SizingResult.

        `algorithm` overrides the one the study was built with, so the same
        study object can be run twice and the two fronts compared. That
        comparison is the honest way to tell a converged front from a
        lucky one.
        """
        t_start = time.perf_counter()
        name = algorithm or self.algorithm
        entry = alg_mod.get(name)

        seeds = []
        screen_solutions = []
        prefilter = self._prefilter()

        # An enumeration visits the coarse lattice anyway, and it has no
        # starting points to seed, so screening before it is pure waste.
        if use_screening and not entry["exact"]:
            screen_solutions, seeds = screen_mod.lattice_screen(
                self.space,
                self.evaluate,
                levels=screen_levels,
                prefilter=prefilter,
                progress_fn=(
                    (lambda i, n: progress_fn("screening", i, n))
                    if progress_fn else None
                ),
            )

        def _prog(it, total, front, evals):
            if progress_fn:
                progress_fn("optimising", it + 1, total, front, evals)

        opt = alg_mod.build(
            name,
            self.space,
            self.evaluate,
            n_particles=n_particles,
            n_iterations=n_iterations,
            seed=self.seed,
            progress_fn=_prog if progress_fn else None,
            time_budget_s=time_budget_s,
            prefilter=prefilter,
        )
        search = opt.run(seed_points=seeds)

        for s in screen_solutions:
            search.archive.add(s)

        return SizingResult(
            self, search, screen_solutions, time.perf_counter() - t_start
        )

    def _prefilter(self):
        """
        Lattice points that pass the annual energy-balance test, or None.

        Cheap and best-effort: any failure here costs a little speed and
        nothing else, so it is swallowed rather than allowed to end a study
        that would otherwise have succeeded.
        """
        try:
            if self.space.size() > 200000:
                return None
            yields = screen_mod.estimate_unit_yields(
                self.resources, self.base_system, keys=self.space.keys
            )
            limit = (
                self.base_system.grid.import_limit_kw
                if self.base_system.grid else 0.0
            )
            return screen_mod.analytic_filter(
                self.space, sum(self.resources["load"]), yields, limit,
                must_take=screen_mod.must_take_mask(self.space.keys),
            )
        except Exception:
            return None


class SizingResult:
    """The outcome of a sizing study, with the front and a recommendation."""

    def __init__(self, study, search_result, screen_solutions, elapsed_s):
        self.study = study
        self.search = search_result
        self.screen_solutions = screen_solutions
        self.elapsed_s = elapsed_s

    @property
    def mopso(self):
        """
        The raw search result, under its original name.

        Kept because reports, tests and notebooks written before there was
        a choice of algorithm reach for `.mopso`; it now returns whichever
        algorithm ran. `.search` is the name to use in new code.
        """
        return self.search

    @property
    def algorithm(self):
        return self.search.algorithm

    @property
    def front(self):
        """Feasible non-dominated designs, best compromise first."""
        members = self.search.archive.feasible_members()
        if not members:
            members = list(self.search.archive)
        return members

    def recommended(self):
        """
        The design to lead the report with: the knee of the feasible front.
        """
        feasible = self.search.archive.feasible_members()
        if not feasible:
            return self.search.archive.knee_point()
        from .optim.pareto import Archive

        tmp = Archive(capacity=len(feasible))
        for s in feasible:
            tmp.add(s)
        return tmp.knee_point()

    # ------------------------------------------------------- highlights
    #
    # These three are what a reader looks at first, so how they are chosen
    # matters as much as the search that produced the front.
    #
    # The trap they all share: reliability and renewable fraction SATURATE.
    # Once a design meets the load in every hour, LPSP is 0.0 and adding
    # capacity cannot improve it; once renewables cover the demand, the
    # fraction is 1.0 and stays there. A plain `min(lpsp)` or `max(rf)` over
    # the archive therefore returns whichever member of a large tied set the
    # iteration order happens to reach - and because bigger designs enter
    # the archive later and are never worse on a saturated objective, that
    # is reliably the largest system the search ever tried, pinned to the
    # top corner of the search box. Reporting it as "most reliable" invites
    # the reader to treat a search artefact as a recommendation: it is the
    # answer to "what is the biggest thing you looked at", not to "what
    # should I build".
    #
    # Every selector below therefore (a) considers only FEASIBLE designs,
    # (b) treats near-equal values on the headline objective as equal, using
    # a tolerance rather than exact float comparison, and (c) breaks the tie
    # on net present cost. The cheapest design that is as reliable as the
    # best is the one worth building.

    # Two designs whose LPSP differs by less than this are equally reliable
    # for reporting purposes: 0.05% of annual demand is far inside the
    # uncertainty of the load forecast that produced it.
    LPSP_TIE_TOLERANCE = 5e-4
    # Likewise one percentage point of renewable fraction.
    RF_TIE_TOLERANCE = 1e-2

    def _pool(self):
        """
        Feasible designs, or the whole archive when nothing is feasible.

        Falling back is deliberate - a study where no design meets the
        constraint should still show its best attempts - but the caller is
        told, through `feasible` on each row, which case it is in.
        """
        return self.search.archive.feasible_members() or list(self.search.archive)

    def _npc(self, s):
        v = s.metrics.get("npc")
        return float("inf") if v is None else v

    def _best_with_cost_tiebreak(self, key_fn, tolerance, maximise=False):
        """
        Best on `key_fn`, then cheapest among everything within `tolerance`
        of that best. This is what stops a saturated objective from
        selecting the largest system in the archive.

        Returns (solution, n_tied) so the caller can say how many designs
        were equally good - a highlight chosen from a tie of forty is a
        different statement from one chosen from a tie of one.
        """
        pool = self._pool()
        if not pool:
            return None, 0
        values = [key_fn(s) for s in pool]
        best = max(values) if maximise else min(values)
        if maximise:
            tied = [s for s, v in zip(pool, values) if v >= best - tolerance]
        else:
            tied = [s for s, v in zip(pool, values) if v <= best + tolerance]
        return min(tied, key=self._npc), len(tied)

    def cheapest(self):
        """Lowest net present cost among feasible designs."""
        pool = self._pool()
        if not pool:
            return None
        return min(pool, key=self._npc)

    def most_reliable(self):
        """
        The most reliable design worth building: lowest LPSP, and among
        designs that are equally reliable within tolerance, the cheapest.

        Infeasible designs are excluded. Previously they were not, which
        meant a design violating the study's own constraint could be
        presented as the headline recommendation.
        """
        s, _ = self._best_with_cost_tiebreak(
            lambda x: x.metrics.get("lpsp", 1.0),
            self.LPSP_TIE_TOLERANCE,
        )
        return s

    def greenest(self):
        """
        Highest renewable fraction, cheapest among those within a
        percentage point of it.
        """
        s, _ = self._best_with_cost_tiebreak(
            lambda x: x.metrics.get("renewable_fraction", 0.0),
            self.RF_TIE_TOLERANCE,
            maximise=True,
        )
        return s

    def highlight_notes(self):
        """
        One line per headline card explaining how it was chosen.

        A card labelled "most reliable" showing a small, cheap system looks
        wrong until you know that every design on the front met the load in
        every hour, so reliability could not distinguish them and cost broke
        the tie. Saying so is the difference between a result that reads as
        a mistake and one that reads as an answer.
        """
        notes = {}
        pool = self._pool()
        if not pool:
            return notes

        _, n_rel = self._best_with_cost_tiebreak(
            lambda x: x.metrics.get("lpsp", 1.0), self.LPSP_TIE_TOLERANCE
        )
        best_lpsp = min(x.metrics.get("lpsp", 1.0) for x in pool)
        if n_rel > 1:
            if best_lpsp <= self.LPSP_TIE_TOLERANCE:
                notes["most_reliable"] = (
                    f"{n_rel} of the {len(pool)} designs on the front meet the "
                    f"load in essentially every hour, so reliability cannot "
                    f"separate them. This is the cheapest of those - buying "
                    f"more capacity would not make the supply any firmer."
                )
            else:
                notes["most_reliable"] = (
                    f"Lowest unserved energy on the front "
                    f"({100 * best_lpsp:.2f}%); the cheapest of the {n_rel} "
                    f"designs that reach it."
                )

        _, n_green = self._best_with_cost_tiebreak(
            lambda x: x.metrics.get("renewable_fraction", 0.0),
            self.RF_TIE_TOLERANCE, maximise=True,
        )
        best_rf = max(x.metrics.get("renewable_fraction", 0.0) for x in pool)
        if n_green > 1:
            notes["greenest"] = (
                f"Highest renewable share on the front ({100 * best_rf:.0f}%); "
                f"the cheapest of the {n_green} designs within a percentage "
                f"point of it."
            )
        if not self.search.archive.feasible_members():
            for k in ("most_reliable", "greenest", "cheapest", "recommended"):
                notes[k] = (
                    "No design met the study's constraints, so these are the "
                    "best attempts rather than valid answers. Relax the "
                    "constraint or widen the search."
                ) + ("" if k not in notes else " " + notes[k])
        return notes

    def rebuild_system(self, solution):
        """Reconstruct the full SystemConfig for a solution on the front."""
        return self.study.base_system.with_decision(solution.x)

    def rerun(self, solution):
        """Re-simulate a solution to recover its full hourly detail."""
        system = self.rebuild_system(solution)
        result = dispatch_mod.simulate(
            system, self.study.resources, self.study.strategy
        )
        econ_result = econ_mod.evaluate(system, result, self.study.econ)
        m = metrics_mod.compute(result, system, econ_result)
        return system, result, econ_result, m

    def end_of_life_check(self, years=None):
        """
        Re-simulate the recommended design as it will be in its final year.

        Everything else in this study describes year one. That is the year a
        design is least likely to fail and the one no client operates in for
        long: the array has degraded, and at most sites the demand has grown.
        A system that meets the load with nothing to spare in year one does
        not meet it in year twenty, and a study that never says so has
        answered a question nobody asked.

        This costs one extra dispatch run, not twenty, because the question
        is not "what is the year-by-year cash flow" - the discounting
        handles that - but "does the design still work at the end". The
        worst year is the last one, so it is the one to simulate.

        Returns None when there is nothing to check: no recommended design,
        or a study with neither degradation nor load growth, in which case
        the final year is identical to the first and saying so twice adds
        nothing.
        """
        rec = self.recommended()
        if rec is None:
            return None

        n = int(years if years is not None else self.study.econ.project_years)
        growth = float(getattr(self.study.econ, "load_growth_rate", 0.0) or 0.0)
        system = self.rebuild_system(rec)

        pv = system.pv
        pv_factor = 1.0
        if pv is not None and getattr(pv, "degradation_per_year", 0.0):
            pv_factor = pv.yield_after_years(n)

        load_factor = (1.0 + growth) ** n
        if abs(pv_factor - 1.0) < 1e-9 and abs(load_factor - 1.0) < 1e-9:
            return None

        res = dict(self.study.resources)
        res["load"] = [v * load_factor for v in res["load"]]
        if pv_factor != 1.0 and res.get("pv_unit"):
            res["pv_unit"] = [v * pv_factor for v in res["pv_unit"]]
        # Cached per-asset series would otherwise carry year-one output
        # straight into the year-twenty run.
        for k in list(res):
            if k.startswith("unit::") or k.startswith("profile::"):
                res.pop(k)

        result = dispatch_mod.simulate(system, res, self.study.strategy)
        econ_result = econ_mod.evaluate(system, result, self.study.econ)
        m = metrics_mod.compute(result, system, econ_result)

        first = rec.metrics
        notes = []
        if m.get("lpsp", 0.0) > first.get("lpsp", 0.0) + 1e-6:
            notes.append(
                f"Unserved energy rises from {100 * first.get('lpsp', 0):.2f}% "
                f"in year one to {100 * m['lpsp']:.2f}% in year {n}."
            )
        if m.get("renewable_fraction", 0.0) < first.get("renewable_fraction", 0.0) - 1e-6:
            notes.append(
                f"The renewable share falls from "
                f"{100 * first.get('renewable_fraction', 0):.0f}% to "
                f"{100 * m['renewable_fraction']:.0f}%."
            )
        constraint = (self.study.constraints or {}).get("lpsp")
        if constraint and constraint[0] in ("<=", "<"):
            if m.get("lpsp", 0.0) > float(constraint[1]) + 1e-9:
                notes.append(
                    f"By year {n} the design no longer meets the study's own "
                    f"reliability constraint (LPSP <= "
                    f"{100 * float(constraint[1]):.1f}%). Size it against the "
                    f"final year, not the first."
                )
        return {
            "years": n,
            "pv_degradation_factor": pv_factor,
            "load_growth_factor": load_factor,
            "metrics": {k: v for k, v in m.items() if not k.startswith("_")},
            "year_one": {
                "lpsp": first.get("lpsp"),
                "renewable_fraction": first.get("renewable_fraction"),
                "unmet_kwh": first.get("unmet_kwh"),
            },
            "notes": notes,
        }

    def boundary_warnings(self):
        """
        Flag when a recommended design sits on a search boundary.

        A solution pinned to an upper bound is not an optimum, it is a
        truncated search, and reporting it as an optimum is misleading.
        """
        rec = self.recommended()
        if not rec:
            return []
        # Name the dimension from the search space's own keys. A fixed list
        # of four is right only when all four technologies are present, and
        # wrong - silently, in a sentence the user is meant to act on -
        # whenever they are not.
        labels = {
            "pv": "PV units", "wind": "wind turbines",
            "battery": "battery units", "genset": "generator units",
            "ev_fleet": "EV charge points",
        }
        names = [
            labels.get(k, k.replace("_", " ") + " units")
            for k in self.study.space.keys
        ]
        out = []
        for i, (lo, hi) in enumerate(self.study.space.bounds):
            if hi <= lo:
                continue
            if rec.x[i] >= hi:
                out.append(
                    f"The recommended design uses the maximum allowed number "
                    f"of {names[i]} ({hi}). The true optimum may lie beyond "
                    f"the search bounds - widen them and re-run."
                )
            elif rec.x[i] <= lo and lo > 0:
                out.append(
                    f"The recommended design sits at the minimum allowed "
                    f"number of {names[i]} ({lo})."
                )
        return out

    def summary(self):
        rec = self.recommended()
        entry = alg_mod.get(self.search.algorithm)
        return {
            "elapsed_s": self.elapsed_s,
            "algorithm": self.search.algorithm,
            "algorithm_label": entry["label"],
            # An exact front is a different kind of answer from an
            # approximate one, and a report that does not say which it is
            # invites the reader to assume the stronger claim.
            "exact": bool(self.search.exact),
            "front_size": len(self.front),
            "evaluations": self.search.evaluations,
            "cache_hits": self.search.cache_hits,
            "iterations": self.search.iterations,
            "objectives": list(self.study.objectives),
            "constraints": {
                k: [v[0], v[1]] for k, v in (self.study.constraints or {}).items()
            },
            "search_space_size": self.study.space.size(),
            "recommended": rec.x if rec else None,
            "warnings": self.boundary_warnings() + self.search.notes,
        }
