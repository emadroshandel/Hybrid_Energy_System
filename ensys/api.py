"""
JSON API layer.

One dispatch table maps an action name to a handler taking a plain dict and
returning a plain dict. Both transports use it unchanged:

  * `server.py` exposes it over HTTP for the local and network case
  * `boot.js` calls it directly through Pyodide for the no-server case

Keeping the transport out of this module is what lets the same engine serve
a browser tab, a desktop window and a colleague on the LAN without three
copies of the logic drifting apart.

Every handler returns {"ok": True, ...} or {"ok": False, "error": ...}.
Exceptions are caught at the boundary and turned into readable messages,
because an engineer looking at a traceback in a browser console learns
nothing useful.
"""

from __future__ import annotations

import json
import time
import traceback

from . import __version__, capabilities
from . import catalogue as catalogue_mod
from . import costs as costs_mod
from . import i18n as i18n_mod
from . import dispatch as dispatch_mod
from . import economics as econ_mod
from . import metrics as metrics_mod
from .models.battery import Battery, CHEMISTRY_PRESETS
from .models.ev import EVFleet
from .models.genset import Generator, FUEL_CURVE_PRESETS
from .models.grid import GridConnection
from .models.pv import PVArray, optimum_tilt
from .models.wind import WindTurbine, library as wind_library, SHEAR_EXPONENTS
from .optim import algorithms as alg_mod
from .optimise import SizingStudy
from .resources import providers as prov_mod
from .resources.geo import Location, PRESETS
from .resources.synth import synthesise_year
from .sizing.design import size_system
from . import validate as validate_mod
from .assets import Dispatchable, NonDispatchable, Storage
from .sizing.inverter import PVModule
from .diagram import sld
from .timeseries import (
    coerce_year, stats, monthly_totals, monthly_means, daily_profile,
    duration_curve, month_index,
)

# Sessions hold the expensive intermediate state between calls, so the UI can
# fetch resources once, then run several studies without re-downloading.
_SESSIONS = {}
_SESSION_LIMIT = 8


def _session(sid):
    if sid not in _SESSIONS:
        if len(_SESSIONS) >= _SESSION_LIMIT:
            oldest = min(_SESSIONS, key=lambda k: _SESSIONS[k].get("touched", 0))
            del _SESSIONS[oldest]
        _SESSIONS[sid] = {"created": time.time()}
    _SESSIONS[sid]["touched"] = time.time()
    return _SESSIONS[sid]


# =====================================================================
# Handlers
# =====================================================================

def h_info(payload):
    """Engine capabilities and the option lists the UI needs to populate."""
    return {
        "ok": True,
        "version": __version__,
        "capabilities": capabilities(),
        "presets": {
            "locations": {
                k: v.to_dict() for k, v in PRESETS.items()
            },
            "turbines": {
                k: {
                    "name": t.name, "rated_kw": t.rated_kw,
                    "hub_height_m": t.hub_height_m,
                    "rotor_diameter_m": t.rotor_diameter_m,
                    "v_cutin": t.v_cutin, "v_rated": t.v_rated,
                    "v_cutout": t.v_cutout,
                }
                for k, t in wind_library().items()
            },
            "battery_chemistries": {
                k: {
                    "efficiency": v[0], "soc_min": v[1], "soc_max": v[2],
                    "cycles": v[3], "calendar_years": v[4],
                }
                for k, v in CHEMISTRY_PRESETS.items()
            },
            "fuel_curves": FUEL_CURVE_PRESETS,
            "terrain": SHEAR_EXPONENTS,
            "strategies": [
                dispatch_mod.LOAD_FOLLOWING,
                dispatch_mod.CYCLE_CHARGING,
                dispatch_mod.TARIFF_ARBITRAGE,
            ],
            "technologies": catalogue_mod.ui_schema(
                payload.get("language", "en")
            ),
            "algorithms": alg_mod.catalogue(),
            "default_algorithm": alg_mod.DEFAULT,
            "objectives": [
                {"key": "npc", "label": "Net present cost", "direction": "min"},
                {"key": "lcoe", "label": "Levelised cost of energy", "direction": "min"},
                {"key": "lpsp", "label": "Loss of power supply probability", "direction": "min"},
                {"key": "renewable_fraction", "label": "Renewable fraction", "direction": "max"},
                {"key": "emissions_kg", "label": "Annual CO2 emissions", "direction": "min"},
                {"key": "initial_capital", "label": "Initial capital", "direction": "min"},
                {"key": "self_sufficiency", "label": "Self-sufficiency", "direction": "max"},
            ],
        },
    }


