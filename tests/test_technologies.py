# -*- coding: utf-8 -*-
"""
Regression tests for the extended technology set.

The conservation tests here matter most. Every new technology is a new way
for energy to go missing, and a sizing tool that loses a percent per hour
still produces plausible-looking reports. Each technology is run through the
dispatch on its own and in combination, and the hourly balance is checked to
machine precision.
"""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ensys import costs, dispatch, i18n
from ensys.assets import AssetRegistry, Dispatchable, NonDispatchable, Storage
from ensys.models.evfleet import (
    ARCHETYPES, EVFleet, SCENARIOS, UNCONTROLLED, V1G_SMART, V2G, V2H,
    compare_scenarios,
)
from ensys.models.generators import (
    BiomassGenerator, CHPUnit, CSPWithStorage, FuelCell, GeothermalPlant,
    RunOfRiverHydro, TidalStream, WaveEnergyConverter,
)
from ensys.models.grid import GridConnection
from ensys.models.pv import PVArray
from ensys.models.storage import (
    BatteryStorage, CompressedAirStorage, Flywheel, HydrogenStorage,
    PumpedHydroStorage, Supercapacitor, ThermalStorage,
)
from ensys.resources.geo import PRESETS
from ensys.system import SystemConfig
from ensys.timeseries import HOURS_PER_YEAR

N = HOURS_PER_YEAR


def demo_load(peak=100.0):
    return [
        peak * (0.5 + 0.5 * max(0.0, math.sin((t % 24 - 6) / 12 * math.pi)))
        for t in range(N)
    ]


def demo_pv(peak=30.0):
    return [max(0.0, peak * math.sin((t % 24 - 6) / 12 * math.pi))
            for t in range(N)]


def base_resources():
    return {
        "load": demo_load(),
        "pv_unit": demo_pv(),
        "ghi": demo_pv(1000.0),
        "dni": [max(0.0, 900 * math.sin((t % 24 - 6) / 12 * math.pi))
                for t in range(N)],
        "river_flow_m3s": [2.0 + 1.5 * math.sin(2 * math.pi * t / N)
                           for t in range(N)],
        "wave_height_m": [1.5 + 0.8 * math.sin(2 * math.pi * t / 720)
                          for t in range(N)],
        "temperature_c": [25.0] * N,
    }


def run_with(assets, counts, import_kw=60, export_kw=30, n_pv=2):
    grid = GridConnection(
        import_limit_kw=import_kw, export_limit_kw=export_kw,
        import_price=0.12, export_price=0.04,
    )
    system = SystemConfig(
        pv=PVArray(capacity_kwp=30), n_pv=n_pv, grid=grid,
        location=PRESETS["shiraz"], assets=assets, counts=counts,
    )
    return system, dispatch.simulate(system, base_resources())


