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
    ("copper", "pvc"): 115,
    ("copper", "xlpe"): 143,
    ("aluminium", "pvc"): 76,
    ("aluminium", "xlpe"): 94,
}

# Base ampacity, A, for two/three loaded copper conductors at 30 C ambient,
# XLPE insulated, installation method C (clipped direct). Indicative values
# from IEC 60364-5-52; a real design must use the table for the actual
# installation method.
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
               clearing_time_s=0.4, parallel_runs=1):
    """
    Select a conductor meeting all three criteria.

    Returns a specification recording which criterion governed, because that
    is the useful engineering information: a cable governed by volt drop can
    be shortened or split, while one governed by ampacity cannot.
    """
    if current_a <= 0:
        return None

    derate = derating_factor(ambient_c, n_circuits, insulation)

    # Escalate to parallel runs when one conductor cannot carry the current.
    # Above roughly 400 A no single cable is practical anyway: the largest
    # sizes are difficult to bend and terminate, and their reactance makes
    # the volt drop worse per mm2 than two smaller cables in parallel.
    largest = STANDARD_CSA[-1]
    max_single = BASE_AMPACITY_CU_XLPE.get(largest, 0) * derate
    if material == "aluminium":
        max_single *= 0.78
    if max_single > 0:
        needed = int(math.ceil(current_a / max_single))
        if needed > parallel_runs:
            parallel_runs = needed

    per_run = current_a / max(1, parallel_runs)

    by_ampacity = None
    for csa in STANDARD_CSA:
        capacity = BASE_AMPACITY_CU_XLPE.get(csa, 0) * derate
        if material == "aluminium":
            capacity *= 0.78          # approximate Al/Cu ampacity ratio
        if capacity >= per_run:
            by_ampacity = csa
            break
    if by_ampacity is None:
        by_ampacity = STANDARD_CSA[-1]

    by_drop = None
    for csa in STANDARD_CSA:
        _v, pct = voltage_drop(
            csa * parallel_runs, current_a, length_m, voltage_v, phases,
            material, power_factor,
        )
        if pct <= max_voltage_drop_pct:
            by_drop = csa
            break
    if by_drop is None:
        by_drop = STANDARD_CSA[-1]

    by_fault = None
    if fault_current_a:
        need = adiabatic_minimum_csa(
            fault_current_a, clearing_time_s, material, insulation
        )
        for csa in STANDARD_CSA:
            if csa >= need:
                by_fault = csa
                break
        by_fault = by_fault or STANDARD_CSA[-1]

    candidates = {"ampacity": by_ampacity, "voltage_drop": by_drop}
    if by_fault:
        candidates["short_circuit"] = by_fault

    chosen = max(candidates.values())
    governing = [k for k, v in candidates.items() if v == chosen]

    final_v, final_pct = voltage_drop(
        chosen * parallel_runs, current_a, length_m, voltage_v, phases,
        material, power_factor,
    )
    capacity = BASE_AMPACITY_CU_XLPE.get(chosen, 0) * derate * (
        0.78 if material == "aluminium" else 1.0
    )

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
        "current_a": current_a,
        "current_per_run_a": per_run,
        "length_m": length_m,
        "voltage_v": voltage_v,
        "phases": phases,
        "ampacity_a": capacity * parallel_runs,
        "utilisation": (
            current_a / (capacity * parallel_runs) if capacity > 0 else None
        ),
        "voltage_drop_v": final_v,
        "voltage_drop_pct": final_pct,
        "derating_factor": derate,
        "governing_criterion": "+".join(governing),
        "candidates": candidates,
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
