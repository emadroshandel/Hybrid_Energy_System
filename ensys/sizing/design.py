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


from . import standards as std_mod
from . import iec60909 as iec60909_mod

# Fault contribution of each kind of source, multiples of rated current.
# Inverter-based sources are current-limited by their controls: 1.1-2 pu
# is typical, 1.5 is used (IEC TS 62257-7-1 4.1.8 asks for the sources of
# prospective fault current to be identified). A synchronous generator
# feeds roughly 1/x"d in the first cycles, about 6-7 pu for x"d = 0.15,
# falling to about 3 pu sustained with a PMG/AREP excitation.
INVERTER_FAULT_PU = 1.5
GENSET_SUBTRANSIENT_PU = 1.0 / 0.15
GENSET_SUSTAINED_PU = 3.0


def _current(kva, v):
    return kva * 1000.0 / (math.sqrt(3) * v) if v > 0 else 0.0


def build_busbar(system, grid_connected, fault_level_ka, lv_v, dist,
                 power_factor=0.95, converter_fault_pu=1.2,
                 battery_fault_pu=1.5, generator_xd=0.15,
                 voltage_tolerance="lv6", network_r_over_x=0.1):
    """
    The LV main busbar as an IEC 60909-0 source model.

    Network: the operator's three-phase fault level at the PCC. For an
    MV-connected plant that level is at MV and reaches an LV block through
    its step-up transformer (6.3.3, with K_T). Generators: impedance with
    K_G (6.8.2). PV, wind and battery converters: current sources of
    `k` x rated current (5.2.3, 6.10.2) - the manufacturer's figure should
    replace the default; PV and wind are left out of the minimum (7.1.3 d).
    In an island there is no network: the battery inverter and the
    generators are all there is, which is what decides whether breakers
    can operate at all.
    """
    bus = iec60909_mod.Busbar(lv_v, voltage_tolerance)
    pv_kva = (system.pv_capacity_kwp / 1.2 / power_factor) if system.pv else 0.0
    wind_kva = (system.wind_capacity_kw / power_factor) if system.wind else 0.0
    bat_kva = (system.battery_power_kw / power_factor) if system.battery else 0.0

    if grid_connected and fault_level_ka:
        if dist.get("is_mv"):
            block = min(volt_mod.MAX_BLOCK_KVA,
                        max(500.0, pv_kva + wind_kva + bat_kva))
            block = volt_mod.standard_transformer(block)
            uk = 6.0 if block <= 1000 else 6.5
            bus.add_network(fault_level_ka, network_r_over_x,
                            via_transformer=(block, uk, 1.0),
                            hv_v=dist.get("mv_voltage_v") or 20000.0)
        else:
            bus.add_network(fault_level_ka, network_r_over_x)
    if system.genset and system.n_genset:
        bus.add_generator(system.genset.rated_kw / 0.8, system.n_genset,
                          generator_xd, 0.8)
    if pv_kva:
        bus.add_converter("PV inverters", pv_kva, converter_fault_pu, True)
    if wind_kva:
        bus.add_converter("wind converters", wind_kva, converter_fault_pu, True)
    if bat_kva:
        bus.add_converter("battery PCS", bat_kva, battery_fault_pu, False)
    return bus


def _annual_loss_kwh(series_kw, units, cab, voltage_v, phases=3, pf=0.95):
    """Resistive loss over the year in `units` identical circuits."""
    if not cab or not series_kw or units <= 0:
        return 0.0
    csa = cab["csa_mm2"]
    runs = cab.get("parallel_runs", 1)
    length = cab["length_m"]
    total = 0.0
    for p in series_kw:
        if p <= 0:
            continue
        per = p / units
        if phases == 3:
            i = per * 1000.0 / (math.sqrt(3) * voltage_v * pf)
        else:
            i = per * 1000.0 / voltage_v
        total += cab_mod.conductor_loss_kw(csa, i, length, phases,
                                           cab.get("material", "copper"), runs)
    return total * units


