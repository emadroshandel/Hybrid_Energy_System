"""
Conductor sizing.

Three criteria are applied, and the largest result wins. Sizing on current
alone - which is what most quick calculations do - is the usual reason a
system commissions correctly and then underperforms: the cable is legal but
the volt drop eats the yield.

  1. Current-carrying capacity (ampacity), derated for installation method,
     ambient temperature and grouping, per IEC 60364-5-52.
  2. Voltage drop against a design limit. IEC 60364 Appendix G suggests 3%
     for lighting and 5% for other uses from the origin of the installation;
     PV practice is stricter, typically 1% on DC strings and 2% on AC.
  3. Short-circuit withstand: the adiabatic equation from IEC 60364-5-54,
     k^2 S^2 >= I^2 t. A conductor that carries load current happily can
     still be destroyed by a fault before the protection clears it.
"""

from __future__ import annotations

import math

from . import iec60364_5_52 as t552

# Standard metric conductor cross-sections, mm2.
STANDARD_CSA = [
    1.0, 1.5, 2.5, 4, 6, 10, 16, 25, 35, 50, 70, 95, 120, 150, 185, 240,
    300, 400, 500, 630,
]

# Resistivity at 20 C, ohm mm2/m.
RESISTIVITY = {"copper": 0.01724, "aluminium": 0.02826}

# Temperature coefficient of resistance per K at 20 C.
TEMP_COEFF = {"copper": 0.00393, "aluminium": 0.00403}

# Adiabatic k factors, IEC 60364-5-54 Table 43A.
K_FACTOR = {
    ("copper", "pv"): 143,
    ("copper", "pvc"): 115,
    ("copper", "xlpe"): 143,
    ("aluminium", "pvc"): 76,
    ("aluminium", "xlpe"): 94,
}

# Kept for backward compatibility only. These are IEC 60364-5-52 Table
# B.52.2 method C values - PVC, TWO loaded conductors - which the engine
# used to apply, labelled XLPE, to every circuit. Sizing now reads the
# Annex B table for the actual method, insulation and loaded conductors
# (see iec60364_5_52.py).
BASE_AMPACITY_CU_XLPE = {
    1.0: 15, 1.5: 19.5, 2.5: 27, 4: 36, 6: 46, 10: 63, 16: 85, 25: 112,
    35: 138, 50: 168, 70: 213, 95: 258, 120: 299, 150: 344, 185: 392,
    240: 461, 300: 530, 400: 634, 500: 723, 630: 826,
}

# Derating for ambient temperature, XLPE conductors (90 C rated).
AMBIENT_DERATE_XLPE = {
    10: 1.15, 15: 1.12, 20: 1.08, 25: 1.04, 30: 1.00, 35: 0.96, 40: 0.91,
    45: 0.87, 50: 0.82, 55: 0.76, 60: 0.71, 65: 0.65, 70: 0.58,
}

# PV cable to EN 50618 (H1Z2Z2-K, 90 C continuous, 120 C permitted for
# 20 000 h). Single cable in free air, rated at 60 C ambient - the standard
# assumes it lives behind a module. Values from EN 50618 Table A.3 as
# reproduced in manufacturers' data; use the actual product's table for a
# final design.
BASE_AMPACITY_PV_EN50618 = {
    1.5: 30, 2.5: 41, 4: 55, 6: 70, 10: 98, 16: 132, 25: 176, 35: 218,
    50: 276, 70: 347, 95: 416, 120: 488, 150: 566, 185: 644, 240: 775,
}
AMBIENT_DERATE_PV = {
    60: 1.00, 70: 0.91, 80: 0.82, 90: 0.71, 100: 0.58, 110: 0.41,
}

# Minimum conductor cross-section for fixed power circuits, IEC 60364-5-52
# Table 52.2: 1.5 mm2 copper, 10 mm2 aluminium (aligned with IEC 60228).
MIN_CSA = {"copper": 1.5, "aluminium": 10.0}

# Derating for grouping of circuits, method C.
GROUPING_DERATE = {1: 1.00, 2: 0.85, 3: 0.79, 4: 0.75, 5: 0.73, 6: 0.72,
                   7: 0.72, 8: 0.71, 9: 0.70, 12: 0.68, 16: 0.66, 20: 0.64}


def _interp_table(table, key):
    keys = sorted(table)
    if key <= keys[0]:
        return table[keys[0]]
    if key >= keys[-1]:
        return table[keys[-1]]
    for i in range(len(keys) - 1):
        if keys[i] <= key <= keys[i + 1]:
            lo, hi = keys[i], keys[i + 1]
            f = (key - lo) / (hi - lo)
            return table[lo] + (table[hi] - table[lo]) * f
    return table[keys[-1]]


def derating_factor(ambient_c=30, n_circuits=1, insulation="xlpe"):
    """Combined installation derating applied to the base ampacity."""
    if insulation == "pv":
        # EN 50618 ratings are at 60 C; nothing is gained below that.
        amb = _interp_table(AMBIENT_DERATE_PV, max(60.0, ambient_c))
    else:
        amb = _interp_table(AMBIENT_DERATE_XLPE, ambient_c)
    grp = _interp_table(GROUPING_DERATE, n_circuits)
    return amb * grp


