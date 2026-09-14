"""
Worldwide technology cost library.

What this is: a set of published benchmark ranges, by technology and by
region, so that a user anywhere can start a study without inventing numbers.

What this is not: a quotation. Installed cost varies by more than a factor
of two between two projects in the same country in the same year, driven by
scale, ground conditions, grid connection, labour, import duty and financing.
Every figure here is a starting point to be replaced with a real quote, and
the engine reports which numbers came from the library and which the user
supplied, so a reader can tell the difference.

Two decisions worth naming:

  * Costs are stored per kW or per kWh, not per unit. A "unit" is a
    modelling convenience whose size the user chooses; cost per kW is the
    quantity that is actually published and comparable.

  * Regional multipliers are applied to a global base rather than storing a
    full matrix. A full matrix implies a precision that does not exist, and
    it goes stale unevenly. A base plus a stated multiplier makes the
    assumption visible and easy to override.

Sources are named per entry. Where a figure is an informed estimate rather
than a published benchmark it says so, because the difference matters to
anyone defending the study.
"""

from __future__ import annotations

# Reference year for every figure below, so a reader knows how stale it is.
REFERENCE_YEAR = 2024

# ---------------------------------------------------------------- regions
#
# Multipliers on the global base cost. They fold together labour rates,
# import duties, logistics, typical project scale and market maturity.
# They are indicative: a specific project can sit well outside its region's
# multiplier, and a real quote always wins.
REGIONS = {
    "global": {
        "label": "Global average", "multiplier": 1.00, "currency": "USD",
        "note": "IRENA and IEA global weighted averages.",
    },
    "middle_east": {
        "label": "Middle East and North Africa", "multiplier": 0.85,
        "currency": "USD",
        "note": "Low labour cost and very large PV tenders pull utility-scale "
                "solar well below the global average; imported equipment and "
                "currency access can push it back up sharply.",
    },
    "iran": {
        "label": "Iran", "multiplier": 0.95, "currency": "USD",
        "note": "Domestic manufacturing lowers some equipment costs while "
                "sanctions raise import and finance costs. Treat this "
                "multiplier as very uncertain and replace it with local "
                "quotations; the spread between projects is unusually wide.",
    },
    "europe": {
        "label": "Europe", "multiplier": 1.15, "currency": "EUR",
        "note": "Higher labour and permitting cost; mature supply chain.",
    },
    "north_america": {
        "label": "North America", "multiplier": 1.25, "currency": "USD",
        "note": "NREL ATB. Labour and interconnection costs are high; "
                "incentives are not included here and can offset much of it.",
    },
    "china": {
        "label": "China", "multiplier": 0.70, "currency": "CNY",
        "note": "Lowest equipment cost worldwide for PV, wind and batteries.",
    },
    "india": {
        "label": "India", "multiplier": 0.75, "currency": "INR",
        "note": "Low labour cost, large domestic manufacturing base.",
    },
    "southeast_asia": {
        "label": "Southeast Asia", "multiplier": 0.85, "currency": "USD",
        "note": "",
    },
    "sub_saharan_africa": {
        "label": "Sub-Saharan Africa", "multiplier": 1.30, "currency": "USD",
        "note": "Logistics, small project scale and expensive finance "
                "dominate. Diesel displacement economics are usually strong "
                "even so, because the alternative is also expensive.",
    },
    "latin_america": {
        "label": "Latin America", "multiplier": 1.05, "currency": "USD",
        "note": "",
    },
    "australia": {
        "label": "Australia and Oceania", "multiplier": 1.20, "currency": "AUD",
        "note": "High labour cost, excellent resource, very high rooftop PV "
                "penetration keeps residential installed cost low.",
    },
}

