"""
Multi-objective particle swarm optimisation over integer unit counts.

The velocity and position update is the one from the original MATLAB
`nextgen.m`:

    V(t+1) = w*V(t) + c1*r1*(Pbest - X) + c2*r2*(Gbest - X)
    X(t+1) = round(X(t) + V(t+1))

with three changes made for the multi-objective case:

  * `Gbest` becomes a leader drawn from the external archive rather than a
    single global best, because with multiple objectives there is no single
    best. The leader is chosen by crowding tournament, which pushes
    particles toward under-explored parts of the front.

  * `Pbest` is only replaced when the new position dominates the old one.
    With a scalar objective "better" is unambiguous; here, replacing on any
    change would let a particle drift along the front without improving.

  * Inertia weight decays linearly from `w_max` to `w_min` across the run.
    A fixed high inertia explores well but never settles; a fixed low one
    converges onto whatever it found first. The decay is the standard fix
    and matters more here than in the single-objective case because the
    archive keeps the early exploration anyway.

Velocity clamping is applied per dimension at a fraction of that
dimension's span. Without it, `round()` on an unbounded velocity makes
particles teleport across the whole space and the swarm never converges -
a failure that is easy to miss because the archive still fills with
scattered points and the front looks superficially plausible.
"""

from __future__ import annotations

import random
import time

from .base import SearchResult
from .pareto import Archive, Solution, dominates, crowding_distance


class MOPSOResult(SearchResult):
    """
    Outcome of an optimisation run.

    The shape is shared with every other algorithm (see `optim.base`), so
    the study driver and the report never learn which one produced it. The
    name is kept because it is what the tests and the MATLAB port notes
    call it.
    """

    def __init__(self):
        super().__init__("mopso")


