"""
Protection and switchgear selection.

Covers the devices a hybrid system needs on each circuit: overcurrent
protection, earth-fault protection, surge protection and isolation.

The governing rule for overcurrent coordination, IEC 60364-4-43:

    I_B  <=  I_N  <=  I_Z          and        I_2  <=  1.45 * I_Z

where I_B is the design current, I_N the device rating, I_Z the conductor's
derated ampacity and I_2 the device's conventional tripping current. Both
inequalities must hold. Checking only the first is the common shortcut and
it permits a device that will let a cable overheat before it trips.

PV DC circuits are treated separately throughout, because they behave
unlike AC in the two ways that matter for protection: the fault current
from a PV array is barely above its operating current (so overcurrent
devices cannot detect a string fault the way they detect an AC fault), and
DC arcs do not self-extinguish at a current zero, so every isolator must be
DC-rated for the full array voltage.
"""

from __future__ import annotations

import math

# Standard breaker and fuse ratings, A (IEC 60947 / 60269 preferred values).
STANDARD_RATINGS = [
    6, 10, 13, 16, 20, 25, 32, 40, 50, 63, 80, 100, 125, 160, 200, 250,
    315, 400, 500, 630, 800, 1000, 1250, 1600, 2000, 2500, 3200, 4000,
]

# Conventional tripping current as a multiple of rating.
I2_FACTOR = {
    "mcb": 1.45,       # IEC 60898 miniature circuit breaker
    "mccb": 1.30,      # IEC 60947-2 moulded case
    "acb": 1.30,       # IEC 60947-2 air circuit breaker
    "fuse_gg": 1.60,   # IEC 60269 general purpose fuse
    "fuse_gpv": 1.45,  # IEC 60269-6 PV fuse
}

# Breaker curves: multiple of In for instantaneous magnetic trip.
TRIP_CURVES = {
    "B": (3, 5),      # resistive loads, long cables
    "C": (5, 10),     # general purpose, mixed loads
    "D": (10, 20),    # high inrush: motors, transformers
}

# Standard gPV fuse ratings, A (IEC 60269-6). String fuses come in finer
# steps than distribution devices; using the 6/10/13/16/20/25 A breaker
# series for them skips the 12 and 15 A fuses that are usually the right
# answer for a 7-9 A module and pushes the rating out of the IEC 62548
# window.
GPV_FUSE_RATINGS = [1, 2, 3, 4, 5, 6, 8, 10, 12, 15, 16, 20, 25, 30, 32,
                    40, 50, 63, 80, 100, 125, 160, 200, 250, 315, 400]

# Rated ultimate short-circuit breaking capacity typically available, kA.
# IEC 60364-4-43 cl. 434.5.1: a device's breaking capacity shall not be
# less than the prospective fault current where it is installed. A
# miniature breaker on a 25 kA busbar is a fault that becomes an explosion.
BREAKING_CAPACITY_KA = {
    "mcb": 15.0,       # IEC 60898-1 Icn 10 kA / IEC 60947-2 Icu 15 kA ranges;
                       # 6 kA domestic units exist - check the product
    "mccb": 36.0,      # IEC 60947-2, common Icu
    "acb": 65.0,       # IEC 60947-2 air circuit breaker
    "fuse_gg": 80.0,   # IEC 60269 HRC fuse
    "fuse_gpv": 30.0,  # IEC 60269-6 (DC, current-limited source anyway)
}

# Energy let through by a current-limiting device in the instantaneous
# region, A^2 s. IEC 60364-4-43 cl. 434.5.2 allows the adiabatic check to
# use the device's let-through I^2t instead of I^2 x t when the fault is
# cleared in under 0.1 s. Values for MCBs are the energy-limiting class 3
# limits of IEC 60898-1 Annex ZA at 10 kA; MCCBs and fuses are represented
# by a half-cycle clearing time. A real design uses the manufacturer's
# let-through curve - these are conservative stand-ins for it.
MCB_CLASS3_I2T = [(16, 37_000.0), (32, 52_000.0)]
CLEARING_TIME_S = {
    "mcb": 0.010,
    "fuse_gg": 0.010,
    "fuse_gpv": 0.010,
    "mccb": 0.020,     # instantaneous release, one cycle at 50 Hz
    "acb": 0.300,      # short-time delay kept for selectivity
}