# ------------------------------------------------------------ technologies
#
# capex   : capital cost per kW (or per kWh where the entry says so)
# opex    : annual O&M as a FRACTION of capex, unless given absolutely
# life    : years
# repl    : replacement cost as a fraction of capex
#
# Ranges are (low, typical, high) so the UI can offer a sensible spread
# rather than a single number that reads as more certain than it is.
TECHNOLOGIES = {
    # ------------------------------------------------------------ solar
    "pv_utility": {
        "label": "Solar PV — utility scale",
        "basis": "kW", "capex": (550, 850, 1400), "opex_fraction": 0.014,
        "life": 30, "repl_fraction": 0.0,
        "source": "IRENA Renewable Power Generation Costs 2023; global "
                  "weighted average USD 758/kW in 2023.",
    },
    "pv_commercial": {
        "label": "Solar PV — commercial rooftop",
        "basis": "kW", "capex": (700, 1050, 1800), "opex_fraction": 0.015,
        "life": 30, "repl_fraction": 0.0,
        "source": "IRENA 2023; NREL ATB 2024 commercial segment.",
    },
    "pv_residential": {
        "label": "Solar PV — residential rooftop",
        "basis": "kW", "capex": (900, 1500, 2800), "opex_fraction": 0.016,
        "life": 30, "repl_fraction": 0.0,
        "source": "IRENA 2023. The spread here is the widest of any "
                  "technology: soft costs dominate and vary hugely by market.",
    },
    "pv_bifacial": {
        "label": "Solar PV — bifacial, ground mount",
        "basis": "kW", "capex": (600, 920, 1500), "opex_fraction": 0.014,
        "life": 30, "repl_fraction": 0.0,
        "source": "Estimated: about 8% above monofacial for typically 5-15% "
                  "more yield over high-albedo ground.",
    },
    "pv_floating": {
        "label": "Solar PV — floating",
        "basis": "kW", "capex": (900, 1300, 2000), "opex_fraction": 0.020,
        "life": 25, "repl_fraction": 0.0,
        "source": "World Bank / SERIS Floating Solar Handbook; typically "
                  "20-40% above ground mount.",
    },
    "csp_tower": {
        "label": "Concentrating solar power — tower with storage",
        "basis": "kW", "capex": (3500, 4700, 7000), "opex_fraction": 0.020,
        "life": 30, "repl_fraction": 0.0,
        "source": "IRENA 2023; global weighted average USD 4,585/kW. Cost "
                  "includes the thermal store, so compare it against PV plus "
                  "batteries, not against PV alone.",
    },
    # ------------------------------------------------------------- wind
    "wind_onshore": {
        "label": "Wind — onshore",
        "basis": "kW", "capex": (900, 1300, 2200), "opex_fraction": 0.025,
        "life": 25, "repl_fraction": 0.0,
        "source": "IRENA 2023; global weighted average USD 1,160/kW.",
    },
    "wind_offshore": {
        "label": "Wind — offshore, fixed bottom",
        "basis": "kW", "capex": (2500, 3200, 5000), "opex_fraction": 0.030,
        "life": 25, "repl_fraction": 0.0,
        "source": "IRENA 2023; global weighted average USD 2,800/kW.",
    },
    "wind_small": {
        "label": "Wind — small, under 100 kW",
        "basis": "kW", "capex": (2500, 4000, 7000), "opex_fraction": 0.030,
        "life": 20, "repl_fraction": 0.50,
        "source": "Estimated from small-wind market surveys. Small wind is "
                  "far more expensive per kW than utility scale and usually "
                  "loses to PV unless the site is genuinely windy and shaded.",
    },
    # ---------------------------------------------------------- storage
    "battery_lfp": {
        "label": "Battery — lithium iron phosphate, 4-hour system",
        "basis": "kWh", "capex": (170, 260, 420), "opex_fraction": 0.020,
        "life": 15, "repl_fraction": 0.60,
        "source": "BNEF and NREL ATB 2024 turnkey system cost including "
                  "PCS, containers and installation, not the bare cell.",
    },
    "battery_nmc": {
        "label": "Battery — lithium NMC",
        "basis": "kWh", "capex": (190, 290, 460), "opex_fraction": 0.020,
        "life": 13, "repl_fraction": 0.60,
        "source": "NREL ATB 2024.",
    },
    "battery_lead_acid": {
        "label": "Battery — lead acid",
        "basis": "kWh", "capex": (100, 160, 260), "opex_fraction": 0.030,
        "life": 7, "repl_fraction": 0.90,
        "source": "Cheap per kWh and expensive per cycle. Its short life "
                  "usually makes it more costly over a project than lithium, "
                  "which the replacement schedule in the model will show.",
    },
    "battery_flow": {
        "label": "Battery — vanadium redox flow",
        "basis": "kWh", "capex": (300, 480, 800), "opex_fraction": 0.020,
        "life": 20, "repl_fraction": 0.30,
        "source": "Estimated from published projects. Power and energy scale "
                  "independently, so long-duration duty is where it competes.",
    },
    "pumped_hydro": {
        "label": "Pumped hydro storage",
        "basis": "kW", "capex": (1200, 2000, 4500), "opex_fraction": 0.015,
        "life": 60, "repl_fraction": 0.15,
        "source": "IEA and IHA. Extremely site-specific — the number is "
                  "dominated by civil works, not equipment.",
    },
    "flywheel": {
        "label": "Flywheel storage",
        "basis": "kW", "capex": (1500, 2800, 5000), "opex_fraction": 0.020,
        "life": 20, "repl_fraction": 0.30,
        "source": "Estimated. Priced per kW because it is a power device; "
                  "quoting it per kWh makes it look absurd.",
    },
    "supercapacitor": {
        "label": "Supercapacitor bank",
        "basis": "kW", "capex": (300, 600, 1200), "opex_fraction": 0.010,
        "life": 15, "repl_fraction": 0.40,
        "source": "Estimated.",
    },
    "caes": {
        "label": "Compressed air energy storage",
        "basis": "kW", "capex": (800, 1400, 2500), "opex_fraction": 0.020,
        "life": 30, "repl_fraction": 0.20,
        "source": "Estimated. Needs suitable geology; without a cavern the "
                  "above-ground vessel cost changes the picture entirely.",
    },
    "thermal_storage": {
        "label": "Thermal storage — sensible heat",
        "basis": "kWh", "capex": (15, 35, 80), "opex_fraction": 0.010,
        "life": 25, "repl_fraction": 0.20,
        "source": "Estimated. Far cheaper per kWh than any electrical store, "
                  "which is why it wins wherever the demand is actually heat.",
    },
    "hydrogen_electrolyser": {
        "label": "Hydrogen — electrolyser",
        "basis": "kW", "capex": (800, 1400, 2600), "opex_fraction": 0.030,
        "life": 15, "repl_fraction": 0.40,
        "source": "IEA Global Hydrogen Review 2024. Alkaline at the low end, "
                  "PEM at the high end.",
    },
    "hydrogen_tank": {
        "label": "Hydrogen — compressed storage",
        "basis": "kWh", "capex": (8, 18, 40), "opex_fraction": 0.015,
        "life": 25, "repl_fraction": 0.20,
        "source": "IEA 2024, 350-700 bar vessels. Cheap per kWh, which is why "
                  "hydrogen suits seasonal duty despite its poor round trip.",
    },
    "fuel_cell": {
        "label": "Fuel cell",
        "basis": "kW", "capex": (1200, 2200, 4000), "opex_fraction": 0.040,
        "life": 15, "repl_fraction": 0.50,
        "source": "IEA 2024; stack replacement is the dominant recurring "
                  "cost and is scheduled on run hours, not years.",
    },
    # -------------------------------------------------------- dispatchable
    "genset_diesel": {
        "label": "Diesel generator",
        "basis": "kW", "capex": (200, 400, 800), "opex_fraction": 0.000,
        "life": 15, "repl_fraction": 0.80,
        "source": "Cheap to buy, expensive to run. Capital is almost "
                  "irrelevant next to lifetime fuel, so a study that skimps "
                  "on fuel price accuracy is not a study.",
    },
    "genset_gas": {
        "label": "Gas generator",
        "basis": "kW", "capex": (500, 900, 1600), "opex_fraction": 0.000,
        "life": 20, "repl_fraction": 0.70,
        "source": "Estimated.",
    },
    "chp": {
        "label": "Combined heat and power",
        "basis": "kW", "capex": (900, 1600, 3000), "opex_fraction": 0.000,
        "life": 15, "repl_fraction": 0.70,
        "source": "Estimated. Economics depend almost entirely on the heat "
                  "credit, so a heat demand profile is not optional.",
    },
    "biomass": {
        "label": "Biomass gasifier / generator",
        "basis": "kW", "capex": (1500, 2500, 4500), "opex_fraction": 0.035,
        "life": 20, "repl_fraction": 0.50,
        "source": "IRENA 2023; global weighted average around USD 2,160/kW.",
    },
    "geothermal": {
        "label": "Geothermal — binary cycle",
        "basis": "kW", "capex": (2500, 4000, 7000), "opex_fraction": 0.025,
        "life": 30, "repl_fraction": 0.20,
        "source": "IRENA 2023. Drilling risk sits outside this number and is "
                  "often the deciding factor.",
    },
    "hydro_small": {
        "label": "Small hydro, under 10 MW",
        "basis": "kW", "capex": (1300, 2400, 5000), "opex_fraction": 0.020,
        "life": 40, "repl_fraction": 0.25,
        "source": "IRENA 2023. Civil works dominate; head and access decide "
                  "the cost far more than the turbine.",
    },
    "tidal": {
        "label": "Tidal stream",
        "basis": "kW", "capex": (4000, 7000, 12000), "opex_fraction": 0.050,
        "life": 25, "repl_fraction": 0.40,
        "source": "Ocean Energy Europe. Pre-commercial: costs are falling "
                  "quickly and published figures date fast.",
    },
    "wave": {
        "label": "Wave energy converter",
        "basis": "kW", "capex": (5000, 9000, 16000), "opex_fraction": 0.060,
        "life": 20, "repl_fraction": 0.40,
        "source": "Ocean Energy Europe. Earlier stage than tidal, with a "
                  "correspondingly wider range.",
    },
    # -------------------------------------------------------- balance of plant
    "inverter": {
        "label": "PV inverter",
        "basis": "kW", "capex": (40, 70, 130), "opex_fraction": 0.010,
        "life": 12, "repl_fraction": 0.90,
        "source": "Typically replaced once or twice over a PV system's life, "
                  "which the replacement schedule handles.",
    },
    "battery_pcs": {
        "label": "Battery power conversion system",
        "basis": "kW", "capex": (60, 110, 200), "opex_fraction": 0.010,
        "life": 15, "repl_fraction": 0.85,
        "source": "Estimated.",
    },
    "ev_charger_ac": {
        "label": "EV charger — AC, 7-22 kW",
        "basis": "kW", "capex": (150, 350, 700), "opex_fraction": 0.030,
        "life": 12, "repl_fraction": 0.80,
        "source": "Estimated; installation often exceeds the hardware cost.",
    },
    "ev_charger_dc": {
        "label": "EV charger — DC fast, 50-350 kW",
        "basis": "kW", "capex": (400, 750, 1400), "opex_fraction": 0.040,
        "life": 10, "repl_fraction": 0.80,
        "source": "Estimated. Grid connection reinforcement is frequently "
                  "the largest single line and is not included here.",
    },
    "ev_charger_v2g": {
        "label": "EV charger — bidirectional V2G",
        "basis": "kW", "capex": (400, 900, 1800), "opex_fraction": 0.040,
        "life": 12, "repl_fraction": 0.80,
        "source": "Estimated; roughly two to three times a unidirectional "
                  "unit of the same rating.",
    },
}