class MOPSO:
    """
    Multi-objective PSO with an external archive.

    Parameters
    ----------
    space : a SearchSpace giving integer bounds per dimension
    evaluate_fn : callable(x) -> (objectives, metrics, feasible, violation)
    n_particles : swarm size
    n_iterations : generations
    w_max, w_min : inertia weight at the start and end of the run
    c1, c2 : cognitive and social acceleration coefficients
    mutation_rate : per-particle probability of a random restart in one
        dimension, which keeps the swarm from stalling in a local basin
    """

    def __init__(
        self,
        space,
        evaluate_fn,
        n_particles=30,
        n_iterations=60,
        w_max=0.9,
        w_min=0.4,
        c1=1.5,
        c2=1.5,
        archive_capacity=150,
        velocity_clamp_fraction=0.25,
        mutation_rate=0.10,
        seed=1234,
        progress_fn=None,
        stall_iterations=15,
        time_budget_s=None,
    ):
        self.space = space
        self.evaluate_fn = evaluate_fn
        self.n_particles = int(n_particles)
        self.n_iterations = int(n_iterations)
        self.w_max = float(w_max)
        self.w_min = float(w_min)
        self.c1 = float(c1)
        self.c2 = float(c2)
        self.archive_capacity = int(archive_capacity)
        self.velocity_clamp_fraction = float(velocity_clamp_fraction)
        self.mutation_rate = float(mutation_rate)
        self.seed = int(seed)
        self.progress_fn = progress_fn
        self.stall_iterations = int(stall_iterations)
        self.time_budget_s = time_budget_s

        if self.n_particles < 2:
            raise ValueError("swarm needs at least 2 particles")

        self._cache = {}
        self._evaluations = 0
        self._cache_hits = 0

    # ------------------------------------------------------------ evaluate

    def _evaluate(self, x):
        """Evaluate with memoisation on the integer decision vector."""
        key = tuple(x)
        if key in self._cache:
            self._cache_hits += 1
            return self._cache[key]
        objectives, metrics, feasible, violation = self.evaluate_fn(list(x))
        sol = Solution(list(x), objectives, metrics, feasible, violation)
        self._cache[key] = sol
        self._evaluations += 1
        return sol

    # ----------------------------------------------------------------- run

    def run(self, seed_points=None):
        """
        Execute the search.

        `seed_points` is an optional list of decision vectors used to place
        part of the initial swarm - this is where the screening stage (or an
        MILP relaxation, when a solver is available) feeds in.
        """
        rng = random.Random(self.seed)
        t_start = time.perf_counter()
        result = MOPSOResult()
        archive = Archive(self.archive_capacity)

        clamps = [
            max(1.0, self.velocity_clamp_fraction * self.space.span(i))
            for i in range(self.space.dimensions)
        ]

        # ------------------------------------------------ initialise swarm
        positions = []
        seeds = list(seed_points or [])
        for i in range(self.n_particles):
            if i < len(seeds):
                positions.append(self.space.clamp(seeds[i]))
            else:
                positions.append(self.space.random_point(rng))

        velocities = [
            [rng.uniform(-clamps[d], clamps[d]) for d in range(self.space.dimensions)]
            for _ in range(self.n_particles)
        ]

        current = [self._evaluate(p) for p in positions]
        personal_best = list(current)
        archive.extend(current)

        best_front_size = len(archive)
        stall = 0

        # ------------------------------------------------------- main loop
        for it in range(self.n_iterations):
            if self.n_iterations > 1:
                w = self.w_max - (self.w_max - self.w_min) * it / (
                    self.n_iterations - 1
                )
            else:
                w = self.w_min

            for i in range(self.n_particles):
                leader = archive.leader(rng)
                gbest = leader.x if leader else personal_best[i].x
                pbest = personal_best[i].x

                new_v = []
                new_x = []
                for d in range(self.space.dimensions):
                    lo, hi = self.space.bounds[d]
                    if hi == lo:
                        new_v.append(0.0)
                        new_x.append(lo)
                        continue

                    v = (
                        w * velocities[i][d]
                        + self.c1 * rng.random() * (pbest[d] - positions[i][d])
                        + self.c2 * rng.random() * (gbest[d] - positions[i][d])
                    )
                    if v > clamps[d]:
                        v = clamps[d]
                    elif v < -clamps[d]:
                        v = -clamps[d]
                    new_v.append(v)
                    new_x.append(int(round(positions[i][d] + v)))

                # Turbulence: a small chance of resampling one dimension.
                if rng.random() < self.mutation_rate:
                    d = rng.randrange(self.space.dimensions)
                    lo, hi = self.space.bounds[d]
                    if hi > lo:
                        new_x[d] = rng.randint(lo, hi)

                new_x = self.space.clamp(new_x)

                # A particle driven into a wall keeps hitting it unless its
                # velocity is reflected, which wastes most of the swarm on
                # long runs. Reflect on contact.
                for d in range(self.space.dimensions):
                    lo, hi = self.space.bounds[d]
                    if new_x[d] in (lo, hi) and hi > lo:
                        new_v[d] = -0.5 * new_v[d]

                positions[i] = new_x
                velocities[i] = new_v

                sol = self._evaluate(new_x)
                current[i] = sol
                archive.add(sol)

                if dominates(sol, personal_best[i]):
                    personal_best[i] = sol
                elif not dominates(personal_best[i], sol) and rng.random() < 0.5:
                    # Mutually non-dominated: switch half the time so the
                    # particle does not anchor to a stale memory.
                    personal_best[i] = sol

            # ------------------------------------------------ bookkeeping
            size = len(archive)
            result.history.append(
                {
                    "iteration": it,
                    "front_size": size,
                    "evaluations": self._evaluations,
                    "inertia": w,
                    "best": [
                        archive.best_by(m).objectives[m]
                        if archive.members else None
                        for m in range(len(current[0].objectives))
                    ],
                }
            )

            if self.progress_fn:
                self.progress_fn(it, self.n_iterations, size, self._evaluations)

            if size > best_front_size:
                best_front_size = size
                stall = 0
            else:
                stall += 1

            if self.stall_iterations and stall >= self.stall_iterations:
                result.converged = True
                result.notes.append(
                    f"Stopped at iteration {it + 1}: the Pareto front stopped "
                    f"growing for {stall} consecutive iterations."
                )
                break

            if self.time_budget_s:
                if time.perf_counter() - t_start > self.time_budget_s:
                    result.notes.append(
                        f"Stopped at iteration {it + 1}: time budget of "
                        f"{self.time_budget_s:.0f} s reached."
                    )
                    break

        crowding_distance(archive.members)
        result.archive = archive
        result.evaluations = self._evaluations
        result.cache_hits = self._cache_hits
        result.iterations = len(result.history)
        result.elapsed_s = time.perf_counter() - t_start

        explored = len(self._cache)
        total = self.space.size()
        if total and explored >= total:
            result.exact = True
            result.notes.append(
                "The search space was explored exhaustively; the reported "
                "front is exact, not an approximation."
            )
        return result


def single_objective(space, evaluate_fn, objective_index=0, **kwargs):
    """
    Convenience wrapper that runs the same swarm but reports the single best
    solution on one objective - the closest equivalent to the original
    MATLAB behaviour, useful for validating the port.
    """
    opt = MOPSO(space, evaluate_fn, **kwargs)
    result = opt.run()
    if not result.archive or not len(result.archive):
        return None, result
    best = result.archive.best_by(objective_index)
    return best, result
