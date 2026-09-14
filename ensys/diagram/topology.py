"""
Drawing topology: which single-line diagram this design actually is.

A 5 kW rooftop system and a 5 MW plant are not the same drawing with
different numbers on it. They have different connection points, different
switchgear, different voltage levels and different things the network
operator will look for. Drawing one layout for every case produces a
diagram that is wrong for nearly all of them.

Nine classes are recognised, following the embedded-generator templates
issued by network operators:

  1  RESIDENTIAL_PV          single phase, PV only, up to about 20 kW
  2  RESIDENTIAL_PV_STORAGE  single phase, PV and storage, all loads backed
  3  RESIDENTIAL_SPLIT       single phase, storage backing essential loads only
  4  COMMERCIAL_3P           three phase LV, roughly 15 kVA to 1 MW
  5  UTILITY_MV              1 MW and above, LV/MV step-up onto an MV bus
  6  MV_CUSTOMER             existing MV customer, generation via ring main units
  7  OFFGRID                 no utility connection at all
  8  BACKUP_UPS              storage only, no generation: backup or UPS duty
  9  PV_WITH_GENSET          grid-tied generation with a standby generator

The class is inferred from the design, and can be overridden — a client may
want the three-phase drawing for a 15 kW system because that is how the
site is supplied.

Everything the drawing prints is computed here, so the renderer only places
things and never calculates. That keeps the drawing and the schedules
consistent by construction: both read the same numbers.
"""

from __future__ import annotations

import math

RESIDENTIAL_PV = "residential_pv"
RESIDENTIAL_PV_STORAGE = "residential_pv_storage"
RESIDENTIAL_SPLIT = "residential_split"
COMMERCIAL_3P = "commercial_3p"
UTILITY_MV = "utility_mv"
MV_CUSTOMER = "mv_customer"
OFFGRID = "offgrid"
BACKUP_UPS = "backup_ups"
PV_WITH_GENSET = "pv_with_genset"

TOPOLOGIES = {
    RESIDENTIAL_PV: {
        "label": "Grid-tied PV, no storage",
        "scope": "Single phase, up to 20 kW",
        "phases": 1, "mv": False,
    },
    RESIDENTIAL_PV_STORAGE: {
        "label": "Grid-tied hybrid PV with storage - all loads",
        "scope": "Single phase, up to 20 kW",
        "phases": 1, "mv": False,
    },
    RESIDENTIAL_SPLIT: {
        "label": "Grid-tied hybrid PV with storage - essential loads split",
        "scope": "Single phase, up to 20 kW",
        "phases": 1, "mv": False,
    },
    COMMERCIAL_3P: {
        "label": "Grid-tied three-phase embedded generator",
        "scope": "Three phase LV, 15 kVA to 1 MW",
        "phases": 3, "mv": False,
    },
    UTILITY_MV: {
        "label": "Embedded generator with LV/MV step-up",
        "scope": "Three phase, 1 MW to 20 MW",
        "phases": 3, "mv": True,
    },
    MV_CUSTOMER: {
        "label": "LV to MV embedded generation for an MV customer",
        "scope": "Three phase, MV connected via ring main units",
        "phases": 3, "mv": True,
    },
    OFFGRID: {
        "label": "Off-grid / stand-alone hybrid system",
        "scope": "No utility connection",
        "phases": 1, "mv": False,
    },
    BACKUP_UPS: {
        "label": "Back-up / UPS standby system",
        "scope": "Storage only, no generation",
        "phases": 1, "mv": False,
    },
    PV_WITH_GENSET: {
        "label": "Grid-tied PV with standby generator",
        "scope": "Generation with a change-over to a standby set",
        "phases": 1, "mv": False,
    },
}

# Standard LV/MV distribution voltages, for picking a sensible step-up.
MV_LEVELS = [3300, 6600, 11000, 20000, 22000, 33000]


class DrawingSpec:
    """Everything the renderer needs, already computed."""

    def __init__(self, topology, **kw):
        self.topology = topology
        self.meta = TOPOLOGIES[topology]
        self.__dict__.update(kw)

    def get(self, key, default=None):
        return self.__dict__.get(key, default)