# ---------------------------------------------------------------- currency
#
# Indicative rates per 1 USD. They are a convenience for presentation, not a
# financial data feed, and the UI says so. A study that matters should be
# done in the currency the equipment is bought in.
CURRENCIES = {
    "USD": {"symbol": "$", "per_usd": 1.0, "label": "US dollar"},
    "EUR": {"symbol": "€", "per_usd": 0.92, "label": "Euro"},
    "GBP": {"symbol": "£", "per_usd": 0.79, "label": "Pound sterling"},
    "IRR": {"symbol": "﷼", "per_usd": 42000.0, "label": "Iranian rial",
            "note": "Official rate. The market rate differs by a large "
                    "multiple; use the rate your procurement actually faces."},
    "IRT": {"symbol": "تومان", "per_usd": 4200.0, "label": "Iranian toman",
            "note": "One toman is ten rials. Same caveat on the rate."},
    "AED": {"symbol": "د.إ", "per_usd": 3.67, "label": "UAE dirham"},
    "SAR": {"symbol": "﷼", "per_usd": 3.75, "label": "Saudi riyal"},
    "TRY": {"symbol": "₺", "per_usd": 32.5, "label": "Turkish lira"},
    "INR": {"symbol": "₹", "per_usd": 83.0, "label": "Indian rupee"},
    "CNY": {"symbol": "¥", "per_usd": 7.2, "label": "Chinese yuan"},
    "JPY": {"symbol": "¥", "per_usd": 150.0, "label": "Japanese yen"},
    "AUD": {"symbol": "A$", "per_usd": 1.52, "label": "Australian dollar"},
    "CAD": {"symbol": "C$", "per_usd": 1.36, "label": "Canadian dollar"},
    "ZAR": {"symbol": "R", "per_usd": 18.5, "label": "South African rand"},
    "BRL": {"symbol": "R$", "per_usd": 5.1, "label": "Brazilian real"},
    "NGN": {"symbol": "₦", "per_usd": 1550.0, "label": "Nigerian naira"},
    "EGP": {"symbol": "E£", "per_usd": 48.0, "label": "Egyptian pound"},
    "PKR": {"symbol": "₨", "per_usd": 278.0, "label": "Pakistani rupee"},
    "IDR": {"symbol": "Rp", "per_usd": 15800.0, "label": "Indonesian rupiah"},
}


