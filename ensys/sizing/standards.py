"""
Standards checks that sit on top of the electrical design.

Each function here takes a sized design (or a component of it) and returns a
record of what a named clause asks for, what the design provides and
whether the two agree. They are deliberately small and separate from the
sizing itself: the sizing decides what to build; these say whether what was
decided would pass a reviewer holding the standard.

Every record has the same shape so the interface and the report can list
them uniformly:

    {"standard": "...", "clause": "...", "title": "...",
     "status": "pass" | "warn" | "fail" | "info",
     "detail": "...", "values": {...}}

Sources (all in the project's References folder):

  IEC TS 62257-7-3:2008   Generator sets - sizing band and site derating
  IEC TS 62257-7-1:2010   PV generators - cable ratings, string protection
  IEC TS 62257-9-2:2006   Microgrids - voltage drop limits
  IEC TS 62257-5:2015     Protection against electrical hazards - DC faults
  BS EN IEC 62933-2-1     EES unit parameters - RTE at the POC, auxiliaries,
                          capacity planned for end of service life
  IEEE Std 1547.9-2022    Energy storage interconnection - Category B
                          reactive capability, Category III ride-through
  IEEE Std 2030.2.1-2019  BESS design - PCS DC voltage window, unbalance
  NFPA 855:2026           Stationary ESS installation - Table 1.3
                          thresholds, 50 kWh groups, Chapter 15 dwellings
  UL 9540:2023            ESS listing
  IEC 61400-1 (by ref.)   Wind turbine class against site mean wind speed
"""

from __future__ import annotations

import math

PASS, WARN, FAIL, INFO = "pass", "warn", "fail", "info"


def _rec(standard, clause, title, status, detail, **values):
    return {
        "standard": standard, "clause": clause, "title": title,
        "status": status, "detail": detail, "values": values,
    }


# ------------------------------------------------------------- generator

def genset_site_derating(ambient_c=25.0, altitude_m=0.0, humidity_pct=30.0):
    """
    Site derating factor for a generator set, IEC TS 62257-7-3 Table 1.

      air temperature   2.5 % per 5 C above 25 C
      altitude          3 % per 300 m above 300 m
      humidity          above 60 % RH: 0.5 % (30-40 C), 1.0 % (40-50 C) or
                        1.5 % (> 50 C) per 10 % RH

    The reference conditions are those of ISO 8528-1 (25 C, 89.9 kPa,
    30 % RH). The standard's note asks for the derating to be added to the
    required size, which is what dividing the available output by this
    factor does.
    """
    t = float(ambient_c if ambient_c is not None else 25.0)
    h = float(altitude_m or 0.0)
    rh = float(humidity_pct if humidity_pct is not None else 30.0)

    d_temp = 0.025 * max(0.0, t - 25.0) / 5.0
    d_alt = 0.03 * max(0.0, h - 300.0) / 300.0
    if t > 50:
        per = 0.015
    elif t > 40:
        per = 0.010
    elif t >= 30:
        per = 0.005
    else:
        per = 0.0
    d_hum = per * max(0.0, rh - 60.0) / 10.0
    total = d_temp + d_alt + d_hum
    factor = max(0.3, 1.0 - total)
    return {
        "factor": factor,
        "temperature_derate": d_temp,
        "altitude_derate": d_alt,
        "humidity_derate": d_hum,
        "conditions": {"ambient_c": t, "altitude_m": h, "humidity_pct": rh},
        "standard": "IEC TS 62257-7-3:2008 Table 1 (ISO 8528-1 reference)",
    }


