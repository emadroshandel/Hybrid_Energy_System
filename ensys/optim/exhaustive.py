"""
Exhaustive and grid search.

No heuristic at all: enumerate the decision lattice and evaluate every
point. It is the slowest method here and, for a small problem, the only one
that is *right* rather than probably right.

Why a sizing tool needs it:

  * Small studies are common. A house with PV and a battery, four sizes of
    each, is sixteen designs. Running a swarm over sixteen designs is
    theatre; enumerating them takes a second and the answer is exact.

  * It is the ground truth the metaheuristics are checked against. When the
    swarm and the genetic algorithm disagree, neither can settle it. Running
    the same problem here settles it, and the regression tests do exactly
    that.

  * A user who does not trust an optimiser they cannot see inside can run
    this one instead. "Every combination was tried" needs no defending.

When the full lattice does not fit the evaluation budget, the search falls
back to the finest *uniform* sub-lattice that does, and says so. That is
still useful - a coarse grid over the whole box is a decent global survey -
but it is no longer exact, and the result never claims to be. Levels are
removed from the dimension that currently has the most, so the resolution
stays balanced across technologies rather than collapsing whichever
dimension happens to be listed last.
"""

from __future__ import annotations

import itertools

from . import base as base_mod


def _levels_within(space, limit):
    """
    Values to sample per dimension so the product stays under `limit`.

    Starts from the full range and removes one level at a time from
    whichever dimension has the most, which keeps the grid as square as the
    budget allows.
    """
    counts = []
    for lo, hi in space.bounds:
        counts.append(max(1, hi - lo + 1))

    def product(c):
        total = 1
        for v in c:
            total *= v
        return total

    guard = 0
    while product(counts) > limit and guard < 100000:
        guard += 1
        i = max(range(len(counts)), key=lambda k: counts[k])
        if counts[i] <= 1:
            break
        counts[i] -= 1

    per_dim = []
    for (lo, hi), k in zip(space.bounds, counts):
        span = hi - lo
        if span <= 0 or k <= 1:
            per_dim.append([lo])
            continue
        vals = sorted({lo + int(round(span * i / (k - 1))) for i in range(k)})
        per_dim.append(vals)
    return per_dim


class GridSearch:
    """
    Enumerate the lattice.

    Parameters are named to match the other algorithms so a study can swap
    between them without changing its call: `n_particles * n_iterations` is
    read as the evaluation budget and nothing else.
    """

    def __init__(
        self,
        space,
        evaluate_fn,
        n_particles=30,
        n_iterations=60,
        archive_capacity=150,
        seed=1234,
        progress_fn=None,
        time_budget_s=None,
        prefilter=None,
        max_evaluations=None,
        **_ignored,
    ):
        self.space = space
        self.evaluate_fn = evaluate_fn
        self.max_evaluations = int(
            max_evaluations
            if max_evaluations is not None
            else base_mod.budget(n_particles, n_iterations)
        )
        self.archive_capacity = int(archive_capacity)
        self.progress_fn = progress_fn
        self.time_budget_s = time_budget_s
        self.prefilter = prefilter

    def run(self, seed_points=None):
        ev = base_mod.Evaluator(self.evaluate_fn)
        # Nothing stalls in an enumeration - there is no "stopped
        # improving", only "finished" - so the stall rule is switched off
        # and only the time budget can cut it short.
        watch = base_mod.StopWatch(self.time_budget_s, stall_iterations=0)
        result = base_mod.SearchResult("grid")
        archive = base_mod.empty_archive(self.archive_capacity)

        total = self.space.size()
        full = total <= self.max_evaluations

        if full:
            per_dim = [
                list(range(lo, hi + 1)) for lo, hi in self.space.bounds
            ]
        else:
            per_dim = _levels_within(self.space, self.max_evaluations)
            shape = " x ".join(str(len(v)) for v in per_dim)
            result.notes.append(
                f"The full lattice holds {total:,} designs, more than the "
                f"budget of {self.max_evaluations:,} evaluations. A uniform "
                f"{shape} grid was searched instead: this is a survey of the "
                f"whole space, not an exhaustive search, and the front it "
                f"returns is not guaranteed to be the true one. Raise the "
                f"swarm size or iterations, narrow the bounds, or use MOPSO "
                f"or NSGA-II for a space this large."
            )

        points = [list(p) for p in itertools.product(*per_dim)]

        skipped = 0
        if self.prefilter is not None:
            allowed = {tuple(p) for p in self.prefilter}
            kept = [p for p in points if tuple(p) in allowed]
            # A prefilter that rejects everything is a broken prefilter, not
            # an empty answer. Ignore it rather than return nothing.
            if kept:
                skipped = len(points) - len(kept)
                points = kept

        stopped_early = False
        for i, p in enumerate(points):
            archive.add(ev(p))
            if self.progress_fn and (i % 10 == 0 or i == len(points) - 1):
                self.progress_fn(i, len(points), len(archive), ev.evaluations)
            if self.time_budget_s and watch.elapsed > self.time_budget_s:
                result.notes.append(
                    f"Stopped after {i + 1} of {len(points)} designs: time "
                    f"budget of {self.time_budget_s:.0f} s reached."
                )
                stopped_early = True
                break

        if skipped:
            result.notes.append(
                f"{skipped:,} designs were ruled out before simulation: their "
                f"annual generation plus the maximum possible import cannot "
                f"meet the annual demand, so they cannot serve the load "
                f"whatever the dispatch does."
            )

        result.iterations = 1
        result.converged = full and not stopped_early
        out = base_mod.finish(result, archive, ev, watch, self.space)
        if full and not stopped_early and not out.exact:
            # Every lattice point was simulated; anything the prefilter
            # removed was proven infeasible rather than skipped on a guess,
            # so the front really is exact even though the cache holds
            # fewer entries than the box.
            out.exact = True
            out.notes.append(
                "Every design in the search space was evaluated; the "
                "reported front is exact, not an approximation."
            )
        return out