def technology_cost(tech, region="global", level="typical", currency=None,
                    overrides=None):
    """
    Look up a cost entry, apply the regional multiplier and convert currency.

    Returns a dict with the resolved figures and a `provenance` field naming
    the source and every adjustment applied, so a report can show where the
    number came from rather than presenting it as fact.
    """
    entry = TECHNOLOGIES.get(tech)
    if not entry:
        raise KeyError(
            f"unknown technology '{tech}'. Available: "
            f"{', '.join(sorted(TECHNOLOGIES))}"
        )
    reg = REGIONS.get(region, REGIONS["global"])
    idx = {"low": 0, "typical": 1, "high": 2}.get(level, 1)

    base = float(entry["capex"][idx])
    multiplier = reg["multiplier"]
    capex = base * multiplier

    cur = currency or reg.get("currency", "USD")
    rate = CURRENCIES.get(cur, CURRENCIES["USD"])["per_usd"]
    capex_local = capex * rate

    out = {
        "technology": tech,
        "label": entry["label"],
        "basis": entry["basis"],
        "capex_per_unit": capex_local,
        "capex_usd": capex,
        "replacement_per_unit": capex_local * entry["repl_fraction"],
        "om_per_unit_year": capex_local * entry["opex_fraction"],
        "lifetime_years": entry["life"],
        "currency": cur,
        "region": region,
        "level": level,
        "provenance": {
            "base_usd_per_unit": base,
            "reference_year": REFERENCE_YEAR,
            "regional_multiplier": multiplier,
            "region_note": reg.get("note", ""),
            "currency_rate_per_usd": rate,
            "source": entry["source"],
            "user_overridden": False,
        },
    }

    if overrides and tech in overrides:
        for k, v in overrides[tech].items():
            out[k] = v
        out["provenance"]["user_overridden"] = True
        out["provenance"]["source"] = "User-supplied figures."
    return out