def h_optimum_tilt(payload):
    lat = float(payload.get("latitude", 0.0))
    return {"ok": True, "tilt_deg": optimum_tilt(lat),
            "azimuth_deg": 180.0 if lat >= 0 else 0.0}


def h_fetch_resources(payload):
    """
    Retrieve resource data for a location.

    `mode` selects between online providers and a purely offline synthesis
    from monthly averages, so the tool remains usable with no network.
    """
    loc = Location.from_dict(payload.get("location", {}))
    mode = payload.get("mode", "online")
    sid = payload.get("session", "default")
    sess = _session(sid)

    findings = []
    if mode == "offline":
        monthly_ghi = payload.get("monthly_ghi")
        if not monthly_ghi:
            return {
                "ok": False,
                "error": (
                    "Offline mode needs twelve monthly GHI totals in kWh/m2. "
                    "These are on the Global Solar Atlas page for the site, or "
                    "from a local meteorological station."
                ),
            }
        # Twelve numbers around 2-9 are daily averages, not monthly totals.
        # Correcting that here, loudly, is worth more than any warning
        # attached to the result thirty-fold later.
        monthly_ghi, f = validate_mod.check_monthly_ghi(monthly_ghi)
        findings.extend(f)
        if validate_mod.worst_level(f) == "error":
            return {"ok": False, "error": f[-1]["message"], "findings": findings}
        data = synthesise_year(
            loc,
            monthly_ghi=monthly_ghi,
            monthly_temperature=payload.get("monthly_temperature"),
            mean_wind_speed=payload.get("mean_wind_speed"),
        )
    else:
        prefer = payload.get("providers") or (
            "pvgis_tmy", "open_meteo", "nasa_power"
        )
        data = prov_mod.fetch_resources(
            loc, prefer=tuple(prefer),
            cache_dir=payload.get("cache_dir"),
            year=int(payload.get("year", 2020)),
        )

    sess["location"] = loc
    sess["resources"] = data

    lta = data.get("long_term_average") or {}
    return {
        "ok": True,
        "provider": data.get("provider"),
        "licence": data.get("licence"),
        "synthetic": bool(data.get("synthetic")),
        "annual_ghi_kwh_m2": data.get("annual_ghi_kwh_m2"),
        "warnings": data.get("warnings", []),
        "findings": findings,
        # Stated explicitly so a misaligned dataset can be seen rather than
        # inferred from a disappointing yield ten steps later.
        "time_reference": data.get("time_reference", "unknown"),
        "utc_offset_applied_hours": data.get("utc_offset_applied_hours"),
        "provenance": data.get("provenance"),
        "long_term_average": {
            k: lta.get(k) for k in (
                "ghi_kwh_m2_year", "dni_kwh_m2_year", "pvout_kwh_kwp_year",
                "optimum_tilt_deg", "mean_temperature_c", "elevation_m",
            )
        } if lta else None,
        "monthly_ghi": monthly_totals_kwh(data.get("ghi", [])),
        "monthly_temperature": monthly_means(data.get("temperature_c", [0.0] * 8760)),
        "wind_available": any(
            k.startswith("wind_speed") for k in data
        ),
    }


def monthly_totals_kwh(ghi_w_m2):
    if not ghi_w_m2:
        return None
    return [round(v / 1000.0, 1) for v in monthly_totals(ghi_w_m2)]