def classify(design, system=None, override=None, phases=None,
             split_loads=False, mv_customer=False):
    """
    Choose the drawing class for a design.

    `phases` forces single- or three-phase; `split_loads` selects the
    essential/non-essential variant; `mv_customer` selects the ring-main
    drawing for a site already supplied at medium voltage.
    """
    if override in TOPOLOGIES:
        return override

    pv_kw = (design.get("pv") or {}).get("capacity_kwp", 0.0)
    wind_kw = (design.get("wind") or {}).get("capacity_kw", 0.0)
    bat_kwh = (design.get("battery") or {}).get("capacity_kwh", 0.0)
    gen_kw = (design.get("genset") or {}).get("capacity_kw", 0.0)
    grid = design.get("grid") or {}
    grid_kva = grid.get("rated_kva", 0.0)

    generation_kw = pv_kw + wind_kw
    for rec in (design.get("extra_assets") or {}).values():
        if rec.get("class") in ("non_dispatchable", "dispatchable"):
            generation_kw += rec.get("rated_kw", 0.0) or 0.0

    islanded = grid_kva <= 0

    if islanded:
        return OFFGRID
    if generation_kw <= 0 and bat_kwh > 0:
        return BACKUP_UPS
    if mv_customer:
        return MV_CUSTOMER

    # The threshold that matters is the connection rating, because that is
    # what decides the voltage level and the switchgear class, not the
    # generator nameplate.
    connection_kva = max(grid_kva, generation_kw / 0.95)

    if connection_kva >= 1000.0:
        return UTILITY_MV
    if phases == 3 or connection_kva > 20.0:
        return COMMERCIAL_3P
    if gen_kw > 0:
        return PV_WITH_GENSET
    if bat_kwh > 0:
        return RESIDENTIAL_SPLIT if split_loads else RESIDENTIAL_PV_STORAGE
    return RESIDENTIAL_PV