def genset_loading_check(output_series, unit_kw, n_units, derate=1.0,
                         band=(0.50, 0.80)):
    """
    IEC TS 62257-7-3 5.2.2: the engine should work between 50 % and 80 % of
    its maximum nominal power in normal operation.

    Loading is measured per RUNNING unit (the dispatch commits units one at
    a time), against the site-derated rating, over the hours the set runs.
    """
    lo, hi = band
    unit_avail = unit_kw * derate
    run = below = above = inside = 0
    for p in output_series:
        if p <= 1e-9:
            continue
        run += 1
        on = max(1, min(n_units, int(math.ceil(p / unit_avail - 1e-9))))
        ratio = p / (on * unit_avail) if unit_avail > 0 else 0.0
        if ratio < lo - 1e-9:
            below += 1
        elif ratio > hi + 1e-9:
            above += 1
        else:
            inside += 1
    share = inside / run if run else 1.0
    if not run:
        status = INFO
        detail = "The generator does not run in the simulated year."
    elif share >= 0.7:
        status = PASS
        detail = (f"{100 * share:.0f}% of running hours fall in the 50-80% "
                  f"loading band the standard recommends.")
    else:
        status = WARN
        parts = []
        if below:
            parts.append(f"{below} h below 50% (light loading: wet stacking, "
                         f"glazing, poor specific consumption)")
        if above:
            parts.append(f"{above} h above 80% (shortened engine life)")
        detail = ("Only " + f"{100 * share:.0f}% of running hours fall in the "
                  "50-80% band; " + "; ".join(parts) + ". Consider a smaller "
                  "set, two sets of unequal size (cl. 5.2.2), or a "
                  "cycle-charging strategy.")
    return _rec("IEC TS 62257-7-3", "5.2.2", "Generator loading band",
                status, detail, run_hours=run, hours_below=below,
                hours_above=above, hours_in_band=inside, share_in_band=share)


# -------------------------------------------------------------- PV yield

def pv_yields_61724(p0_kwp, poa_w_m2, dc_kw, ac_kw, cell_temp_c=None,
                    gamma=-0.0035, g_ref=1000.0):
    """
    IEC 61724-1:2021 clause 13 and 14 quantities for the simulated year.

      Yr = H_i / G_ref                      reference yield      (13.6.4)
      Ya = E_A / P0                         array (DC) yield     (13.6.2)
      Yf = E_out / P0                       final (AC) yield     (13.6.3)
      Lc = Yr - Ya, Ls = Ya - Yf            capture, BOS losses  (13.7)
      PR = Yf / Yr                                               (14.3.1)
      PR'25C with C_k = 1 + gamma (T_mod,k - 25 C)               (14.3.2.2)

    Hourly intervals (tau = 1 h). The series are per array, AC at the
    inverter output before any curtailment by the dispatch.
    """
    p0 = float(p0_kwp)
    n = min(len(poa_w_m2), len(dc_kw), len(ac_kw))
    scale_dc = 1.0
    if p0 <= 0 or n == 0:
        return None
    h_i = sum(max(0.0, g) for g in poa_w_m2[:n]) / 1000.0      # kWh/m2
    e_a = sum(dc_kw[:n]) * scale_dc
    e_out = sum(ac_kw[:n])
    yr = h_i / (g_ref / 1000.0)
    ya = e_a / p0
    yf = e_out / p0
    exp25 = 0.0
    wsum = tsum = 0.0
    for k in range(n):
        g = max(0.0, poa_w_m2[k])
        if g <= 0:
            continue
        t = cell_temp_c[k] if cell_temp_c else 25.0
        ck = 1.0 + gamma * (t - 25.0)
        exp25 += ck * p0 * g / g_ref
        wsum += g
        tsum += g * t
    pr = yf / yr if yr > 0 else 0.0
    return {
        "yr_h": yr, "ya_h": ya, "yf_h": yf,
        "lc_h": yr - ya, "ls_h": ya - yf,
        "pr": pr,
        "pr_25c": e_out / exp25 if exp25 > 0 else 0.0,
        "t_mod_irradiance_weighted_c": tsum / wsum if wsum else None,
        "array_efficiency_ratio": ya / yr if yr else 0.0,
        "bos_efficiency": yf / ya if ya else 0.0,
        "standard": "IEC 61724-1:2021",
    }


# ------------------------------------------------------------ microgrids

def cumulative_voltage_drop(segments, limit_pct=3.0, title="PV array to PCC"):
    """
    Sum of voltage drops along one path. PV plant practice (and the cable
    sizing reference in the project folder) caps the accumulated DC + AC
    drop at 3 %; IEC TS 62257-7-1 recommends no more than 5 % from the most
    remote module to the application terminals.
    """
    total = sum(float(v or 0.0) for _n, v in segments)
    status = PASS if total <= limit_pct + 1e-9 else WARN
    return _rec(
        "IEC TS 62257-7-1 / project spec", "6.1.4 / 4.1.9",
        f"Accumulated voltage drop, {title}", status,
        f"{total:.2f}% along " + " + ".join(
            f"{n} {float(v or 0):.2f}%" for n, v in segments)
        + f" against a {limit_pct:.1f}% limit.",
        total_pct=total, limit_pct=limit_pct,
        segments=[[n, v] for n, v in segments],
    )