def let_through_i2t(device, rating_a, fault_current_a):
    """
    I^2 t the conductor must survive when this device clears a fault.

    The adiabatic check used to multiply the prospective fault current by
    a flat 0.4 s for every circuit. 0.4 s is the IEC 60364-4-41 maximum
    DISCONNECTION time for a TN final circuit - a shock-protection limit,
    not the time a breaker takes to clear a bolted fault - and applying it
    to a 10 kA fault put a 50 mm2 cable on a 10 A circuit. A miniature
    breaker clears in the first half cycle and limits the energy to tens of
    kA^2 s; that, not I^2 x 0.4, is what the conductor sees.
    """
    i = float(fault_current_a or 0.0)
    if i <= 0:
        return 0.0
    t = CLEARING_TIME_S.get(device, 0.4)
    full = i * i * t
    if device == "mcb":
        for upto, limit in MCB_CLASS3_I2T:
            if rating_a <= upto:
                return min(full, limit)
        return full
    return full


def check_breaking_capacity(device, fault_current_a):
    """IEC 60364-4-43 434.5.1 - breaking capacity against prospective fault."""
    cap_ka = BREAKING_CAPACITY_KA.get(device)
    fault_ka = float(fault_current_a or 0.0) / 1000.0
    ok = cap_ka is None or fault_ka <= cap_ka + 1e-9
    return {
        "device": device,
        "breaking_capacity_ka": cap_ka,
        "prospective_fault_ka": fault_ka,
        "adequate": ok,
    }


def upgrade_for_breaking_capacity(device, fault_current_a):
    """
    The lightest device class whose breaking capacity covers the fault.

    MCB -> MCCB -> ACB. Returned unchanged when it is already adequate.
    """
    order = ["mcb", "mccb", "acb"]
    if device not in order:
        return device
    for d in order[order.index(device):]:
        if check_breaking_capacity(d, fault_current_a)["adequate"]:
            return d
    return order[-1]


def next_rating(current_a, ratings=None):
    """Smallest standard device rating at or above a current."""
    ratings = ratings or STANDARD_RATINGS
    for r in ratings:
        if r >= current_a - 1e-9:
            return r
    return ratings[-1]


def select_overcurrent(design_current_a, cable_ampacity_a, device="mcb",
                       curve="C", circuit="AC"):
    """
    Select an overcurrent device satisfying both IEC 60364-4-43 conditions.

    Returns the selection with an explicit `compliant` flag and the reason
    when it is not, rather than silently returning an unsafe device.
    """
    i_b = float(design_current_a)
    i_z = float(cable_ampacity_a)

    rating = next_rating(i_b)
    i2_factor = I2_FACTOR.get(device, 1.45)

    # Condition 1: I_B <= I_N <= I_Z
    cond1 = i_b <= rating <= i_z + 1e-9
    # Condition 2: I_2 <= 1.45 * I_Z
    i2 = rating * i2_factor
    cond2 = i2 <= 1.45 * i_z + 1e-9

    reasons = []
    if not cond1:
        if rating > i_z:
            reasons.append(
                f"The smallest device that carries the {i_b:.1f} A design "
                f"current is {rating} A, which exceeds the cable's derated "
                f"capacity of {i_z:.1f} A. Increase the conductor size."
            )
    if not cond2:
        reasons.append(
            f"The device's conventional tripping current ({i2:.1f} A) exceeds "
            f"1.45 x the cable capacity ({1.45 * i_z:.1f} A). The cable could "
            f"be damaged before the device operates."
        )

    lo, hi = TRIP_CURVES.get(curve, (5, 10))
    return {
        "device": device,
        "rating_a": rating,
        "curve": curve if device in ("mcb", "mccb") else None,
        "circuit": circuit,
        "design_current_a": i_b,
        "cable_ampacity_a": i_z,
        "i2_a": i2,
        "magnetic_trip_range_a": [rating * lo, rating * hi],
        "compliant": cond1 and cond2,
        "conditions": {
            "IB<=IN<=IZ": cond1,
            "I2<=1.45*IZ": cond2,
        },
        "reasons": reasons,
        "dc_rated": circuit == "DC",
    }


