"""
System configuration: which technologies are available, and how many of each.

A SystemConfig is what the optimiser searches over. Component objects
describe ONE unit and stay fixed; the integer unit counts are the decision
variables.

Sizing by unit count rather than by continuous capacity is deliberate. Real
procurement is discrete — you buy three turbines, not 5.7 MW of turbine — and
a continuous relaxation produces answers that cannot be built and that
quietly overstate performance, because half a turbine has no cut-in speed.
Where finer resolution is wanted, the unit is made smaller (a 1 kWp PV block
rather than a 25 kWp one) rather than the variable made continuous.

The constructor accepts both the original named form
(`SystemConfig(pv=..., n_pv=...)`) and an arbitrary asset list, so existing
studies keep working while new technologies plug in without a signature
change.
"""

from __future__ import annotations

from .assets import (
    AssetRegistry, Dispatchable, FlexibleLoad, NonDispatchable, Storage,
)


class SystemConfig:
    """
    A candidate system design.

    Parameters
    ----------
    assets : list of (key, asset) pairs or bare assets, in decision-vector
        order
    counts : dict of key -> unit count
    grid : the GridConnection, which is not sized by the optimiser
    location : used by technologies whose output depends on the site
    """

    def __init__(self, name="System", assets=None, counts=None, grid=None,
                 location=None, converters=None,
                 # ---- original named form, retained
                 pv=None, n_pv=0, wind=None, n_wind=0,
                 battery=None, n_battery=0, genset=None, n_genset=0,
                 ev=None, n_chargers=0):
        self.name = name
        self.grid = grid
        self.location = location
        self.converters = converters or {}
        self.registry = AssetRegistry()
        self.counts = {}

        # Named components first, so their decision-vector positions stay
        # stable across versions: pv, wind, battery, genset, ev.
        for asset, count, key in (
            (pv, n_pv, "pv"),
            (wind, n_wind, "wind"),
            (battery, n_battery, "battery"),
            (genset, n_genset, "genset"),
            (ev, n_chargers, "ev_fleet"),
        ):
            if asset is not None:
                k = self.registry.add(asset, key)
                self.counts[k] = int(count)

        for entry in (assets or []):
            if isinstance(entry, (tuple, list)):
                key, asset = entry
                k = self.registry.add(asset, key)
            else:
                k = self.registry.add(entry)
            self.counts[k] = 0

        for k, v in (counts or {}).items():
            if k not in self.counts:
                raise KeyError(
                    f"count given for unknown asset '{k}'. Assets present: "
                    f"{', '.join(self.registry.keys()) or 'none'}"
                )
            self.counts[k] = int(v)

        for k, v in self.counts.items():
            if v < 0:
                raise ValueError(f"unit count for '{k}' cannot be negative: {v}")

    # ------------------------------------------------------------- access

    def asset(self, key):
        return self.registry.get(key)

    def count(self, key):
        return self.counts.get(key, 0)

    def installed_kw(self, key):
        a = self.registry.get(key)
        return a.rated_power_kw() * self.counts.get(key, 0) if a else 0.0

    def installed_kwh(self, key):
        a = self.registry.get(key)
        return a.rated_energy_kwh() * self.counts.get(key, 0) if a else 0.0

    # Legacy properties, kept so existing sizing and report code is unchanged.
    @property
    def pv(self):
        return self.registry.get("pv")

    @property
    def wind(self):
        return self.registry.get("wind")

    @property
    def battery(self):
        return self.registry.get("battery")

    @property
    def genset(self):
        return self.registry.get("genset")

    @property
    def ev(self):
        return self.registry.get("ev_fleet")

    @property
    def n_pv(self):
        return self.counts.get("pv", 0)

    @property
    def n_wind(self):
        return self.counts.get("wind", 0)

    @property
    def n_battery(self):
        return self.counts.get("battery", 0)

    @property
    def n_genset(self):
        return self.counts.get("genset", 0)

    @property
    def n_chargers(self):
        return self.counts.get("ev_fleet", 0)

    @property
    def pv_capacity_kwp(self):
        return self.installed_kw("pv")

    @property
    def wind_capacity_kw(self):
        return self.installed_kw("wind")

    @property
    def battery_capacity_kwh(self):
        return self.installed_kwh("battery")

    @property
    def battery_power_kw(self):
        return self.installed_kw("battery")

    @property
    def genset_capacity_kw(self):
        return self.installed_kw("genset")

    @property
    def generation_capacity_kw(self):
        """Installed generation, excluding storage and the grid."""
        total = 0.0
        for key, asset in self.registry:
            if isinstance(asset, (NonDispatchable, Dispatchable)):
                total += asset.rated_power_kw() * self.counts.get(key, 0)
        return total

    @property
    def storage_capacity_kwh(self):
        total = 0.0
        for key, asset in self.registry:
            if isinstance(asset, Storage):
                total += asset.rated_energy_kwh() * self.counts.get(key, 0)
        return total

    @property
    def is_empty(self):
        """A design with no generation and no grid cannot serve anything."""
        return (
            self.generation_capacity_kw <= 0
            and (self.grid is None or self.grid.is_islanded)
        )

    # ---------------------------------------------------- decision vector

    def decision_keys(self):
        """Asset keys in decision-vector order."""
        return [k for k, _ in self.registry.sizable()]

    def decision_vector(self):
        return [self.counts.get(k, 0) for k in self.decision_keys()]

    def with_decision(self, x):
        """Return a copy with new unit counts, sharing the asset objects."""
        keys = self.decision_keys()
        new = SystemConfig.__new__(SystemConfig)
        new.name = self.name
        new.grid = self.grid
        new.location = self.location
        new.converters = self.converters
        new.registry = self.registry
        new.counts = dict(self.counts)
        for k, v in zip(keys, x):
            new.counts[k] = int(v)
        return new

    # ------------------------------------------------------------ summary

    def summary(self):
        out = {
            "name": self.name,
            "generation_capacity_kw": self.generation_capacity_kw,
            "storage_capacity_kwh": self.storage_capacity_kwh,
            "grid_import_limit_kw": self.grid.import_limit_kw if self.grid else 0.0,
            "grid_export_limit_kw": self.grid.export_limit_kw if self.grid else 0.0,
            "assets": {},
            # Legacy keys the report and diagram already read.
            "n_pv": self.n_pv,
            "pv_capacity_kwp": self.pv_capacity_kwp,
            "n_wind": self.n_wind,
            "wind_capacity_kw": self.wind_capacity_kw,
            "wind_model": self.wind.name if self.wind else None,
            "n_battery": self.n_battery,
            "battery_capacity_kwh": self.battery_capacity_kwh,
            "battery_power_kw": self.battery_power_kw,
            "battery_chemistry": (
                getattr(self.battery, "chemistry", None) if self.battery else None
            ),
            "n_genset": self.n_genset,
            "genset_capacity_kw": self.genset_capacity_kw,
            "n_chargers": self.n_chargers,
            "ev_scenario": (
                getattr(self.ev, "scenario", None) if self.ev else None
            ),
        }
        for key, asset in self.registry:
            c = self.counts.get(key, 0)
            if c <= 0 and asset.sizable:
                continue
            out["assets"][key] = {
                "technology": asset.technology,
                "name": asset.name,
                "units": c,
                "rated_kw": asset.rated_power_kw() * max(c, 1),
                "rated_kwh": asset.rated_energy_kwh() * max(c, 1),
                "description": asset.describe(c),
            }
        return out

    def __repr__(self):
        parts = []
        for key, asset in self.registry:
            c = self.counts.get(key, 0)
            if c > 0:
                parts.append(asset.describe(c))
        if self.grid and not self.grid.is_islanded:
            parts.append(f"Grid {self.grid.import_limit_kw:.0f} kW")
        return f"SystemConfig({'; '.join(parts) or 'empty'})"


