"""
The catalogue of search algorithms, and the one place that names them.

Adding an algorithm means adding an entry here and nothing else: the study
looks it up by key, the API serves this list to the interface, and the
interface builds its menu from what it is served. Nothing downstream has a
hard-coded list of algorithm names, so nothing downstream has to be edited
to gain one.

The descriptions are written for the person choosing, not for the person
maintaining. Someone sizing a microgrid should be able to tell from the
menu which one suits their problem without knowing what a distribution
index is.
"""

from __future__ import annotations

from . import exhaustive as exhaustive_mod
from . import mopso as mopso_mod
from . import nsga2 as nsga2_mod

DEFAULT = "mopso"

_ALGORITHMS = [
    {
        "key": "mopso",
        "label": "Particle swarm (MOPSO)",
        "family": "Swarm intelligence",
        "summary": "Fast to a good compromise design. The default.",
        "detail": (
            "Multi-objective particle swarm optimisation with an external "
            "archive. Candidate designs move through the search space "
            "attracted to their own best result and to a leader drawn from "
            "the current front. It usually reaches the knee of the trade-off "
            "curve in the fewest evaluations, which makes it the right "
            "choice when a run has to finish while you wait."
        ),
        "exact": False,
        "stochastic": True,
        "class": mopso_mod.MOPSO,
    },
    {
        "key": "nsga2",
        "label": "Genetic algorithm (NSGA-II)",
        "family": "Evolutionary",
        "summary": "Evener spread across the whole trade-off curve.",
        "detail": (
            "The elitist non-dominated sorting genetic algorithm of Deb et "
            "al. (2002), the reference method of multi-objective "
            "optimisation. Designs are recombined and mutated, then parents "
            "and children compete together so nothing good is ever lost. It "
            "typically finds the extremes of the front - the cheapest and "
            "the greenest designs - more reliably than the swarm, at the "
            "cost of converging on the middle a little more slowly. Running "
            "it as a second opinion is the cheapest way to check that a "
            "front has actually converged."
        ),
        "exact": False,
        "stochastic": True,
        "class": nsga2_mod.NSGA2,
    },
    {
        "key": "grid",
        "label": "Exhaustive search",
        "family": "Enumeration",
        "summary": "Every combination, exactly. Only for small studies.",
        "detail": (
            "Simulates every combination of unit counts in the search "
            "space. No heuristic and no random seed: the front it returns "
            "is the true front, and re-running it changes nothing. Use it "
            "for small problems, and to check the other two. If the space "
            "is larger than the evaluation budget it falls back to a "
            "uniform coarse grid over the whole box and says so in the "
            "run notes - at that point it is a survey, not a proof."
        ),
        "exact": True,
        "stochastic": False,
        "class": exhaustive_mod.GridSearch,
    },
]

_BY_KEY = {a["key"]: a for a in _ALGORITHMS}


def catalogue():
    """The list the interface builds its menu from (no classes in it)."""
    return [
        {k: v for k, v in a.items() if k != "class"} for a in _ALGORITHMS
    ]


def keys():
    return [a["key"] for a in _ALGORITHMS]


def get(name):
    """
    Look up an algorithm entry by key.

    An unknown key raises rather than silently falling back to the default:
    a study that ran a different algorithm from the one the report names is
    worse than a study that refused to run.
    """
    key = (name or DEFAULT)
    if key not in _BY_KEY:
        raise ValueError(
            f"Unknown optimisation algorithm {name!r}. "
            f"Available: {', '.join(keys())}."
        )
    return _BY_KEY[key]


def build(name, space, evaluate_fn, **kwargs):
    """Instantiate an algorithm. Unsupported keywords are dropped."""
    entry = get(name)
    cls = entry["class"]
    return cls(space, evaluate_fn, **_accepted(cls, kwargs))


def _accepted(cls, kwargs):
    """
    Keep only the keywords this algorithm's constructor understands.

    The study passes one parameter set to whichever algorithm is chosen.
    They share most of it - population size, iterations, seed, progress,
    time budget - but not all: `prefilter` means nothing to a swarm, and
    `mutation_rate` means nothing to an enumeration. Filtering here keeps
    the study from having to know which is which.
    """
    try:
        code = cls.__init__.__code__
        names = set(code.co_varnames[: code.co_argcount])
        # A constructor that takes **kwargs has already said it will cope.
        if code.co_flags & 0x08:
            return dict(kwargs)
    except AttributeError:            # pragma: no cover - not a Python class
        return dict(kwargs)
    return {k: v for k, v in kwargs.items() if k in names}