def select_pv_string_fuse(module_isc_a, n_strings_parallel, module_fuse_rating_a=None,
                          has_battery_on_dc_bus=False):
    """
    String overcurrent protection per IEC 62548 / IEC TS 62257-7-1 5.3.4.

    The test is the module's own reverse-current withstand, not a fixed
    string count. A faulted string can be back-fed by every other string in
    parallel with it, so protection is needed when

        (Np - 1) x Isc_mod  >  I_MOD_MAX_OCPR

    where I_MOD_MAX_OCPR is the maximum series fuse rating on the module
    datasheet. When it is not given, 1.8 x Isc is assumed - the typical
    figure for crystalline modules (25 A for a 14 A module) - which makes
    the rule reduce to "three or more strings", the familiar shorthand.

    A battery on the same DC bus can drive far more than (Np-1) Isc into a
    fault, so IEC 62257-7-1 requires string protection whenever one is
    present, whatever the string count.

    The rating is chosen inside the IEC 62548 window

        1.5 x Isc_mod  <=  In  <=  2.4 x Isc_mod,   In <= I_MOD_MAX_OCPR

    from the gPV fuse series (IEC 60269-6).
    """
    isc = float(module_isc_a)
    np_ = max(1, int(n_strings_parallel))
    ocpr = float(module_fuse_rating_a) if module_fuse_rating_a else 1.8 * isc
    reverse = (np_ - 1) * isc

    required = reverse > ocpr + 1e-9 or (has_battery_on_dc_bus and np_ >= 1)
    if not required:
        return {
            "required": False,
            "strings_parallel": np_,
            "max_reverse_current_a": reverse,
            "module_max_series_fuse_a": ocpr,
            "reason": (
                f"With {np_} string(s) in parallel the worst reverse current "
                f"into a faulted string is {reverse:.1f} A, within the "
                f"module's {ocpr:.0f} A maximum series fuse rating. "
                f"IEC 62548 does not require string overcurrent protection."
            ),
            "standard": "IEC 62548 / IEC TS 62257-7-1 cl. 5.3.4",
        }

    lo, hi = 1.5 * isc, min(2.4 * isc, ocpr)
    rating = None
    for r in GPV_FUSE_RATINGS:
        if lo - 1e-9 <= r <= hi + 1e-9:
            rating = r
            break
    warning = None
    if rating is None:
        rating = next_rating(lo, GPV_FUSE_RATINGS)
        warning = (
            f"No standard gPV fuse lies between 1.5 x Isc ({lo:.1f} A) and "
            f"the lower of 2.4 x Isc and the module's maximum series fuse "
            f"({hi:.1f} A). The nearest is {rating} A; confirm the module's "
            f"reverse-current rating with the manufacturer or reduce the "
            f"number of parallel strings per combiner."
        )

    return {
        "required": True,
        "device": "gPV fuse (IEC 60269-6), both poles",
        "rating_a": rating,
        "calculated_a": lo,
        "window_a": [lo, hi],
        "module_isc_a": isc,
        "module_max_series_fuse_a": ocpr,
        "strings_parallel": np_,
        "max_reverse_current_a": reverse,
        "reason": (
            "A battery shares the DC bus; its fault current is not limited "
            "like a PV string's." if has_battery_on_dc_bus and reverse <= ocpr
            else
            f"{np_} strings in parallel can drive {reverse:.1f} A back into a "
            f"faulted string, above the module's {ocpr:.0f} A rating."
        ),
        "warning": warning,
        "dc_rated": True,
        "standard": "IEC 62548 / IEC TS 62257-7-1 cl. 5.3.4",
    }