def h_import_series(payload):
    """
    Accept user-supplied hourly data.

    This is the "or the user can provide them in a specific template"
    path. Accepts a list of numbers, or CSV text with a named column.
    """
    sid = payload.get("session", "default")
    sess = _session(sid)
    name = payload.get("name", "load")
    notes = []

    values = payload.get("values")
    if values is None and payload.get("csv"):
        values, note = parse_csv_column(
            payload["csv"], payload.get("column"), payload.get("delimiter", ",")
        )
        if note:
            notes.append(note)

    if not values:
        return {
            "ok": False,
            "error": (
                "No numeric values found. Provide either a list of numbers or "
                "CSV text with a header row and a named column."
            ),
        }

    try:
        series, note = coerce_year(values, name)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    if note:
        notes.append(note)

    scale = float(payload.get("scale", 1.0))
    if scale != 1.0:
        series = [v * scale for v in series]
        notes.append(f"Scaled by {scale:g}.")

    sess.setdefault("imported", {})[name] = series
    s = stats(series)
    findings = validate_mod.check_load(series, name) if name == "load" else []
    return {
        "ok": True,
        "name": name,
        "hours": len(series),
        "stats": s,
        "notes": notes,
        "findings": findings,
        "monthly": monthly_totals(series),
        "daily_profile": daily_profile(series),
    }