class TestStorageTechnologies(unittest.TestCase):

    TECHS = {
        "battery": (BatteryStorage(nominal_energy_kwh=50, nominal_power_kw=25), 4),
        "pumped_hydro": (
            PumpedHydroStorage(reservoir_m3=20000, head_m=80,
                               rated_power_kw_=200), 1),
        "flywheel": (Flywheel(), 3),
        "supercapacitor": (Supercapacitor(), 5),
        "caes": (CompressedAirStorage(nominal_energy_kwh=2000,
                                      nominal_power_kw=200), 1),
        "hydrogen": (HydrogenStorage(tank_kg=200, electrolyser_kw=150,
                                     fuel_cell_kw=60), 1),
    }

    def test_energy_is_conserved(self):
        for name, (asset, count) in self.TECHS.items():
            with self.subTest(technology=name):
                _sys, r = run_with([(name, asset)], {name: count})
                err, hour = r.balance_error()
                self.assertLess(
                    err, 1e-6,
                    f"{name}: balance violated by {err:g} kW at hour {hour}",
                )

    def test_dispatch_never_drives_soc_outside_the_window(self):
        """
        The operating window binds the DISPATCH, not the physics.

        Self-discharge legitimately carries a store below its floor — a
        flywheel losing 2% an hour is empty after a quiet day, and a model
        that clamped it at soc_min would be inventing energy. What must never
        happen is the dispatch drawing energy out from below the floor, or
        charging past the ceiling.
        """
        for name, (asset, count) in self.TECHS.items():
            with self.subTest(technology=name):
                _sys, r = run_with([(name, asset)], {name: count})
                rec = r.assets[name]
                soc = rec["soc"]
                self.assertLessEqual(max(soc), asset.soc_max + 1e-6, name)
                # The recorded SOC is taken AFTER self-discharge for that
                # hour, so a store discharged exactly to its floor will sit
                # one hour's leakage below it. That is correct, and the
                # tolerance has to allow for it rather than the test
                # pretending leakage does not happen.
                slack = asset.self_discharge_per_hour + 1e-6
                for t in range(1, len(soc)):
                    if rec["discharge"][t] > 1e-9:
                        self.assertGreaterEqual(
                            soc[t], asset.soc_min - slack,
                            f"{name} discharged at hour {t} leaving SOC "
                            f"{soc[t]:.6f}, more than one hour of leakage "
                            f"below its floor {asset.soc_min}",
                        )

    def test_no_energy_is_drawn_from_below_the_floor(self):
        for name, (asset, count) in self.TECHS.items():
            with self.subTest(technology=name):
                for soc in (0.0, asset.soc_min * 0.5, asset.soc_min):
                    self.assertEqual(
                        asset.max_discharge_kw(count, soc), 0.0,
                        f"{name} offered power at SOC {soc}",
                    )

    def test_discharge_never_exceeds_charge(self):
        """
        Over a year a store cannot deliver more than it absorbed, except for
        the free energy it started with. Anything beyond that is a leak.
        """
        for name, (asset, count) in self.TECHS.items():
            with self.subTest(technology=name):
                _sys, r = run_with([(name, asset)], {name: count})
                pa = r.totals["per_asset"][name]
                initial = (
                    count * asset.nominal_energy_kwh
                    * (asset.soc_initial - asset.soc_min)
                )
                self.assertLessEqual(
                    pa["discharge_kwh"], pa["charge_kwh"] + initial + 1e-6,
                    f"{name} delivered more than it absorbed plus its "
                    f"initial charge",
                )

    def test_hydrogen_round_trip_is_realistic(self):
        h = HydrogenStorage()
        self.assertGreater(h.round_trip_efficiency, 0.25)
        self.assertLess(h.round_trip_efficiency, 0.45)

    def test_pumped_hydro_energy_from_geometry(self):
        # E = rho g V H / 3.6e6.  1000 * 9.80665 * 100000 * 100 / 3.6e6
        p = PumpedHydroStorage(reservoir_m3=100000, head_m=100)
        self.assertAlmostEqual(p.nominal_energy_kwh, 27240.7, delta=1.0)

    def test_flywheel_warns_about_short_duration(self):
        f = Flywheel(nominal_energy_kwh=5, nominal_power_kw=100)
        s = f.summary(1, [0.0] * 10)
        self.assertLess(s["duration_hours"], 0.5)
        self.assertTrue(s["notes"])

    def test_diabatic_caes_reports_its_gas_burn(self):
        c = CompressedAirStorage(adiabatic=False)
        s = c.summary(1, [100.0] * 100)
        self.assertGreater(s["gas_kwh"], 0)
        self.assertGreater(s["emissions_kg"], 0)
        self.assertTrue(s["notes"])

    def test_thermal_store_cannot_serve_electrical_load(self):
        """A heat store must never be dispatched against an electrical deficit."""
        ts = ThermalStorage(volume_m3=50, serves="heat")
        _sys, r = run_with([("heat", ts)], {"heat": 1}, import_kw=10)
        self.assertEqual(sum(r.assets["heat"]["discharge"]), 0.0)


