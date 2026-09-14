"""
NSGA-II over integer unit counts.

The elitist non-dominated sorting genetic algorithm of Deb, Pratap, Agarwal
and Meyarivan (2002). It is the reference algorithm of multi-objective
optimisation, and having it beside the swarm is what makes a result
defensible: two unrelated searches that agree on a front are evidence the
front is real, and two that disagree are evidence one of them has not
converged. A single algorithm can only ever tell you what it found.

The generational loop:

  1. binary tournament on (rank, crowding distance) picks the parents
  2. simulated binary crossover produces two children per pair
  3. polynomial mutation perturbs each gene with probability 1/n
  4. parents and children are merged, sorted into fronts, and the best N
     survive - the last admitted front trimmed by crowding distance

Step 4 is the elitism, and it is the whole point: a good design can never
be lost to the next generation, which is exactly the failure that makes
plain multi-objective PSO produce patchy fronts and why MOPSO needs an
external archive to compensate. One is kept here too, but only so the
result object is identical; the population itself already carries the front.

Three adaptations for this problem:

  * The decision variables are counts of physical machines, so SBX and
    polynomial mutation - both defined on the reals - are rounded and
    clamped after each operator. Rounding a real-coded operator is the
    standard treatment and behaves well as long as the bounds are wide
    compared with one unit, which they are here.

  * A dimension whose bounds are equal (a fixed charge-point count, a
    technology the user did not enable) is skipped by every operator
    rather than being sampled and clamped back. Sampling it wastes nothing
    numerically but does waste the mutation probability budget on a gene
    that cannot change.

  * Constraint handling is Deb's feasibility rule throughout, inherited
    from `pareto.dominates`, so a design that fails the LPSP limit can
    guide the search without ever entering the reported front.

Compared with the swarm on the same evaluation budget, NSGA-II usually
returns a more evenly spread front and finds the extremes more reliably;
the swarm usually converges to the knee faster. Neither is universally
better, which is why the choice belongs to the user.
"""

from __future__ import annotations

import random

from . import base as base_mod
from .pareto import crowding_distance, non_dominated_sort