def cable_loss_check(loss_kwh, energy_kwh, limit_fraction=0.015):
    """
    Annual resistive loss in the cabling as a share of the energy carried.
    Utility PV specifications commonly cap it at 1-1.5 %.
    """
    share = loss_kwh / energy_kwh if energy_kwh > 0 else 0.0
    status = PASS if share <= limit_fraction else WARN
    return _rec(
        "PV plant practice (IEC 60287 losses)", "-",
        "Annual cable losses", status,
        f"{loss_kwh:,.0f} kWh/yr, {100 * share:.2f}% of the energy carried "
        f"(target <= {100 * limit_fraction:.1f}%).",
        loss_kwh=loss_kwh, share=share, limit=limit_fraction,
    )


# ---------------------------------------------------- stand-alone systems

def stand_alone_check(load_kwh_series, renewable_kwh_series, battery_kwh,
                      usable_fraction, eol_fraction, chemistry="lithium_lfp",
                      min_battery_temp_c=25.0, autonomy_days=2.0,
                      design_margin=1.15, critical=False,
                      inverter_efficiency=0.95, has_generator=False):
    """
    IEEE Std 1013-2019 battery capacity and IEEE Std 1562-2021 array-to-
    load ratio for a stand-alone system.

    Battery (IEEE 1013 6.1-6.3), in energy rather than ampere-hours:
        unadjusted = autonomy x worst-month average daily load / inverter eff.
        adjusted   = max(unadjusted / MDOD, unadjusted / EOL)
                     x temperature factor x design margin (1.10-1.25)
    MDOD is the usable SOC window; EOL the end-of-life capacity. The
    temperature factor is applied for lead-acid below 25 C (about 1 % of
    capacity per K - the manufacturer's curve should replace it); lithium
    systems with thermal management are taken as 1.

    Array (IEEE 1562 9.1): A:L in the design (worst) month - renewable
    energy available over load - typically 1.1-1.2 for non-critical loads
    with good resource, 1.3-1.4 or more for critical loads or poor resource.

    The optimiser sizes against the simulated year; this is the
    deterministic rule-of-thumb check a reviewer holding IEEE 1013/1562
    will make, and the place where days of autonomy enter.
    """
    from ..timeseries import month_index
    n = len(load_kwh_series)
    months = [m - 1 for m in month_index()][:n]   # month_index is 1-12
    by_m_load = [0.0] * 12
    by_m_ren = [0.0] * 12
    days = [0] * 12
    for i in range(n):
        m = months[i] if i < len(months) else 11
        by_m_load[m] += load_kwh_series[i]
        if i < len(renewable_kwh_series):
            by_m_ren[m] += renewable_kwh_series[i]
    for i in range(n):
        days[months[i] if i < len(months) else 11] += 1
    days = [max(1, d / 24.0) for d in days]
    daily = [by_m_load[m] / days[m] for m in range(12)]
    worst_daily = max(daily)
    ratios = [by_m_ren[m] / by_m_load[m] if by_m_load[m] > 0 else float("inf")
              for m in range(12)]
    design_month = min(range(12), key=lambda m: ratios[m])
    al = ratios[design_month]

    unadjusted = autonomy_days * worst_daily / max(0.5, inverter_efficiency)
    mdod = max(0.05, float(usable_fraction))
    eol = max(0.3, float(eol_fraction))
    adj = max(unadjusted / mdod, unadjusted / eol)
    temp_factor = 1.0
    if str(chemistry).startswith("lead") and min_battery_temp_c < 25.0:
        temp_factor = 1.0 / max(0.5, 1.0 - 0.01 * (25.0 - min_battery_temp_c))
    required = adj * temp_factor * design_margin
    al_target = 1.3 if critical else 1.1

    ok_bat = battery_kwh >= required - 1e-6
    ok_al = al >= al_target
    status = PASS if (ok_bat and ok_al) else (INFO if has_generator else WARN)
    mname = ("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec").split()[design_month]
    detail = (
        f"Battery: {autonomy_days:g} days of autonomy at the worst month's "
        f"{worst_daily:,.1f} kWh/day, adjusted for {100 * mdod:.0f}% usable "
        f"window, {100 * eol:.0f}% end-of-life capacity"
        + (f", x{temp_factor:.2f} for {min_battery_temp_c:.0f} C" if temp_factor > 1 else "")
        + f" and a x{design_margin:.2f} design margin, needs {required:,.0f} kWh "
        f"nameplate; {battery_kwh:,.0f} kWh is installed. "
        f"Array-to-load ratio in the design month ({mname}) is {al:.2f} "
        f"against {al_target:.1f} for a {'critical' if critical else 'non-critical'} load."
        + (" A generator covers what these do not, so they are reported, not failed."
           if has_generator and not (ok_bat and ok_al) else "")
    )
    return _rec("IEEE Std 1013-2019 / IEEE Std 1562-2021", "6.3 / 9.1",
                "Stand-alone sizing (autonomy and array-to-load ratio)",
                status, detail, required_kwh=required, installed_kwh=battery_kwh,
                worst_daily_kwh=worst_daily, al_ratio=al, al_target=al_target,
                design_month=mname, autonomy_days=autonomy_days)


