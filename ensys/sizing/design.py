"""
Detailed electrical design of a sized system.

Takes an optimised SystemConfig and its dispatch result, and works out the
physical equipment: converters, string layouts, conductors, protection and
isolation. The output is one record that both the report and the diagram
generator consume, so the drawing and the schedules cannot disagree.

Nothing here re-optimises. The unit counts are already decided; this stage
turns them into a bill of materials with every rating traceable to the
calculation that produced it.
"""

from __future__ import annotations

import math

from . import inverter as inv_mod
from . import cables as cab_mod
from . import protection as prot_mod
from . import voltage as volt_mod


def size_system(system, result, location=None, system_voltage_v=400,
                power_factor=0.95, module=None, ambient_max_c=45,
                ambient_min_c=-5, cable_lengths=None, fault_level_ka=10.0):
    """
    Produce the full electrical design record.

    Parameters
    ----------
    system : the optimised SystemConfig
    result : its DispatchResult
    cable_lengths : {feeder: metres}; sensible defaults are used where absent,
        and the report flags that they are assumptions rather than survey data
    """
    lengths = {
        "pv_dc": 60, "pv_ac": 25, "wind": 80, "battery": 15,
        "genset": 20, "grid": 30, "load": 40, "ev": 50,
    }
    lengths.update(cable_lengths or {})

    module = module or inv_mod.PVModule()

    # What voltage is this installation actually built at? Deciding this
    # first is what keeps the conductors sane: sizing a multi-megawatt
    # plant at 400 V produces tens of thousands of amps and dozens of
    # cables in parallel, which is not a design but a symptom of never
    # having asked the question.
    plant_kw = (
        (system.pv_capacity_kwp if system.pv else 0.0)
        + (system.wind_capacity_kw if system.wind else 0.0)
        + (system.genset_capacity_kw if system.genset else 0.0)
        + (system.battery_power_kw if system.battery else 0.0)
    )
    connection_kw = max(
        result.totals.get("peak_import_kw", 0.0),
        result.totals.get("peak_export_kw", 0.0),
        result.totals.get("peak_load_kw", 0.0),
    )
    dist = volt_mod.select_distribution(
        plant_kw, connection_kw, lv_voltage_v=system_voltage_v,
        power_factor=power_factor,
    )
    lv_v = dist["lv_voltage_v"]

    design = {
        "distribution": dist,
        "system_voltage_v": system_voltage_v,
        "power_factor": power_factor,
        "frequency_hz": 50,
        "earthing_system": "TN-S",
        "fault_level_ka": fault_level_ka,
        "assumptions": [],
        "warnings": [],
        "diagram_notes": [],
    }
    if not cable_lengths:
        design["assumptions"].append(
            "Cable route lengths are default estimates, not survey data. "
            "Voltage-drop results and conductor sizes will change once actual "
            "routes are measured."
        )

    schedules = {"cables": [], "protection": [], "isolation": []}

    # ------------------------------------------------------------------ PV
    if system.pv and system.n_pv > 0:
        kwp = system.pv_capacity_kwp
        dc_series = [p for p in result.pv]

        invspec = inv_mod.size_pv_inverter(
            dc_series, kwp, target_dc_ac=1.20,
            max_clipping_loss=0.02,
        )
        # The MPPT limits belong to the inverter that was actually selected,
        # and the array is divided across however many units there are.
        mppt = inv_mod.mppt_config(invspec["rated_ac_kw"])
        strings = inv_mod.design_strings(
            kwp / max(1, invspec["units"]), module,
            t_min_c=ambient_min_c,
            t_max_cell_c=ambient_max_c + 25,
            n_mppt=mppt["n_mppt"],
            inverter_i_max_per_mppt=mppt["i_max_per_mppt"],
        )
        if strings:
            strings["inverter_class"] = mppt["class"]
            strings["per_inverter"] = True
            strings["inverter_units"] = invspec["units"]
            # Report the whole-array totals alongside the per-inverter design.
            strings["array_strings_total"] = (
                strings["strings"] * invspec["units"]
            )
            strings["array_modules_total"] = (
                strings["modules_total"] * invspec["units"]
            )
            strings["array_capacity_kwp"] = (
                strings["actual_capacity_kwp"] * invspec["units"]
            )

        # One cable per inverter. An array is not a single machine, and
        # sizing its whole output as one conductor is what turns a
        # perfectly ordinary plant into forty cables in parallel.
        ac_kw = invspec["total_ac_kw"]
        pv_units = volt_mod.feeder(ac_kw, invspec["units"], lv_v, 3,
                                   power_factor)
        i_ac = cab_mod.design_current(
            pv_units["unit_kw"], pv_units["voltage_v"], 3, power_factor
        )
        cab_ac, prot_ac = coordinate(
            i_ac, lengths["pv_ac"], pv_units["voltage_v"], 3,
            max_voltage_drop_pct=2.0, ambient_c=ambient_max_c,
            fault_current_a=fault_level_ka * 1000,
            device="mccb" if i_ac > 100 else "mcb",
        )

        # DC string cabling: 1.25 x Isc for continuous duty, and a tight
        # 1% volt-drop budget because DC losses are pure yield loss.
        i_dc = module.isc * 1.25
        v_dc = strings["string_vmp_stc_v"] if strings else 600
        cab_dc = cab_mod.size_cable(
            i_dc, lengths["pv_dc"], v_dc, phases=0,
            max_voltage_drop_pct=1.0, ambient_c=ambient_max_c + 20,
        )
        string_fuse = prot_mod.select_pv_string_fuse(
            module.isc, strings["strings_per_mppt"] if strings else 1
        )

        design["pv"] = {
            "capacity_kwp": kwp,
            "units": system.n_pv,
            "unit_kwp": system.pv.capacity_kwp,
            "tilt_deg": system.pv.resolved_tilt(
                location.latitude if location else 30.0
            ),
            "azimuth_deg": system.pv.azimuth_deg,
            "inverter": invspec,
            "strings": strings,
            "dc_cable": cab_dc,
            "ac_cable": cab_ac,
            "ac_protection": prot_ac,
            "string_fuse": string_fuse,
            "spd_dc": prot_mod.select_spd(dc_side=True,
                                          array_voltage_v=strings["string_voc_cold_v"]
                                          if strings else 1000),
            "annual_dc_kwh": invspec["annual_dc_kwh"],
            "annual_ac_kwh": invspec["annual_ac_kwh"],
            "feeders": pv_units,
        }
        schedules["cables"] += [
            _cable_row("PV DC string", cab_dc, circuits=pv_units["units"]),
            _cable_row("PV inverter AC", cab_ac, circuits=pv_units["units"], feeders=pv_units),
        ]
        schedules["protection"].append(
            _prot_row("PV inverter AC", prot_ac, circuits=pv_units["units"])
        )
        if strings and strings["errors"]:
            design["warnings"] += strings["errors"]
        design["warnings"] += invspec["notes"]
        if not prot_ac["compliant"]:
            design["warnings"] += prot_ac["reasons"]

    # ---------------------------------------------------------------- wind
    if system.wind and system.n_wind > 0:
        kw = system.wind_capacity_kw
        conv = inv_mod.size_wind_converter(result.wind, kw)
        # Each turbine has its own terminals, its own cable and its own
        # protection; the farm is not one machine.
        wind_units = volt_mod.feeder(kw, system.n_wind, lv_v, 3, power_factor)
        i = cab_mod.design_current(
            wind_units["unit_kw"], wind_units["voltage_v"], 3, power_factor
        )
        cab, prot = coordinate(
            i, lengths["wind"], wind_units["voltage_v"], 3,
            max_voltage_drop_pct=2.0, ambient_c=ambient_max_c,
            fault_current_a=fault_level_ka * 1000,
            device="mccb" if i > 100 else "mcb", curve="D",
        )
        design["wind"] = {
            "capacity_kw": kw, "units": system.n_wind,
            "unit_kw": system.wind.rated_kw,
            "hub_height_m": system.wind.hub_height_m,
            "converter": conv, "cable": cab, "ac_protection": prot,
            "annual_kwh": sum(result.wind),
            "feeders": wind_units,
        }
        schedules["cables"].append(
            _cable_row("Wind turbine AC", cab, circuits=wind_units["units"], feeders=wind_units)
        )
        schedules["protection"].append(
            _prot_row("Wind turbine AC", prot, circuits=wind_units["units"])
        )
        design["diagram_notes"].append(
            "Wind feeder protection uses a type D curve to ride through "
            "generator inrush at cut-in."
        )

    # ------------------------------------------------------------- battery
    if system.battery and system.n_battery > 0:
        pcs = inv_mod.size_battery_pcs(
            result.battery_charge, result.battery_discharge,
            system.battery_power_kw,
        )
        bat_units = volt_mod.feeder(pcs["rated_kw"], system.n_battery, lv_v,
                                    3, power_factor)
        i = cab_mod.design_current(
            bat_units["unit_kw"], bat_units["voltage_v"], 3, power_factor
        )
        cab, prot = coordinate(
            i, lengths["battery"], bat_units["voltage_v"], 3,
            max_voltage_drop_pct=1.5, ambient_c=ambient_max_c,
            fault_current_a=fault_level_ka * 1000,
            device="mccb" if i > 100 else "mcb",
        )
        design["battery"] = {
            "capacity_kwh": system.battery_capacity_kwh,
            "power_kw": system.battery_power_kw,
            "units": system.n_battery,
            "chemistry": system.battery.chemistry,
            "soc_window": [system.battery.soc_min, system.battery.soc_max],
            "pcs": pcs, "cable": cab, "ac_protection": prot,
            "cycles_per_year": system.battery.equivalent_full_cycles(
                system.n_battery, result.totals["battery_discharge_kwh"]
            ),
            "expected_life_years": system.battery.expected_life_years(
                system.n_battery, result.totals["battery_discharge_kwh"]
            ),
            "feeders": bat_units,
        }
        schedules["cables"].append(
            _cable_row("Battery PCS AC", cab, circuits=bat_units["units"], feeders=bat_units)
        )
        schedules["protection"].append(
            _prot_row("Battery PCS AC", prot, circuits=bat_units["units"])
        )
        design["warnings"] += pcs["notes"]

    # -------------------------------------------------------------- genset
    if system.genset and system.n_genset > 0:
        kw = system.genset_capacity_kw
        gen_units = volt_mod.feeder(kw, system.n_genset, lv_v, 3, 0.8)
        i = cab_mod.design_current(gen_units["unit_kw"],
                                   gen_units["voltage_v"], 3, 0.8)
        cab, prot = coordinate(
            i, lengths["genset"], gen_units["voltage_v"], 3,
            max_voltage_drop_pct=2.5, ambient_c=ambient_max_c,
            power_factor=0.8, fault_current_a=fault_level_ka * 1000,
            device="mccb", curve="D",
        )
        summary = system.genset.annual_summary(system.n_genset, result.genset)
        design["genset"] = {
            "capacity_kw": kw, "units": system.n_genset,
            "unit_kw": system.genset.rated_kw,
            "cable": cab, "ac_protection": prot,
            "run_hours": summary["run_hours"],
            "starts": summary["starts"],
            "fuel_units": summary["fuel_units"],
            "mean_load_ratio": summary["mean_load_ratio"],
            "low_load_hours": summary["low_load_hours"],
            "feeders": gen_units,
        }
        schedules["cables"].append(
            _cable_row("Generator AC", cab, circuits=gen_units["units"], feeders=gen_units)
        )
        schedules["protection"].append(
            _prot_row("Generator AC", prot, circuits=gen_units["units"])
        )
        if summary["low_load_hours"] > 200:
            design["warnings"].append(
                f"The generator runs below its minimum load ratio for "
                f"{summary['low_load_hours']} hours a year. Extended light "
                f"loading causes wet stacking and cylinder glazing. Consider "
                f"a smaller unit, or a load bank."
            )

    # ---------------------------------------------------------------- grid
    if system.grid and not system.grid.is_islanded:
        conn_v = dist["connection_voltage_v"]
        pcc = inv_mod.size_grid_interface(
            result.grid_import, result.grid_export, conn_v, power_factor,
        )
        i = pcc["line_current_a"]
        pcc["connection_voltage_v"] = conn_v
        cab, prot = coordinate(
            i, lengths["grid"], conn_v, 3,
            max_voltage_drop_pct=1.0, ambient_c=ambient_max_c,
            fault_current_a=fault_level_ka * 1000,
            device="mccb" if i > 100 else "mcb",
        )
        pcc["protection"] = prot
        pcc["cable"] = cab
        design["grid"] = pcc
        schedules["cables"].append(_cable_row("Grid PCC", cab))
        schedules["protection"].append(_prot_row("Grid PCC", prot))

    # ---------------------------------------------------------------- load
    peak = result.totals["peak_load_kw"]
    # A site load genuinely can be split across more distribution boards,
    # unlike a machine, so this one is allowed to divide.
    load_units = volt_mod.split_into_units(peak, 1, lv_v, 3, power_factor,
                                           allow_split=True)
    i_load = cab_mod.design_current(
        load_units["unit_kw"], lv_v, 3, power_factor
    )
    cab_load, prot_load = coordinate(
        i_load, lengths["load"], lv_v, 3,
        max_voltage_drop_pct=4.0, ambient_c=ambient_max_c,
        fault_current_a=fault_level_ka * 1000,
        device="mccb" if i_load > 100 else "mcb",
    )
    design["load"] = {
        "peak_kw": peak,
        "annual_mwh": result.totals["load_kwh"] / 1000.0,
        "cable": cab_load, "protection": prot_load,
        "load_factor": (
            (result.totals["load_kwh"] / len(result.load)) / peak
            if peak > 0 else 0.0
        ),
        "feeders": load_units,
    }
    schedules["cables"].append(
        _cable_row("Main load feeder", cab_load, circuits=load_units["units"])
    )
    schedules["protection"].append(
        _prot_row("Main load feeder", prot_load, circuits=load_units["units"])
    )

    # ------------------------------------------------------------------ EV
    if system.ev and system.n_chargers > 0:
        total_kw = system.n_chargers * system.ev.charger_kw
        # Diversity: not every charger draws full power simultaneously.
        diversity = 0.7 if system.n_chargers > 4 else 1.0
        design_kw = total_kw * diversity
        ev_units = volt_mod.split_into_units(design_kw, 1, lv_v, 3, 0.99,
                                             allow_split=True)
        i = cab_mod.design_current(ev_units["unit_kw"], lv_v, 3, 0.99)
        cab, prot = coordinate(
            i, lengths["ev"], lv_v, 3,
            max_voltage_drop_pct=3.0, ambient_c=ambient_max_c,
            fault_current_a=fault_level_ka * 1000,
            device="mccb" if i > 100 else "mcb",
        )
        design["ev"] = {
            "chargers": system.n_chargers,
            "charger_kw": system.ev.charger_kw,
            "total_kw": total_kw,
            "diversity_factor": diversity,
            "design_kw": design_kw,
            "cable": cab, "protection": prot,
            "rcd": prot_mod.select_rcd("final"),
            "v2g": system.ev.v2g,
        }
        schedules["cables"].append(_cable_row("EV charger feeder", cab))
        schedules["protection"].append(_prot_row("EV charger feeder", prot))
        design["diagram_notes"].append(
            f"EV feeder sized with a {diversity:.2f} diversity factor on "
            f"{system.n_chargers} chargers."
        )

    # ----------------------------------------------- low to medium voltage
    # A plant too large for a low-voltage connection is collected at low
    # voltage in blocks and stepped up. The transformers are part of the
    # design, not an afterthought for the drawing, so they are sized here
    # and appear in the schedules like everything else.
    if dist["is_mv"]:
        # A wind turbine carries its own transformer up the tower; inverters
        # and battery containers are grouped into stations. Counting every
        # machine as its own block gave a 5 MW PV field fifty step-up
        # transformers, which is not a plant, it is an arithmetic error with
        # a drawing attached.
        PER_MACHINE = {"wind"}
        machine_v = lv_v
        groups = []
        for key, label in (("pv", "PV inverter station"),
                           ("wind", "Wind turbine"),
                           ("battery", "Battery block"),
                           ("genset", "Generator")):
            rec = design.get(key)
            f = rec.get("feeders") if rec else None
            if not f:
                continue
            machine_v = max(machine_v, f.get("voltage_v", lv_v))
            groups.append((label, f["units"] * f["unit_kw"], f["units"],
                           key in PER_MACHINE))
        rows, n_tx, total_kva = volt_mod.step_up_groups(groups, power_factor)
        mv_v = dist["mv_voltage_v"]
        i_mv = volt_mod.current_for(plant_kw, mv_v, 3, power_factor)
        cab_mv = cab_mod.size_cable(
            i_mv, lengths.get("mv_collector", 300), mv_v, 3,
            max_voltage_drop_pct=2.0, ambient_c=ambient_max_c,
        )
        biggest = max(rows, key=lambda r: r["kva_each"]) if rows else None
        design["step_up"] = {
            "transformers": n_tx,
            "groups": rows,
            # The scalar rating is the largest block, kept because a
            # drawing needs one number for the symbol it puts on the page.
            # `groups` is the answer; this is the headline.
            "kva_each": biggest["kva_each"] if biggest else 0.0,
            "mixed_ratings": len({r["kva_each"] for r in rows}) > 1,
            "total_kva": total_kva,
            "vector_group": "Dyn11",
            "impedance_pct": biggest["impedance_pct"] if biggest else 6.0,
            "lv_voltage_v": machine_v,
            "mv_voltage_v": mv_v,
            "lv_current_a": volt_mod.current_for(
                (biggest["kva_each"] if biggest else 0.0) * power_factor,
                machine_v, 3, power_factor
            ),
            "mv_current_a": i_mv,
            "cable": cab_mv,
        }
        schedules["cables"].append(
            _cable_row(f"MV collector {mv_v / 1000:,.0f} kV", cab_mv)
        )
        design["assumptions"].append(dist["reason"])
        ducted = [r["circuit"] for r in schedules["cables"] if r.get("busduct")]
        if ducted:
            design["assumptions"].append(
                "The following connections carry more current than cable can "
                "practically be terminated for and are shown as bus duct or "
                "busbar trunking rather than conductors in parallel: "
                + ", ".join(ducted) + "."
            )
        listed = ", ".join(
            f"{r['count']} x {r['kva_each']:,.0f} kVA ({r['name'].lower()})"
            for r in rows
        ) or "step-up"
        design["warnings"].append(
            f"A medium-voltage connection needs the network operator's "
            f"protection settings, an earthing study and a grid-code "
            f"compliance assessment. The step-up ({listed}) and the "
            f"{mv_v / 1000:,.0f} kV switchgear shown are typical selections "
            f"and must be confirmed with them."
        )

    # ----------------------------------------------------- system-wide items
    design["spd_ac"] = prot_mod.select_spd(
        "main", system_voltage_v=system_voltage_v
    )
    design["rcd"] = prot_mod.select_rcd(
        "general", has_transformerless_inverter=bool(design.get("pv"))
    )
    schedules["isolation"] = prot_mod.isolation_requirements(
        has_pv=bool(design.get("pv")),
        has_battery=bool(design.get("battery")),
        has_genset=bool(design.get("genset")),
        has_grid=bool(design.get("grid")),
        array_voltage_v=(
            design.get("pv", {}).get("strings", {}).get("string_voc_cold_v", 1000)
            if design.get("pv") else 1000
        ),
    )

    # Selectivity between the main incomer and the largest outgoing feeder.
    # Selectivity is only meaningful between devices in series on the path
    # from the supply to a load. A PV, wind, battery or generator feeder is a
    # SOURCE: it sits in parallel with the grid incomer on the busbar, not
    # downstream of it, so comparing their ratings says nothing about
    # discrimination and would raise a warning about a perfectly sound design.
    main = design.get("grid", {}).get("protection")
    if main:
        load_circuits = {"Main load feeder", "EV charger feeder"}
        biggest = max(
            (r for r in schedules["protection"] if r["circuit"] in load_circuits),
            key=lambda r: r["rating_a"], default=None,
        )
        if biggest:
            disc = prot_mod.check_discrimination(
                main["rating_a"], biggest["rating_a"]
            )
            design["discrimination"] = disc
            if not disc["selective"]:
                design["warnings"].append(disc["reason"])

    design["schedules"] = schedules
    design["totals"] = {
        "converter_kw": sum(
            filter(None, [
                design.get("pv", {}).get("inverter", {}).get("total_ac_kw"),
                design.get("battery", {}).get("pcs", {}).get("rated_kw"),
                design.get("wind", {}).get("converter", {}).get("rated_kw"),
            ])
        ),
        "cable_runs": len(schedules["cables"]),
        "protection_devices": len(schedules["protection"]),
        "copper_kg_estimate": _copper_estimate(schedules["cables"]),
    }
    design["diagram_notes"].append(
        f"All ratings derived from the {system_voltage_v:.0f} V three-phase "
        f"design at {power_factor:.2f} power factor; fault level "
        f"{fault_level_ka:.0f} kA."
    )
    return design


