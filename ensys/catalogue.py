"""
Technology catalogue: the bridge between a configuration dict and an asset.

The interface sends a plain dict per technology; this module turns it into
the right asset object with the right cost figures. Keeping that mapping in
one place means adding a technology touches this file and nothing else in
the request path.

Each entry declares the fields the interface should show, with units and a
short explanation, so the UI is generated from the catalogue rather than
hand-written per technology and then drifting out of step with the model.
"""

from __future__ import annotations

from . import costs as costs_mod
from .models.evfleet import ARCHETYPES, SCENARIOS, SCENARIO_LABELS, EVFleet
from .models.generators import (
    BiomassGenerator, CHPUnit, CSPWithStorage, FuelCell, GeothermalPlant,
    ReservoirHydro, RunOfRiverHydro, TidalStream, WaveEnergyConverter,
)
from .models.genset import Generator
from .models.pv import PVArray
from .models.storage import (
    BatteryStorage, CompressedAirStorage, Flywheel, HydrogenStorage,
    PumpedHydroStorage, Supercapacitor, ThermalStorage,
)
from .models.wind import WindTurbine, library as wind_library


def F(key, label, default, unit=None, help=None, kind="number",
      options=None, step=None):
    """One configurable field, as the interface should render it."""
    return {
        "key": key, "label": label, "default": default, "unit": unit,
        "help": help, "kind": kind, "options": options, "step": step,
    }


# Fields every sizable technology shares.
COST_FIELDS = [
    F("capital_cost", "Capital cost per unit", 0, "currency",
      "Leave at zero to use the regional cost library."),
    F("replacement_cost", "Replacement cost per unit", 0, "currency"),
    F("om_cost_per_year", "O&M per unit per year", 0, "currency"),
]

# Cost fields a technology cannot accept, because it charges that cost on a
# different basis. A generator's maintenance is billed per RUNNING HOUR - an
# engine that never starts costs nothing to maintain - so an annual figure
# is not merely unused, it is the wrong quantity, and its constructor
# refuses it. Offering the field anyway put a number in the interface that
# crashed the build the moment anyone typed in it.
COST_FIELDS_NOT_APPLICABLE = {
    "genset": {"om_cost_per_year"},
    "biomass": {"om_cost_per_year"},
    "chp": {"om_cost_per_year"},
    "fuel_cell": {"om_cost_per_year"},
}


def cost_fields_for(tech):
    """The cost fields this technology can actually accept."""
    skip = COST_FIELDS_NOT_APPLICABLE.get(tech, set())
    return [f for f in COST_FIELDS if f["key"] not in skip]