# ------------------------------------------------------------- storage

# NFPA 855:2026 Table 1.3 - threshold quantity per fire area or outdoor
# installation above which the standard applies, kWh.
NFPA855_THRESHOLD_KWH = {
    "lithium_nmc": 20, "lithium_lfp": 20, "lithium_titanate": 20,
    "lithium": 20, "lead_acid_flooded": 70, "lead_acid_agm": 70,
    "nickel_iron": 70, "flow_vanadium": 20, "flow_zinc_bromine": 20,
    "sodium_sulphur": 70, "sodium_ion": 10, "generic": 10,
}
NFPA855_GROUP_KWH = 50.0          # 9.5.1.1
NFPA855_GROUP_SPACING_M = 0.9     # 9.5.1.2
# Chapter 15, one- and two-family dwellings and townhouse units.
NFPA855_DWELLING_UNIT_MAX_KWH = 20.0          # 15.5.1 (non lead/Ni chemistries)
NFPA855_DWELLING_AGGREGATE_KWH = {            # Table 15.5.2
    "utility_closet": 40.0,
    "attached_garage": 100.0,
    "exterior_wall": 200.0,
    "detached_structure": 200.0,
    "detached_structure_3m": 600.0,
    "outdoor_ground_3m": 600.0,
}
NFPA855_DWELLING_PROPERTY_MAX_KWH = 600.0     # 15.5.4
_AQUEOUS = ("lead_acid", "nickel", "flow_")