def pv_string_cable_current(module_isc_a, n_strings_parallel, fuse=None,
                            k_i=1.25):
    """
    Minimum current a PV string cable must carry, IEC 62548-1:2023 Table 5.

      string protection provided   In of the string fuse
      not provided, one string     K_I x Isc_mod
      not provided, Npo strings    In(downstream) + K_I x Isc_mod x (Npo - 1)

    with K_I = 1.25 x K_corr (Annex F.4; K_corr > 1 for bifacial modules).
    With no downstream protection In is zero. The 2010 off-grid TS
    (IEC TS 62257-7-1 Table 6) used 1.45 in place of K_I; the 2023 edition
    of IEC 62548-1 supersedes it for this purpose.
    """
    isc = float(module_isc_a)
    np_ = max(1, int(n_strings_parallel))
    if fuse and fuse.get("required"):
        return max(float(fuse["rating_a"]), k_i * isc)
    return max(k_i * isc, k_i * isc * (np_ - 1))


def select_rcd(circuit_type="general", has_transformerless_inverter=False):
    """
    Residual current device selection.

    The type matters and is frequently got wrong. A transformerless PV
    inverter can inject smooth DC residual current, which blinds a type AC
    or type A RCD - it will not trip when it should. IEC 62109 requires
    either a type B RCD or an inverter with integrated DC fault monitoring.
    """
    if has_transformerless_inverter:
        return {
            "type": "B",
            "rating_ma": 30,
            "reason": (
                "A transformerless inverter can produce smooth DC residual "
                "current, which desensitises type AC and type A devices. "
                "IEC 62109-2 requires a type B RCD unless the inverter "
                "provides integrated DC residual monitoring (RCMU)."
            ),
        }
    if circuit_type == "final":
        return {
            "type": "A", "rating_ma": 30,
            "reason": "Additional protection for socket outlets, IEC 60364-4-41.",
        }
    return {
        "type": "A", "rating_ma": 300,
        "reason": "Fire protection on the distribution circuit.",
    }


STANDARD_UCPV_V = [600, 800, 1000, 1100, 1200, 1500]


def select_spd(location_type="main", exposure="medium", dc_side=False,
               system_voltage_v=400, array_voltage_v=1000, earthing="TN-S"):
    """
    Surge protective device selection per IEC 61643 / IEC 60364-5-53 and
    IEC 62305.

    PV arrays are extended outdoor conductors on a roof and are exposed to
    induced surges even without a direct strike, so the DC side needs its
    own SPD - one on the AC side does not protect the array.

    Maximum continuous operating voltage:
      * AC (IEC 60364-5-53 Table 534.2): Uc >= 1.1 x U0 line-to-earth in
        TN and TT systems; Uc >= the line voltage U in an IT system, where
        a first earth fault lifts the healthy phases to line voltage.
      * DC (IEC 61643-31 / -32): Ucpv >= the array's maximum open-circuit
        voltage, i.e. at the lowest expected cell temperature.

    The AC figure used to be 1.1 x U x sqrt(2) / sqrt(3) x 1.5 - a peak
    value with an unexplained 1.5 on it - which asked for a 540 V device on
    a 230/400 V system. Uc is an RMS rating.
    """
    classes = {
        "main": ("Type 1+2", "12.5 kA (10/350)", "Origin of installation"),
        "sub": ("Type 2", "20 kA (8/20)", "Distribution board"),
        "equipment": ("Type 3", "5 kA (8/20)", "At sensitive equipment"),
    }
    cls, iimp, where = classes.get(location_type, classes["sub"])

    if dc_side:
        need = float(array_voltage_v)
        ucpv = next((u for u in STANDARD_UCPV_V if u >= need - 1e-9),
                    STANDARD_UCPV_V[-1])
        return {
            "class": "Type 2 DC (PV)",
            "discharge_current": "20 kA (8/20)",
            "location": "PV combiner and inverter DC input",
            "uc_v": float(ucpv),
            "uc_required_v": need,
            "standard": "IEC 61643-31 / IEC 61643-32",
            "notes": [
                "Must be DC rated. An AC SPD on a DC circuit cannot clear the "
                "follow current and will fail short.",
                "Required at the inverter when the DC cable run exceeds about "
                "10 m, and at the array when it exceeds about 50 m.",
            ],
        }

    u_line = float(system_voltage_v)
    u0 = u_line / math.sqrt(3.0) if u_line > 300 else u_line
    if str(earthing).upper().startswith("IT"):
        uc_need = u_line
    else:
        uc_need = 1.1 * u0
    standard_uc = [150, 175, 275, 320, 385, 440, 600, 760]
    uc = next((u for u in standard_uc if u >= uc_need - 1e-9), uc_need)
    return {
        "class": cls,
        "discharge_current": iimp,
        "location": where,
        "uc_v": float(uc),
        "uc_required_v": uc_need,
        "earthing": earthing,
        "exposure": exposure,
        "standard": "IEC 60364-5-53 / IEC 61643-11",
        "notes": [
            "Coordinate with the earthing arrangement: the SPD's protection "
            "level Up must be below the withstand voltage of the equipment "
            "it protects.",
        ],
    }