def parse_csv_column(text, column=None, delimiter=","):
    """Pull one numeric column out of CSV text, skipping comment lines."""
    import csv
    import io

    lines = [
        ln for ln in text.splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    if not lines:
        return [], "The file contained no data rows."

    reader = csv.reader(io.StringIO("\n".join(lines)), delimiter=delimiter)
    rows = list(reader)
    if not rows:
        return [], None

    header = rows[0]
    body = rows[1:]

    idx = None
    note = None
    if column:
        for i, h in enumerate(header):
            if h.strip().lower() == str(column).strip().lower():
                idx = i
                break
        if idx is None:
            for i, h in enumerate(header):
                if str(column).strip().lower() in h.strip().lower():
                    idx = i
                    note = f"Matched column '{header[i]}' for '{column}'."
                    break
    if idx is None:
        # Fall back to the first column that parses as a number.
        for i in range(len(header)):
            try:
                float(body[0][i])
                idx = i
                note = f"No column named; using '{header[i]}'."
                break
            except (ValueError, IndexError):
                continue
    if idx is None:
        return [], (
            f"No numeric column found. Available columns: "
            f"{', '.join(header)}"
        )

    values = []
    bad = 0
    for row in body:
        try:
            values.append(float(row[idx]))
        except (ValueError, IndexError):
            bad += 1
    if bad:
        note = (note or "") + f" Skipped {bad} unparseable row(s)."
    return values, note


# Technologies the interface has always shown by name, in the order the
# decision vector expects them. Everything else in the catalogue is built
# the same way and simply appears after them.
CORE_TECHNOLOGIES = ("pv", "wind", "battery", "genset", "ev_fleet")


def _build_components(cfg, location, region="global", currency=None,
                      cost_level="typical"):
    """
    Instantiate component models from the interface's configuration dict.

    Every technology is built through `catalogue.build()`, which is what the
    catalogue was written for: it knows the field names, the constructor
    arguments, the places the two sensibly differ, and how to fall back to
    the regional cost library when a cost is left at zero.

    This function used to hand-build five technologies with literal field
    reads and default values written a second time. Two things followed from
    that, neither of them visible in a result:

      * The other fourteen technologies in the catalogue - CSP, biomass,
        geothermal, fuel cell, CHP, both hydros, tidal, wave, flywheel,
        supercapacitor, pumped hydro, CAES, hydrogen, thermal storage - had
        models, cost entries, translations and diagram symbols, and no way to
        reach any of them. They could not be selected, so they were never
        considered, so no study could ever recommend one.
      * The regional cost library was bypassed. `capital_cost` defaulted to
        0.0 here rather than to the library figure, so choosing a region
        changed the cost browser and nothing in the actual sizing.

    Returns a dict of key -> asset. The grid is built separately because it
    is not sized and takes no unit count.
    """
    out = {}

    ordered = list(CORE_TECHNOLOGIES) + [
        k for k in catalogue_mod.CATALOGUE
        if k not in CORE_TECHNOLOGIES
    ]

    for tech in ordered:
        # The interface names the fleet "ev" for historical reasons.
        conf = cfg.get(tech)
        if conf is None and tech == "ev_fleet":
            conf = cfg.get("ev")
        if not conf or not conf.get("enabled"):
            continue

        conf = dict(conf)
        if tech == "pv":
            conf.setdefault("azimuth_deg", location.optimal_azimuth)
            conf.setdefault("albedo", location.albedo)
        if tech == "ev_fleet":
            # The legacy v2g checkbox, superseded by the scenario list.
            if not conf.get("scenario") and conf.get("v2g"):
                conf["scenario"] = "v2g"

        try:
            out[tech] = catalogue_mod.build(
                tech, conf, region=region, currency=currency,
                cost_level=cost_level,
            )
        except (KeyError, TypeError, ValueError) as e:
            # One misconfigured technology must not lose the whole study.
            # The caller turns this into a finding the user can act on.
            out.setdefault("_errors", []).append(
                f"{tech}: {type(e).__name__}: {e}"
            )

    gr = cfg.get("grid") or {}
    if gr.get("enabled", True):
        out["grid"] = GridConnection(
            import_limit_kw=float(gr.get("import_limit_kw", 1e6)),
            export_limit_kw=float(gr.get("export_limit_kw", 0.0)),
            import_price=gr.get("import_price", 0.10),
            export_price=gr.get("export_price", 0.05),
            tariff_type=gr.get("tariff_type", "single"),
            tou_bands=gr.get("tou_bands"),
            demand_charge_per_kw_month=float(gr.get("demand_charge", 0.0)),
            standing_charge_per_year=float(gr.get("standing_charge", 0.0)),
            emission_factor_kg_per_kwh=float(gr.get("emission_factor", 0.4)),
            availability=float(gr.get("availability", 1.0)),
            mean_outage_hours=float(gr.get("mean_outage_hours", 4.0)),
        )
    return out


def h_run_study(payload):
    """Run a full sizing study and return the Pareto front."""
    # Check the cheap things first. An unknown algorithm name is reported,
    # not silently swapped for the default: a study that quietly ran
    # something other than what the report says it ran is worse than one
    # that refused to run at all.
    algorithm = payload.get("algorithm") or alg_mod.DEFAULT
    try:
        alg_mod.get(algorithm)
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    sid = payload.get("session", "default")
    sess = _session(sid)

    location = sess.get("location") or Location.from_dict(
        payload.get("location", {})
    )
    resources = sess.get("resources")
    imported = sess.get("imported", {})

    load = imported.get("load")
    if not load:
        return {
            "ok": False,
            "error": (
                "No load profile has been provided. Import an hourly load "
                "series before running a study - the sizing has nothing to "
                "serve without it."
            ),
        }

    cfg = payload.get("config", {})
    ccfg = payload.get("costs", {}) or {}
    comps = _build_components(
        cfg, location,
        region=ccfg.get("region", "global"),
        currency=ccfg.get("currency"),
        cost_level=ccfg.get("level", "typical"),
    )
    build_errors = comps.pop("_errors", [])

    # Plausibility, before anything expensive runs. These never block the
    # study - an unusual site is still a site - but they travel back with
    # the result so the headline capacity is read next to the assumptions
    # that produced it.
    findings = list(validate_mod.check_load(load))
    findings += validate_mod.check_component_scale(
        load, comps, grid=comps.get("grid")
    )
    for msg in build_errors:
        findings.append({
            "level": "error", "code": "component_build_failed",
            "message": (
                f"This technology could not be built from the values given "
                f"and has been left out of the study — {msg}"
            ),
        })

    # Build the per-unit generation series.
    res = {"load": load}
    if comps.get("pv"):
        if not resources:
            return {"ok": False,
                    "error": "PV is enabled but no solar resource data has been loaded."}
        dc, pvinfo = comps["pv"].output_series(
            resources["ghi"], location,
            dni=resources.get("dni"), dhi=resources.get("dhi"),
            temperature_c=resources.get("temperature_c"),
            wind_speed_ms=resources.get("wind_speed_10m"),
        )
        res["pv_unit"] = dc
        sess["pv_info"] = pvinfo
        findings += validate_mod.check_pv_yield(
            dc, comps["pv"].capacity_kwp, location,
            time_reference=resources.get("time_reference"),
        )

    if comps.get("wind"):
        ws = imported.get("wind_speed")
        # The turbine model and the catalogue both call this
        # `reference_height_m`; the interface used to send `ref_height_m`,
        # and a saved project still can. Reading only one of the two names
        # silently ignored the setting and sheared every wind speed from the
        # wrong height.
        wcfg = cfg.get("wind", {}) or {}
        ref_h = float(
            wcfg.get("reference_height_m")
            or wcfg.get("ref_height_m")
            or getattr(comps["wind"], "reference_height_m", 10.0)
            or 10.0
        )
        if not ws and resources:
            for k, h in (("wind_speed_100m", 100), ("wind_speed_50m", 50),
                         ("wind_speed_10m", 10)):
                if resources.get(k):
                    ws = resources[k]
                    ref_h = h
                    break
        if not ws:
            return {"ok": False,
                    "error": "Wind is enabled but no wind-speed series is available."}
        pw, winfo = comps["wind"].output_series(
            ws, ref_height_m=ref_h,
            shear_exponent=location.shear_exponent,
            elevation_m=location.elevation_m,
            temperature_c=resources.get("temperature_c") if resources else None,
        )
        res["wind_unit"] = pw
        sess["wind_info"] = winfo
        findings += validate_mod.check_wind_yield(pw, comps["wind"].rated_kw)

    if comps.get("ev_fleet"):
        # Per charge point, so the number of points is a decision the
        # optimiser can make rather than a constant baked into the sample.
        res["ev_profile"] = comps["ev_fleet"].unit_profile()

    # Per-unit output for every other non-dispatchable in the catalogue.
    # Each asset knows how to generate its own series from the resource
    # dict; without this they would be built, sized and dispatched at zero
    # output, which looks exactly like "the optimiser did not want one".
    for key, asset in comps.items():
        if key in ("pv", "wind", "grid", "ev_fleet"):
            continue
        if not isinstance(asset, NonDispatchable):
            continue
        try:
            series, info = asset.generate(resources or {}, location)
        except Exception as e:
            findings.append({
                "level": "error", "code": "resource_missing",
                "message": (
                    f"{asset.name} needs resource data this study does not "
                    f"have ({type(e).__name__}: {e}), so it was left out."
                ),
            })
            continue
        res[f"unit::{key}"] = series
        sess.setdefault("unit_info", {})[key] = info

    ecfg = payload.get("economics", {})
    econ = econ_mod.EconomicParameters(
        project_years=int(ecfg.get("project_years", 20)),
        interest_rate=float(ecfg.get("interest_rate", 0.08)),
        escalation_rate=float(ecfg.get("escalation_rate", 0.02)),
        currency=ecfg.get("currency", "USD"),
        unmet_load_penalty=float(ecfg.get("unmet_load_penalty", 0.0)),
        emissions_price=float(ecfg.get("emissions_price", 0.0)),
        load_growth_rate=float(ecfg.get("load_growth_rate", 0.0)),
    )

    from .system import SystemConfig

    # The five named technologies keep their fixed positions in the decision
    # vector, so studies and reports written against the old order still
    # read correctly; everything else in the catalogue follows them.
    extra = [
        (k, a) for k, a in comps.items()
        if k not in CORE_TECHNOLOGIES and k != "grid"
    ]
    ev_cfg = cfg.get("ev_fleet") or cfg.get("ev") or {}
    base = SystemConfig(
        pv=comps.get("pv"), wind=comps.get("wind"),
        battery=comps.get("battery"), genset=comps.get("genset"),
        grid=comps.get("grid"), ev=comps.get("ev_fleet"),
        n_chargers=int(ev_cfg.get("chargers", 0) or 0),
        assets=extra,
    )

    objectives = tuple(payload.get("objectives") or ("npc", "lpsp", "renewable_fraction"))
    cons = {}
    for k, v in (payload.get("constraints") or {"lpsp": ["<=", 0.05]}).items():
        cons[k] = (v[0], float(v[1]))

    findings += validate_mod.check_economics(econ, comps.get("grid"))

    study = SizingStudy(
        base, res, econ, objectives=objectives, constraints=cons,
        strategy=payload.get("strategy", dispatch_mod.LOAD_FOLLOWING),
        seed=int(payload.get("seed", 1234)),
        size_ev_fleet=bool(payload.get("size_ev_fleet", False)),
        algorithm=algorithm,
    )

    result = study.run(
        n_particles=int(payload.get("particles", 24)),
        n_iterations=int(payload.get("iterations", 40)),
        time_budget_s=payload.get("time_budget_s"),
    )

    sess["study"] = study
    sess["result"] = result

    front = []
    for s in sorted(result.front, key=lambda x: x.metrics.get("npc", 0)):
        front.append(_front_row(s, study))

    rec = result.recommended()
    if rec is not None:
        findings += validate_mod.check_result(
            rec.metrics, base.with_decision(rec.x), load, comps.get("grid")
        )

    return {
        "ok": True,
        "summary": result.summary(),
        "findings": findings,
        "front": front,
        "recommended_index": (
            next((i for i, r in enumerate(front) if r["x"] == rec.x), 0)
            if rec else None
        ),
        "highlights": {
            "recommended": _front_row(rec, study) if rec else None,
            "cheapest": _front_row(result.cheapest(), study),
            "greenest": _front_row(result.greenest(), study),
            "most_reliable": _front_row(result.most_reliable(), study),
        },
        # How each headline was picked, so a small "most reliable" design
        # reads as an answer rather than as a bug.
        "highlight_notes": result.highlight_notes(),
        # Year one is not the year the design has to survive.
        "end_of_life": result.end_of_life_check(),
        "search_space": {
            "bounds": study.space.bounds,
            "size": study.space.size(),
        },
    }


def _front_row(s, study):
    if not s:
        return None
    m = s.metrics
    base = study.base_system

    # Read the decision vector by key, not by position. The vector is as
    # long as the system has sizable assets, so a fixed x[0..3] is right
    # only when all four happen to be present — and reads the wrong asset,
    # or falls off the end, whenever they are not.
    counts = dict(zip(study.space.keys, s.x))
    n = lambda k: counts.get(k, 0)

    return {
        "x": s.x,
        "feasible": s.feasible,
        "counts": counts,
        "pv_kwp": n("pv") * (base.pv.capacity_kwp if base.pv else 0),
        "wind_kw": n("wind") * (base.wind.rated_kw if base.wind else 0),
        "battery_kwh": n("battery") * (
            base.battery.nominal_energy_kwh if base.battery else 0),
        "genset_kw": n("genset") * (base.genset.rated_kw if base.genset else 0),
        "chargers": n("ev_fleet"),
        "ev_kw": n("ev_fleet") * (base.ev.charger_kw if base.ev else 0),
        "npc": m.get("npc"),
        "lcoe": m.get("lcoe"),
        "initial_capital": m.get("initial_capital"),
        "lpsp": m.get("lpsp"),
        "renewable_fraction": m.get("renewable_fraction"),
        "self_sufficiency": m.get("self_sufficiency"),
        "emissions_kg": m.get("emissions_kg"),
        "curtailment_rate": m.get("curtailment_rate"),
        "unmet_kwh": m.get("unmet_kwh"),
    }


def h_detail(payload):
    """
    Full detail for one design on the front: hourly flows, the electrical
    design, the schedules and the diagrams.
    """
    sid = payload.get("session", "default")
    sess = _session(sid)
    result = sess.get("result")
    if not result:
        return {"ok": False, "error": "No study has been run in this session."}

    x = payload.get("x")
    target = None
    for s in result.front:
        if s.x == x:
            target = s
            break
    if target is None:
        target = result.recommended()
    if target is None:
        return {"ok": False, "error": "That design is not on the current front."}

    system, disp, econ_result, m = result.rerun(target)
    location = sess.get("location")

    design = size_system(
        system, disp, location=location,
        system_voltage_v=float(payload.get("system_voltage_v", 400)),
        ambient_max_c=float(payload.get("ambient_max_c", 45)),
        ambient_min_c=float(payload.get("ambient_min_c", -5)),
        module=PVModule(),
        cable_lengths=payload.get("cable_lengths"),
        fault_level_ka=float(payload.get("fault_level_ka", 10)),
    )

    title = payload.get("title") or "Hybrid energy system"
    subtitle = (
        f"{location.name if location else 'Site'}  |  "
        f"NPC {econ_result['currency']} {econ_result['npc']:,.0f}  |  "
        f"LCOE {econ_result['currency']} {econ_result['lcoe']:.4f}/kWh"
    )
    # Drawing options: the class is inferred from the design, but a client
    # can force it — a site supplied at three phase wants the three-phase
    # drawing even for a small system, and whether the essential loads are
    # split is a decision about the installation, not something the sizing
    # can know.
    diagram_kw = {}
    for key in ("topology", "phases", "split_loads", "mv_customer",
                "project", "revision"):
        if payload.get(key) is not None:
            diagram_kw[key] = payload[key]
    diagrams = sld.render_pair(design, title, subtitle, **diagram_kw)

    months = month_index()
    idx, dur = duration_curve(disp.load)

    return {
        "ok": True,
        "system": system.summary(),
        "metrics": {k: v for k, v in m.items() if not k.startswith("_")},
        "economics": {
            "npc": econ_result["npc"],
            "lcoe": econ_result["lcoe"],
            "initial_capital": econ_result["initial_capital"],
            "annualised_cost": econ_result["annualised_cost"],
            "currency": econ_result["currency"],
            "items": {
                k: {
                    "npc": v["npc"], "capital": v["capital"],
                    "replacement": v["replacement"], "om": v["om"],
                    "recurring": v["recurring"],
                    "n_replacements": v["n_replacements"],
                }
                for k, v in econ_result["items"].items()
            },
        },
        "energy": disp.totals,
        "design": _strip_series(design),
        "diagrams": diagrams,
        "charts": {
            "monthly_generation": {
                "pv": monthly_totals(disp.pv, months),
                "wind": monthly_totals(disp.wind, months),
                "genset": monthly_totals(disp.genset, months),
                "import": monthly_totals(disp.grid_import, months),
                "load": monthly_totals(disp.load, months),
            },
            "daily_profile": {
                "load": daily_profile(disp.load),
                "pv": daily_profile(disp.pv),
                "wind": daily_profile(disp.wind),
                "battery_discharge": daily_profile(disp.battery_discharge),
                "import": daily_profile(disp.grid_import),
            },
            "load_duration": {"index": idx, "values": dur},
            "soc_daily": [
                sum(disp.battery_soc[d * 24:(d + 1) * 24]) / 24.0
                for d in range(365)
            ],
        },
        "sample_week": _sample_week(disp, payload.get("week_start_day", 180)),
    }


def _sample_week(disp, start_day=180):
    """One week of hourly flows, for the stacked dispatch chart."""
    a = int(start_day) * 24
    b = min(a + 168, len(disp.load))
    return {
        "hours": list(range(a, b)),
        "load": disp.load[a:b],
        "pv": disp.pv[a:b],
        "wind": disp.wind[a:b],
        "genset": disp.genset[a:b],
        "import": disp.grid_import[a:b],
        "export": disp.grid_export[a:b],
        "battery_charge": disp.battery_charge[a:b],
        "battery_discharge": disp.battery_discharge[a:b],
        "soc": disp.battery_soc[a:b],
        "curtailed": disp.curtailed[a:b],
        "unmet": disp.unmet[a:b],
    }


def _strip_series(design):
    """Remove bulky arrays that the UI does not plot, keeping the payload small."""
    import copy

    d = copy.deepcopy(design)
    if d.get("pv", {}).get("inverter", {}).get("clipping_curve"):
        curve = d["pv"]["inverter"]["clipping_curve"]
        d["pv"]["inverter"]["clipping_curve"] = curve[::2]
    return d


def h_catalogue(payload):
    """The technology catalogue, translated, for the interface to render."""
    return {
        "ok": True,
        "schema": catalogue_mod.ui_schema(payload.get("language", "en")),
    }


def h_costs(payload):
    """Browse the regional cost library."""
    region = payload.get("region", "global")
    currency = payload.get("currency")
    level = payload.get("level", "typical")
    return {
        "ok": True,
        "region": region,
        "currency": currency,
        "reference_year": costs_mod.REFERENCE_YEAR,
        "catalogue": costs_mod.catalogue(region, currency, level),
        "regions": costs_mod.REGIONS,
        "currencies": costs_mod.CURRENCIES,
        "caveats": costs_mod.caveats(region, currency or "USD"),
    }


def h_translations(payload):
    """
    The whole string catalogue for one language.

    Sent in one call so the browser build can render Persian without a
    round trip per label, and so a partially translated language degrades
    visibly rather than silently.
    """
    lang = payload.get("language", "en")
    t = i18n_mod.get(lang)
    return {
        "ok": True,
        "language": lang,
        "dir": t.dir,
        "strings": t.catalogue(),
        "available": i18n_mod.available(),
        "coverage": i18n_mod.coverage(),
    }


def h_ev_scenarios(payload):
    """
    Compare charging scenarios on identical hardware.

    Isolating the control strategy from the equipment is the point: it is
    usually the cheapest improvement available at a site with EVs, and it
    cannot be seen unless everything else is held constant.
    """
    from .models.evfleet import SCENARIOS, SCENARIO_LABELS, ARCHETYPES

    return {
        "ok": True,
        "scenarios": [
            {"key": s, "label": SCENARIO_LABELS[s]} for s in SCENARIOS
        ],
        "archetypes": [
            {"key": k, "label": v["label"],
             "charger_kw": v["charger_kw"], "battery_kwh": v["battery_kwh"],
             "v2g_plausible": v["v2g_plausible"]}
            for k, v in ARCHETYPES.items()
        ],
    }


HANDLERS = {
    "info": h_info,
    "catalogue": h_catalogue,
    "costs": h_costs,
    "translations": h_translations,
    "ev_scenarios": h_ev_scenarios,
    "optimum_tilt": h_optimum_tilt,
    "fetch_resources": h_fetch_resources,
    "import_series": h_import_series,
    "run_study": h_run_study,
    "detail": h_detail,
}


def handle(action, payload):
    """Single entry point used by every transport."""
    fn = HANDLERS.get(action)
    if not fn:
        return {
            "ok": False,
            "error": f"Unknown action '{action}'. Available: "
                     f"{', '.join(sorted(HANDLERS))}",
        }
    try:
        return fn(payload or {})
    except Exception as e:
        return {
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "traceback": traceback.format_exc(limit=6),
        }


def handle_json(action, payload_json):
    """String-in, string-out wrapper, which is what Pyodide finds easiest."""
    try:
        payload = json.loads(payload_json) if payload_json else {}
    except ValueError as e:
        return json.dumps({"ok": False, "error": f"Invalid JSON payload: {e}"})
    return json.dumps(handle(action, payload), default=_json_default)


def _json_default(o):
    if isinstance(o, (set, tuple)):
        return list(o)
    if hasattr(o, "to_dict"):
        return o.to_dict()
    return str(o)