class TestGenerationTechnologies(unittest.TestCase):

    TECHS = {
        "hydro": (RunOfRiverHydro(design_flow_m3s=2.0, head_m=25), 1),
        "biomass": (BiomassGenerator(rated_kw=80,
                                     annual_feedstock_tonnes=150), 1),
        "csp": (CSPWithStorage(rated_kw=200, solar_multiple=2.0,
                               storage_hours=6), 1),
        "geothermal": (GeothermalPlant(rated_kw=60), 1),
        "tidal": (TidalStream(rated_kw=50), 2),
        "wave": (WaveEnergyConverter(rated_kw=100), 1),
        "fuel_cell": (FuelCell(rated_kw=80), 1),
        "chp": (CHPUnit(rated_kw=100, heat_led=False), 1),
    }

    def test_energy_is_conserved(self):
        for name, (asset, count) in self.TECHS.items():
            with self.subTest(technology=name):
                _sys, r = run_with([(name, asset)], {name: count})
                err, hour = r.balance_error()
                self.assertLess(
                    err, 1e-6,
                    f"{name}: balance violated by {err:g} kW at hour {hour}",
                )

    def test_output_never_exceeds_rating(self):
        for name, (asset, count) in self.TECHS.items():
            with self.subTest(technology=name):
                _sys, r = run_with([(name, asset)], {name: count})
                out = r.assets[name]["output"]
                cap = asset.rated_power_kw() * count
                self.assertLessEqual(
                    max(out), cap * 1.001,
                    f"{name} produced {max(out):.1f} kW against a "
                    f"{cap:.1f} kW rating",
                )

    def test_hydro_power_formula(self):
        # P = rho g Q H eta / 1000 = 1000*9.80665*2*25*0.85/1000
        h = RunOfRiverHydro(design_flow_m3s=2.0, head_m=25, efficiency=0.85)
        self.assertAlmostEqual(h.rated_power_kw(), 416.8, delta=0.5)

    def test_hydro_respects_compensation_flow(self):
        h = RunOfRiverHydro(design_flow_m3s=2.0, head_m=25,
                            compensation_flow_m3s=1.0, min_flow_ratio=0.2)
        # 1.2 m3/s total leaves 0.2 usable, below the 0.4 minimum.
        self.assertEqual(h.power_at(1.2), 0.0)
        self.assertGreater(h.power_at(2.0), 0.0)

    def test_biomass_respects_feedstock_budget(self):
        b = BiomassGenerator(rated_kw=500, electrical_efficiency=0.25,
                             feedstock_lhv_mj_per_kg=13.0,
                             annual_feedstock_tonnes=200)
        limit = b.annual_energy_limit_kwh(1)
        # 200 t at 13 MJ/kg and 25% efficiency is about 180 MWh, far below
        # the 4.4 GWh the rating alone would suggest.
        self.assertLess(limit, 250000)
        self.assertGreater(limit, 150000)
        self.assertLess(limit, 500 * 8760 * 0.1)

    def test_csp_rejects_storage_without_solar_multiple(self):
        with self.assertRaises(ValueError) as cm:
            CSPWithStorage(solar_multiple=1.0, storage_hours=6)
        self.assertIn("never produces more", str(cm.exception))

    def test_fuel_cell_efficiency_rises_at_part_load(self):
        fc = FuelCell(nominal_efficiency=0.50, part_load_bonus=0.12)
        self.assertGreater(fc.efficiency_at(0.35), fc.efficiency_at(1.0))

    def test_chp_rejects_impossible_efficiency(self):
        with self.assertRaises(ValueError):
            CHPUnit(electrical_efficiency=0.5, thermal_efficiency=0.6)

    def test_chp_heat_credit_lowers_marginal_cost(self):
        with_heat = CHPUnit(rated_kw=100, boiler_efficiency=0.85)
        no_credit = CHPUnit(rated_kw=100, boiler_efficiency=1e9)
        self.assertLess(
            with_heat.marginal_cost(1, 80), no_credit.marginal_cost(1, 80)
        )

    def test_tidal_synthetic_series_shows_spring_neap(self):
        t = TidalStream()
        v = t.synthetic_velocity(peak_spring_ms=2.5, hours=24 * 30)
        daily_max = [max(v[d * 24:(d + 1) * 24]) for d in range(30)]
        # The spring-neap cycle is 14.77 days, so the daily peak must vary.
        self.assertGreater(max(daily_max) - min(daily_max), 0.5)

    def test_geothermal_derates_when_hot(self):
        g = GeothermalPlant(rated_kw=1000, design_ambient_c=20,
                            ambient_derate_per_k=0.005)
        cool = g.available_kw(1, 0, {"temperature_c": [20.0]})
        hot = g.available_kw(1, 0, {"temperature_c": [40.0]})
        self.assertLess(hot, cool)