CATALOGUE = {
    # ================================================================ solar
    "pv": {
        "label": "Solar PV", "group": "Solar", "class": "non_dispatchable",
        "cost_key": "pv_utility", "sizing_basis": "kW",
        "resource": ["ghi"],
        "fields": [
            F("unit_kwp", "Unit size", 25, "kWp",
              "The optimiser buys whole units. Smaller units give finer "
              "resolution; larger ones search faster."),
            F("tilt_deg", "Tilt", None, "°",
              "Leave blank for the latitude-based optimum."),
            F("azimuth_deg", "Azimuth", 180, "°",
              "180 is due south, 0 due north."),
            F("system_losses", "DC system losses", 0.14, "fraction",
              "Soiling, mismatch, wiring and degradation combined."),
            F("tracking", "Mounting", "fixed", None, None, "select",
              ["fixed", "single_axis", "two_axis"]),
        ],
    },
    "csp": {
        "label": "Concentrating solar power", "group": "Solar",
        "class": "dispatchable", "cost_key": "csp_tower",
        "sizing_basis": "kW", "resource": ["dni"],
        "fields": [
            F("rated_kw", "Power block rating", 1000, "kW"),
            F("solar_multiple", "Solar multiple", 2.0, None,
              "Field thermal rating divided by the power block's demand. "
              "Must exceed about 1.3 for storage to have anything to store."),
            F("storage_hours", "Thermal storage", 6, "hours",
              "Hours the block can run at rating from a full store."),
            F("block_efficiency", "Power block efficiency", 0.40, "fraction"),
        ],
    },
    # ================================================================= wind
    "wind": {
        "label": "Wind turbine", "group": "Wind",
        "class": "non_dispatchable", "cost_key": "wind_onshore",
        "sizing_basis": "kW", "resource": ["wind_speed"],
        "fields": [
            F("preset", "Machine", "", None,
              "Choose a library machine, or leave blank and enter your own.",
              "select", [""] + list(wind_library().keys())),
            F("rated_kw", "Rated power", 100, "kW"),
            F("hub_height_m", "Hub height", 30, "m",
              "Wind speed rises with height; doubling it typically adds "
              "10-15%."),
            F("reference_height_m", "Data measurement height", 10, "m",
              "The height your wind data was measured at. Not the hub "
              "height — confusing the two is a 10-20% error."),
            F("v_cutin", "Cut-in speed", 3, "m/s"),
            F("v_rated", "Rated speed", 12, "m/s"),
            F("v_cutout", "Cut-out speed", 25, "m/s"),
        ],
    },
    # ================================================================ hydro
    "run_of_river_hydro": {
        "label": "Run-of-river hydro", "group": "Hydro",
        "class": "non_dispatchable", "cost_key": "hydro_small",
        "sizing_basis": "kW", "resource": ["river_flow_m3s"],
        "fields": [
            F("design_flow_m3s", "Design flow", 1.0, "m³/s"),
            F("head_m", "Gross head", 20, "m"),
            F("efficiency", "Turbine efficiency", 0.85, "fraction"),
            F("min_flow_ratio", "Minimum technical flow", 0.20, "fraction",
              "Below this fraction of design flow the turbine cannot run."),
            F("compensation_flow_m3s", "Compensation flow", 0.0, "m³/s",
              "Environmental flow that must pass the intake. Usually a "
              "licence condition."),
        ],
    },
    "reservoir_hydro": {
        "label": "Reservoir hydro", "group": "Hydro",
        "class": "dispatchable", "cost_key": "hydro_small",
        "sizing_basis": "kW", "resource": ["river_flow_m3s"],
        "fields": [
            F("rated_kw", "Rated power", 500, "kW"),
            F("head_m", "Head", 40, "m"),
            F("reservoir_m3", "Reservoir volume", 50000, "m³"),
            F("efficiency", "Turbine efficiency", 0.88, "fraction"),
        ],
    },
    # ============================================================== marine
    "tidal": {
        "label": "Tidal stream", "group": "Marine",
        "class": "non_dispatchable", "cost_key": "tidal",
        "sizing_basis": "kW", "resource": ["tidal_velocity_ms"],
        "fields": [
            F("rated_kw", "Rated power", 100, "kW"),
            F("rotor_diameter_m", "Rotor diameter", 10, "m"),
            F("cut_in_ms", "Cut-in velocity", 0.7, "m/s"),
            F("rated_ms", "Rated velocity", 2.5, "m/s"),
            F("cp", "Power coefficient", 0.40, None),
        ],
    },
    "wave": {
        "label": "Wave energy converter", "group": "Marine",
        "class": "non_dispatchable", "cost_key": "wave",
        "sizing_basis": "kW", "resource": ["wave_height_m"],
        "fields": [
            F("rated_kw", "Rated power", 250, "kW"),
            F("capture_width_m", "Capture width", 20, "m"),
            F("capture_width_ratio", "Capture width ratio", 0.25, None,
              "Device efficiency. Most of the uncertainty in wave energy "
              "sits in this one number."),
        ],
    },
    # ========================================================== combustion
    "genset": {
        "label": "Diesel / gas generator", "group": "Dispatchable",
        "class": "dispatchable", "cost_key": "genset_diesel",
        "sizing_basis": "kW", "resource": [],
        "fields": [
            F("rated_kw", "Rated power", 100, "kW"),
            F("min_load_ratio", "Minimum load ratio", 0.30, "fraction",
              "Running below this causes wet stacking and engine damage."),
            F("fuel_curve", "Fuel curve", "diesel_medium", None, None,
              "select", ["diesel_small", "diesel_medium", "diesel_large",
                         "gas_natural"]),
            F("fuel_price", "Fuel price", 1.0, "currency/litre"),
            F("om_cost_per_hour", "O&M per running hour", 1.5, "currency"),
        ],
    },
    "biomass": {
        "label": "Biomass / biogas", "group": "Dispatchable",
        "class": "dispatchable", "cost_key": "biomass",
        "sizing_basis": "kW", "resource": [],
        "fields": [
            F("rated_kw", "Rated power", 100, "kW"),
            F("electrical_efficiency", "Electrical efficiency", 0.25,
              "fraction"),
            F("feedstock_lhv_mj_per_kg", "Feedstock heating value", 13.0,
              "MJ/kg"),
            F("feedstock_price_per_tonne", "Feedstock price", 40,
              "currency/tonne"),
            F("annual_feedstock_tonnes", "Feedstock available per year", None,
              "tonnes",
              "The binding constraint in most real projects. Leave blank "
              "only if supply is genuinely unlimited."),
        ],
    },
    "chp": {
        "label": "Combined heat and power", "group": "Dispatchable",
        "class": "dispatchable", "cost_key": "chp",
        "sizing_basis": "kW", "resource": [],
        "fields": [
            F("rated_kw", "Electrical rating", 100, "kW"),
            F("electrical_efficiency", "Electrical efficiency", 0.35,
              "fraction"),
            F("thermal_efficiency", "Thermal efficiency", 0.50, "fraction"),
            F("fuel_price_per_kwh", "Fuel price", 0.035, "currency/kWh"),
            F("boiler_efficiency", "Displaced boiler efficiency", 0.85,
              "fraction",
              "The heat credit is the boiler fuel this avoids. Without it "
              "CHP never looks worth building."),
            F("heat_led", "Heat-led operation", 1, None,
              "Most real CHP is sized by heat demand, with electricity as "
              "the by-product.", "checkbox"),
        ],
    },
    "geothermal": {
        "label": "Geothermal", "group": "Dispatchable",
        "class": "dispatchable", "cost_key": "geothermal",
        "sizing_basis": "kW", "resource": [],
        "fields": [
            F("rated_kw", "Rated power", 1000, "kW"),
            F("availability", "Availability", 0.95, "fraction"),
            F("ambient_derate_per_k", "Derate per K above design", 0.005,
              "fraction",
              "Binary-cycle output falls in hot weather, exactly when "
              "demand peaks in hot climates."),
        ],
    },
    "fuel_cell": {
        "label": "Fuel cell", "group": "Dispatchable",
        "class": "dispatchable", "cost_key": "fuel_cell",
        "sizing_basis": "kW", "resource": [],
        "fields": [
            F("rated_kw", "Rated power", 100, "kW"),
            F("nominal_efficiency", "Efficiency at rating", 0.50, "fraction"),
            F("fuel", "Fuel", "hydrogen", None,
              "Choose 'hydrogen_onsite' when an electrolyser in this system "
              "makes the hydrogen, so its cost is not counted twice.",
              "select", ["hydrogen", "hydrogen_onsite", "natural_gas"]),
            F("fuel_price_per_kg", "Fuel price", 5.0, "currency/kg"),
            F("stack_life_hours", "Stack life", 40000, "hours"),
        ],
    },
    # ============================================================= storage
    "battery": {
        "label": "Battery storage", "group": "Storage", "class": "storage",
        "cost_key": "battery_lfp", "sizing_basis": "kWh", "resource": [],
        "fields": [
            F("unit_kwh", "Unit energy", 50, "kWh"),
            F("unit_kw", "Unit power", 25, "kW",
              "Power divided by energy is the C-rate. 0.5 is a two-hour "
              "battery."),
            F("chemistry", "Chemistry", "lithium_lfp", None, None, "select",
              list(BatteryStorage.PRESETS.keys())),
            F("soc_initial", "Initial state of charge", 0.5, "fraction"),
        ],
    },
    "pumped_hydro": {
        "label": "Pumped hydro storage", "group": "Storage",
        "class": "storage", "cost_key": "pumped_hydro",
        "sizing_basis": "kW", "resource": [],
        "fields": [
            F("reservoir_m3", "Upper reservoir volume", 100000, "m³"),
            F("head_m", "Head", 100, "m"),
            F("rated_power_kw_", "Rated power", 1000, "kW"),
            F("pump_efficiency", "Pump efficiency", 0.88, "fraction"),
            F("turbine_efficiency", "Turbine efficiency", 0.90, "fraction"),
        ],
    },
    "flywheel": {
        "label": "Flywheel", "group": "Storage", "class": "storage",
        "cost_key": "flywheel", "sizing_basis": "kW", "resource": [],
        "fields": [
            F("nominal_energy_kwh", "Unit energy", 5, "kWh"),
            F("nominal_power_kw", "Unit power", 100, "kW"),
            F("self_discharge_per_hour", "Self-discharge", 0.02, "per hour",
              "Percent per HOUR, not per month. A flywheel is empty after a "
              "quiet day and cannot shift energy overnight."),
        ],
    },
    "supercapacitor": {
        "label": "Supercapacitor", "group": "Storage", "class": "storage",
        "cost_key": "supercapacitor", "sizing_basis": "kW", "resource": [],
        "fields": [
            F("nominal_energy_kwh", "Unit energy", 0.5, "kWh"),
            F("nominal_power_kw", "Unit power", 50, "kW"),
        ],
    },
    "caes": {
        "label": "Compressed air storage", "group": "Storage",
        "class": "storage", "cost_key": "caes", "sizing_basis": "kW",
        "resource": [],
        "fields": [
            F("nominal_energy_kwh", "Unit energy", 10000, "kWh"),
            F("nominal_power_kw", "Unit power", 1000, "kW"),
            F("adiabatic", "Adiabatic (heat recovered)", 1, None,
              "Diabatic plant burns gas on expansion and is not zero-carbon "
              "storage.", "checkbox"),
        ],
    },
    "thermal_storage": {
        "label": "Thermal storage", "group": "Storage", "class": "storage",
        "cost_key": "thermal_storage", "sizing_basis": "kWh", "resource": [],
        "fields": [
            F("volume_m3", "Volume", 50, "m³"),
            F("delta_t_k", "Usable temperature swing", 50, "K"),
            F("medium", "Medium", "water", None, None, "select",
              ["water", "molten_salt", "concrete", "rock", "oil"]),
            F("standing_loss_per_hour", "Standing loss", 0.005, "per hour"),
            F("serves", "Serves", "heat", None,
              "A heat store can only meet a heat demand. It cannot supply "
              "an electrical deficit.", "select", ["heat", "power_block"]),
        ],
    },
    "hydrogen": {
        "label": "Hydrogen (electrolyser + tank + fuel cell)",
        "group": "Storage", "class": "storage",
        "cost_key": "hydrogen_electrolyser", "sizing_basis": "kW",
        "resource": [],
        "fields": [
            F("tank_kg", "Storage capacity", 100, "kg H₂"),
            F("electrolyser_kw", "Electrolyser rating", 100, "kW"),
            F("fuel_cell_kw", "Fuel cell rating", 50, "kW"),
            F("electrolyser_efficiency", "Electrolyser efficiency", 0.65,
              "fraction"),
            F("fuel_cell_efficiency", "Fuel cell efficiency", 0.55,
              "fraction",
              "Round trip is about 35%. Only worth it for seasonal storage, "
              "or where the hydrogen itself has a use."),
        ],
    },
    # ============================================================= flexible
    "ev_fleet": {
        "label": "EV charging", "group": "Flexible load",
        "class": "flexible_load", "cost_key": "ev_charger_ac",
        "sizing_basis": "kW", "resource": [],
        "fields": [
            F("archetype", "Fleet type", "residential", None,
              "Sets arrival, departure, dwell and daily energy behaviour.",
              "select", list(ARCHETYPES.keys())),
            F("scenario", "Charging scenario", "v1g_smart", None,
              "Usually the largest single decision at a site with EVs.",
              "select", list(SCENARIOS)),
            F("chargers", "Charge points", 6, None),
            F("charger_kw", "Power per charge point", 7.4, "kW"),
            F("battery_kwh", "Vehicle battery", 60, "kWh"),
            F("vehicles_per_point", "Vehicles per charge point", 1.0, None),
            F("soc_departure_target", "Departure SOC target", 0.80,
              "fraction",
              "A hard service obligation. Departures below it are reported "
              "as failures."),
            F("degradation_cost_per_kwh", "V2G degradation cost", 0.03,
              "currency/kWh",
              "Battery life consumed by discharging for the site's benefit. "
              "A V2G case that omits this is not a business case."),
        ],
    },
}


