"""
Pareto machinery: dominance, non-dominated sorting, crowding and selection.

Every objective vector reaching this module is already a minimisation
target - `metrics.objective_vector` performs the sign flip for objectives
that are naturally maximised. Nothing here re-checks that, so the invariant
matters.

Constraint handling uses Deb's feasibility rules rather than a penalty
weight, because a penalty weight has to be tuned per problem and silently
changes which solutions win when the scale of an objective changes:

  1. a feasible solution always beats an infeasible one
  2. between two feasible solutions, Pareto dominance decides
  3. between two infeasible solutions, the smaller total violation wins

That keeps the search informed inside an infeasible region without letting
infeasible points contaminate the reported front.
"""

from __future__ import annotations

import math


class Solution:
    """One evaluated design: decision vector, objectives, feasibility."""

    __slots__ = ("x", "objectives", "metrics", "feasible", "violation",
                 "rank", "crowding", "extra")

    def __init__(self, x, objectives, metrics=None, feasible=True,
                 violation=0.0, extra=None):
        self.x = list(x)
        self.objectives = list(objectives)
        self.metrics = metrics or {}
        self.feasible = bool(feasible)
        self.violation = float(violation)
        self.rank = None
        self.crowding = 0.0
        self.extra = extra or {}

    def key(self):
        """Hashable identity, used to deduplicate the archive."""
        return tuple(self.x)

    def __repr__(self):
        obj = ", ".join(f"{o:.4g}" for o in self.objectives)
        flag = "" if self.feasible else f" INFEASIBLE({self.violation:.3g})"
        return f"Solution({self.x} -> [{obj}]{flag})"


def dominates(a, b):
    """
    True when solution `a` dominates `b` under Deb's constrained rules.
    """
    if a.feasible and not b.feasible:
        return True
    if b.feasible and not a.feasible:
        return False
    if not a.feasible and not b.feasible:
        return a.violation < b.violation - 1e-12

    better_anywhere = False
    for oa, ob in zip(a.objectives, b.objectives):
        if oa > ob + 1e-12:
            return False
        if oa < ob - 1e-12:
            better_anywhere = True
    return better_anywhere


def non_dominated_sort(population):
    """
    Fast non-dominated sort (Deb et al., NSGA-II).

    Assigns `rank` in place and returns the list of fronts, each a list of
    solutions. Rank 0 is the Pareto front.

    Complexity is O(M N^2) in the number of objectives M and population N.
    For the population sizes this tool uses (tens to low hundreds) that is
    entirely adequate and much simpler than the O(N log^(M-1) N) alternatives.
    """
    n = len(population)
    dominated_by = [[] for _ in range(n)]
    domination_count = [0] * n
    fronts = [[]]

    for i in range(n):
        for j in range(i + 1, n):
            if dominates(population[i], population[j]):
                dominated_by[i].append(j)
                domination_count[j] += 1
            elif dominates(population[j], population[i]):
                dominated_by[j].append(i)
                domination_count[i] += 1

    for i in range(n):
        if domination_count[i] == 0:
            population[i].rank = 0
            fronts[0].append(population[i])

    current = 0
    indices = {id(s): k for k, s in enumerate(population)}
    while fronts[current]:
        nxt = []
        for sol in fronts[current]:
            i = indices[id(sol)]
            for j in dominated_by[i]:
                domination_count[j] -= 1
                if domination_count[j] == 0:
                    population[j].rank = current + 1
                    nxt.append(population[j])
        current += 1
        fronts.append(nxt)

    return [f for f in fronts if f]


def crowding_distance(front):
    """
    Assign crowding distance within one front, in place.

    Boundary solutions get infinite distance so the extremes of the front
    are never pruned - losing them collapses the range of the reported
    trade-off, which is the one thing a Pareto study exists to show.
    """
    n = len(front)
    if n == 0:
        return
    for s in front:
        s.crowding = 0.0
    if n <= 2:
        for s in front:
            s.crowding = float("inf")
        return

    n_obj = len(front[0].objectives)
    for m in range(n_obj):
        front.sort(key=lambda s: s.objectives[m])
        lo = front[0].objectives[m]
        hi = front[-1].objectives[m]
        front[0].crowding = float("inf")
        front[-1].crowding = float("inf")
        span = hi - lo
        if span <= 1e-12 or math.isinf(span):
            continue
        for i in range(1, n - 1):
            if math.isinf(front[i].crowding):
                continue
            front[i].crowding += (
                front[i + 1].objectives[m] - front[i - 1].objectives[m]
            ) / span


