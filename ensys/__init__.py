"""
HES - hybrid renewable energy system sizing and design.

A dependency-light engine for sizing PV, wind, battery, EV/V2G, genset and
grid-connected hybrid systems from geographic resource data, with
multi-objective optimisation, power-component sizing and single/three-line
diagram generation.

Design constraints (deliberate):
  * Standard library only. No numpy, scipy or pandas in the core engine.
    This keeps the whole engine runnable client-side under Pyodide with no
    install step, exactly like the Earthing_System project.
  * Optional accelerators (numpy, PuLP/HiGHS) are detected at runtime and
    used when present, but nothing depends on them.
  * Every number that reaches a report carries its unit and its provenance.
"""

__version__ = "0.1.0"
__author__ = "Emad Roshandel"

HOURS_PER_YEAR = 8760

# Runtime capability detection. Never import these at module scope elsewhere;
# always go through these flags so the pure-Python path stays authoritative.
try:  # pragma: no cover - environment dependent
    import numpy as _np  # noqa: F401

    HAS_NUMPY = True
except Exception:  # pragma: no cover
    HAS_NUMPY = False

try:  # pragma: no cover - environment dependent
    import pulp as _pulp  # noqa: F401

    HAS_MILP = True
except Exception:  # pragma: no cover
    HAS_MILP = False


def capabilities():
    """Report which optional accelerators are available on this machine."""
    return {
        "version": __version__,
        "numpy": HAS_NUMPY,
        "milp": HAS_MILP,
        "engine": "pure-python",
    }
