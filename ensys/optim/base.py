"""
What every search algorithm in this package has in common.

Three things are shared, and sharing them is what makes the algorithms
interchangeable rather than merely similar:

  * `SearchResult` — the object a run returns. `SizingStudy` and everything
    downstream of it read only this, so swapping the algorithm changes
    nothing above this line.

  * `Evaluator` — memoised evaluation keyed on the integer decision vector.
    Every algorithm here revisits points: a swarm converges onto them, a
    genetic population inherits them, a lattice hits them from two
    directions. A dispatch simulation costs milliseconds and there are
    thousands of them, so the cache is not a micro-optimisation - on a
    converged run it commonly serves more than half the requests.

  * `stop_reason` — the rules for stopping early. An algorithm that runs its
    full budget after the front stopped moving is burning the user's time,
    and one that stops without saying why is unauditable. Both algorithms
    stop on the same two conditions and record the same sentence.

The comparison between algorithms only means something if they are given
the same evaluation budget, so `budget(n_particles, n_iterations)` is
defined here once and used by all of them.
"""

from __future__ import annotations

import time

from .pareto import Archive, Solution, crowding_distance


def budget(n_particles, n_iterations):
    """
    Evaluations a run is allowed, from the two numbers the interface asks
    for. Every algorithm converts the same pair the same way, so a study
    re-run with a different algorithm is a like-for-like comparison.
    """
    return max(1, int(n_particles) * max(1, int(n_iterations)))


class SearchResult:
    """
    Outcome of one optimisation run, whatever produced it.

    `archive` is the external non-dominated set. It is the deliverable: the
    population or swarm that produced it is an implementation detail and is
    not kept.
    """

    def __init__(self, algorithm="unknown"):
        self.algorithm = algorithm
        self.archive = None
        self.history = []
        self.evaluations = 0
        self.cache_hits = 0
        self.elapsed_s = 0.0
        self.iterations = 0
        self.converged = False
        self.exact = False          # True only when the space was enumerated
        self.notes = []

    @property
    def front(self):
        return list(self.archive) if self.archive else []

    def best_compromise(self):
        return self.archive.knee_point() if self.archive else None

    def summary(self):
        return {
            "algorithm": self.algorithm,
            "front_size": len(self.archive) if self.archive else 0,
            "evaluations": self.evaluations,
            "cache_hits": self.cache_hits,
            "iterations": self.iterations,
            "elapsed_s": self.elapsed_s,
            "converged": self.converged,
            "exact": self.exact,
            "eval_rate_per_s": (
                self.evaluations / self.elapsed_s if self.elapsed_s > 0 else 0.0
            ),
            "notes": self.notes,
        }


class Evaluator:
    """Memoised objective evaluation over integer decision vectors."""

    def __init__(self, evaluate_fn):
        self.evaluate_fn = evaluate_fn
        self.cache = {}
        self.evaluations = 0
        self.cache_hits = 0

    def __call__(self, x):
        key = tuple(x)
        hit = self.cache.get(key)
        if hit is not None:
            self.cache_hits += 1
            return hit
        objectives, metrics, feasible, violation = self.evaluate_fn(list(x))
        sol = Solution(list(x), objectives, metrics, feasible, violation)
        self.cache[key] = sol
        self.evaluations += 1
        return sol

    @property
    def explored(self):
        return len(self.cache)


class StopWatch:
    """Time budget and stall counter, with the sentences to explain both."""

    def __init__(self, time_budget_s=None, stall_iterations=15):
        self.time_budget_s = time_budget_s
        self.stall_iterations = int(stall_iterations or 0)
        self.t0 = time.perf_counter()
        self._best = -1
        self._stall = 0

    @property
    def elapsed(self):
        return time.perf_counter() - self.t0

    def tick(self, front_size):
        """Record this generation's front size; returns the stall count."""
        if front_size > self._best:
            self._best = front_size
            self._stall = 0
        else:
            self._stall += 1
        return self._stall

    def stop_reason(self, iteration):
        """
        None to carry on, or the sentence to record and stop on.
        """
        if self.stall_iterations and self._stall >= self.stall_iterations:
            return (
                f"Stopped at iteration {iteration + 1}: the Pareto front "
                f"stopped growing for {self._stall} consecutive iterations."
            )
        if self.time_budget_s and self.elapsed > self.time_budget_s:
            return (
                f"Stopped at iteration {iteration + 1}: time budget of "
                f"{self.time_budget_s:.0f} s reached."
            )
        return None


def finish(result, archive, ev, watch, space):
    """Common tail of a run: crowding, counters, and the exactness note."""
    crowding_distance(archive.members)
    result.archive = archive
    result.evaluations = ev.evaluations
    result.cache_hits = ev.cache_hits
    result.elapsed_s = watch.elapsed

    total = space.size()
    if total and ev.explored >= total:
        result.exact = True
        result.notes.append(
            "The search space was explored exhaustively; the reported front "
            "is exact, not an approximation."
        )
    return result


def seeded_population(space, rng, size, seed_points=None):
    """
    An initial population: the supplied seeds first, then random points.

    Duplicated seeds are not filtered out. A screening stage that returns
    the same point twice is telling the algorithm that region is good, and
    the cache makes the repeat evaluation free.
    """
    pop = []
    seeds = list(seed_points or [])
    for i in range(int(size)):
        pop.append(
            space.clamp(seeds[i]) if i < len(seeds) else space.random_point(rng)
        )
    return pop


def empty_archive(capacity):
    return Archive(capacity)