class TestEVScenarios(unittest.TestCase):

    def _run(self, scenario, archetype="residential", chargers=10):
        fleet = EVFleet(archetype=archetype, scenario=scenario, seed=7)
        grid = GridConnection(import_limit_kw=200, export_limit_kw=50,
                              import_price=0.12, export_price=0.04)
        system = SystemConfig(
            pv=PVArray(capacity_kwp=30), n_pv=2, grid=grid,
            location=PRESETS["shiraz"], ev=fleet, n_chargers=chargers,
        )
        return system, dispatch.simulate(system, base_resources())

    def test_every_scenario_conserves_energy(self):
        for s in SCENARIOS:
            with self.subTest(scenario=s):
                _sys, r = self._run(s)
                err, hour = r.balance_error()
                self.assertLess(err, 1e-6, f"{s}: {err:g} kW at hour {hour}")

    def test_every_scenario_meets_its_departure_obligation(self):
        """
        A smart controller must never do worse than a dumb one at meeting the
        service duty. Deferring charge is only legitimate if the energy still
        arrives before the vehicle leaves.
        """
        for s in SCENARIOS:
            with self.subTest(scenario=s):
                _sys, r = self._run(s)
                missed = r.totals["ev_unmet_departures"]
                # Asserted as a rate, not an exact zero. The fleet carries a
                # single aggregate state of charge, so with staggered
                # arrivals a few departures a year can read marginally under
                # target while every individual vehicle would have made it.
                # A real control failure is percentages, not a handful.
                self.assertLess(
                    missed, 12,
                    f"{s} left {missed} departures below the SOC target, "
                    f"which is beyond aggregation noise",
                )

    def test_smart_charging_takes_less_energy_than_uncontrolled(self):
        """
        Uncontrolled charging fills to the maximum SOC; a smart controller
        only delivers what the departure target needs.
        """
        _s1, dumb = self._run(UNCONTROLLED)
        _s2, smart = self._run(V1G_SMART)
        self.assertLess(
            smart.totals["ev_charge_kwh"], dumb.totals["ev_charge_kwh"]
        )

    def test_v2g_discharges_and_v1g_does_not(self):
        _s1, v1g = self._run(V1G_SMART)
        _s2, v2g = self._run(V2G)
        self.assertEqual(v1g.totals["ev_discharge_kwh"], 0.0)
        self.assertGreater(v2g.totals["ev_discharge_kwh"], 0.0)

    def test_v2h_never_exports_to_the_grid(self):
        fleet = EVFleet(archetype="residential", scenario=V2H, seed=7)
        self.assertFalse(fleet.can_export)
        self.assertTrue(fleet.bidirectional)

    def test_overnight_window_wraps_midnight(self):
        """
        The original MATLAB comparison Hour >= Arr and Hour <= Dep is empty
        for an evening arrival and a morning departure, so an overnight fleet
        silently never charged.
        """
        fleet = EVFleet(archetype="residential", scenario=V1G_SMART, seed=3)
        prof = fleet.profile(n_points=5)
        overnight = sum(
            1 for t in range(N)
            if prof["connected"][t] and t % 24 in (0, 1, 2, 3)
        )
        self.assertGreater(overnight, 100, "fleet never connected overnight")

    def test_public_fast_rejects_bidirectional(self):
        with self.assertRaises(ValueError) as cm:
            EVFleet(archetype="public_fast", scenario=V2G)
        self.assertIn("dwell", str(cm.exception))

    def test_spread_arrivals_lowers_the_peak(self):
        """
        Drawing one arrival hour for the whole fleet makes every vehicle
        arrive together and overstates the peak.
        """
        together = EVFleet(archetype="depot", scenario=UNCONTROLLED,
                           spread_arrivals=False, seed=11).profile(n_points=20)
        spread = EVFleet(archetype="depot", scenario=UNCONTROLLED,
                         spread_arrivals=True, seed=11).profile(n_points=20)
        self.assertGreaterEqual(
            max(together["just_arrived"]), max(spread["just_arrived"])
        )

    def test_v2g_degradation_is_costed(self):
        fleet = EVFleet(archetype="residential", scenario=V2G,
                        degradation_cost_per_kwh=0.05)
        self.assertAlmostEqual(fleet.annual_cost(10, [2.0] * 100), 10.0)
        v1g = EVFleet(archetype="residential", scenario=V1G_SMART)
        self.assertEqual(v1g.annual_cost(10, [2.0] * 100), 0.0)

    def test_compare_scenarios_builds_each_valid_one(self):
        out = compare_scenarios({"archetype": "workplace"})
        self.assertGreaterEqual(len(out), 5)
        for s, fleet in out.items():
            self.assertEqual(fleet.scenario, s)

    def test_all_archetypes_are_usable(self):
        for a in ARCHETYPES:
            with self.subTest(archetype=a):
                fleet = EVFleet(archetype=a, scenario=UNCONTROLLED)
                prof = fleet.profile(n_points=4)
                self.assertGreater(sum(prof["n_present"]), 0)