class SearchSpace:
    """
    Bounds on the decision variables.

    Ports the role of `xi.m`, `limmin.m` and `limmax.m` from the original
    MATLAB — initial placement inside the box, and the two bound tests — and
    generalises them from four fixed dimensions to however many technologies
    the study includes.
    """

    def __init__(self, bounds=None, keys=None, **named):
        """
        `bounds` is a list of (lo, hi) pairs in decision-vector order.
        The original keyword form (n_pv=(0,10), n_wind=(0,5), ...) is still
        accepted.
        """
        if bounds is not None:
            self.bounds = [tuple(int(v) for v in b) for b in bounds]
            self.keys = list(keys) if keys else [
                f"x{i}" for i in range(len(self.bounds))
            ]
        else:
            order = ["n_pv", "n_wind", "n_battery", "n_genset"]
            extra = [k for k in named if k not in order]
            order += extra
            self.bounds = []
            self.keys = []
            for k in order:
                if k in named:
                    lo, hi = named[k]
                    self.bounds.append((int(lo), int(hi)))
                    self.keys.append(k.replace("n_", "", 1))
            if not self.bounds:
                raise ValueError("a search space needs at least one dimension")

        for i, (lo, hi) in enumerate(self.bounds):
            if lo > hi:
                raise ValueError(
                    f"search dimension {i} ({self.keys[i]}): lower bound {lo} "
                    f"exceeds upper bound {hi}"
                )

    @property
    def dimensions(self):
        return len(self.bounds)

    @property
    def active_dimensions(self):
        return [i for i, (lo, hi) in enumerate(self.bounds) if hi > lo]

    def size(self):
        total = 1
        for lo, hi in self.bounds:
            total *= (hi - lo + 1)
        return total

    def random_point(self, rng):
        """Port of xi.m: a random integer point inside the box."""
        return [rng.randint(lo, hi) for lo, hi in self.bounds]

    def clamp(self, x):
        """Port of limmin/limmax applied as a repair operator."""
        out = []
        for v, (lo, hi) in zip(x, self.bounds):
            v = int(round(v))
            out.append(lo if v < lo else (hi if v > hi else v))
        return out

    def violates(self, x):
        return any(v < lo or v > hi for v, (lo, hi) in zip(x, self.bounds))

    def span(self, i):
        lo, hi = self.bounds[i]
        return hi - lo

    def label(self, i):
        return self.keys[i] if i < len(self.keys) else f"x{i}"