class NSGA2:
    """
    Elitist non-dominated sorting genetic algorithm.

    Parameters
    ----------
    space : SearchSpace giving integer bounds per dimension
    evaluate_fn : callable(x) -> (objectives, metrics, feasible, violation)
    n_particles : population size (named for interchangeability with MOPSO;
        an odd value is rounded up so pairs always mate)
    n_iterations : generations
    crossover_rate : probability a selected pair is recombined at all
    eta_c, eta_m : SBX and polynomial-mutation distribution indices. Larger
        values keep children closer to their parents; 15 and 20 are the
        values used in the original paper and behave well here.
    mutation_rate : per-gene probability, or None for the usual 1/n
    """

    def __init__(
        self,
        space,
        evaluate_fn,
        n_particles=30,
        n_iterations=60,
        crossover_rate=0.9,
        eta_c=15.0,
        eta_m=20.0,
        mutation_rate=None,
        archive_capacity=150,
        seed=1234,
        progress_fn=None,
        stall_iterations=15,
        time_budget_s=None,
    ):
        self.space = space
        self.evaluate_fn = evaluate_fn
        self.n_particles = max(4, int(n_particles))
        if self.n_particles % 2:
            self.n_particles += 1
        self.n_iterations = int(n_iterations)
        self.crossover_rate = float(crossover_rate)
        self.eta_c = float(eta_c)
        self.eta_m = float(eta_m)
        self.mutation_rate = mutation_rate
        self.archive_capacity = int(archive_capacity)
        self.seed = int(seed)
        self.progress_fn = progress_fn
        self.stall_iterations = int(stall_iterations)
        self.time_budget_s = time_budget_s

    # ------------------------------------------------------------ operators

    def _tournament(self, pop, rng):
        """
        Binary tournament on the crowded-comparison operator.

        Lower rank wins; equal rank, the less crowded wins. This is what
        drives the population toward an even spread rather than a cluster.
        """
        a, b = rng.choice(pop), rng.choice(pop)
        if a.rank != b.rank:
            return a if a.rank < b.rank else b
        if a.crowding != b.crowding:
            return a if a.crowding > b.crowding else b
        return a if rng.random() < 0.5 else b

    def _sbx(self, p1, p2, rng):
        """Simulated binary crossover, rounded back onto the integer lattice."""
        c1, c2 = list(p1), list(p2)
        if rng.random() > self.crossover_rate:
            return c1, c2

        for d in range(self.space.dimensions):
            lo, hi = self.space.bounds[d]
            if hi <= lo or rng.random() > 0.5:
                continue
            x1, x2 = float(p1[d]), float(p2[d])
            if abs(x1 - x2) < 1e-9:
                continue
            if x1 > x2:
                x1, x2 = x2, x1

            u = rng.random()
            # The bounded form: beta is derived from how much room each
            # parent has inside the box, so children never need clamping
            # back from far outside it - clamping is what turns SBX into a
            # bound-seeking operator and piles the population onto the walls.
            beta = 1.0 + (2.0 * (x1 - lo) / (x2 - x1))
            alpha = 2.0 - beta ** (-(self.eta_c + 1.0))
            betaq = (
                (u * alpha) ** (1.0 / (self.eta_c + 1.0))
                if u <= 1.0 / alpha
                else (1.0 / (2.0 - u * alpha)) ** (1.0 / (self.eta_c + 1.0))
            )
            y1 = 0.5 * ((x1 + x2) - betaq * (x2 - x1))

            beta = 1.0 + (2.0 * (hi - x2) / (x2 - x1))
            alpha = 2.0 - beta ** (-(self.eta_c + 1.0))
            betaq = (
                (u * alpha) ** (1.0 / (self.eta_c + 1.0))
                if u <= 1.0 / alpha
                else (1.0 / (2.0 - u * alpha)) ** (1.0 / (self.eta_c + 1.0))
            )
            y2 = 0.5 * ((x1 + x2) + betaq * (x2 - x1))

            if rng.random() < 0.5:
                y1, y2 = y2, y1
            c1[d] = min(hi, max(lo, int(round(y1))))
            c2[d] = min(hi, max(lo, int(round(y2))))
        return c1, c2

    def _mutate(self, x, rng):
        """Polynomial mutation, rounded, with a guaranteed step of one unit."""
        active = self.space.active_dimensions
        if not active:
            return list(x)
        rate = (
            float(self.mutation_rate)
            if self.mutation_rate is not None
            else 1.0 / len(active)
        )

        out = list(x)
        for d in active:
            if rng.random() > rate:
                continue
            lo, hi = self.space.bounds[d]
            span = float(hi - lo)
            y = float(out[d])
            d1 = (y - lo) / span
            d2 = (hi - y) / span
            u = rng.random()
            mut_pow = 1.0 / (self.eta_m + 1.0)
            if u <= 0.5:
                xy = 1.0 - d1
                val = 2.0 * u + (1.0 - 2.0 * u) * (xy ** (self.eta_m + 1.0))
                delta = val ** mut_pow - 1.0
            else:
                xy = 1.0 - d2
                val = (
                    2.0 * (1.0 - u)
                    + 2.0 * (u - 0.5) * (xy ** (self.eta_m + 1.0))
                )
                delta = 1.0 - val ** mut_pow
            new = int(round(y + delta * span))
            # Polynomial mutation makes small steps by design; on an integer
            # lattice most of them round back to where they started, and a
            # mutation that changes nothing is a wasted draw. Force at least
            # one unit of movement in the direction the operator chose.
            if new == out[d]:
                new = out[d] + (1 if delta >= 0 else -1)
            out[d] = min(hi, max(lo, new))
        return out

    # ----------------------------------------------------------------- run

    def run(self, seed_points=None):
        rng = random.Random(self.seed)
        ev = base_mod.Evaluator(self.evaluate_fn)
        watch = base_mod.StopWatch(self.time_budget_s, self.stall_iterations)
        result = base_mod.SearchResult("nsga2")
        archive = base_mod.empty_archive(self.archive_capacity)

        pop = [
            ev(x)
            for x in base_mod.seeded_population(
                self.space, rng, self.n_particles, seed_points
            )
        ]
        self._assign(pop)
        archive.extend(pop)

        for it in range(self.n_iterations):
            # ------------------------------------------------- offspring
            children = []
            while len(children) < self.n_particles:
                p1 = self._tournament(pop, rng)
                p2 = self._tournament(pop, rng)
                c1, c2 = self._sbx(p1.x, p2.x, rng)
                children.append(ev(self._mutate(c1, rng)))
                children.append(ev(self._mutate(c2, rng)))
            children = children[: self.n_particles]

            for c in children:
                archive.add(c)

            # ------------------------------------- elitist survival (mu+lambda)
            # Parents and children compete together, so the best design found
            # in any generation survives to the next one by construction.
            pop = self._survivors(pop + children)

            size = len(archive)
            result.history.append(
                {
                    "iteration": it,
                    "front_size": size,
                    "evaluations": ev.evaluations,
                    "best": [
                        archive.best_by(m).objectives[m]
                        if archive.members else None
                        for m in range(len(pop[0].objectives))
                    ],
                }
            )
            if self.progress_fn:
                self.progress_fn(it, self.n_iterations, size, ev.evaluations)

            watch.tick(size)
            why = watch.stop_reason(it)
            if why:
                result.converged = "stopped growing" in why
                result.notes.append(why)
                break

        result.iterations = len(result.history)
        return base_mod.finish(result, archive, ev, watch, self.space)

    # ------------------------------------------------------------ helpers

    def _assign(self, pop):
        """Rank and crowding for a population, in place."""
        for front in non_dominated_sort(pop):
            crowding_distance(front)

    def _survivors(self, merged):
        """
        Keep the best `n_particles` by front, filling the last one by
        crowding distance so the extremes of the front survive.

        Duplicate decision vectors are collapsed first. The cache returns
        the *same* Solution object for a repeated point, so without this a
        population can fill with one design wearing many hats and the
        crowding distances stop meaning anything.
        """
        seen = set()
        pool = []
        for s in merged:
            k = s.key()
            if k in seen:
                continue
            seen.add(k)
            pool.append(s)

        fronts = non_dominated_sort(pool)
        out = []
        for front in fronts:
            crowding_distance(front)
            if len(out) + len(front) <= self.n_particles:
                out.extend(front)
                continue
            room = self.n_particles - len(out)
            if room > 0:
                out.extend(
                    sorted(front, key=lambda s: s.crowding, reverse=True)[:room]
                )
            break

        # Fewer unique designs than the population size only happens on a
        # tiny or exhausted space; pad by repetition so the loop still has
        # pairs to mate.
        while len(out) < self.n_particles and pool:
            out.append(pool[len(out) % len(pool)])
        return out
