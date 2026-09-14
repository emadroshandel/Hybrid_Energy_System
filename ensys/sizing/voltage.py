"""
Distribution voltage and the division of a plant into units.

Two mistakes are easy to make when turning a sized system into an
electrical design, and both produce numbers that look like arithmetic and
read as nonsense to anyone who builds things.

The first is to size a feeder for the whole technology at once. Fifteen
megawatts of wind is not one machine on one cable; it is five turbines,
each with its own terminals, its own cable and its own protection. Sizing
the collection as a single conductor gives 28,000 A and forty cables in
parallel, which is not a design, it is a symptom.

The second is to keep everything at low voltage however large it gets. Low
voltage runs out at a few thousand amps — that is what switchgear is built
to, what a busbar can carry, and what a cable can be terminated into.
Beyond it the plant is collected at low voltage in blocks, stepped up
through a transformer per block, and carried at medium voltage. This is
why a wind farm has a transformer at the base of every tower.

The rules here are the ordinary ones: an LV feeder is kept within the
range of standard LV switchgear, and the medium-voltage level is the
lowest standard one at which the connection current fits a normal MV
feeder.
"""

from __future__ import annotations

import math

# Standard distribution voltages, V.
LV_LEVELS = [230.0, 400.0, 690.0]
MV_LEVELS = [3300.0, 6600.0, 11000.0, 20000.0, 22000.0, 33000.0]

# The current at which low voltage stops being an engineering option.
# Standard LV air circuit breakers reach 6300 A and LV busbar systems a
# little beyond, but a design sitting at that limit has no margin and no
# practical cable termination, so the changeover is set below it.
MAX_LV_FEEDER_A = 1600.0        # one cable feeder, one set of conductors
MAX_LV_CONNECTION_A = 4000.0    # the whole site's incomer
BUSBAR_A = 2000.0               # beyond this a feeder is bus duct, not cable

# Machines above a few hundred kW are not built for 400 V. Large PV
# inverters, wind converters and battery PCS are 690 V machines, which is
# why a megawatt-scale plant's low-voltage collection runs at 690 V and
# only the auxiliaries and the site loads sit at 400 V.
MACHINE_LV_V = 690.0
MACHINE_LV_THRESHOLD_KW = 500.0

# A normal medium-voltage feeder. Above this an MV circuit is split too.
MAX_MV_FEEDER_A = 630.0

# Practical limit on conductors in parallel per circuit. Beyond about four
# the terminations, the current sharing and the containment all become the
# governing problem, and the answer is a busbar or a higher voltage.
MAX_PARALLEL_RUNS = 4

# Standard transformer sizes, kVA.
TRANSFORMER_KVA = [50, 100, 160, 200, 250, 315, 400, 500, 630, 800, 1000,
                   1250, 1600, 2000, 2500, 3150, 4000, 5000, 6300]


def current_for(kw, voltage_v, phases=3, power_factor=0.95):
    """Line current for a real power at a voltage."""
    if voltage_v <= 0:
        return 0.0
    pf = power_factor or 1.0
    if phases == 3:
        return kw * 1000.0 / (math.sqrt(3) * voltage_v * pf)
    if phases == 1:
        return kw * 1000.0 / (voltage_v * pf)
    return kw * 1000.0 / voltage_v          # DC


def standard_transformer(kva):
    for s in TRANSFORMER_KVA:
        if s >= kva - 1e-9:
            return float(s)
    return float(TRANSFORMER_KVA[-1])


def select_mv_level(kva, power_factor=0.95):
    """
    The lowest standard medium voltage at which the connection fits a
    normal MV feeder. Going higher than necessary costs switchgear money
    for no benefit; going lower needs parallel MV circuits.
    """
    for v in MV_LEVELS:
        if current_for(kva * power_factor, v, 3, power_factor) <= MAX_MV_FEEDER_A:
            return v
    return MV_LEVELS[-1]


def machine_voltage(unit_kw, site_lv_v=400.0):
    """
    The voltage a machine of this size is actually built at.

    Below a few hundred kilowatts converters are 400 V devices. Above it
    they are 690 V, and pretending otherwise is what produces feeder
    currents no terminal could accept.
    """
    if unit_kw >= MACHINE_LV_THRESHOLD_KW and site_lv_v < MACHINE_LV_V:
        return MACHINE_LV_V
    return site_lv_v


def select_distribution(plant_kw, connection_kw=None, lv_voltage_v=400.0,
                        phases=3, power_factor=0.95):
    """
    Decide how the installation is distributed.

    Returns the voltage the site is connected at, the voltage its machines
    sit at, and whether a step-up is needed — with the reason recorded, so
    the report can say why rather than presenting a transformer that
    appeared from nowhere.
    """
    rating_kw = max(plant_kw, connection_kw or 0.0)
    lv_current = current_for(rating_kw, lv_voltage_v, phases, power_factor)

    if lv_current <= MAX_LV_CONNECTION_A:
        return {
            "is_mv": False,
            "lv_voltage_v": lv_voltage_v,
            "mv_voltage_v": None,
            "connection_voltage_v": lv_voltage_v,
            "connection_current_a": lv_current,
            "reason": (
                f"{rating_kw:,.0f} kW at {lv_voltage_v:,.0f} V is "
                f"{lv_current:,.0f} A, within the range of standard low-"
                f"voltage switchgear, so the site is connected at low "
                f"voltage."
            ),
        }

    mv = select_mv_level(rating_kw / (power_factor or 1.0), power_factor)
    mv_current = current_for(rating_kw, mv, phases, power_factor)
    return {
        "is_mv": True,
        "lv_voltage_v": lv_voltage_v,
        "mv_voltage_v": mv,
        "connection_voltage_v": mv,
        "connection_current_a": mv_current,
        "reason": (
            f"{rating_kw:,.0f} kW would be {lv_current:,.0f} A at "
            f"{lv_voltage_v:,.0f} V, beyond the {MAX_LV_CONNECTION_A:,.0f} A "
            f"limit for a low-voltage connection. The site is therefore "
            f"connected at {mv / 1000:,.0f} kV ({mv_current:,.0f} A) and the "
            f"generation is collected at low voltage and stepped up."
        ),
    }