def build(design, system=None, topology=None, **kw):
    """
    Assemble the full drawing specification.

    Every rating that appears on the drawing is derived here, including the
    ones the sizing stage does not itself need: DC isolator voltage class,
    string fuse rating, battery isolation, CT ratio for export limiting,
    step-up transformer rating and vector group.
    """
    topo = topology or classify(design, system, **kw)
    meta = TOPOLOGIES[topo]
    phases = kw.get("phases") or meta["phases"]
    sys_v = design.get("system_voltage_v", 400 if phases == 3 else 230)
    pf = design.get("power_factor", 0.95)

    pv = design.get("pv") or {}
    wind = design.get("wind") or {}
    bat = design.get("battery") or {}
    gen = design.get("genset") or {}
    grid = design.get("grid") or {}
    load = design.get("load") or {}
    ev = design.get("ev") or {}

    spec = {
        "topology": topo,
        "phases": phases,
        "system_voltage_v": sys_v,
        "power_factor": pf,
        "frequency_hz": design.get("frequency_hz", 50),
        "earthing_system": design.get("earthing_system", "TN-S"),
        "fault_level_ka": design.get("fault_level_ka", 10.0),
        "is_mv": meta["mv"],
        "islanded": topo == OFFGRID,
        # A board fed through a change-over from a standby source carries
        # only what the standby source can support, so those topologies
        # split the load; a hybrid inverter big enough for the whole
        # installation does not.
        "split_loads": topo in (RESIDENTIAL_SPLIT, PV_WITH_GENSET),
        "has_changeover": topo in (
            RESIDENTIAL_PV_STORAGE, RESIDENTIAL_SPLIT, BACKUP_UPS,
            PV_WITH_GENSET, OFFGRID,
        ),
        "has_sub_db": topo in (
            RESIDENTIAL_PV_STORAGE, RESIDENTIAL_SPLIT, BACKUP_UPS,
            PV_WITH_GENSET,
        ),
        "warnings": [],
    }

    # ----------------------------------------------------- point of supply
    if not spec["islanded"]:
        imp = grid.get("peak_import_kw") or load.get("peak_kw", 0.0)
        kva = grid.get("rated_kva") or (imp / pf if pf else imp)
        current = (
            kva * 1000.0 / (math.sqrt(3) * sys_v) if phases == 3
            else kva * 1000.0 / sys_v
        )
        prot = grid.get("protection") or {}
        spec["supply"] = {
            "kva": kva,
            "current_a": current,
            "voltage_v": sys_v,
            "poles": 4 if phases == 3 else 2,
            "puc_rating_a": prot.get("rating_a"),
            "puc_device": prot.get("device", "mccb"),
            "meter": "Bidirectional import/export meter",
            "main_switch_a": _standard_switch(current),
            "export_limit_kw": grid.get("peak_export_kw", 0.0),
            "cable": grid.get("cable"),
        }
        # Export limiting needs a CT on the supply side; the drawing has to
        # show it and its ratio or the scheme cannot be commissioned.
        if grid.get("peak_export_kw", 0.0) > 0:
            spec["supply"]["ct_ratio"] = _ct_ratio(current)

    # ------------------------------------------------------------------ PV
    if pv.get("capacity_kwp", 0) > 0:
        inv = pv.get("inverter") or {}
        st = pv.get("strings") or {}
        dc_cable = pv.get("dc_cable") or {}
        ac_cable = pv.get("ac_cable") or {}
        prot = pv.get("ac_protection") or {}
        fuse = pv.get("string_fuse") or {}
        voc = st.get("string_voc_cold_v", 0.0)

        ac_kw = inv.get("total_ac_kw", 0.0)
        ac_current = (
            ac_kw * 1000.0 / (math.sqrt(3) * sys_v * pf) if phases == 3
            else ac_kw * 1000.0 / (sys_v * pf)
        )
        spec["pv"] = {
            "capacity_kwp": pv["capacity_kwp"],
            "tilt_deg": pv.get("tilt_deg"),
            "azimuth_deg": pv.get("azimuth_deg"),
            "strings": st.get("array_strings_total") or st.get("strings"),
            "modules_per_string": st.get("modules_per_string"),
            "modules_total": st.get("array_modules_total")
                             or st.get("modules_total"),
            "module_w": st.get("module_pmax_w"),
            "voc_cold_v": voc,
            "vmp_hot_v": st.get("string_vmp_hot_v"),
            "isc_a": st.get("string_isc_a"),
            "mppt_inputs": st.get("mppt_inputs"),
            "strings_per_mppt": st.get("strings_per_mppt"),
            "inverter_units": inv.get("units", 1),
            "inverter_kw_each": inv.get("rated_ac_kw", 0.0),
            "inverter_kw_total": ac_kw,
            "dc_ac_ratio": inv.get("dc_ac_ratio"),
            "inverter_ac_current_a": ac_current,
            "ac_protection": prot,
            "ac_cable": ac_cable,
            "dc_cable": dc_cable,
            # DC-side switchgear the templates require explicitly
            "dc_isolator": {
                "voltage_class_v": _dc_voltage_class(voc),
                "current_a": _standard_switch(
                    (st.get("string_isc_a", 10.0) or 10.0)
                    * (st.get("strings_per_mppt", 1) or 1) * 1.25
                ),
                "poles": 2,
            },
            "dc_fuse": {
                "required": fuse.get("required", False),
                "rating_a": fuse.get("rating_a"),
                "type": fuse.get("device", "gPV fuse (IEC 60269-6)"),
                "reason": fuse.get("reason"),
            },
            "dc_spd": pv.get("spd_dc") or {},
            "combiner_boxes": max(
                1, int(st.get("mppt_inputs", 1) or 1) // 6 + 1
            ) if st else 1,
        }
        if st and st.get("errors"):
            spec["warnings"] += st["errors"]

    # ---------------------------------------------------------------- wind
    if wind.get("capacity_kw", 0) > 0:
        conv = wind.get("converter") or {}
        spec["wind"] = {
            "capacity_kw": wind["capacity_kw"],
            "units": wind.get("units", 1),
            "unit_kw": wind.get("unit_kw", 0.0),
            "hub_height_m": wind.get("hub_height_m"),
            "converter_kw": conv.get("rated_kw", 0.0),
            "protection": wind.get("ac_protection") or {},
            "cable": wind.get("cable"),
        }

    # ------------------------------------------------------------- battery
    if bat.get("capacity_kwh", 0) > 0:
        pcs = bat.get("pcs") or {}
        # A battery can deliver thousands of amps into a short, so its
        # protection sits at the battery terminals and the drawing must
        # show it there rather than at the converter.
        dc_bus_v = _battery_dc_bus_v(bat.get("capacity_kwh", 0))
        dc_current = (
            pcs.get("rated_kw", 0.0) * 1000.0 / dc_bus_v if dc_bus_v else 0.0
        )
        spec["battery"] = {
            "capacity_kwh": bat["capacity_kwh"],
            "power_kw": bat.get("power_kw", 0.0),
            "chemistry": bat.get("chemistry", ""),
            "soc_window": bat.get("soc_window"),
            "pcs_kw": pcs.get("rated_kw", 0.0),
            "pcs_quadrants": "4-quadrant",
            "cycles_per_year": bat.get("cycles_per_year"),
            "expected_life_years": bat.get("expected_life_years"),
            "protection": bat.get("ac_protection") or {},
            "cable": bat.get("cable"),
            "dc_bus_v": dc_bus_v,
            "dc_current_a": dc_current,
            "dc_fuse_a": _standard_switch(dc_current * 1.25),
            "dc_isolator_a": _standard_switch(dc_current * 1.25),
        }

    # -------------------------------------------------------------- genset
    if gen.get("capacity_kw", 0) > 0:
        gkva = gen["capacity_kw"] / 0.8
        gi = (
            gkva * 1000.0 / (math.sqrt(3) * sys_v) if phases == 3
            else gkva * 1000.0 / sys_v
        )
        spec["genset"] = {
            "capacity_kw": gen["capacity_kw"],
            "kva": gkva,
            "units": gen.get("units", 1),
            "unit_kw": gen.get("unit_kw", 0.0),
            "current_a": gi,
            "protection": gen.get("ac_protection") or {},
            "cable": gen.get("cable"),
            "run_hours": gen.get("run_hours"),
            "interlock": "Mechanically interlocked change-over. "
                         "No inter-connection with the utility supply.",
        }

    # ------------------------------------------------------------------ EV
    if ev.get("chargers", 0) > 0:
        spec["ev"] = {
            "chargers": ev["chargers"],
            "charger_kw": ev.get("charger_kw", 0.0),
            "total_kw": ev.get("total_kw", 0.0),
            "design_kw": ev.get("design_kw", 0.0),
            "diversity": ev.get("diversity_factor", 1.0),
            "protection": ev.get("protection") or {},
            "rcd": ev.get("rcd") or {},
            "v2g": ev.get("v2g", False),
            "cable": ev.get("cable"),
        }

    # ---------------------------------------------------------------- load
    spec["load"] = {
        "peak_kw": load.get("peak_kw", 0.0),
        "annual_mwh": load.get("annual_mwh", 0.0),
        "protection": load.get("protection") or {},
        "cable": load.get("cable"),
        "essential_kw": load.get("peak_kw", 0.0) * 0.35,
        "non_essential_kw": load.get("peak_kw", 0.0) * 0.65,
        "ways": max(4, min(12, int(load.get("peak_kw", 0) / 8) + 4)),
    }

    # ------------------------------------------------- medium voltage side
    if meta["mv"]:
        total_kva = 0.0
        for key in ("pv", "wind"):
            rec = spec.get(key) or {}
            total_kva += (
                rec.get("inverter_kw_total") or rec.get("capacity_kw", 0.0)
            ) / pf
        total_kva = max(total_kva, spec.get("supply", {}).get("kva", 0.0))
        mv_v = _mv_level(total_kva)
        n_tx = max(1, int(math.ceil(total_kva / 2500.0)))
        tx_kva = _standard_transformer(total_kva / n_tx)
        spec["mv"] = {
            "mv_voltage_v": mv_v,
            "lv_voltage_v": sys_v,
            "transformers": n_tx,
            "transformer_kva": tx_kva,
            "vector_group": "Dyn11",
            "impedance_pct": 6.0 if tx_kva <= 1000 else 6.5,
            "mv_current_a": total_kva * 1000.0 / (math.sqrt(3) * mv_v),
            "lv_current_a": (
                tx_kva * 1000.0 / (math.sqrt(3) * sys_v)
            ),
            "ring_main_units": n_tx if topo == MV_CUSTOMER else 0,
            "mv_switchgear": f"{mv_v / 1000:.0f} kV, "
                             f"{_mv_breaking_ka(mv_v):.0f} kA breaking",
        }
        spec["warnings"].append(
            f"Connection at {mv_v / 1000:.0f} kV requires the network "
            f"operator's protection settings and a grid-code compliance "
            f"study. The transformer vector group and earthing arrangement "
            f"shown are typical and must be confirmed with them."
        )

    # --------------------------------------------------- protection scheme
    spec["protection_scheme"] = {
        "anti_islanding": "Loss-of-mains protection to the local grid code "
                          "(IEC 62116 / IEEE 1547 or national equivalent)",
        "rcd": design.get("rcd") or {},
        "spd_ac": design.get("spd_ac") or {},
        "earthing": design.get("earthing_system", "TN-S"),
        "isolation": (design.get("schedules") or {}).get("isolation", []),
    }

    spec["warnings"] += design.get("warnings", [])
    spec.pop("topology")          # DrawingSpec records it as its first field
    return DrawingSpec(topo, **spec)