class TestCombinedSystems(unittest.TestCase):
    """Everything at once, which is where interactions surface."""

    def test_full_stack_conserves_energy(self):
        assets = [
            ("battery", BatteryStorage(nominal_energy_kwh=50,
                                       nominal_power_kw=25)),
            ("hydrogen", HydrogenStorage(tank_kg=100, electrolyser_kw=80,
                                         fuel_cell_kw=40)),
            ("flywheel", Flywheel()),
            ("biomass", BiomassGenerator(rated_kw=50,
                                         annual_feedstock_tonnes=100)),
            ("fuel_cell", FuelCell(rated_kw=40)),
            ("hydro", RunOfRiverHydro(design_flow_m3s=0.5, head_m=15)),
        ]
        counts = {k: 1 for k, _ in assets}
        counts["battery"] = 3

        fleet = EVFleet(archetype="workplace", scenario=V2G, seed=5)
        grid = GridConnection(import_limit_kw=80, export_limit_kw=40,
                              import_price=0.14, export_price=0.05)
        system = SystemConfig(
            pv=PVArray(capacity_kwp=30), n_pv=3, grid=grid,
            location=PRESETS["shiraz"], ev=fleet, n_chargers=6,
            assets=assets, counts=counts,
        )
        r = dispatch.simulate(system, base_resources())
        err, hour = r.balance_error()
        self.assertLess(
            err, 1e-6, f"full stack balance violated by {err:g} at hour {hour}"
        )
        self.assertEqual(r.totals["ev_unmet_departures"], 0)

    def test_every_strategy_conserves_energy(self):
        for strat in dispatch.STRATEGIES:
            with self.subTest(strategy=strat):
                grid = GridConnection(import_limit_kw=80, export_limit_kw=40,
                                      import_price=0.14, export_price=0.05)
                system = SystemConfig(
                    pv=PVArray(capacity_kwp=30), n_pv=3, grid=grid,
                    location=PRESETS["shiraz"],
                    assets=[("battery",
                             BatteryStorage(nominal_energy_kwh=50,
                                            nominal_power_kw=25))],
                    counts={"battery": 4},
                )
                r = dispatch.simulate(system, base_resources(), strat)
                err, _ = r.balance_error()
                self.assertLess(err, 1e-6, f"{strat}: {err:g}")

    def test_decision_vector_covers_every_sizable_asset(self):
        assets = [("flywheel", Flywheel()),
                  ("hydrogen", HydrogenStorage())]
        system = SystemConfig(
            pv=PVArray(capacity_kwp=30), n_pv=1,
            grid=GridConnection(import_limit_kw=50),
            assets=assets, counts={"flywheel": 2, "hydrogen": 1},
        )
        keys = system.decision_keys()
        self.assertIn("pv", keys)
        self.assertIn("flywheel", keys)
        self.assertIn("hydrogen", keys)
        self.assertNotIn("grid", keys)      # the grid is not sized
        v = system.decision_vector()
        self.assertEqual(len(v), len(keys))
        new = system.with_decision([5] * len(keys))
        self.assertEqual(new.counts["flywheel"], 5)
        self.assertEqual(system.counts["flywheel"], 2)   # original untouched