def nfpa855_check(total_kwh, unit_kwh, chemistry="lithium_lfp",
                  dwelling=False, location="exterior_wall", units=1):
    """
    NFPA 855:2026 applicability and the size limits a sizing tool can see.

    It cannot see the room, the walls or the detection - those are listed as
    obligations, not checked.
    """
    chem = (chemistry or "generic").lower()
    out = []
    aqueous = any(chem.startswith(a) for a in _AQUEOUS) and not chem.startswith("flow_")

    if dwelling:
        out.append(_rec(
            "NFPA 855:2026", "1.3.2 / 15.1", "Applicability (dwelling)",
            INFO if total_kwh >= 1.0 else PASS,
            "ESS of 1 kWh or more at a one- or two-family dwelling falls under "
            "Chapter 15: UL 9540 listing (15.2.1), 0.9 m between units unless "
            "fire testing shows less (15.3.1), no sleeping rooms (15.4.3), "
            "interconnected smoke/heat alarms (15.7).",
            total_kwh=total_kwh))
        if not aqueous:
            ok = unit_kwh <= NFPA855_DWELLING_UNIT_MAX_KWH + 1e-9
            out.append(_rec(
                "NFPA 855:2026", "15.5.1", "Individual unit rating",
                PASS if ok else FAIL,
                f"Each unit is {unit_kwh:.1f} kWh; the limit for this chemistry "
                f"in a dwelling is {NFPA855_DWELLING_UNIT_MAX_KWH:.0f} kWh."
                + ("" if ok else " Use smaller listed units."),
                unit_kwh=unit_kwh, limit_kwh=NFPA855_DWELLING_UNIT_MAX_KWH))
            lim = NFPA855_DWELLING_AGGREGATE_KWH.get(location, 200.0)
            ok = total_kwh <= lim + 1e-9
            out.append(_rec(
                "NFPA 855:2026", "Table 15.5.2", "Aggregate rating at location",
                PASS if ok else FAIL,
                f"{total_kwh:.0f} kWh against {lim:.0f} kWh for "
                f"'{location.replace('_', ' ')}'."
                + ("" if ok else " Split across locations, move outdoors "
                   "3 m from the dwelling and property line, or design to "
                   "Chapters 4-9 (15.5.5)."),
                total_kwh=total_kwh, limit_kwh=lim, location=location))
            ok = total_kwh <= NFPA855_DWELLING_PROPERTY_MAX_KWH + 1e-9
            out.append(_rec(
                "NFPA 855:2026", "15.5.4", "Aggregate on the property",
                PASS if ok else FAIL,
                f"{total_kwh:.0f} kWh against the "
                f"{NFPA855_DWELLING_PROPERTY_MAX_KWH:.0f} kWh property limit.",
                total_kwh=total_kwh,
                limit_kwh=NFPA855_DWELLING_PROPERTY_MAX_KWH))
        return out

    thr = NFPA855_THRESHOLD_KWH.get(chem, 10)
    applies = total_kwh > thr
    out.append(_rec(
        "NFPA 855:2026", "1.3 / Table 1.3", "Applicability threshold",
        INFO if applies else PASS,
        (f"{total_kwh:,.0f} kWh exceeds the {thr} kWh threshold for this "
         f"chemistry, so NFPA 855 applies in full: UL 9540 listing, "
         f"UL 9540A fire test data, hazard mitigation analysis, detection "
         f"and suppression, commissioning and decommissioning plans.")
        if applies else
        f"{total_kwh:,.0f} kWh is within the {thr} kWh threshold; NFPA 855 "
        f"does not apply to this installation.",
        total_kwh=total_kwh, threshold_kwh=thr))
    if applies:
        groups = int(math.ceil(total_kwh / NFPA855_GROUP_KWH))
        ok = unit_kwh <= NFPA855_GROUP_KWH + 1e-9
        out.append(_rec(
            "NFPA 855:2026", "9.5.1.1-9.5.1.3", "Group size and separation",
            PASS if ok else WARN,
            f"Arrange as at least {groups} group(s) of <= "
            f"{NFPA855_GROUP_KWH:.0f} kWh, each {NFPA855_GROUP_SPACING_M} m "
            f"from other groups and walls."
            + ("" if ok else f" A single {unit_kwh:,.0f} kWh unit exceeds the "
               "group limit and needs AHJ approval based on UL 9540A large-"
               "scale fire testing (9.5.1.3)."),
            groups=groups, group_kwh=NFPA855_GROUP_KWH,
            spacing_m=NFPA855_GROUP_SPACING_M))
    return out


def ess_reactive_capability(p_rated_kw, s_rated_kva, category="B",
                            name="Battery PCS"):
    """
    IEEE Std 1547.9-2022 5.1-5.2: ES DER should meet normal operating
    performance Category B, i.e. +/-0.44 pu reactive power of nameplate
    apparent power - which is what lets an operator require PF 0.90 at full
    active power without further agreement.

    A converter rated in kVA equal to its kW cannot do that at full output.
    """
    need = p_rated_kw / 0.90 if category.upper() == "B" else p_rated_kw / 0.97
    ok = s_rated_kva >= need - 1e-6
    return _rec(
        "IEEE Std 1547.9-2022", "5.2", f"{name} reactive capability",
        PASS if ok else WARN,
        (f"{s_rated_kva:,.0f} kVA covers {p_rated_kw:,.0f} kW at PF 0.90."
         if ok else
         f"{p_rated_kw:,.0f} kW at PF 0.90 needs {need:,.0f} kVA; the "
         f"selected {s_rated_kva:,.0f} kVA converter would have to curtail "
         f"active power to deliver Category B reactive power."),
        p_kw=p_rated_kw, s_kva=s_rated_kva, required_kva=need,
        category=category)


