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
    "fuse_gg": 1.60,   # IEC 60269 general purpose fuse
    "fuse_gpv": 1.45,  # IEC 60269-6 PV fuse
}

# Breaker curves: multiple of In for instantaneous magnetic trip.
TRIP_CURVES = {
    "B": (3, 5),      # resistive loads, long cables
    "C": (5, 10),     # general purpose, mixed loads
    "D": (10, 20),    # high inrush: motors, transformers
}


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


def select_pv_string_fuse(module_isc_a, n_strings_parallel, module_fuse_rating_a=None):
    """
    String overcurrent protection per IEC 62548.

    Fuses are only required when three or more strings are in parallel: with
    two strings, the maximum reverse current one string can drive into a
    faulted other is one string's Isc, which the module withstands. Fitting
    fuses to a two-string array is a common and harmless waste; omitting
    them from a six-string array is neither.
    """
    required = n_strings_parallel >= 3
    if not required:
        return {
            "required": False,
            "reason": (
                f"With {n_strings_parallel} string(s) in parallel, the maximum "
                f"reverse fault current is below the module's own withstand. "
                f"IEC 62548 does not require string fuses below three parallel "
                f"strings."
            ),
        }

    # 1.5 x Isc is the standard rule; the fuse must also be below the
    # module's maximum series fuse rating.
    computed = 1.5 * module_isc_a
    rating = next_rating(computed)

    warning = None
    if module_fuse_rating_a and rating > module_fuse_rating_a:
        warning = (
            f"The calculated fuse rating ({rating} A) exceeds the module's "
            f"maximum series fuse rating ({module_fuse_rating_a} A). Reduce "
            f"the number of parallel strings per combiner."
        )

    return {
        "required": True,
        "device": "gPV fuse (IEC 60269-6)",
        "rating_a": rating,
        "calculated_a": computed,
        "module_isc_a": module_isc_a,
        "strings_parallel": n_strings_parallel,
        "max_reverse_current_a": (n_strings_parallel - 1) * module_isc_a,
        "warning": warning,
        "dc_rated": True,
    }


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


def select_spd(location_type="main", exposure="medium", dc_side=False,
               system_voltage_v=400, array_voltage_v=1000):
    """
    Surge protective device selection per IEC 61643 and IEC 62305.

    PV arrays are extended outdoor conductors on a roof and are exposed to
    induced surges even without a direct strike, so the DC side needs its
    own SPD - one on the AC side does not protect the array.
    """
    classes = {
        "main": ("Type 1+2", "12.5 kA (10/350)", "Origin of installation"),
        "sub": ("Type 2", "20 kA (8/20)", "Distribution board"),
        "equipment": ("Type 3", "5 kA (8/20)", "At sensitive equipment"),
    }
    cls, iimp, where = classes.get(location_type, classes["sub"])

    if dc_side:
        return {
            "class": "Type 2 DC",
            "discharge_current": "20 kA (8/20)",
            "location": "PV combiner and inverter DC input",
            "uc_v": array_voltage_v * 1.2,
            "notes": [
                "Must be DC rated. An AC SPD on a DC circuit cannot clear the "
                "follow current and will fail short.",
                "Required at the inverter when the DC cable run exceeds about "
                "10 m, and at the array when it exceeds about 50 m.",
            ],
        }

    return {
        "class": cls,
        "discharge_current": iimp,
        "location": where,
        "uc_v": system_voltage_v * 1.1 * math.sqrt(2) / math.sqrt(3) * 1.5,
        "exposure": exposure,
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