def isolation_requirements(has_pv=False, has_battery=False, has_genset=False,
                           has_grid=False, array_voltage_v=1000):
    """
    Enumerate the isolation and switching devices the system needs.

    A hybrid system has multiple sources, which is what makes its isolation
    scheme different from a simple load installation: opening the main
    switch does not make the installation safe if a battery or PV array can
    still energise it. Every source needs its own lockable isolator, and the
    scheme needs signage saying so.
    """
    items = []
    if has_pv:
        items.append({
            "device": "PV array DC isolator",
            "rating": f"DC rated to {array_voltage_v} V, load-break",
            "location": "At the array and at the inverter",
            "standard": "IEC 60947-3 / IEC 62548",
            "note": (
                "Must be DC rated for the full open-circuit voltage at the "
                "coldest expected temperature. A DC arc does not self-"
                "extinguish; an AC-rated switch used on DC will weld closed."
            ),
        })
    if has_battery:
        items.append({
            "device": "Battery DC isolator and fuse",
            "rating": "DC rated, with a fuse close to the battery terminals",
            "location": "Within 300 mm of the battery",
            "standard": "IEC 62619 / IEC 60364-7-712",
            "note": (
                "A battery can deliver thousands of amps into a short. The "
                "protection must be at the source end of the cable, not at "
                "the far end."
            ),
        })
    if has_genset:
        items.append({
            "device": "Generator changeover / interlock",
            "rating": "Mechanically interlocked or 4-pole changeover",
            "location": "Main distribution board",
            "standard": "IEC 60364-5-51",
            "note": (
                "Must make back-feeding the network physically impossible, "
                "not merely electrically unlikely."
            ),
        })
    if has_grid:
        items.append({
            "device": "Grid interface protection and main switch",
            "rating": "Lockable, accessible to the network operator",
            "location": "Point of common coupling",
            "standard": "IEC 62116 / IEEE 1547 / local grid code",
            "note": (
                "Anti-islanding protection is mandatory: the system must "
                "disconnect within the time the local grid code specifies "
                "when the network is lost, so that it cannot energise a "
                "circuit someone is working on."
            ),
        })

    items.append({
        "device": "Multi-source warning signage",
        "rating": "Durable label at every isolation point",
        "location": "Main switchboard, meter position, each isolator",
        "standard": "IEC 60364-7-712",
        "note": (
            "The installation has multiple sources of supply. Isolating one "
            "does not make it dead."
        ),
    })
    return items


def check_discrimination(upstream_rating_a, downstream_rating_a, ratio=1.6):
    """
    Check selectivity between two series devices.

    A rule-of-thumb ratio test. Real discrimination depends on the published
    let-through energy curves of the specific devices, so this flags likely
    problems rather than certifying compliance.
    """
    if downstream_rating_a <= 0:
        return {"selective": False, "reason": "Invalid downstream rating."}
    actual = upstream_rating_a / downstream_rating_a
    ok = actual >= ratio
    return {
        "selective": ok,
        "ratio": actual,
        "required_ratio": ratio,
        "upstream_a": upstream_rating_a,
        "downstream_a": downstream_rating_a,
        "reason": (
            None if ok else
            f"The rating ratio is {actual:.2f}, below the {ratio:.1f} "
            f"rule-of-thumb for selectivity. A downstream fault may trip the "
            f"upstream device and black out more of the installation than "
            f"necessary. Verify against the manufacturer's let-through curves."
        ),
    }