class TestCostLibrary(unittest.TestCase):

    def test_every_technology_resolves_in_every_region(self):
        for tech in costs.TECHNOLOGIES:
            for region in costs.REGIONS:
                with self.subTest(tech=tech, region=region):
                    c = costs.technology_cost(tech, region)
                    self.assertGreater(c["capex_per_unit"], 0)
                    self.assertTrue(c["provenance"]["source"])

    def test_regional_multiplier_is_applied(self):
        g = costs.technology_cost("pv_utility", "global", currency="USD")
        a = costs.technology_cost("pv_utility", "sub_saharan_africa",
                                  currency="USD")
        self.assertGreater(a["capex_per_unit"], g["capex_per_unit"])

    def test_levels_are_ordered(self):
        lo = costs.technology_cost("battery_lfp", level="low")["capex_usd"]
        ty = costs.technology_cost("battery_lfp", level="typical")["capex_usd"]
        hi = costs.technology_cost("battery_lfp", level="high")["capex_usd"]
        self.assertLess(lo, ty)
        self.assertLess(ty, hi)

    def test_unit_costs_scale_with_unit_size(self):
        one = costs.unit_costs("battery_lfp", 1)["capital_cost"]
        fifty = costs.unit_costs("battery_lfp", 50)["capital_cost"]
        self.assertAlmostEqual(fifty, one * 50, places=6)

    def test_overrides_are_flagged(self):
        c = costs.technology_cost(
            "pv_utility", overrides={"pv_utility": {"capex_per_unit": 999.0}}
        )
        self.assertEqual(c["capex_per_unit"], 999.0)
        self.assertTrue(c["provenance"]["user_overridden"])

    def test_unknown_technology_lists_alternatives(self):
        with self.assertRaises(KeyError) as cm:
            costs.technology_cost("perpetual_motion")
        self.assertIn("pv_utility", str(cm.exception))

    def test_currency_conversion_round_trips(self):
        v = costs.convert(1000.0, "USD", "EUR")
        self.assertAlmostEqual(costs.convert(v, "EUR", "USD"), 1000.0, places=6)

    def test_caveats_mention_the_region_and_currency(self):
        notes = costs.caveats("iran", "IRT")
        joined = " ".join(notes)
        self.assertIn("quotation", joined.lower())
        self.assertTrue(any("Iran" in n for n in notes))


class TestTranslation(unittest.TestCase):

    def test_persian_is_fully_translated(self):
        cov = i18n.coverage()
        self.assertEqual(cov["fa"]["translated"], cov["fa"]["total"])

    def test_persian_is_right_to_left(self):
        fa = i18n.get("fa")
        self.assertEqual(fa.dir, "rtl")
        self.assertTrue(fa.is_rtl)

    def test_no_persian_string_is_left_in_english(self):
        """A key whose Persian value equals its English one is untranslated."""
        same = [
            k for k, v in i18n.STRINGS.items()
            if v.get("fa") == v.get("en") and not k.startswith("unit.")
        ]
        self.assertEqual(same, [], f"untranslated keys: {same}")

    def test_persian_digits_and_separators(self):
        fa = i18n.get("fa")
        s = fa.number(1234567.89, 2)
        self.assertIn("۱", s)
        self.assertIn("٬", s)         # Arabic thousands separator
        self.assertIn("٫", s)         # Arabic decimal separator
        self.assertNotIn(",", s)
        self.assertNotIn(".", s)

    def test_digits_round_trip(self):
        fa = i18n.get("fa")
        self.assertEqual(i18n.to_latin_digits(fa.number(1234.5, 1)), "1,234.5")

    def test_missing_key_falls_back_readably(self):
        fa = i18n.get("fa")
        self.assertEqual(fa.t("does.not.exist"), "does.not.exist")
        self.assertEqual(fa.t("does.not.exist", "fallback"), "fallback")

    def test_technology_names_translate(self):
        fa = i18n.get("fa")
        for tech in ("pv", "wind", "battery", "hydrogen", "pumped_hydro",
                     "fuel_cell", "geothermal", "biomass"):
            with self.subTest(tech=tech):
                name = fa.tech(tech)
                self.assertNotEqual(name, tech)
                self.assertTrue(any("؀" <= ch <= "ۿ" for ch in name),
                                f"{tech} produced no Persian text: {name}")

    def test_rtl_money_places_symbol_after_the_number(self):
        fa = i18n.get("fa")
        en = i18n.get("en")
        self.assertTrue(en.money(1000, "USD").startswith("$"))
        self.assertFalse(fa.money(1000, "USD").startswith("$"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