def coordinate(design_current_a, length_m, voltage_v, phases=3,
               max_voltage_drop_pct=2.0, ambient_c=30, power_factor=0.95,
               fault_current_a=None, device="mcb", curve="C",
               max_passes=4, **cable_kwargs):
    """
    Size a conductor and its protective device together.

    Doing these in one pass is wrong and is the most common coordination
    error in a hand calculation. The sequence must be:

        I_B  ->  choose I_N  ->  size the cable so that I_N <= I_Z

    Sizing the cable for I_B alone frequently leaves it one standard step
    below the breaker that has to protect it, because device ratings and
    conductor ampacities step on different scales. The loop below re-sizes
    the conductor against the chosen device and re-selects until the pair
    satisfies both IEC 60364-4-43 conditions.
    """
    target = design_current_a
    cab = prot = None

    for _ in range(max_passes):
        cab = cab_mod.size_cable(
            target, length_m, voltage_v, phases,
            max_voltage_drop_pct=max_voltage_drop_pct,
            ambient_c=ambient_c, power_factor=power_factor,
            fault_current_a=fault_current_a, **cable_kwargs
        )
        if cab is None:
            return None, None
        prot = prot_mod.select_overcurrent(
            design_current_a, cab["ampacity_a"], device=device, curve=curve
        )
        if prot["compliant"]:
            break
        # The device the design current demands is larger than the conductor
        # can carry. Re-size the conductor for the device rating, with the
        # 1.45 margin the second condition needs.
        needed = max(prot["rating_a"], prot["i2_a"] / 1.45)
        if needed <= target * 1.001:
            break                     # no progress possible, report as-is
        target = needed

    return cab, prot