def unit_costs(tech, unit_size, region="global", level="typical",
               currency=None, overrides=None):
    """
    Convert a per-kW or per-kWh benchmark into the per-UNIT costs the
    optimiser needs, given the chosen unit size.
    """
    c = technology_cost(tech, region, level, currency, overrides)
    size = float(unit_size)
    return {
        "capital_cost": c["capex_per_unit"] * size,
        "replacement_cost": c["replacement_per_unit"] * size,
        "om_cost_per_year": c["om_per_unit_year"] * size,
        "lifetime_years": c["lifetime_years"],
        "currency": c["currency"],
        "basis": c["basis"],
        "unit_size": size,
        "provenance": c["provenance"],
        "label": c["label"],
    }


def catalogue(region="global", currency=None, level="typical"):
    """The whole library resolved for one region, for the UI to browse."""
    out = {}
    for tech in TECHNOLOGIES:
        try:
            out[tech] = technology_cost(tech, region, level, currency)
        except Exception:
            continue
    return out


def convert(amount, from_currency, to_currency):
    """Convert between the indicative rates."""
    a = CURRENCIES.get(from_currency, CURRENCIES["USD"])["per_usd"]
    b = CURRENCIES.get(to_currency, CURRENCIES["USD"])["per_usd"]
    if a <= 0:
        return amount
    return amount / a * b


def format_money(amount, currency="USD", decimals=0):
    c = CURRENCIES.get(currency, CURRENCIES["USD"])
    return f"{c['symbol']}{amount:,.{decimals}f}"


def caveats(region="global", currency="USD"):
    """
    The statements a report must carry when it uses library figures.

    Returned as data rather than baked into the report so they translate
    with everything else.
    """
    notes = [
        f"Cost figures are {REFERENCE_YEAR} published benchmarks, not "
        f"quotations. Installed cost commonly varies by a factor of two "
        f"between comparable projects; replace these with real quotes before "
        f"any commitment.",
    ]
    reg = REGIONS.get(region)
    if reg and reg.get("note"):
        notes.append(f"{reg['label']}: {reg['note']}")
    cur = CURRENCIES.get(currency)
    if cur and cur.get("note"):
        notes.append(f"{cur['label']}: {cur['note']}")
    if currency != "USD":
        notes.append(
            "Currency conversion uses an indicative fixed rate. It is a "
            "presentation convenience, not a financial data feed."
        )
    return notes