# ---------------------------------------------------------------- helpers

_SWITCH_SIZES = [6, 10, 16, 20, 25, 32, 40, 50, 63, 80, 100, 125, 160, 200,
                 250, 315, 400, 500, 630, 800, 1000, 1250, 1600, 2000, 2500,
                 3200, 4000]

_TX_SIZES = [50, 100, 160, 200, 250, 315, 400, 500, 630, 800, 1000, 1250,
             1600, 2000, 2500]


def _standard_switch(current_a):
    for s in _SWITCH_SIZES:
        if s >= (current_a or 0) - 1e-9:
            return s
    return _SWITCH_SIZES[-1]


def _standard_transformer(kva):
    for s in _TX_SIZES:
        if s >= (kva or 0) - 1e-9:
            return s
    return _TX_SIZES[-1]


def _dc_voltage_class(voc_v):
    """
    Voltage class for the DC isolator.

    Sized on the cold-day open-circuit voltage, not the operating voltage:
    Voc rises as temperature falls, and the isolator has to break at the
    worst case, not the typical one.
    """
    v = voc_v or 0
    for cls in (600, 1000, 1500):
        if v <= cls * 0.98:
            return cls
    return 1500


def _ct_ratio(current_a):
    """A standard CT ratio comfortably above the connection current."""
    primaries = [50, 75, 100, 150, 200, 250, 300, 400, 500, 600, 800,
                 1000, 1200, 1500, 2000, 3000]
    for p in primaries:
        if p >= current_a * 1.2:
            return f"{p}/5 A"
    return f"{primaries[-1]}/5 A"