def _cable_row(name, cab, circuits=1, feeders=None):
    """
    One row of the cable schedule.

    `circuits` is how many identical runs of this cable the installation
    has — one per turbine, per inverter, per converter block. Stating it
    keeps the schedule honest about quantity without pretending the whole
    technology shares one enormous conductor.
    """
    if not cab:
        return {"circuit": name, "csa_mm2": None, "circuits": circuits}
    # A connection carrying more than a couple of thousand amps is not
    # cable. It is bus duct or busbar, and saying so is more useful than
    # quoting six conductors in parallel that nobody would install.
    busduct = bool(feeders and feeders.get("busbar"))
    return {
        "circuit": name,
        "circuits": int(circuits),
        "busduct": busduct,
        "csa_mm2": cab["csa_mm2"],
        "runs": cab["parallel_runs"],
        "material": cab["material"],
        "insulation": cab["insulation"],
        "length_m": cab["length_m"],
        "current_a": round(cab["current_a"], 1),
        "ampacity_a": round(cab["ampacity_a"], 1),
        "voltage_drop_pct": round(cab["voltage_drop_pct"], 2),
        "governing": cab["governing_criterion"],
    }


def _prot_row(name, prot, circuits=1):
    return {
        "circuit": name,
        "circuits": int(circuits),
        "device": prot["device"],
        "rating_a": prot["rating_a"],
        "curve": prot.get("curve"),
        "compliant": prot["compliant"],
        "design_current_a": round(prot["design_current_a"], 1),
    }


def _copper_estimate(cable_rows):
    """
    Rough conductor mass, for a cost sanity check.

    Copper density 8960 kg/m3; four conductors per three-phase run (3 + N).
    """
    total = 0.0
    for r in cable_rows:
        csa = r.get("csa_mm2")
        if not csa:
            continue
        runs = r.get("runs", 1) or 1
        circuits = r.get("circuits", 1) or 1
        length = r.get("length_m", 0) or 0
        cores = 4
        total += csa * 1e-6 * length * runs * circuits * cores * 8960.0
    return round(total, 1)