def resistance_per_m(csa_mm2, material="copper", conductor_temp_c=70):
    """AC resistance per metre at operating temperature."""
    rho20 = RESISTIVITY[material]
    alpha = TEMP_COEFF[material]
    rho = rho20 * (1.0 + alpha * (conductor_temp_c - 20.0))
    return rho / csa_mm2


def voltage_drop(csa_mm2, current_a, length_m, voltage_v, phases=3,
                 material="copper", power_factor=0.95, conductor_temp_c=70,
                 reactance_per_m=0.00008):
    """
    Voltage drop in volts and as a percentage.

    Includes reactance, which matters for large cross-sections: above about
    95 mm2 the reactive term dominates and a resistance-only calculation
    understates the drop.

    `phases`: 3 for three-phase, 1 for single-phase AC, 2 for a DC circuit
    (out and back).
    """
    r = resistance_per_m(csa_mm2, material, conductor_temp_c)
    x = reactance_per_m
    sin_phi = math.sqrt(max(0.0, 1.0 - power_factor ** 2))

    if phases == 3:
        drop = math.sqrt(3) * current_a * length_m * (
            r * power_factor + x * sin_phi
        )
    elif phases == 1:
        drop = 2.0 * current_a * length_m * (r * power_factor + x * sin_phi)
    else:  # DC
        drop = 2.0 * current_a * length_m * r

    pct = (drop / voltage_v * 100.0) if voltage_v > 0 else 0.0
    return drop, pct


def adiabatic_minimum_csa(fault_current_a, clearing_time_s,
                          material="copper", insulation="xlpe"):
    """
    Minimum cross-section to survive a fault, IEC 60364-5-54:

        S >= sqrt(I^2 * t) / k
    """
    k = K_FACTOR.get((material, insulation), 115)
    return math.sqrt(fault_current_a ** 2 * clearing_time_s) / k