def _battery_dc_bus_v(capacity_kwh):
    """
    Typical DC bus voltage for a battery of this size.

    Small systems run at 48 V; anything above a few tens of kWh uses a
    high-voltage string, because 48 V at 100 kW is two thousand amps and no
    practical conductor carries that.
    """
    if capacity_kwh <= 30:
        return 48.0
    if capacity_kwh <= 200:
        return 400.0
    return 800.0


def _mv_level(kva):
    """Distribution voltage appropriate to the connection size."""
    if kva <= 3000:
        return 11000
    if kva <= 10000:
        return 22000
    return 33000


def _mv_breaking_ka(mv_v):
    return 25.0 if mv_v <= 11000 else 20.0


def describe(spec):
    """One-line description for the title block."""
    meta = spec.meta
    bits = [meta["label"]]
    parts = []
    if spec.get("pv"):
        parts.append(f"{spec.pv['capacity_kwp']:,.0f} kWp PV")
    if spec.get("wind"):
        parts.append(f"{spec.wind['capacity_kw']:,.0f} kW wind")
    if spec.get("battery"):
        parts.append(f"{spec.battery['capacity_kwh']:,.0f} kWh storage")
    if spec.get("genset"):
        parts.append(f"{spec.genset['capacity_kw']:,.0f} kW genset")
    if parts:
        bits.append(" + ".join(parts))
    return " - ".join(bits)