# Which asset class each catalogue entry builds, and how its config maps.
_BUILDERS = {
    "pv": PVArray,
    "csp": CSPWithStorage,
    "wind": WindTurbine,
    "run_of_river_hydro": RunOfRiverHydro,
    "reservoir_hydro": ReservoirHydro,
    "tidal": TidalStream,
    "wave": WaveEnergyConverter,
    "genset": Generator,
    "biomass": BiomassGenerator,
    "chp": CHPUnit,
    "geothermal": GeothermalPlant,
    "fuel_cell": FuelCell,
    "battery": BatteryStorage,
    "pumped_hydro": PumpedHydroStorage,
    "flywheel": Flywheel,
    "supercapacitor": Supercapacitor,
    "caes": CompressedAirStorage,
    "thermal_storage": ThermalStorage,
    "hydrogen": HydrogenStorage,
    "ev_fleet": EVFleet,
}

# Config keys that are not constructor arguments.
_NON_CONSTRUCTOR = {
    "enabled", "chargers", "unit_kwp", "unit_kwh", "unit_kw", "preset",
    "ref_height_m", "cost_level", "cost_region",
}


def build(tech, config, region="global", currency=None, cost_level="typical"):
    """
    Construct one asset from a configuration dict.

    Costs left at zero are filled from the regional library, and the asset
    records that they were, so the report can distinguish a user's quoted
    figure from a benchmark.
    """
    entry = CATALOGUE.get(tech)
    builder = _BUILDERS.get(tech)
    if not entry or not builder:
        raise KeyError(
            f"unknown technology '{tech}'. Available: "
            f"{', '.join(sorted(CATALOGUE))}"
        )

    cfg = {k: v for k, v in (config or {}).items() if v is not None}
    kwargs = {k: v for k, v in cfg.items() if k not in _NON_CONSTRUCTOR}

    # A cost this technology charges on another basis is dropped rather than
    # passed to a constructor that will refuse it. The interface no longer
    # offers these, but a saved project or an API caller still might.
    for field in COST_FIELDS_NOT_APPLICABLE.get(tech, ()):  # noqa: B007
        kwargs.pop(field, None)

    # Technology-specific fixups where the UI field name and the constructor
    # argument sensibly differ.
    if tech == "pv":
        kwargs["capacity_kwp"] = cfg.get("unit_kwp", 25)
    elif tech == "battery":
        kwargs["nominal_energy_kwh"] = cfg.get("unit_kwh", 50)
        kwargs["nominal_power_kw"] = cfg.get("unit_kw", 25)
    elif tech == "wind":
        lib = wind_library()
        preset = cfg.get("preset")
        if preset and preset in lib:
            base = lib[preset]
            for f in ("rated_kw", "v_cutin", "v_rated", "v_cutout",
                      "hub_height_m", "rotor_diameter_m"):
                kwargs.setdefault(f, getattr(base, f))
            kwargs.setdefault("power_curve", base.power_curve)
            kwargs.setdefault("name", base.name)
        else:
            # Without a preset the class default name is "Generic 2 MW",
            # which would appear on the diagram beside a 100 kW rating.
            rated = float(kwargs.get("rated_kw", 100))
            kwargs.setdefault(
                "name",
                f"Wind turbine {rated / 1000:.1f} MW" if rated >= 1000
                else f"Wind turbine {rated:.0f} kW",
            )
    elif tech == "ev_fleet":
        kwargs.pop("chargers", None)
        if "v2g" in kwargs:
            kwargs.pop("v2g")

    # ---- cost defaults from the library
    unit_size = _unit_size(tech, cfg, kwargs)
    needs_cost = not any(
        float(cfg.get(k, 0) or 0) > 0
        for k in ("capital_cost", "replacement_cost", "om_cost_per_year")
    )
    provenance = None

    # An interface sends a number for every field it shows, so a cost the
    # user left blank arrives here as an explicit 0.0 rather than as an
    # absence. `setdefault` then has nothing to fill and the regional cost
    # library never applies - which is precisely the case it exists for, so
    # choosing a region changed the cost browser and nothing in the sizing.
    # Clearing the zeros first is what makes "leave at zero to use the
    # library" true.
    if needs_cost:
        for field in ("capital_cost", "replacement_cost", "om_cost_per_year"):
            if float(kwargs.get(field, 0) or 0) == 0.0:
                kwargs.pop(field, None)
    # Some constructors do not accept every cost field. A generator's O&M is
    # charged per RUNNING HOUR, not per year — an engine that never starts
    # costs nothing to maintain — so the library's annual figure does not
    # apply to it and is not offered.
    skip_cost_fields = {"genset": {"om_cost_per_year"}}.get(tech, set())

    if needs_cost and entry.get("cost_key") and unit_size > 0:
        c = costs_mod.unit_costs(
            entry["cost_key"], unit_size, region=region,
            currency=currency, level=cost_level,
        )
        for field, value in (
            ("capital_cost", c["capital_cost"]),
            ("replacement_cost", c["replacement_cost"]),
            ("om_cost_per_year", c["om_cost_per_year"]),
        ):
            if field not in skip_cost_fields:
                kwargs.setdefault(field, value)
        if tech not in ("genset", "chp", "biomass", "fuel_cell"):
            kwargs.setdefault("lifetime_years", c["lifetime_years"])
        provenance = c["provenance"]

    asset = builder(**kwargs)
    asset.cost_provenance = provenance
    asset.cost_from_library = provenance is not None
    return asset