def truncate_front(front, limit):
    """
    Reduce a front to `limit` members, keeping the most isolated ones.

    Used to bound the external archive. Removing one at a time and
    recomputing would be more faithful but is O(N^2); for archive sizes in
    the hundreds a single pass is a good trade.
    """
    if len(front) <= limit:
        return list(front)
    crowding_distance(front)
    ranked = sorted(front, key=lambda s: s.crowding, reverse=True)
    return ranked[:limit]


class Archive:
    """
    External archive of non-dominated solutions.

    Kept separate from the swarm so that a good design found early is never
    lost when the swarm moves on - the failure mode that makes plain
    multi-objective PSO produce sparse, patchy fronts.
    """

    def __init__(self, capacity=200):
        self.capacity = int(capacity)
        self.members = []
        self._seen = set()

    def add(self, solution):
        """
        Insert if non-dominated. Returns True when accepted.

        Duplicate decision vectors are rejected outright: two identical
        designs carry no extra information and crowd the archive.
        """
        k = solution.key()
        if k in self._seen:
            return False

        for existing in self.members:
            if dominates(existing, solution):
                return False

        survivors = [
            m for m in self.members if not dominates(solution, m)
        ]
        removed = [m for m in self.members if dominates(solution, m)]
        for m in removed:
            self._seen.discard(m.key())

        survivors.append(solution)
        self._seen.add(k)
        self.members = survivors

        if len(self.members) > self.capacity:
            kept = truncate_front(self.members, self.capacity)
            kept_keys = {m.key() for m in kept}
            self._seen = set(kept_keys)
            self.members = kept
        return True

    def extend(self, solutions):
        return sum(1 for s in solutions if self.add(s))

    def leader(self, rng):
        """
        Pick a guide for a particle, biased toward sparse regions.

        Binary tournament on crowding distance. Choosing purely at random
        gives an even but slowly converging search; choosing the least
        crowded every time collapses diversity. The tournament sits between.
        """
        if not self.members:
            return None
        if len(self.members) == 1:
            return self.members[0]
        crowding_distance(self.members)
        a, b = rng.sample(self.members, 2)
        return a if a.crowding >= b.crowding else b

    def best_by(self, index):
        """The archive member with the lowest value of one objective."""
        if not self.members:
            return None
        return min(self.members, key=lambda s: s.objectives[index])

    def knee_point(self):
        """
        The 'best compromise' solution.

        Objectives are normalised to [0, 1] across the front, then the member
        closest to the ideal point (all zeros) in Euclidean distance is
        returned. This is the standard knee heuristic and is what a client
        report should lead with, while still showing the whole front.
        """
        if not self.members:
            return None
        if len(self.members) == 1:
            return self.members[0]

        n_obj = len(self.members[0].objectives)
        los = [min(s.objectives[m] for s in self.members) for m in range(n_obj)]
        his = [max(s.objectives[m] for s in self.members) for m in range(n_obj)]

        best = None
        best_d = float("inf")
        for s in self.members:
            d = 0.0
            for m in range(n_obj):
                span = his[m] - los[m]
                if span <= 1e-12:
                    continue
                norm = (s.objectives[m] - los[m]) / span
                d += norm * norm
            if d < best_d:
                best_d = d
                best = s
        return best

    def feasible_members(self):
        return [m for m in self.members if m.feasible]

    def __len__(self):
        return len(self.members)

    def __iter__(self):
        return iter(self.members)


def hypervolume_2d(front, reference):
    """
    Exact 2-D hypervolume, the standard convergence indicator.

    Only the two-objective case is implemented: exact hypervolume in higher
    dimensions is expensive, and reporting an approximation without saying so
    would be worse than not reporting it. For three or more objectives use
    the front size and spacing instead.
    """
    pts = sorted(
        (
            (s.objectives[0], s.objectives[1])
            for s in front
            if s.objectives[0] <= reference[0] and s.objectives[1] <= reference[1]
        )
    )
    if not pts:
        return 0.0

    total = 0.0
    prev_y = reference[1]
    for x, y in pts:
        if y < prev_y:
            total += (reference[0] - x) * (prev_y - y)
            prev_y = y
    return total


def spacing(front):
    """
    Schott's spacing metric: the standard deviation of the distance from
    each member to its nearest neighbour. Lower is a more evenly spread
    front. Reported so a user can tell an even trade-off curve from one
    that clusters in a corner.
    """
    n = len(front)
    if n < 2:
        return 0.0
    dists = []
    for i in range(n):
        best = float("inf")
        for j in range(n):
            if i == j:
                continue
            d = sum(
                abs(a - b)
                for a, b in zip(front[i].objectives, front[j].objectives)
            )
            if d < best:
                best = d
        dists.append(best)
    mean = sum(dists) / n
    return math.sqrt(sum((d - mean) ** 2 for d in dists) / n)