def size_cable(current_a, length_m, voltage_v, phases=3,
               max_voltage_drop_pct=2.0, material="copper",
               insulation="xlpe", ambient_c=30, n_circuits=1,
               power_factor=0.95, fault_current_a=None,
               clearing_time_s=0.4, parallel_runs=1, fault_i2t=None,
               operating_current_a=None, method="C",
               loaded_conductors=None, ground_temp_c=20.0,
               soil_resistivity=2.5, arrangement=None):
    """
    Select a conductor meeting all three criteria.

    Ampacity is taken from IEC 60364-5-52 Annex B for the installation
    `method`, the insulation and the number of loaded conductors (3 for a
    three-phase circuit, 2 for single-phase and DC), corrected for ambient
    air or ground temperature, soil resistivity and grouping. Parallel runs
    of one circuit are themselves a group (B.52.17 note 5) and are counted
    with `n_circuits`. PV cable (`insulation="pv"`) uses EN 50618.

    Returns a specification recording which criterion governed, because that
    is the useful engineering information: a cable governed by volt drop can
    be shortened or split, while one governed by ampacity cannot.
    """
    if current_a <= 0:
        return None

    method = (method or "C").upper()
    loaded = loaded_conductors or (3 if phases == 3 else 2)
    n_circuits = max(1, int(n_circuits or 1))

    if insulation == "pv":
        table = BASE_AMPACITY_PV_EN50618

        def factors(runs):
            amb = _interp_table(AMBIENT_DERATE_PV, max(60.0, ambient_c))
            grp = _interp_table(t552.GROUP_TRAY, n_circuits * runs)
            return {"temperature": amb, "soil": 1.0, "grouping": grp,
                    "total": amb * grp}
        method_used = "PV cable in free air (EN 50618)"
    else:
        table = t552.base_table(material, insulation, method, loaded)
        if not table:
            table = t552.base_table(material, insulation, "C", loaded)
            method = "C"

        def factors(runs):
            return t552.correction_factor(
                insulation, method, ambient_c, ground_temp_c,
                soil_resistivity, n_circuits * runs, arrangement)
        method_used = t552.METHOD_LABELS.get(method, method)

    sizes = [c for c in STANDARD_CSA
             if c >= MIN_CSA.get(material, 1.5) and c in table]

    # Escalate to parallel runs when one conductor cannot carry the current.
    # Each extra run joins the group and lowers the factor for all of them,
    # so the check is repeated with the grouping that number of runs causes.
    runs = max(1, int(parallel_runs or 1))
    while True:
        f = factors(runs)
        if table[sizes[-1]] * f["total"] * runs >= current_a or runs >= 40:
            break
        runs += 1
    parallel_runs = runs
    derate = f["total"]
    per_run = current_a / parallel_runs
    # Volt drop is a performance criterion and is checked at the current the
    # circuit actually carries (Imp for a PV string, rated output for an
    # inverter), not at the 1.25 x Isc safety figure the ampacity check uses.
    # IEC TS 62257-7-1 6.1.4.1 treats the two criteria separately for this
    # reason.
    i_vd = float(operating_current_a) if operating_current_a else current_a

    by_ampacity = next((c for c in sizes if table[c] * derate >= per_run),
                       sizes[-1])

    by_drop = None
    for csa in sizes:
        _v, pct = voltage_drop(
            csa * parallel_runs, i_vd, length_m, voltage_v, phases,
            material, power_factor,
        )
        if pct <= max_voltage_drop_pct:
            by_drop = csa
            break
    if by_drop is None:
        by_drop = sizes[-1]

    by_fault = None
    need = None
    if fault_i2t:
        # Let-through energy of the protective device, IEC 60364-4-43
        # 434.5.2: S >= sqrt(I^2 t) / k. Shared across parallel runs.
        k = K_FACTOR.get((material, insulation), 115)
        need = math.sqrt(fault_i2t) / k / max(1, parallel_runs)
    elif fault_current_a:
        need = adiabatic_minimum_csa(
            fault_current_a, clearing_time_s, material, insulation
        ) / max(1, parallel_runs)
    if need is not None:
        by_fault = next((c for c in sizes if c >= need), sizes[-1])

    candidates = {"ampacity": by_ampacity, "voltage_drop": by_drop}
    if by_fault:
        candidates["short_circuit"] = by_fault

    chosen = max(candidates.values())
    governing = [k for k, v in candidates.items() if v == chosen]

    final_v, final_pct = voltage_drop(
        chosen * parallel_runs, i_vd, length_m, voltage_v, phases,
        material, power_factor,
    )
    capacity = table.get(chosen, 0) * derate

    notes = []
    if final_pct > max_voltage_drop_pct:
        notes.append(
            f"Even the largest standard conductor gives {final_pct:.2f}% drop "
            f"against a {max_voltage_drop_pct:.1f}% limit over {length_m:.0f} m. "
            f"Use parallel runs, raise the distribution voltage, or move the "
            f"equipment closer."
        )
    if length_m > 150 and phases != 3:
        notes.append(
            f"A {length_m:.0f} m single-phase or DC run is long. Three-phase "
            f"distribution at this distance would use roughly half the copper."
        )

    return {
        "csa_mm2": chosen,
        "parallel_runs": parallel_runs,
        "total_csa_mm2": chosen * parallel_runs,
        "material": material,
        "insulation": insulation,
        "installation_method": method if insulation != "pv" else "PV",
        "installation": method_used,
        "loaded_conductors": loaded,
        "current_a": current_a,
        "current_per_run_a": per_run,
        "operating_current_a": i_vd,
        "length_m": length_m,
        "voltage_v": voltage_v,
        "phases": phases,
        "base_ampacity_a": table.get(chosen, 0),
        "ampacity_a": capacity * parallel_runs,
        "utilisation": (
            current_a / (capacity * parallel_runs) if capacity > 0 else None
        ),
        "voltage_drop_v": final_v,
        "voltage_drop_pct": final_pct,
        "derating_factor": derate,
        "correction_factors": f,
        "grouped_with": n_circuits * parallel_runs,
        "governing_criterion": "+".join(governing),
        "candidates": candidates,
        "min_csa_for_fault_mm2": need,
        "standard": ("EN 50618" if insulation == "pv"
                     else "IEC 60364-5-52 Annex B"),
        "notes": notes,
    }


def design_current(power_kw, voltage_v, phases=3, power_factor=0.95,
                   safety_factor=1.25):
    """
    Design current for a circuit.

    The 1.25 factor is the IEC/NEC continuous-load rule: a circuit expected
    to run at full load for three hours or more is sized at 125% of it. PV
    output circuits carry it twice (1.25 for continuous operation, 1.25 for
    irradiance enhancement) giving the familiar 1.5625 total.
    """
    if voltage_v <= 0:
        return 0.0
    if phases == 3:
        i = power_kw * 1000.0 / (math.sqrt(3) * voltage_v * power_factor)
    elif phases == 1:
        i = power_kw * 1000.0 / (voltage_v * power_factor)
    else:
        i = power_kw * 1000.0 / voltage_v
    return i * safety_factor


def conductor_loss_kw(csa_mm2, current_a, length_m, phases=3,
                      material="copper", parallel_runs=1,
                      conductor_temp_c=70):
    """
    Resistive loss in a circuit, kW: 3 I^2 R L for three-phase, 2 I^2 R L
    for single-phase and DC (out and back).

    Used for the annual cable-loss estimate. PV plant specifications
    commonly cap total cable losses at 1-1.5% of annual energy, and a
    design that passes every volt-drop check can still fail that once the
    hours at full current are added up.
    """
    if csa_mm2 <= 0 or current_a <= 0 or length_m <= 0:
        return 0.0
    r = resistance_per_m(csa_mm2 * max(1, parallel_runs), material,
                         conductor_temp_c)
    n = 3 if phases == 3 else 2
    return n * current_a ** 2 * r * length_m / 1000.0