def _unit_size(tech, cfg, kwargs):
    """Size of one unit, in the basis the cost library uses."""
    if tech == "pv":
        return float(cfg.get("unit_kwp", 25))
    if tech == "battery":
        return float(cfg.get("unit_kwh", 50))
    if tech == "thermal_storage":
        vol = float(cfg.get("volume_m3", 50))
        dt = float(cfg.get("delta_t_k", 50))
        return vol * 1000.0 * 4.186 * dt / 3600.0
    if tech == "hydrogen":
        return float(cfg.get("electrolyser_kw", 100))
    if tech == "pumped_hydro":
        return float(cfg.get("rated_power_kw_", 1000))
    if tech in ("flywheel", "supercapacitor", "caes"):
        return float(cfg.get("nominal_power_kw", 100))
    if tech == "ev_fleet":
        return float(cfg.get("charger_kw", 7.4))
    for k in ("rated_kw", "design_flow_m3s"):
        if k in cfg:
            if k == "design_flow_m3s":
                q = float(cfg[k])
                h = float(cfg.get("head_m", 20))
                e = float(cfg.get("efficiency", 0.85))
                return 1000.0 * 9.80665 * q * h * e / 1000.0
            return float(cfg[k])
    return 0.0


def ui_schema(language="en"):
    """
    The catalogue, translated, for the interface to render.

    Generating the component pages from this rather than hand-writing them
    is what keeps the interface and the model in step: a field added to a
    technology appears in the UI without anyone remembering to add it.
    """
    from . import i18n

    t = i18n.get(language)
    groups = {}
    for tech, entry in CATALOGUE.items():
        g = entry["group"]
        groups.setdefault(g, [])
        groups[g].append({
            "technology": tech,
            "label": t.tech(tech) if t.t(f"tech.{tech}") != f"tech.{tech}"
                     else entry["label"],
            "class": entry["class"],
            "sizing_basis": entry["sizing_basis"],
            "resource": entry["resource"],
            "cost_key": entry.get("cost_key"),
            "fields": entry["fields"] + cost_fields_for(tech),
        })
    return {
        "groups": groups,
        "ev_scenarios": [
            {"key": s, "label": SCENARIO_LABELS[s]} for s in SCENARIOS
        ],
        "ev_archetypes": [
            {"key": k, "label": v["label"],
             "v2g_plausible": v["v2g_plausible"]}
            for k, v in ARCHETYPES.items()
        ],
        "regions": [
            {"key": k, "label": v["label"], "currency": v["currency"],
             "multiplier": v["multiplier"], "note": v.get("note", "")}
            for k, v in costs_mod.REGIONS.items()
        ],
        "currencies": [
            {"key": k, "label": v["label"], "symbol": v["symbol"],
             "note": v.get("note", "")}
            for k, v in costs_mod.CURRENCIES.items()
        ],
        "languages": i18n.available(),
    }