def interconnection_settings(frequency_hz=50.0):
    """
    Default trip settings of IEEE Std 1547-2018, Category II for voltage and
    all categories for frequency, as cited by IEEE 1547.9. Frequencies are
    the 60 Hz values scaled to the system frequency, which is how they are
    usually adapted; on a 50 Hz network the local grid code (EN 50549-1 in
    Europe, AS/NZS 4777.2 in Australia) takes precedence and should be used.
    IEEE 1547.9 6.x recommends Category III ride-through for storage.
    """
    f = float(frequency_hz or 50.0)
    k = f / 60.0
    return {
        "standard": "IEEE Std 1547-2018 defaults (cited by IEEE 1547.9-2022)",
        "ride_through_category": "III (recommended for ES DER, IEEE 1547.9)",
        "reactive_category": "B",
        "voltage": [
            {"function": "OV2 (59)", "pickup_pu": 1.20, "clearing_s": 0.16},
            {"function": "OV1 (59)", "pickup_pu": 1.10, "clearing_s": 2.0},
            {"function": "UV1 (27)", "pickup_pu": 0.70, "clearing_s": 10.0},
            {"function": "UV2 (27)", "pickup_pu": 0.45, "clearing_s": 0.16},
        ],
        "frequency": [
            {"function": "OF2 (81O)", "pickup_hz": round(62.0 * k, 2), "clearing_s": 0.16},
            {"function": "OF1 (81O)", "pickup_hz": round(61.2 * k, 2), "clearing_s": 300.0},
            {"function": "UF1 (81U)", "pickup_hz": round(58.5 * k, 2), "clearing_s": 300.0},
            {"function": "UF2 (81U)", "pickup_hz": round(56.5 * k, 2), "clearing_s": 0.16},
        ],
        "anti_islanding_s": 2.0,
        "note": (
            "Defaults only. The network operator's settings and the local "
            "grid code govern; on 50 Hz networks use EN 50549-1, "
            "AS/NZS 4777.2 or the national equivalent."
        ),
    }


def battery_dc_fault(unit_kwh, n_units, dc_voltage_v):
    """
    Prospective DC short-circuit current at the battery terminals when the
    internal resistance is unknown, IEC TS 62257-5 9.4.2.3:  Ik = 10 x C,
    C in Ah. The battery fuse or DC breaker must break this, DC-rated, at
    the source end of the cable.
    """
    if dc_voltage_v <= 0 or unit_kwh <= 0:
        return None
    ah = unit_kwh * 1000.0 / dc_voltage_v
    ik_unit = 10.0 * ah
    return {
        "dc_voltage_v": dc_voltage_v,
        "unit_ah": ah,
        "ik_unit_a": ik_unit,
        "ik_bus_a": ik_unit * max(1, n_units),
        "standard": "IEC TS 62257-5:2015 9.4.2.3 (Ik = 10 x C)",
    }


# ------------------------------------------------------------------ wind

IEC_WIND_CLASS_VAVE = (("I", 10.0), ("II", 8.5), ("III", 7.5))


def wind_class_check(mean_hub_speed_ms, turbine_class=None):
    """
    IEC 61400-1 classes are defined by the reference speed; the annual mean
    at hub height is Vave = 0.2 Vref: 10, 8.5 and 7.5 m/s for classes I, II
    and III. A site whose mean exceeds the class figure loads the turbine
    beyond its design basis.
    """
    v = float(mean_hub_speed_ms or 0.0)
    needed = "S"
    for cls, vave in reversed(IEC_WIND_CLASS_VAVE):
        if v <= vave + 1e-9:
            needed = cls
            break
    rank = {"III": 3, "II": 2, "I": 1, "S": 0}
    if turbine_class:
        tc = str(turbine_class).upper().replace("IEC", "").strip()
        tc = tc.rstrip("ABCT").strip() or tc
        ok = rank.get(tc, 0) <= rank.get(needed, 0) if tc in rank else True
        status = PASS if ok else FAIL
        detail = (f"Hub-height mean {v:.2f} m/s requires IEC class {needed} or "
                  f"stronger; the turbine is class {turbine_class}.")
    else:
        status = INFO
        detail = (f"Hub-height mean {v:.2f} m/s: specify a turbine of IEC "
                  f"class {needed} or stronger (61400-1, Vave = 0.2 Vref). "
                  f"Turbulence class (A/B/C) needs site measurement.")
    return _rec("IEC 61400-1", "6.2", "Wind turbine class", status, detail,
                mean_hub_speed_ms=v, required_class=needed,
                turbine_class=turbine_class)


def summarise(checks):
    worst = PASS
    order = {PASS: 0, INFO: 0, WARN: 1, FAIL: 2}
    counts = {PASS: 0, INFO: 0, WARN: 0, FAIL: 0}
    for c in checks:
        counts[c["status"]] = counts.get(c["status"], 0) + 1
        if order.get(c["status"], 0) > order.get(worst, 0):
            worst = c["status"]
    return {"worst": worst, "counts": counts}