# Conductors are sized for the design current, not the nominal one: the
# continuous-duty factor is applied before the ampacity check, so the limit
# on a feeder has to be compared against the same number.
DESIGN_FACTOR = 1.25


def split_into_units(total_kw, natural_units=1, voltage_v=400.0, phases=3,
                     power_factor=0.95, max_current_a=MAX_LV_FEEDER_A,
                     allow_split=True):
    """
    How many separate feeders this technology is actually built as.

    `natural_units` is the physical count the sizing produced — turbines,
    inverters, converter blocks. Each has its own terminals and its own
    cable, so that is the starting point.

    A physical machine is never subdivided: a 3 MW turbine is one machine
    with one set of terminals, and if its current is beyond what cable can
    carry the answer is bus duct, not two half-turbines. Load feeders and
    other things that genuinely can be split into more circuits pass
    allow_split.
    """
    units = max(1, int(natural_units or 1))
    if total_kw <= 0:
        return {"units": units, "unit_kw": 0.0, "unit_current_a": 0.0,
                "busbar": False, "split_beyond_natural": False}

    def design_current(n):
        return current_for(total_kw / n, voltage_v, phases,
                           power_factor) * DESIGN_FACTOR

    current = design_current(units)
    if allow_split:
        while current > max_current_a and units < 200:
            units += 1
            current = design_current(units)

    return {
        "units": units,
        "unit_kw": total_kw / units,
        "unit_current_a": current,
        "busbar": current > BUSBAR_A,
        "split_beyond_natural": units > max(1, int(natural_units or 1)),
    }


def feeder(total_kw, natural_units=1, site_lv_v=400.0, phases=3,
           power_factor=0.95, allow_split=False):
    """
    The feeder arrangement for one technology: how many, at what voltage,
    carrying what current.

    The machine voltage is chosen from the size of a single unit, then the
    split is re-checked at that voltage — a 3 MW turbine is a 690 V
    machine, and asking how many 400 V feeders it needs is the wrong
    question.
    """
    first = split_into_units(total_kw, natural_units, site_lv_v, phases,
                             power_factor, allow_split=allow_split)
    v = machine_voltage(first["unit_kw"], site_lv_v)
    out = split_into_units(total_kw, natural_units, v, phases, power_factor,
                           allow_split=allow_split)
    out["voltage_v"] = v
    return out


MAX_BLOCK_KVA = 3150.0


def collector_blocks(total_kw, natural_units=1, per_machine=False,
                     power_factor=0.95, max_block_kva=MAX_BLOCK_KVA):
    """
    How many low-voltage blocks a technology is collected in before the
    step-up.

    Two different arrangements are in use, and treating them alike is what
    puts a transformer on the end of every inverter:

      * `per_machine` — one transformer per machine, because the machine
        stands where it stands. A wind turbine has its own transformer at
        the base of the tower; running fifty turbines' worth of low-voltage
        cable back to a central point is not an option at that distance.

      * otherwise — machines are grouped into a station and share one
        transformer up to a standard block rating. Fifty 100 kWp inverters
        in one PV field are four inverter stations, not fifty; a 5 MW plant
        with fifty step-up transformers is not a plant anyone builds, and
        drawing one says the tool does not know how these are put together.

    Either way a block never exceeds a standard distribution transformer.
    """
    kva = total_kw / (power_factor or 1.0)
    if kva <= 0:
        return 0
    count = max(1, int(natural_units or 1)) if per_machine else 1
    while kva / count > max_block_kva and count < 200:
        count += 1
    return count


def step_up_groups(groups, power_factor=0.95, max_block_kva=MAX_BLOCK_KVA):
    """
    Size the step-up transformers technology by technology.

    `groups` is an iterable of (name, total_kw, natural_units, per_machine).
    Returns (rows, total_count, total_kva). One rating for the whole plant
    would be an average of dissimilar things — a 500 kVA turbine
    transformer and a 2500 kVA inverter station are not two of anything —
    so each technology carries its own count and rating.
    """
    rows = []
    for name, kw, units, per_machine in groups:
        if not kw or kw <= 0:
            continue
        count = collector_blocks(kw, units, per_machine, power_factor,
                                 max_block_kva)
        kva = kw / (power_factor or 1.0)
        each = standard_transformer(kva / count * 1.1)   # 10% sizing margin
        rows.append({
            "name": name,
            "count": count,
            "kva_each": each,
            "kw": kw,
            "total_kva": each * count,
            "per_machine": bool(per_machine),
            "impedance_pct": 6.0 if each <= 1000 else 6.5,
            "vector_group": "Dyn11",
        })
    return (rows,
            sum(r["count"] for r in rows),
            sum(r["total_kva"] for r in rows))