def size_system(system, result, location=None, system_voltage_v=400,
                power_factor=0.95, module=None, ambient_max_c=45,
                ambient_min_c=-5, cable_lengths=None, fault_level_ka=10.0,
                frequency_hz=50.0, earthing="TN-S", pv_dc_series=None,
                cell_temp_max_c=None, installation=None,
                wind_mean_hub_ms=None, genset_derating=None,
                cable_install=None, short_circuit=None,
                pv_poa_series=None, pv_cell_temps=None, stand_alone=None):
    """
    Produce the full electrical design record.

    Parameters
    ----------
    system : the optimised SystemConfig
    result : its DispatchResult
    cable_lengths : {feeder: metres}; sensible defaults are used where absent,
        and the report flags that they are assumptions rather than survey data
    pv_dc_series : hourly DC output of the whole array, before the inverter.
        The dispatch carries AC power; the inverter and string design need
        the DC side. Reconstructed from AC / efficiency when absent.
    cell_temp_max_c : hottest modelled cell temperature (from the hourly
        Faiman model). Used for the Vmp-hot string limit in place of the
        ambient + 25 C rule when available.
    installation : {"dwelling": bool, "ess_location": key of NFPA 855 Table
        15.5.2} for the storage siting checks.
    """
    lengths = {
        "pv_dc": 60, "pv_ac": 25, "wind": 80, "battery": 15,
        "genset": 20, "grid": 30, "load": 40, "ev": 50, "ev_final": 15,
    }
    lengths.update(cable_lengths or {})
    installation = dict(installation or {})

    module = module or inv_mod.PVModule()
    grid_connected = bool(system.grid and not system.grid.is_islanded)

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

    ambient_min_c = round(float(ambient_min_c), 1)
    ambient_max_c = round(float(ambient_max_c), 1)

    sc = dict(short_circuit or {})
    bus = build_busbar(
        system, grid_connected, fault_level_ka, lv_v, dist, power_factor,
        converter_fault_pu=float(sc.get("converter_fault_pu", 1.2) or 1.2),
        battery_fault_pu=float(sc.get("battery_fault_pu", 1.5) or 1.5),
        generator_xd=float(sc.get("generator_xd", 0.15) or 0.15),
        voltage_tolerance=sc.get("voltage_tolerance", "lv6"),
        network_r_over_x=float(sc.get("network_r_over_x", 0.1) or 0.1),
    )
    busfault = bus.fault()
    i_fault = busfault["ik3_max_a"]
    i_fault_min = busfault["ik3_min_a"]
    faults = {
        "maximum_a": i_fault,
        "minimum_a": i_fault_min,
        "earth_fault_min_a": busfault["ik1_min_a"],
        "peak_a": busfault["ip_a"],
        "kappa": busfault["kappa"],
        "basis": bus.notes,
        "islanded": not grid_connected,
        "c_max": bus.c["max"], "c_min": bus.c["min"],
        "standard": "IEC 60909-0:2026",
    }

    # Cable installation (IEC 60364-5-52 Annex B). One reference method for
    # the installation, optionally overridden per circuit, plus the ground
    # conditions for buried methods and whether identical circuits of one
    # technology share a route (and therefore a grouping factor).
    ci = dict(cable_install or {})
    per_circuit = dict(ci.get("methods") or {})
    group_identical = bool(ci.get("group_identical", True))

    def ck(key, n_identical=1):
        return {
            "method": per_circuit.get(key) or ci.get("method") or "auto",
            "insulation": ci.get("insulation") or "xlpe",
            "material": ci.get("material") or "copper",
            "ground_temp_c": float(ci.get("ground_temp_c", 20.0) or 20.0),
            "soil_resistivity": float(ci.get("soil_resistivity", 2.5) or 2.5),
            "n_circuits": min(20, max(1, int(n_identical))) if group_identical else 1,
        }

    design = {
        "distribution": dist,
        "system_voltage_v": system_voltage_v,
        "power_factor": power_factor,
        "frequency_hz": float(frequency_hz or 50.0),
        "earthing_system": earthing or "TN-S",
        # The busbar's prospective fault, which is what switchgear Icu is
        # specified against. Previously the PCC figure entered by the user.
        "fault_level_ka": max(1.0, i_fault / 1000.0),
        "faults": faults,
        "grid_fault_level_ka": fault_level_ka,
        "design_temperatures": {"ambient_min_c": ambient_min_c,
                                "ambient_max_c": ambient_max_c},
        "assumptions": [],
        "warnings": [],
        "diagram_notes": [],
        "compliance": [],
    }
    compliance = design["compliance"]
    if not cable_lengths:
        design["assumptions"].append(
            "Cable route lengths are default estimates, not survey data. "
            "Voltage-drop results and conductor sizes will change once actual "
            "routes are measured."
        )
    design["assumptions"].append(
        f"Short-circuit currents to IEC 60909-0: {i_fault / 1000:.1f} kA "
        f"maximum (peak {busfault['ip_a'] / 1000:.1f} kA) and "
        f"{i_fault_min / 1000:.2f} kA minimum at the LV busbar "
        f"({'; '.join(faults['basis']) or 'no sources'}). Conductors are "
        f"checked against the let-through energy of their protective device "
        f"(IEC 60364-4-43 434.5.2) and each circuit's minimum earth-fault "
        f"current against its disconnection time (IEC 60364-4-41)."
    )

    schedules = {"cables": [], "protection": [], "isolation": []}
    losses = []          # (circuit, kWh/yr, kWh carried)

    def _coord(i, length, v, phases=3, key=None, n_identical=1,
               final_circuit=False, **kw):
        kw = {**ck(key, n_identical), **kw}
        if kw.get("method") == "auto":
            # Usual practice: small circuits are multi-core cable clipped
            # or on a wall (C); large feeders are single-core cables in
            # trefoil on a ladder (F), which is also where Table B.52.12
            # gives sizes up to 630 mm2.
            kw["method"] = "C" if i <= 125 else "F"
        cab, prot = coordinate(i, length, v, phases, fault_current_a=i_fault,
                               min_fault_current_a=None,
                               peak_current_a=busfault["ip_a"], **kw)
        if cab and prot and abs(v - lv_v) < 1e-6:
            # Fault at the far end of this circuit (IEC 60909-0 with the
            # cable's impedance, 7.1.3 f) and the disconnection check of
            # IEC 60364-4-41 411.3.2 for a TN system.
            end = bus.at_circuit_end(cab["csa_mm2"], length,
                                     cab.get("material", "copper"),
                                     cab.get("parallel_runs", 1))
            t_req = iec60909_mod.disconnection_time_s(prot["rating_a"],
                                                      final_circuit)
            ok = iec60909_mod.device_operates(
                prot["device"], prot["rating_a"], prot.get("curve"),
                end["ik1_min_a"], t_req)
            prot["end_fault"] = {k: v_ for k, v_ in end.items()}
            prot["disconnection"] = {
                "required_s": t_req, "ok": ok,
                "ik1_min_a": end["ik1_min_a"],
                "earthing": earthing,
                "final_circuit": final_circuit,
            }
            prot["instantaneous_trip"] = ok
        return cab, prot

    def _prot_warnings(prot, circuit):
        if not prot:
            return
        if not prot["compliant"]:
            design["warnings"] += [f"{circuit}: {r}" for r in prot["reasons"]]
        b = prot.get("breaking")
        if b and not b["adequate"]:
            design["warnings"].append(
                f"{circuit}: the prospective fault ({b['prospective_fault_ka']:.1f} kA) "
                f"exceeds the {b['breaking_capacity_ka']:.0f} kA breaking "
                f"capacity of the largest device class. Use current-limiting "
                f"fuses or cascade/back-up protection (IEC 60364-4-43 434.5.1)."
            )
        d = prot.get("disconnection")
        if d and not d["ok"]:
            design["warnings"].append(
                f"{circuit}: the minimum earth-fault current at the far end is "
                f"{d['ik1_min_a']:,.0f} A, below the "
                f"{prot['magnetic_trip_range_a'][1]:,.0f} A needed to trip the "
                f"{prot['rating_a']} A device within {d['required_s']:g} s "
                f"(IEC 60364-4-41 411.3.2). Use residual-current protection for "
                f"automatic disconnection, a lower instantaneous pickup (B curve "
                f"or an electronic trip unit), a larger conductor"
                + (", or inverters with a higher short-circuit capability."
                   if not grid_connected else ".")
            )
        if prot.get("peak_ok") is False:
            design["warnings"].append(
                f"{circuit}: the peak short-circuit current "
                f"{busfault['ip_a'] / 1000:.1f} kA exceeds the device's making "
                f"capacity (IEC 60947-2 n x Icu)."
            )

    # ------------------------------------------------------------------ PV
    if system.pv and system.n_pv > 0:
        kwp = system.pv_capacity_kwp
        eta_inv = float(getattr(system.pv, "inverter_efficiency", 1.0) or 1.0)
        if pv_dc_series:
            dc_series = list(pv_dc_series)
        else:
            # Reconstruct DC from the AC bus series; clipped hours come back
            # at the clip level, which is the conservative direction.
            dc_series = [p / eta_inv for p in result.pv]

        invspec = inv_mod.size_pv_inverter(
            dc_series, kwp,
            target_dc_ac=float(getattr(system.pv, "dc_ac_ratio", 1.2) or 1.2),
            inverter_efficiency=eta_inv, max_clipping_loss=0.02,
        )
        # The MPPT limits belong to the inverter that was actually selected,
        # and the array is divided across however many units there are.
        mppt = inv_mod.mppt_config(invspec["rated_ac_kw"])

        # Design temperatures (IEC TS 62257-7-1 4.1.9): Voc at the lowest
        # expected ambient, Vmp at the hottest cell temperature. The modelled
        # hourly cell temperature is used when it is hotter than the rule of
        # thumb, so a desert site is not designed against a temperate one.
        t_cell_hot = ambient_max_c + 25.0
        if cell_temp_max_c is not None:
            t_cell_hot = max(t_cell_hot, float(cell_temp_max_c) + 5.0)
        t_cell_hot = round(t_cell_hot, 1)
        design["design_temperatures"].update({
            "cell_max_c": t_cell_hot,
            "pv_cable_c": ambient_max_c + 40.0,
        })
        strings = inv_mod.design_strings(
            kwp / max(1, invspec["units"]), module,
            mppt_v_min=mppt["mppt_v"][0], mppt_v_max=mppt["mppt_v"][1],
            inverter_v_max=mppt["v_max"],
            t_min_c=ambient_min_c,
            t_max_cell_c=t_cell_hot,
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
        cab_ac, prot_ac = _coord(
            i_ac, lengths["pv_ac"], pv_units["voltage_v"], 3,
            key="pv_ac",
            # Inverter stations of an MV plant stand apart in the field;
            # string inverters of an LV plant share a route to the board.
            n_identical=1 if dist["is_mv"] else pv_units["units"],
            max_voltage_drop_pct=2.0, ambient_c=ambient_max_c,
            device="mccb" if i_ac > 100 else "mcb",
            operating_current_a=i_ac / 1.25,
        )

        # String protection first: the string cable is rated for the fuse
        # when there is one (IEC 62548-1:2023 Table 5).
        np_ = strings["strings_per_mppt"] if strings else 1
        string_fuse = prot_mod.select_pv_string_fuse(
            module.isc, np_, module_fuse_rating_a=module.max_series_fuse_a,
        )
        if string_fuse.get("warning"):
            design["warnings"].append("PV strings: " + string_fuse["warning"])

        # PV cable (EN 50618) behind the modules runs at ambient + 40 C
        # (IEC TS 62257-7-1 Table 6 note a), checked for volt drop at Imp.
        i_dc = prot_mod.pv_string_cable_current(module.isc, np_, string_fuse,
                                                k_i=getattr(module, "k_i", 1.25))
        v_dc = strings["string_vmp_stc_v"] if strings else 600
        cab_dc = cab_mod.size_cable(
            i_dc, lengths["pv_dc"], v_dc, phases=0,
            max_voltage_drop_pct=1.0, ambient_c=ambient_max_c + 40,
            insulation="pv", operating_current_a=module.imp,
        )

        voc_max = strings["string_voc_cold_v"] if strings else 1000
        design["pv"] = {
            "capacity_kwp": kwp,
            "units": system.n_pv,
            "unit_kwp": system.pv.capacity_kwp,
            "tilt_deg": system.pv.resolved_tilt(
                location.latitude if location else 30.0
            ),
            "azimuth_deg": system.pv.azimuth_deg,
            "module": module.to_dict(),
            "inverter": invspec,
            "mppt_class": mppt,
            "strings": strings,
            "dc_cable": cab_dc,
            "dc_cable_design_current_a": i_dc,
            "ac_cable": cab_ac,
            "ac_protection": prot_ac,
            "string_fuse": string_fuse,
            "spd_dc": prot_mod.select_spd(dc_side=True, array_voltage_v=voc_max),
            "annual_dc_kwh": invspec["annual_dc_kwh"],
            "annual_ac_kwh": invspec["annual_ac_kwh"],
            "feeders": pv_units,
        }
        schedules["cables"] += [
            _cable_row("PV DC string", cab_dc,
                       circuits=(strings or {}).get("array_strings_total",
                                                    pv_units["units"])),
            _cable_row("PV inverter AC", cab_ac, circuits=pv_units["units"], feeders=pv_units),
        ]
        schedules["protection"].append(
            _prot_row("PV inverter AC", prot_ac, circuits=pv_units["units"])
        )
        if strings and strings["errors"]:
            design["warnings"] += strings["errors"]
        design["warnings"] += invspec["notes"]
        _prot_warnings(prot_ac, "PV inverter AC")

        # Accumulated volt drop string -> inverter -> PCC.
        segs = [("DC string", cab_dc["voltage_drop_pct"] if cab_dc else 0.0),
                ("inverter AC", cab_ac["voltage_drop_pct"] if cab_ac else 0.0)]
        design["pv"]["_vd_segments"] = segs

        # Cable losses over the year.
        if strings and cab_dc:
            n_str = strings["array_strings_total"]
            imp_rated = module.imp
            p_rated = max(1e-9, kwp)
            loss = 0.0
            r = cab_mod.conductor_loss_kw(cab_dc["csa_mm2"], 1.0,
                                          cab_dc["length_m"], 0)
            for p in dc_series:
                if p > 0:
                    i = imp_rated * p / p_rated
                    loss += r * i * i
            losses.append(("PV DC strings", loss * n_str, sum(dc_series)))
        losses.append(("PV inverter AC",
                       _annual_loss_kwh(result.pv, pv_units["units"], cab_ac,
                                        pv_units["voltage_v"], 3, power_factor),
                       sum(result.pv)))

        if pv_poa_series:
            perf = std_mod.pv_yields_61724(
                kwp, pv_poa_series, dc_series, list(result.pv),
                pv_cell_temps, getattr(system.pv, "temp_coeff_pmax", -0.0035))
            design["pv"]["performance"] = perf
            compliance.append(std_mod._rec(
                "IEC 61724-1:2021", "13.6 / 13.7 / 14.3",
                "PV yields and performance ratio", std_mod.INFO,
                f"Reference yield Yr {perf['yr_h']:,.0f} h, array yield Ya "
                f"{perf['ya_h']:,.0f} h, final yield Yf {perf['yf_h']:,.0f} h "
                f"(kWh/kWp); capture loss Lc {perf['lc_h']:,.0f} h, BOS loss "
                f"Ls {perf['ls_h']:,.0f} h. PR {100 * perf['pr']:.1f}%, "
                f"temperature-corrected PR'25C {100 * perf['pr_25c']:.1f}%. "
                f"These are the expected values a monitoring system "
                f"(IEC 61724-1 Class A/B) should be checked against.",
                **{k: v for k, v in perf.items()
                   if k != "standard" and not isinstance(v, list)}))

        if grid_connected:
            rc = std_mod.ess_reactive_capability(
                ac_kw, ac_kw, "B", name="PV inverter")
            # For PV the category is set by the network operator; storage is
            # where IEEE 1547.9 recommends B. Reported, not failed.
            if rc["status"] != std_mod.PASS:
                rc["status"] = std_mod.INFO
                rc["detail"] = (
                    f"If the network operator requires Category B reactive "
                    f"power (PF 0.90 at full output), specify inverters of "
                    f"at least {rc['values']['required_kva']:,.0f} kVA for "
                    f"{ac_kw:,.0f} kW, or accept active-power curtailment "
                    f"when reactive power is called for.")
            compliance.append(rc)

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
        cab, prot = _coord(
            i, lengths["wind"], wind_units["voltage_v"], 3,
            key="wind", n_identical=1,
            max_voltage_drop_pct=2.0, ambient_c=ambient_max_c,
            device="mccb" if i > 100 else "mcb", curve="D",
            operating_current_a=i / 1.25,
        )
        design["wind"] = {
            "capacity_kw": kw, "units": system.n_wind,
            "unit_kw": system.wind.rated_kw,
            "hub_height_m": system.wind.hub_height_m,
            "converter": conv, "cable": cab, "ac_protection": prot,
            "annual_kwh": sum(result.wind),
            "feeders": wind_units,
            "availability": getattr(system.wind, "availability", 1.0),
        }
        schedules["cables"].append(
            _cable_row("Wind turbine AC", cab, circuits=wind_units["units"], feeders=wind_units)
        )
        schedules["protection"].append(
            _prot_row("Wind turbine AC", prot, circuits=wind_units["units"])
        )
        _prot_warnings(prot, "Wind turbine AC")
        design["diagram_notes"].append(
            "Wind feeder protection uses a type D curve to ride through "
            "generator inrush at cut-in."
        )
        losses.append(("Wind AC", _annual_loss_kwh(
            result.wind, wind_units["units"], cab, wind_units["voltage_v"]),
            sum(result.wind)))
        if wind_mean_hub_ms:
            compliance.append(std_mod.wind_class_check(
                wind_mean_hub_ms, getattr(system.wind, "iec_class", None)))

    # ------------------------------------------------------------- battery
    if system.battery and system.n_battery > 0:
        pcs = inv_mod.size_battery_pcs(
            result.battery_charge, result.battery_discharge,
            system.battery_power_kw,
            efficiency=getattr(system.battery, "pcs_efficiency", 0.97),
        )
        # IEEE 1547.9 5.2: grid-connected storage should offer Category B
        # reactive power, +/-0.44 pu - PF 0.90 at full active power. The
        # converter is therefore rated in kVA for P / 0.90.
        if grid_connected:
            pcs["rated_kva"] = inv_mod.standard_size(pcs["rated_kw"] / 0.90)
        else:
            pcs["rated_kva"] = pcs["rated_kw"]
        bat_units = volt_mod.feeder(pcs["rated_kva"], system.n_battery, lv_v,
                                    3, 1.0)
        i = cab_mod.design_current(
            bat_units["unit_kw"], bat_units["voltage_v"], 3, 1.0
        )
        cab, prot = _coord(
            i, lengths["battery"], bat_units["voltage_v"], 3,
            key="battery", n_identical=bat_units["units"],
            max_voltage_drop_pct=1.5, ambient_c=ambient_max_c,
            device="mccb" if i > 100 else "mcb",
            operating_current_a=i / 1.25,
        )
        b = system.battery
        unit_kwh = b.nominal_energy_kwh
        total_kwh = system.battery_capacity_kwh
        eol = float(getattr(b, "eol_capacity_fraction", 0.8) or 0.8)
        dc_v = getattr(b, "dc_voltage_v", None) or (
            51.2 if unit_kwh <= 20 and total_kwh <= 40 else 800.0)
        dcf = std_mod.battery_dc_fault(unit_kwh, system.n_battery, dc_v)

        design["battery"] = {
            "capacity_kwh": total_kwh,
            "power_kw": system.battery_power_kw,
            "units": system.n_battery,
            "unit_kwh": unit_kwh,
            "chemistry": getattr(b, "chemistry", "generic"),
            "soc_window": [b.soc_min, b.soc_max],
            "round_trip_efficiency_poc": b.round_trip_efficiency,
            "pcs_efficiency": getattr(b, "pcs_efficiency", None),
            "auxiliary_w_per_kwh": getattr(b, "auxiliary_w_per_kwh", 0.0),
            "eol_capacity_fraction": eol,
            "usable_kwh_new": b.usable_energy_kwh(system.n_battery),
            "usable_kwh_eol": b.usable_energy_kwh(system.n_battery) * eol,
            "pcs": pcs, "cable": cab, "ac_protection": prot,
            "dc_fault": dcf,
            "cycles_per_year": b.equivalent_full_cycles(
                system.n_battery, result.totals["battery_discharge_kwh"]
            ),
            "expected_life_years": b.expected_life_years(
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
        _prot_warnings(prot, "Battery PCS AC")
        losses.append(("Battery AC", _annual_loss_kwh(
            [c + d for c, d in zip(result.battery_charge, result.battery_discharge)],
            bat_units["units"], cab, bat_units["voltage_v"], 3, 1.0),
            sum(result.battery_discharge) + sum(result.battery_charge)))

        if grid_connected:
            compliance.append(std_mod.ess_reactive_capability(
                pcs["rated_kw"], pcs["rated_kva"], "B"))
        compliance += std_mod.nfpa855_check(
            total_kwh, unit_kwh, getattr(b, "chemistry", "generic"),
            dwelling=bool(installation.get("dwelling")),
            location=installation.get("ess_location") or "exterior_wall",
            units=system.n_battery,
        )
        compliance.append(std_mod._rec(
            "UL 9540:2023 / IEC 62619 / IEC 63056", "-", "ESS listing",
            std_mod.INFO,
            "Specify the battery system listed to UL 9540 (with UL 9540A "
            "cell, module and unit fire-test data) or certified to IEC 62619 "
            "/ IEC 63056 for lithium cells in stationary storage.",
        ))
        if dcf:
            compliance.append(std_mod._rec(
                "IEC TS 62257-5:2015", "9.4.2.3", "Battery DC fault current",
                std_mod.INFO,
                f"At {dc_v:.0f} V DC each {unit_kwh:g} kWh unit is about "
                f"{dcf['unit_ah']:,.0f} Ah, so Ik ~ 10 x C = "
                f"{dcf['ik_unit_a'] / 1000:.1f} kA per unit and "
                f"{dcf['ik_bus_a'] / 1000:.1f} kA on a common DC bus. The "
                f"battery fuse or DC breaker at the terminals must be DC rated "
                f"and break this current.",
                **{k: v for k, v in dcf.items() if k != "standard"}))
        compliance.append(std_mod._rec(
            "IEC 62933-2-1", "5.2.3 / 5.2.4 / 5.2.6",
            "Storage parameters at the point of connection", std_mod.INFO,
            f"Round-trip efficiency at the POC {100 * b.round_trip_efficiency:.1f}% "
            f"(cell x PCS), auxiliaries "
            f"{getattr(b, 'auxiliary_w_per_kwh', 0.0):g} W/kWh, end-of-life "
            f"capacity {100 * eol:.0f}% - the end-of-life check re-runs the "
            f"design at that capacity.",
        ))

    # -------------------------------------------------------------- genset
    if system.genset and system.n_genset > 0:
        g = system.genset
        kw = system.genset_capacity_kw
        gen_units = volt_mod.feeder(kw, system.n_genset, lv_v, 3, 0.8)
        i = cab_mod.design_current(gen_units["unit_kw"],
                                   gen_units["voltage_v"], 3, 0.8)
        cab, prot = _coord(
            i, lengths["genset"], gen_units["voltage_v"], 3,
            key="genset", n_identical=gen_units["units"],
            max_voltage_drop_pct=2.5, ambient_c=ambient_max_c,
            power_factor=0.8, device="mccb", curve="D",
            operating_current_a=i / 1.25,
        )
        summary = g.annual_summary(system.n_genset, result.genset)
        # The derating the study simulated with (site altitude, hottest 1 %
        # of hours) when it is known; otherwise the design ambient.
        derate = genset_derating or std_mod.genset_site_derating(
            ambient_max_c, location.elevation_m if location else 0.0, 30.0,
        )
        design["genset"] = {
            "capacity_kw": kw, "units": system.n_genset,
            "unit_kw": g.rated_kw,
            "unit_kva": g.rated_kw / 0.8,
            "site_derate": getattr(g, "site_derate", 1.0),
            "site_derating": derate,
            "site_kw": kw * getattr(g, "site_derate", 1.0),
            "cable": cab, "ac_protection": prot,
            "run_hours": summary["run_hours"],
            "unit_run_hours": summary.get("unit_run_hours"),
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
        _prot_warnings(prot, "Generator AC")
        losses.append(("Generator AC", _annual_loss_kwh(
            result.genset, gen_units["units"], cab, gen_units["voltage_v"], 3, 0.8),
            sum(result.genset)))

        site = getattr(g, "site_derate", 1.0)
        compliance.append(std_mod._rec(
            "IEC TS 62257-7-3", "5.2.4 / Table 1", "Generator site derating",
            std_mod.INFO if site < 0.999 else std_mod.PASS,
            f"Available output {100 * site:.0f}% of the ISO 8528-1 rating at "
            f"{derate['conditions']['ambient_c']:.0f} C, "
            f"{derate['conditions']['altitude_m']:.0f} m, "
            f"{derate['conditions']['humidity_pct']:.0f}% RH "
            f"(temperature -{100 * derate['temperature_derate']:.1f}%, altitude "
            f"-{100 * derate['altitude_derate']:.1f}%, humidity "
            f"-{100 * derate['humidity_derate']:.1f}%). The simulation uses "
            f"the derated output.",
            factor=site))
        compliance.append(std_mod.genset_loading_check(
            result.genset, g.rated_kw, system.n_genset, site))
        if not grid_connected:
            peak_kva = result.totals["peak_load_kw"] / 0.8
            firm_kva = (g.rated_kw / 0.8) * system.n_genset * site
            if not system.battery:
                ok = firm_kva >= peak_kva
                compliance.append(std_mod._rec(
                    "IEC TS 62257-7-3", "5.2.2 a)", "Alternator apparent power",
                    std_mod.PASS if ok else std_mod.FAIL,
                    f"The alternator must supply the continuous and surge "
                    f"load VA: {firm_kva:,.0f} kVA site-rated against a "
                    f"{peak_kva:,.0f} kVA peak at PF 0.8, before motor "
                    f"starting surges.", firm_kva=firm_kva, peak_kva=peak_kva))
        if summary["low_load_hours"] > 200:
            design["warnings"].append(
                f"The generator runs below 50% of its running rating for "
                f"{summary['low_load_hours']} hours a year. Extended light "
                f"loading causes wet stacking and cylinder glazing "
                f"(IEC TS 62257-7-3 5.2.2 recommends 50-80%). Consider a "
                f"smaller unit, a second unequal set, or a load bank."
            )

    # ---------------------------------------------------------------- grid
    if grid_connected:
        conn_v = dist["connection_voltage_v"]
        pcc = inv_mod.size_grid_interface(
            result.grid_import, result.grid_export, conn_v, power_factor,
        )
        i = pcc["line_current_a"]
        pcc["connection_voltage_v"] = conn_v
        if dist.get("is_mv"):
            cab, prot = coordinate(
                i, lengths["grid"], conn_v, 3, max_voltage_drop_pct=1.0,
                ambient_c=ambient_max_c, device="mccb", **ck("grid"),
                fault_current_a=fault_level_ka * 1000.0,
                operating_current_a=i / 1.15,
            )
        else:
            cab, prot = _coord(
                i, lengths["grid"], conn_v, 3, key="grid",
                max_voltage_drop_pct=1.0, ambient_c=ambient_max_c,
                device="mccb" if i > 100 else "mcb",
                operating_current_a=i / 1.15,
            )
        pcc["protection"] = prot
        pcc["cable"] = cab
        pcc["interconnection"] = std_mod.interconnection_settings(frequency_hz)
        design["grid"] = pcc
        schedules["cables"].append(_cable_row("Grid PCC", cab))
        schedules["protection"].append(_prot_row("Grid PCC", prot))
        _prot_warnings(prot, "Grid PCC")
        losses.append(("Grid PCC", _annual_loss_kwh(
            [a + b for a, b in zip(result.grid_import, result.grid_export)],
            1, cab, conn_v), sum(result.grid_import) + sum(result.grid_export)))
        compliance.append(std_mod._rec(
            "IEEE Std 1547.9-2022 / IEC 62116", "6 / 8.1",
            "Interconnection protection", std_mod.INFO,
            "Anti-islanding within 2 s, voltage and frequency trip settings "
            "and ride-through to the network operator's requirements. "
            "Default IEEE 1547 settings are listed with the PCC; storage "
            "should meet ride-through Category III.",
        ))

    # ------------------------------------------------- stand-alone sizing
    if not grid_connected and system.battery and system.n_battery > 0:
        sa = dict(stand_alone or {})
        ren = [a + b for a, b in zip(result.pv or [0.0] * len(result.load),
                                     result.wind or [0.0] * len(result.load))]
        b = system.battery
        compliance.append(std_mod.stand_alone_check(
            list(result.load), ren, system.battery_capacity_kwh,
            b.soc_max - b.soc_min,
            float(getattr(b, "eol_capacity_fraction", 0.8) or 0.8),
            getattr(b, "chemistry", "generic"),
            min_battery_temp_c=float(sa.get("battery_min_temp_c",
                                            max(ambient_min_c, 0.0))),
            autonomy_days=float(sa.get("autonomy_days", 2.0) or 2.0),
            design_margin=float(sa.get("design_margin", 1.15) or 1.15),
            critical=bool(sa.get("critical", False)),
            inverter_efficiency=float(getattr(b, "pcs_efficiency", 0.95) or 0.95),
            has_generator=bool(system.genset and system.n_genset),
        ))

    # ---------------------------------------------------------------- load
    peak = result.totals["peak_load_kw"]
    # A site load genuinely can be split across more distribution boards,
    # unlike a machine, so this one is allowed to divide.
    load_units = volt_mod.split_into_units(peak, 1, lv_v, 3, power_factor,
                                           allow_split=True)
    i_load = cab_mod.design_current(
        load_units["unit_kw"], lv_v, 3, power_factor
    )
    # Microgrids: IEC TS 62257-9-2 Table 1 caps the main line at 6 %; the
    # 4 % used here leaves the remaining 2 % for final circuits.
    cab_load, prot_load = _coord(
        i_load, lengths["load"], lv_v, 3,
        key="load", n_identical=load_units["units"],
        max_voltage_drop_pct=4.0, ambient_c=ambient_max_c,
        device="mccb" if i_load > 100 else "mcb",
        operating_current_a=i_load / 1.25,
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
    _prot_warnings(prot_load, "Main load feeder")

    # ------------------------------------------------------------------ EV
    if system.ev and system.n_chargers > 0:
        total_kw = system.n_chargers * system.ev.charger_kw
        # IEC 60364-7-722 722.311: all connecting points can be used at once,
        # so the distribution circuit takes a diversity factor of 1 - unless
        # load control is available. A managed fleet (any scenario except
        # uncontrolled) has load control, and the dispatch shows the highest
        # aggregate charging power that control actually allows, which is
        # the figure the feeder must carry (with 10 % headroom). The old
        # blanket 0.7 for more than four chargers had no basis in either.
        managed = bool(getattr(system.ev, "shiftable", False))
        ev_peak = max((c + d for c, d in zip(result.ev_charge or [],
                                              result.ev_discharge or [])),
                      default=0.0)
        if managed and ev_peak > 0:
            design_kw = min(total_kw, ev_peak * 1.10)
        else:
            design_kw = total_kw
        diversity = design_kw / total_kw if total_kw else 1.0
        ev_units = volt_mod.split_into_units(design_kw, 1, lv_v, 3, 0.99,
                                             allow_split=True)
        i = cab_mod.design_current(ev_units["unit_kw"], lv_v, 3, 0.99)
        cab, prot = _coord(
            i, lengths["ev"], lv_v, 3, key="ev",
            max_voltage_drop_pct=3.0, ambient_c=ambient_max_c,
            device="mccb" if i > 100 else "mcb",
            operating_current_a=i / 1.25,
        )
        # 722.314.01: a dedicated final circuit per connecting point, demand
        # factor 1, its own 30 mA RCD (722.531.2.101).
        ckw = system.ev.charger_kw
        ph = 3 if ckw > 7.4 else 1
        v_final = lv_v if ph == 3 else lv_v / math.sqrt(3)
        i_f = cab_mod.design_current(ckw, v_final, ph, 0.99)
        cab_f, prot_f = _coord(
            i_f, lengths.get("ev_final", 15), v_final, ph, key="ev",
            n_identical=min(system.n_chargers, 6), final_circuit=True,
            max_voltage_drop_pct=2.0, ambient_c=ambient_max_c,
            device="mcb", operating_current_a=i_f / 1.25,
        )
        design["ev"] = {
            "chargers": system.n_chargers,
            "charger_kw": system.ev.charger_kw,
            "total_kw": total_kw,
            "diversity_factor": diversity,
            "load_control": managed,
            "observed_peak_kw": ev_peak,
            "design_kw": design_kw,
            "cable": cab, "protection": prot,
            "final_circuit": {"cable": cab_f, "protection": prot_f,
                              "phases": ph, "count": system.n_chargers},
            "rcd": {
                "type": "A + 6 mA DC detection, or B", "rating_ma": 30,
                "reason": (
                    "IEC 60364-7-722 722.531.2.101: each connecting point "
                    "has its own RCD of at least type A, 30 mA, with "
                    "protection against DC fault current above 6 mA (an "
                    "RDC-DD in the charger, or a type B RCD)."),
            },
            "v2g": system.ev.v2g,
        }
        schedules["cables"].append(_cable_row("EV charger feeder", cab))
        schedules["protection"].append(_prot_row("EV charger feeder", prot))
        schedules["cables"].append(_cable_row(
            "EV connecting point (each)", cab_f, circuits=system.n_chargers))
        schedules["protection"].append(_prot_row(
            "EV connecting point (each)", prot_f, circuits=system.n_chargers))
        _prot_warnings(prot, "EV charger feeder")
        _prot_warnings(prot_f, "EV connecting point")
        design["diagram_notes"].append(
            f"EV distribution feeder sized for {design_kw:,.0f} kW "
            f"({'load-controlled peak' if managed else 'all points at rated power'}, "
            f"IEC 60364-7-722 722.311); one dedicated circuit and 30 mA RCD "
            f"per connecting point."
        )
        compliance.append(std_mod._rec(
            "IEC 60364-7-722:2015", "722.311 / 722.314 / 722.531",
            "EV charging installation", std_mod.PASS,
            f"Distribution feeder for {design_kw:,.0f} kW: "
            + ("diversity below 1 is allowed because the fleet is "
               f"load-controlled (highest simulated charging "
               f"{ev_peak:,.0f} kW of {total_kw:,.0f} kW installed). "
               if managed else
               "diversity 1, all connecting points at rated power. ")
            + f"{system.n_chargers} dedicated {ph}-phase final circuits of "
              f"{cab_f['csa_mm2'] if cab_f else '-'} mm2 on "
              f"{prot_f['rating_a'] if prot_f else '-'} A breakers, each with "
              f"its own 30 mA RCD (type A + 6 mA DC detection, or type B).",
            design_kw=design_kw, diversity=diversity))
        if getattr(system.ev, "v2g", False):
            compliance.append(std_mod._rec(
                "NFPA 855:2026 / IEEE 1547.9", "15.11 / 4.12",
                "Vehicle-to-grid / vehicle-to-home", std_mod.INFO,
                "A parked EV that powers the site is an ESS for siting and "
                "interconnection purposes: bidirectional chargers must be "
                "listed for utility interaction and the vehicle use must "
                "comply with NFPA 855 15.11 at dwellings.",
            ))

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
            **{k: v for k, v in ck("mv_collector").items()
               if k != "n_circuits"},
            max_voltage_drop_pct=2.0, ambient_c=ambient_max_c,
            # MV feeder relays: instantaneous element plus breaker, 0.2 s
            # (as in the reference 22 kV study), at the network fault level.
            fault_current_a=fault_level_ka * 1000.0, clearing_time_s=0.2,
        )
        biggest = max(rows, key=lambda r: r["kva_each"]) if rows else None
        for r in rows:
            r["lv_fault_ka"] = (
                r["kva_each"] * 1000.0
                / (math.sqrt(3) * machine_v * r["impedance_pct"] / 100.0) / 1000.0
            )
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
        "main", system_voltage_v=system_voltage_v, earthing=earthing,
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

    # ------------------------------------------- path and plant-wide checks
    if design.get("pv"):
        segs = design["pv"].pop("_vd_segments")
        pcc_vd = 0.0
        if design.get("grid") and not dist["is_mv"] and design["grid"].get("cable"):
            pcc_vd = design["grid"]["cable"]["voltage_drop_pct"]
        total_vd = segs[0][1] + segs[1][1] + pcc_vd
        if total_vd > 3.0 + 1e-9 and design["pv"].get("ac_cable"):
            # Each segment passed its own limit but the path does not: give
            # the inverter AC cable whatever budget the DC string and the
            # PCC leave, and size it again.
            budget = max(0.3, 3.0 - segs[0][1] - pcc_vd)
            old_ac = design["pv"]["ac_cable"]
            pvf = design["pv"]["feeders"]
            cab2, prot2 = _coord(
                old_ac["current_a"], old_ac["length_m"], pvf["voltage_v"], 3,
                key="pv_ac", n_identical=1 if dist["is_mv"] else pvf["units"],
                max_voltage_drop_pct=budget, ambient_c=ambient_max_c,
                device=design["pv"]["ac_protection"]["device"],
                operating_current_a=old_ac["operating_current_a"],
            )
            if cab2 and prot2:
                design["pv"]["ac_cable"] = cab2
                design["pv"]["ac_protection"] = prot2
                for i_, r in enumerate(schedules["cables"]):
                    if r["circuit"] == "PV inverter AC":
                        schedules["cables"][i_] = _cable_row(
                            "PV inverter AC", cab2, circuits=pvf["units"],
                            feeders=pvf)
                for i_, r in enumerate(schedules["protection"]):
                    if r["circuit"] == "PV inverter AC":
                        schedules["protection"][i_] = _prot_row(
                            "PV inverter AC", prot2, circuits=pvf["units"])
                segs[1] = ("inverter AC", cab2["voltage_drop_pct"])
                # the loss entry for this circuit is recomputed below
                losses[:] = [(n, (_annual_loss_kwh(result.pv, pvf["units"], cab2,
                                                   pvf["voltage_v"], 3, power_factor)
                                  if n == "PV inverter AC" else l), e)
                             for n, l, e in losses]
        if pcc_vd:
            segs.append(("PCC", pcc_vd))
        compliance.append(std_mod.cumulative_voltage_drop(segs, 3.0))
    carried = max((e for _n, _l, e in losses if e), default=0.0)
    total_loss = sum(l for _n, l, _e in losses)
    if carried > 0:
        rec = std_mod.cable_loss_check(total_loss, carried)
        rec["values"]["by_circuit"] = [[n, round(l, 1)] for n, l, _e in losses]
        compliance.append(rec)
    design["cable_losses_kwh"] = total_loss

    if design.get("pv"):
        sf = design["pv"]["string_fuse"]
        st = design["pv"]["strings"] or {}
        compliance.append(std_mod._rec(
            "IEC 62548 / IEC TS 62257-7-1", "5.3.4 / Table 6",
            "PV string protection and string cable",
            std_mod.WARN if sf.get("warning") else std_mod.PASS,
            (f"String fuses {sf['rating_a']} A gPV (window "
             f"{sf['window_a'][0]:.1f}-{sf['window_a'][1]:.1f} A). "
             if sf.get("required") else sf.get("reason", "") + " ")
            + f"String cable rated for {design['pv']['dc_cable_design_current_a']:.1f} A "
              f"at {ambient_max_c + 40:.0f} C (EN 50618 PV cable).",
        ))
        compliance.append(std_mod._rec(
            "IEC 62548 / IEC TS 62257-7-1", "4.1.9",
            "PV string voltage window",
            std_mod.PASS if st.get("valid") else std_mod.FAIL,
            (f"{st.get('modules_per_string')} modules per string: Voc "
             f"{st.get('string_voc_cold_v', 0):.0f} V at {ambient_min_c:.0f} C "
             f"(limit {st.get('limits', {}).get('voltage_limit_v', 0):.0f} V), Vmp "
             f"{st.get('string_vmp_cold_v', 0):.0f}-{st.get('string_vmp_hot_v', 0):.0f} V "
             f"within MPPT {st.get('limits', {}).get('mppt_window_v')} V.")
            if st else "No string design.",
        ))
        spd = design["pv"]["spd_dc"]
        compliance.append(std_mod._rec(
            "IEC 61643-31 / IEC 60364-5-53", "534", "Surge protection",
            std_mod.PASS,
            f"DC: Ucpv {spd['uc_v']:.0f} V >= Voc max "
            f"{spd['uc_required_v']:.0f} V. AC: Uc {design['spd_ac']['uc_v']:.0f} V "
            f">= {design['spd_ac']['uc_required_v']:.0f} V ({earthing}).",
        ))

    breaking_bad = [r["circuit"] for r in schedules["protection"]
                    if r.get("breaking_ok") is False]
    compliance.append(std_mod._rec(
        "IEC 60364-4-43", "434.5.1 / 434.5.2",
        "Short-circuit breaking capacity and conductor withstand",
        std_mod.FAIL if breaking_bad else std_mod.PASS,
        (f"Breaking capacity insufficient on: {', '.join(breaking_bad)}."
         if breaking_bad else
         f"Every device's breaking capacity covers the {i_fault / 1000:.1f} kA "
         f"prospective fault, and every conductor withstands its device's "
         f"let-through energy."),
        prospective_ka=i_fault / 1000.0))
    no_trip = [r["circuit"] for r in schedules["protection"]
               if r.get("instantaneous_trip") is False]
    compliance.append(std_mod._rec(
        "IEC 60364-4-41 / IEC 60909-0", "411.3.2 / 7.1.3",
        "Automatic disconnection (minimum earth-fault current)",
        std_mod.WARN if no_trip else std_mod.PASS,
        (f"At the far end of {', '.join(no_trip)} the minimum line-to-earth "
         f"fault current is too low for the overcurrent device to disconnect "
         f"within the Table 41.1 time"
         + (" - in an island only the battery inverter and generators feed "
            "a fault" if not grid_connected else "")
         + ". Provide residual-current protection (411.4.5 / 411.5), lower "
           "instantaneous pickups, or larger conductors.")
        if no_trip else
        "Every circuit's minimum earth-fault current at its far end trips "
        "its device instantaneously, within the 0.4 s / 5 s limits of "
        "Table 41.1.",
        minimum_fault_a=i_fault_min, earthing=earthing))
    if str(earthing).upper().startswith(("TT", "IT")):
        compliance.append(std_mod._rec(
            "IEC 60364-4-41", "411.5 / 411.6", f"{earthing} system",
            std_mod.INFO,
            "The disconnection check above assumes a TN earth-fault loop. In "
            "a TT system automatic disconnection relies on RCDs with "
            "RA x IΔn <= 50 V; in an IT system the first fault is signalled "
            "by an insulation monitoring device and the second must be "
            "cleared as in TN or TT.",
        ))
    compliance.append(std_mod._rec(
        "IEC 60909-0:2026", "5 / 6 / 7.1", "Short-circuit currents",
        std_mod.INFO,
        f"Maximum I\"k {i_fault / 1000:.2f} kA (c = {bus.c['max']:g}), peak "
        f"ip {busfault['ip_a'] / 1000:.2f} kA (kappa {busfault['kappa']:.2f}); "
        f"minimum I\"k {i_fault_min / 1000:.2f} kA (c = {bus.c['min']:g}, "
        f"PV and wind excluded). Sources: {'; '.join(bus.notes) or 'none'}. "
        f"Replace the converter multiples with the manufacturers' figures "
        f"(6.10.2).",
        ik_max_a=i_fault, ik_min_a=i_fault_min, ip_a=busfault["ip_a"]))
    methods_used = sorted({c.get("installation") for c in [
        design.get(k, {}).get(f) for k, f in (("pv", "ac_cable"), ("wind", "cable"),
                                               ("battery", "cable"), ("genset", "cable"),
                                               ("grid", "cable"), ("load", "cable"),
                                               ("ev", "cable"))] if c} - {None})
    compliance.append(std_mod._rec(
        "IEC 60364-5-52", "Annex B", "Current-carrying capacity",
        std_mod.PASS,
        f"Conductors rated from Tables B.52.2-B.52.13 for "
        f"{'; '.join(methods_used) or 'the selected method'}, "
        f"{(ci.get('insulation') or 'xlpe').upper()} insulation, "
        f"{ci.get('material') or 'copper'}, corrected for {ambient_max_c:.0f} C "
        f"ambient (B.52.14)"
        + (f", ground {float(ci.get('ground_temp_c', 20) or 20):.0f} C and soil "
           f"{float(ci.get('soil_resistivity', 2.5) or 2.5):g} K.m/W "
           f"(B.52.15/16)" if any(m and m.startswith('D') for m in methods_used) else "")
        + " and grouping, parallel runs included (B.52.17-19).",
    ))

    design["schedules"] = schedules
    design["compliance_summary"] = std_mod.summarise(compliance)
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
        "cable_losses_kwh": round(total_loss, 1),
    }
    design["diagram_notes"].append(
        f"All ratings derived from the {system_voltage_v:.0f} V three-phase "
        f"design at {power_factor:.2f} power factor; prospective fault "
        f"{i_fault / 1000:.1f} kA at the LV busbar."
    )
    return design


def coordinate(design_current_a, length_m, voltage_v, phases=3,
               max_voltage_drop_pct=2.0, ambient_c=30, power_factor=0.95,
               fault_current_a=None, device="mcb", curve="C",
               max_passes=6, min_fault_current_a=None,
               operating_current_a=None, peak_current_a=None,
               **cable_kwargs):
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

    Short circuit (IEC 60364-4-43 434.5): the device class is raised until
    its breaking capacity covers the prospective fault, and the conductor
    must withstand the energy THAT device lets through - which depends on
    the device and its rating, hence inside the loop.
    """
    if fault_current_a:
        device = prot_mod.upgrade_for_breaking_capacity(device, fault_current_a)
    if design_current_a > 1600 and device in ("mcb", "mccb"):
        device = "acb"          # moulded-case breakers stop at about 1600 A
    elif design_current_a > 125 and device == "mcb":
        device = "mccb"         # miniature breakers stop at 125 A

    target = design_current_a
    cab = prot = None
    i2t = None

    for _ in range(max_passes):
        cab = cab_mod.size_cable(
            target, length_m, voltage_v, phases,
            max_voltage_drop_pct=max_voltage_drop_pct,
            ambient_c=ambient_c, power_factor=power_factor,
            fault_i2t=i2t, operating_current_a=operating_current_a,
            **cable_kwargs
        )
        if cab is None:
            return None, None
        prot = prot_mod.select_overcurrent(
            design_current_a, cab["ampacity_a"], device=device, curve=curve
        )
        need_i2t = (prot_mod.let_through_i2t(device, prot["rating_a"],
                                             fault_current_a)
                    if fault_current_a else None)
        if need_i2t is not None and (i2t is None or need_i2t > i2t * 1.000001):
            i2t = need_i2t
            continue
        if prot["compliant"]:
            break
        # The device the design current demands is larger than the conductor
        # can carry. Re-size the conductor for the device rating, with the
        # 1.45 margin the second condition needs.
        needed = max(prot["rating_a"], prot["i2_a"] / 1.45)
        if needed <= target * 1.001:
            break                     # no progress possible, report as-is
        target = needed

    if prot is not None:
        prot["breaking"] = (prot_mod.check_breaking_capacity(device, fault_current_a)
                            if fault_current_a else None)
        prot["let_through_i2t"] = i2t
        if peak_current_a and prot["breaking"]:
            icu = prot["breaking"]["breaking_capacity_ka"] or 0.0
            icm = iec60909_mod.making_factor(icu) * icu * 1000.0
            prot["making_capacity_a"] = icm
            prot["peak_ok"] = peak_current_a <= icm + 1e-6
            if not prot["peak_ok"]:
                prot["breaking"]["adequate"] = False
        if min_fault_current_a and prot.get("curve"):
            lo, hi = prot_mod.TRIP_CURVES.get(prot["curve"], (5, 10))
            prot["instantaneous_trip"] = (
                float(min_fault_current_a) >= prot["rating_a"] * hi
            )
        else:
            prot["instantaneous_trip"] = None
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
        "method": cab.get("installation_method"),
        "derating": round(cab.get("derating_factor", 1.0), 3),
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
        "breaking_capacity_ka": (prot.get("breaking") or {}).get("breaking_capacity_ka"),
        "disconnection_ok": (prot.get("disconnection") or {}).get("ok"),
        "end_fault_ik1_min_a": round((prot.get("disconnection") or {}).get("ik1_min_a", 0.0), 0) or None,
        "breaking_ok": (prot.get("breaking") or {}).get("adequate"),
        "instantaneous_trip": prot.get("instantaneous_trip"),
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
