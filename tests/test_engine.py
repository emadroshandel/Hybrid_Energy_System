"""
Regression tests for the HES engine.

Run with:  python tests/test_engine.py

Written against the standard library's unittest so the suite runs anywhere
the engine does, with no test-runner dependency.

The tests that matter most here are the conservation and coordination ones.
A sizing tool can produce plausible-looking numbers indefinitely while
quietly losing energy or specifying a breaker that cannot protect its cable,
and neither failure announces itself. Everything else is a convenience.
"""

import json
import math
import re
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ensys import dispatch, economics, metrics
from ensys.models.battery import Battery
from ensys.models.ev import EVFleet
from ensys.models.genset import Generator
from ensys.models.grid import GridConnection
from ensys.models.pv import (
    PVArray, solar_position, erbs_split, hay_davies_poa, faiman_cell_temp,
)
from ensys.models.wind import WindTurbine, air_density_at, weibull_fit
from ensys.optim.pareto import Archive, Solution, dominates, non_dominated_sort
from ensys.optimise import SizingStudy
from ensys.resources.geo import Location, PRESETS
from ensys.resources.synth import synthesise_year
from ensys.sizing import cables as cab
from ensys.sizing import protection as prot
from ensys.sizing.design import size_system, coordinate
from ensys.sizing.inverter import PVModule, design_strings, size_pv_inverter
from ensys.system import SearchSpace, SystemConfig
from ensys.timeseries import coerce_year, HOURS_PER_YEAR


def demo_load(peak=100.0):
    """A simple repeatable daily load shape."""
    return [
        peak * (0.45 + 0.55 * max(0.0, math.sin((t % 24 - 6) / 12 * math.pi)))
        for t in range(HOURS_PER_YEAR)
    ]


def demo_pv(peak=30.0):
    return [
        max(0.0, peak * math.sin((t % 24 - 6) / 12 * math.pi))
        for t in range(HOURS_PER_YEAR)
    ]


class TestTimeSeries(unittest.TestCase):

    def test_accepts_full_year(self):
        v, note = coerce_year([1.0] * 8760)
        self.assertEqual(len(v), 8760)
        self.assertIsNone(note)

    def test_drops_leap_day(self):
        v, note = coerce_year(list(range(8784)))
        self.assertEqual(len(v), 8760)
        self.assertIn("leap", note)
        # Hour 1416 in the output must be the first hour of 1 March, which is
        # index 1440 of the leap-year input.
        self.assertEqual(v[1416], 1440)

    def test_expands_monthly(self):
        v, note = coerce_year([10.0] * 12)
        self.assertEqual(len(v), 8760)
        self.assertIn("monthly", note)

    def test_rejects_nonsense_length(self):
        with self.assertRaises(Exception):
            coerce_year([1.0] * 137)


class TestSolarGeometry(unittest.TestCase):

    def test_sun_is_up_at_noon_and_down_at_midnight(self):
        loc = PRESETS["shiraz"]
        # Summer solstice, local noon.
        noon = (171 * 24) + 12
        zen_noon, _ = solar_position(noon, loc)
        midnight = (171 * 24) + 0
        zen_mid, _ = solar_position(midnight, loc)
        self.assertLess(zen_noon, 30.0)
        self.assertGreater(zen_mid, 90.0)

    def test_southern_hemisphere_sun_is_north(self):
        loc = PRESETS["adelaide"]
        noon = (171 * 24) + 12          # June: winter in Adelaide
        _zen, azi = solar_position(noon, loc)
        # Sun should be in the northern sky.
        self.assertTrue(azi < 90 or azi > 270,
                        f"expected northern azimuth, got {azi:.1f}")

    def test_erbs_split_conserves_ghi(self):
        for zen in (20.0, 45.0, 70.0):
            ghi = 700.0
            dni, dhi = erbs_split(ghi, zen, 12)
            recon = dni * math.cos(math.radians(zen)) + dhi
            self.assertAlmostEqual(recon, ghi, delta=1.0)

    def test_poa_exceeds_ghi_for_tilted_surface_in_winter(self):
        # A tilted surface facing the equator collects more than horizontal
        # when the sun is low. This is the whole reason for tilting.
        poa = hay_davies_poa(700, 120, 400, 60.0, 180.0, 30.0, 180.0)
        self.assertGreater(poa, 400)

    def test_faiman_hotter_than_air_and_cooled_by_wind(self):
        still = faiman_cell_temp(900, 30.0, 0.0)
        windy = faiman_cell_temp(900, 30.0, 6.0)
        self.assertGreater(still, 30.0)
        self.assertLess(windy, still)


class TestPVArray(unittest.TestCase):

    def test_annual_yield_is_plausible(self):
        loc = PRESETS["shiraz"]
        res = synthesise_year(
            loc,
            monthly_ghi=[150, 160, 185, 195, 215, 230, 225, 210, 190, 170, 150, 140],
            monthly_temperature=[7, 10, 14, 20, 25, 31, 33, 32, 28, 21, 14, 9],
        )
        pv = PVArray(capacity_kwp=1.0)
        dc, info = pv.output_series(
            res["ghi"], loc, dni=res["dni"], dhi=res["dhi"],
            temperature_c=res["temperature_c"],
        )
        # Shiraz should land between 1400 and 2200 kWh/kWp before the
        # inverter. Outside that range something is structurally wrong.
        self.assertGreater(info["specific_yield_kwh_kwp"], 1400)
        self.assertLess(info["specific_yield_kwh_kwp"], 2200)
        self.assertGreater(info["transposition_gain"], 1.0)

    def test_no_output_at_night(self):
        loc = PRESETS["shiraz"]
        pv = PVArray(capacity_kwp=10.0)
        ghi = [0.0] * HOURS_PER_YEAR
        dc, _ = pv.output_series(ghi, loc)
        self.assertEqual(sum(dc), 0.0)

    def test_hot_cells_produce_less(self):
        loc = PRESETS["shiraz"]
        ghi = [800.0 if 6 <= t % 24 <= 18 else 0.0 for t in range(HOURS_PER_YEAR)]
        pv = PVArray(capacity_kwp=10.0)
        cool, _ = pv.output_series(ghi, loc, temperature_c=[10.0] * HOURS_PER_YEAR)
        hot, _ = pv.output_series(ghi, loc, temperature_c=[45.0] * HOURS_PER_YEAR)
        self.assertGreater(sum(cool), sum(hot))


class TestWind(unittest.TestCase):

    def test_power_curve_boundaries(self):
        t = WindTurbine(rated_kw=100, v_cutin=3, v_rated=12, v_cutout=25)
        self.assertEqual(t.power_at(2.9), 0.0)
        self.assertEqual(t.power_at(25.1), 0.0)
        self.assertAlmostEqual(t.power_at(12.0), 100.0, delta=0.01)
        self.assertAlmostEqual(t.power_at(20.0), 100.0, delta=0.01)
        self.assertLess(t.power_at(7.0), 100.0)

    def test_air_density_falls_with_altitude(self):
        sea = air_density_at(0)
        high = air_density_at(2000)
        self.assertAlmostEqual(sea, 1.225, delta=0.01)
        self.assertLess(high, sea * 0.85)

    def test_hub_height_increases_wind_speed(self):
        t = WindTurbine(hub_height_m=80.0)
        shifted = t.shift_to_hub_height([5.0] * 10, 10.0, 0.14)
        self.assertGreater(shifted[0], 5.0)

    def test_weibull_recovers_shape(self):
        loc = PRESETS["shiraz"]
        res = synthesise_year(
            loc, monthly_ghi=[150] * 12, monthly_temperature=[20] * 12,
            mean_wind_speed=6.0, weibull_k=2.0,
        )
        fit = weibull_fit(res["wind_speed_10m"])
        self.assertAlmostEqual(fit["k"], 2.0, delta=0.25)
        self.assertAlmostEqual(
            sum(res["wind_speed_10m"]) / HOURS_PER_YEAR, 6.0, delta=0.05
        )


class TestBattery(unittest.TestCase):

    def test_round_trip_efficiency_is_one_way_squared(self):
        b = Battery(efficiency=0.95)
        self.assertAlmostEqual(b.round_trip_efficiency, 0.9025, places=6)

    def test_soc_stays_in_bounds(self):
        b = Battery(nominal_energy_kwh=10, nominal_power_kw=5, soc_min=0.2,
                    soc_max=1.0, soc_initial=0.5)
        soc = 0.5
        for _ in range(200):
            room = b.max_charge_kw(2, soc)
            soc = b.step(2, soc, room, 0.0)
            self.assertLessEqual(soc, 1.0 + 1e-9)
        for _ in range(200):
            avail = b.max_discharge_kw(2, soc)
            soc = b.step(2, soc, 0.0, avail)
            self.assertGreaterEqual(soc, b.soc_min - 1e-6)

    def test_rejects_invalid_soc_window(self):
        with self.assertRaises(ValueError):
            Battery(soc_min=0.8, soc_max=0.3)


class TestDispatch(unittest.TestCase):
    """The conservation tests. These are the ones that must never fail."""

    def _run(self, **kw):
        load = demo_load()
        pv = demo_pv()
        system = SystemConfig(
            pv=PVArray(capacity_kwp=10.0), n_pv=kw.get("n_pv", 5),
            battery=Battery(nominal_energy_kwh=50, nominal_power_kw=25,
                            soc_initial=0.5),
            n_battery=kw.get("n_battery", 4),
            genset=Generator(rated_kw=50) if kw.get("genset") else None,
            n_genset=1 if kw.get("genset") else 0,
            grid=GridConnection(import_limit_kw=kw.get("import_kw", 80),
                                export_limit_kw=kw.get("export_kw", 30)),
            ev=kw.get("ev"), n_chargers=kw.get("chargers", 0),
        )
        res = {"load": load, "pv_unit": pv}
        if kw.get("ev"):
            res["ev_profile"] = kw["ev"].sample_year()
        return system, dispatch.simulate(system, res, kw.get("strategy",
                                                            dispatch.LOAD_FOLLOWING))

    def test_energy_is_conserved(self):
        for kw in ({}, {"genset": True}, {"import_kw": 0},
                   {"ev": EVFleet(), "chargers": 4},
                   {"ev": EVFleet(v2g=True), "chargers": 4},
                   {"strategy": dispatch.CYCLE_CHARGING, "genset": True}):
            with self.subTest(**kw):
                _sys, r = self._run(**kw)
                err, hour = r.balance_error()
                self.assertLess(
                    err, 1e-6,
                    f"energy balance violated by {err:g} kW at hour {hour}",
                )

    def test_no_simultaneous_charge_and_discharge(self):
        """
        The original MATLAB assigned both in several branches, which
        double-counts efficiency losses. It must never happen here.
        """
        _sys, r = self._run()
        for t in range(len(r.load)):
            self.assertFalse(
                r.battery_charge[t] > 1e-9 and r.battery_discharge[t] > 1e-9,
                f"battery both charging and discharging at hour {t}",
            )

    def test_islanded_system_has_no_grid_flow(self):
        _sys, r = self._run(import_kw=0, export_kw=0)
        self.assertEqual(sum(r.grid_import), 0.0)
        self.assertEqual(sum(r.grid_export), 0.0)

    def test_export_respects_limit(self):
        _sys, r = self._run(n_pv=40, export_kw=15)
        self.assertLessEqual(max(r.grid_export), 15.0 + 1e-9)

    def test_overnight_ev_window_charges(self):
        """
        The original comparison Hour >= Arr and Hour <= Dep silently gives an
        empty window for an evening arrival and morning departure. The
        overnight wrap must actually charge.
        """
        fleet = EVFleet(arrival=(19.0, 1.0, 18.0, 20.0),
                        departure=(7.0, 1.0, 6.0, 8.0))
        _sys, r = self._run(ev=fleet, chargers=4, n_pv=8)
        self.assertGreater(sum(r.ev_charge), 0.0,
                           "overnight EV fleet never charged")

    def test_curtailment_only_when_everything_is_full(self):
        _sys, r = self._run(n_pv=60, export_kw=0)
        self.assertGreater(sum(r.curtailed), 0.0)


class TestEconomics(unittest.TestCase):

    def test_replacement_count_matches_matlab(self):
        # rep.m: fix(n/life) + (rem(n,life)>0) - 1
        self.assertEqual(economics.replacements(20, 20), 0)
        self.assertEqual(economics.replacements(20, 10), 1)
        self.assertEqual(economics.replacements(20, 7), 2)
        self.assertEqual(economics.replacements(25, 10), 2)

    def test_annuity_factor(self):
        # 8% over 20 years is a standard textbook 9.8181.
        self.assertAlmostEqual(economics.pwf_annuity(0.08, 20), 9.8181, places=3)

    def test_crf_is_reciprocal_of_annuity(self):
        a = economics.pwf_annuity(0.08, 20)
        c = economics.capital_recovery_factor(0.08, 20)
        self.assertAlmostEqual(a * c, 1.0, places=9)

    def test_escalation_degenerates_safely(self):
        # Equal rates would divide by zero if not special-cased.
        self.assertAlmostEqual(economics.pwf_escalating(0.05, 0.05, 20), 20.0)

    def test_zero_interest_does_not_divide_by_zero(self):
        self.assertAlmostEqual(economics.pwf_annuity(0.0, 15), 15.0)

    def test_lcoe_is_positive_and_finite(self):
        load = demo_load()
        system = SystemConfig(
            pv=PVArray(capacity_kwp=10.0, capital_cost=7000), n_pv=5,
            grid=GridConnection(import_limit_kw=200, import_price=0.1),
        )
        r = dispatch.simulate(system, {"load": load, "pv_unit": demo_pv()})
        econ = economics.EconomicParameters()
        e = economics.evaluate(system, r, econ)
        self.assertGreater(e["lcoe"], 0.0)
        self.assertTrue(math.isfinite(e["lcoe"]))
        # NPC must equal the sum of its parts.
        self.assertAlmostEqual(
            e["npc"], sum(v["npc"] for v in e["items"].values()), places=6
        )


class TestMetrics(unittest.TestCase):

    def test_renewable_fraction_bounded(self):
        load = demo_load()
        system = SystemConfig(
            pv=PVArray(capacity_kwp=10.0), n_pv=60,
            grid=GridConnection(import_limit_kw=200),
        )
        r = dispatch.simulate(system, {"load": load, "pv_unit": demo_pv()})
        m = metrics.compute(r, system)
        self.assertGreaterEqual(m["renewable_fraction"], 0.0)
        self.assertLessEqual(m["renewable_fraction"], 1.0)

    def test_objective_vector_flips_maximised_objectives(self):
        m = {"npc": 100.0, "renewable_fraction": 0.8}
        v = metrics.objective_vector(m, ("npc", "renewable_fraction"))
        self.assertEqual(v[0], 100.0)
        self.assertEqual(v[1], -0.8)

    def test_constraint_check(self):
        ok, viol, total = metrics.check_constraints(
            {"lpsp": 0.01}, {"lpsp": ("<=", 0.05)}
        )
        self.assertTrue(ok)
        bad, viol, total = metrics.check_constraints(
            {"lpsp": 0.10}, {"lpsp": ("<=", 0.05)}
        )
        self.assertFalse(bad)
        self.assertGreater(total, 0.0)


class TestPareto(unittest.TestCase):

    def test_dominance(self):
        a = Solution([1], [1.0, 1.0])
        b = Solution([2], [2.0, 2.0])
        c = Solution([3], [1.0, 3.0])
        self.assertTrue(dominates(a, b))
        self.assertFalse(dominates(b, a))
        self.assertTrue(dominates(a, c))
        self.assertFalse(dominates(c, a))

    def test_feasible_beats_infeasible(self):
        good = Solution([1], [10.0, 10.0], feasible=True)
        bad = Solution([2], [1.0, 1.0], feasible=False, violation=0.5)
        self.assertTrue(dominates(good, bad))
        self.assertFalse(dominates(bad, good))

    def test_archive_keeps_only_non_dominated(self):
        a = Archive(capacity=50)
        a.add(Solution([1], [1.0, 5.0]))
        a.add(Solution([2], [5.0, 1.0]))
        a.add(Solution([3], [3.0, 3.0]))
        a.add(Solution([4], [9.0, 9.0]))     # dominated by [3]
        self.assertEqual(len(a), 3)
        a.add(Solution([5], [0.5, 0.5]))     # dominates everything
        self.assertEqual(len(a), 1)

    def test_archive_rejects_duplicates(self):
        a = Archive()
        s = Solution([1, 2], [1.0, 1.0])
        self.assertTrue(a.add(s))
        self.assertFalse(a.add(Solution([1, 2], [1.0, 1.0])))

    def test_non_dominated_sort_ranks(self):
        pop = [Solution([i], [float(i), float(5 - i)]) for i in range(5)]
        pop.append(Solution([9], [9.0, 9.0]))
        fronts = non_dominated_sort(pop)
        self.assertEqual(fronts[0][0].rank, 0)
        self.assertGreater(len(fronts), 1)


class TestSearchSpace(unittest.TestCase):

    def test_clamp_ports_limmin_limmax(self):
        s = SearchSpace(n_pv=(0, 10), n_wind=(0, 5), n_battery=(2, 8),
                        n_genset=(0, 0))
        self.assertEqual(s.clamp([-3, 99, 0, 7]), [0, 5, 2, 0])
        self.assertTrue(s.violates([-1, 0, 2, 0]))
        self.assertFalse(s.violates([0, 0, 2, 0]))

    def test_size(self):
        s = SearchSpace(n_pv=(0, 2), n_wind=(0, 1), n_battery=(0, 0),
                        n_genset=(0, 0))
        self.assertEqual(s.size(), 3 * 2 * 1 * 1)

    def test_rejects_inverted_bounds(self):
        with self.assertRaises(ValueError):
            SearchSpace(n_pv=(10, 2))


class TestSizingElectrical(unittest.TestCase):

    def test_voltage_drop_falls_with_larger_conductor(self):
        v1, p1 = cab.voltage_drop(16, 100, 50, 400)
        v2, p2 = cab.voltage_drop(95, 100, 50, 400)
        self.assertLess(p2, p1)

    def test_cable_escalates_to_parallel_runs(self):
        c = cab.size_cable(2000.0, 30, 400, 3)
        self.assertGreater(c["parallel_runs"], 1)
        self.assertGreaterEqual(c["ampacity_a"], 2000.0)

    def test_adiabatic_minimum(self):
        s = cab.adiabatic_minimum_csa(10000, 0.4, "copper", "xlpe")
        self.assertGreater(s, 0)
        # k=143 for Cu/XLPE: sqrt(1e8 * 0.4)/143 = 44.2 mm2
        self.assertAlmostEqual(s, 44.2, delta=0.5)

    def test_protection_conditions(self):
        p = prot.select_overcurrent(80.0, 100.0, "mcb")
        self.assertTrue(p["compliant"])
        bad = prot.select_overcurrent(95.0, 96.0, "mcb")
        self.assertFalse(bad["compliant"])   # next rating up is 100 A > 96 A

    def test_coordination_loop_always_produces_compliant_pair(self):
        for amps in (25, 63, 140, 320, 700, 1400):
            with self.subTest(amps=amps):
                c, p = coordinate(
                    float(amps), 40, 400, 3, device="mccb" if amps > 100 else "mcb"
                )
                self.assertIsNotNone(c)
                self.assertTrue(
                    p["compliant"],
                    f"{amps} A: {p['rating_a']} A device on {c['ampacity_a']:.0f} A cable",
                )

    def test_string_fuse_not_required_below_three_strings(self):
        self.assertFalse(prot.select_pv_string_fuse(14.0, 2)["required"])
        self.assertTrue(prot.select_pv_string_fuse(14.0, 5)["required"])

    def test_transformerless_inverter_forces_type_b_rcd(self):
        r = prot.select_rcd(has_transformerless_inverter=True)
        self.assertEqual(r["type"], "B")

    def test_string_voltage_limits_respected(self):
        m = PVModule()
        s = design_strings(100.0, m, t_min_c=-20.0, inverter_v_max=1000.0,
                           n_mppt=12, inverter_i_max_per_mppt=40.0)
        self.assertTrue(s["valid"], s["errors"])
        self.assertLessEqual(s["string_voc_cold_v"], 1000.0)
        self.assertGreaterEqual(
            s["string_vmp_hot_v"], s["limits"]["mppt_window_v"][0]
        )

    def test_string_design_reports_required_mppt_count(self):
        """An infeasible input count must say what would fix it."""
        m = PVModule()
        s = design_strings(100.0, m, n_mppt=2, inverter_i_max_per_mppt=30.0)
        self.assertFalse(s["valid"])
        self.assertIn("needs at least", s["errors"][0])

    def test_battery_discharge_respects_efficiency(self):
        """
        Draining the full available power must land exactly on soc_min, not
        below it. This is the bug the original MATLAB sim4.m carries.
        """
        b = Battery(nominal_energy_kwh=100, nominal_power_kw=1e6,
                    efficiency=0.90, soc_min=0.2, soc_max=1.0, soc_initial=0.9)
        soc = 0.9
        avail = b.max_discharge_kw(1, soc)
        soc = b.step(1, soc, 0.0, avail)
        self.assertAlmostEqual(soc, 0.2, places=9)

    def test_battery_charge_reaches_soc_max(self):
        b = Battery(nominal_energy_kwh=100, nominal_power_kw=1e6,
                    efficiency=0.90, soc_min=0.2, soc_max=1.0, soc_initial=0.3)
        soc = 0.3
        room = b.max_charge_kw(1, soc)
        soc = b.step(1, soc, room, 0.0)
        self.assertAlmostEqual(soc, 1.0, places=9)

    def test_inverter_clipping_within_budget(self):
        dc = demo_pv(peak=100.0)
        spec = size_pv_inverter(dc, 100.0, max_clipping_loss=0.02)
        self.assertLessEqual(spec["clipping_loss"], 0.021)
        self.assertGreater(spec["total_ac_kw"], 0)


class TestFullPipeline(unittest.TestCase):

    def test_study_is_reproducible(self):
        load = demo_load()
        base = SystemConfig(
            pv=PVArray(capacity_kwp=10.0, capital_cost=7000,
                       replacement_cost=6000, om_cost_per_year=90),
            battery=Battery(nominal_energy_kwh=20, nominal_power_kw=10,
                            capital_cost=5000, replacement_cost=4000),
            grid=GridConnection(import_limit_kw=80, export_limit_kw=20,
                                import_price=0.12, export_price=0.04),
        )
        res = {"load": load, "pv_unit": demo_pv()}
        runs = []
        for _ in range(2):
            study = SizingStudy(base, res, seed=99,
                                constraints={"lpsp": ("<=", 0.05)})
            r = study.run(n_particles=8, n_iterations=6, screen_levels=3)
            rec = r.recommended()
            runs.append(rec.x if rec else None)
        self.assertEqual(runs[0], runs[1],
                         "the same seed produced different designs")

    def test_design_produces_complete_schedules(self):
        load = demo_load()
        system = SystemConfig(
            pv=PVArray(capacity_kwp=25.0), n_pv=8,
            battery=Battery(nominal_energy_kwh=50, nominal_power_kw=25), n_battery=4,
            grid=GridConnection(import_limit_kw=100, export_limit_kw=40),
        )
        r = dispatch.simulate(system, {"load": load, "pv_unit": demo_pv()})
        d = size_system(system, r, location=PRESETS["shiraz"])
        self.assertTrue(d["schedules"]["cables"])
        self.assertTrue(d["schedules"]["protection"])
        self.assertTrue(d["schedules"]["isolation"])
        for p in d["schedules"]["protection"]:
            self.assertTrue(
                p["compliant"],
                f"{p['circuit']}: {p['rating_a']} A device not compliant",
            )

    def test_diagram_renders_valid_svg(self):
        from ensys.diagram import sld

        load = demo_load()
        system = SystemConfig(
            pv=PVArray(capacity_kwp=25.0), n_pv=4,
            battery=Battery(nominal_energy_kwh=50, nominal_power_kw=25), n_battery=2,
            grid=GridConnection(import_limit_kw=100),
        )
        r = dispatch.simulate(system, {"load": load, "pv_unit": demo_pv()})
        d = size_system(system, r)
        svg = sld.render(d, "Test")
        self.assertTrue(svg.startswith("<svg"))
        self.assertTrue(svg.rstrip().endswith("</svg>"))
        self.assertEqual(svg.count("<svg"), 1)
        # Well-formedness, cheaply: the parser will raise on malformed XML.
        import xml.etree.ElementTree as ET

        ET.fromstring(svg)


class TestDiagramTopologies(unittest.TestCase):
    """
    A drawing is only useful if it is the right drawing.

    Each case below is a distinct connection arrangement — a rooftop array,
    a plant behind a step-up transformer, a standby set behind a change-over
    — and each is checked twice: that the design is recognised as that
    arrangement, and that the sheet it produces is well-formed and carries
    the ratings a reader needs. Rendering something that parses is the easy
    half; rendering the arrangement the design actually is, is the half that
    goes wrong silently.
    """

    def _design(self, system, peak, **kw):
        r = dispatch.simulate(system, {
            "load": demo_load(peak),
            "pv_unit": demo_pv(),
            "wind_unit": [0.3 + 0.25 * math.sin(t / 137.0)
                          for t in range(HOURS_PER_YEAR)],
        })
        return size_system(system, r, **kw)

    def _cases(self):
        from ensys.diagram import topology as tp

        return {
            tp.RESIDENTIAL_PV: (
                SystemConfig(pv=PVArray(capacity_kwp=1.0), n_pv=5,
                             grid=GridConnection(import_limit_kw=15)),
                6.0, {"system_voltage_v": 230}, {"phases": 1}),
            tp.RESIDENTIAL_PV_STORAGE: (
                SystemConfig(
                    pv=PVArray(capacity_kwp=1.0), n_pv=6,
                    battery=Battery(nominal_energy_kwh=5, nominal_power_kw=3),
                    n_battery=2, grid=GridConnection(import_limit_kw=15)),
                6.0, {"system_voltage_v": 230}, {"phases": 1}),
            tp.RESIDENTIAL_SPLIT: (
                SystemConfig(
                    pv=PVArray(capacity_kwp=1.0), n_pv=6,
                    battery=Battery(nominal_energy_kwh=5, nominal_power_kw=3),
                    n_battery=2, grid=GridConnection(import_limit_kw=15)),
                6.0, {"system_voltage_v": 230},
                {"phases": 1, "split_loads": True}),
            tp.COMMERCIAL_3P: (
                SystemConfig(
                    pv=PVArray(capacity_kwp=25.0), n_pv=6,
                    wind=WindTurbine(rated_kw=50), n_wind=1,
                    battery=Battery(nominal_energy_kwh=50,
                                    nominal_power_kw=25), n_battery=3,
                    ev=EVFleet(charger_kw=22, n_vehicles=8), n_chargers=6,
                    grid=GridConnection(import_limit_kw=250,
                                        export_limit_kw=150)),
                180.0, {}, {}),
            tp.UTILITY_MV: (
                SystemConfig(
                    pv=PVArray(capacity_kwp=250.0), n_pv=8,
                    battery=Battery(nominal_energy_kwh=500,
                                    nominal_power_kw=250), n_battery=4,
                    grid=GridConnection(import_limit_kw=2000,
                                        export_limit_kw=2000)),
                1500.0, {}, {}),
            tp.MV_CUSTOMER: (
                SystemConfig(pv=PVArray(capacity_kwp=250.0), n_pv=6,
                             grid=GridConnection(import_limit_kw=3000,
                                                 export_limit_kw=1200)),
                2200.0, {}, {"mv_customer": True}),
            tp.OFFGRID: (
                SystemConfig(
                    pv=PVArray(capacity_kwp=10.0), n_pv=3,
                    battery=Battery(nominal_energy_kwh=20,
                                    nominal_power_kw=10), n_battery=4,
                    genset=Generator(rated_kw=20), n_genset=1,
                    grid=GridConnection(import_limit_kw=0, export_limit_kw=0)),
                18.0, {"system_voltage_v": 230}, {"phases": 1}),
            tp.BACKUP_UPS: (
                SystemConfig(
                    battery=Battery(nominal_energy_kwh=10,
                                    nominal_power_kw=5), n_battery=2,
                    grid=GridConnection(import_limit_kw=20)),
                8.0, {"system_voltage_v": 230}, {"phases": 1}),
            tp.PV_WITH_GENSET: (
                SystemConfig(pv=PVArray(capacity_kwp=1.0), n_pv=8,
                             genset=Generator(rated_kw=12), n_genset=1,
                             grid=GridConnection(import_limit_kw=18)),
                9.0, {"system_voltage_v": 230}, {"phases": 1}),
        }

    def test_each_arrangement_is_recognised(self):
        from ensys.diagram import topology as tp

        for expected, (system, peak, dkw, tkw) in self._cases().items():
            d = self._design(system, peak, **dkw)
            self.assertEqual(
                tp.classify(d, **tkw), expected,
                f"{expected} was classified as {tp.classify(d, **tkw)}",
            )

    def test_every_arrangement_renders_a_well_formed_sheet(self):
        import xml.etree.ElementTree as ET
        from ensys.diagram import sld

        for name, (system, peak, dkw, tkw) in self._cases().items():
            d = self._design(system, peak, **dkw)
            for three_line in (False, True):
                svg = sld.render(d, title=name, three_line=three_line, **tkw)
                ET.fromstring(svg)          # raises on malformed XML
                self.assertEqual(svg.count("<svg"), 1, name)
                for required in ("MAIN AC DISTRIBUTION BOARD", "LV BUSBAR",
                                 "MAIN EARTH BAR", "REVISION"):
                    self.assertIn(required, svg, f"{name}: missing {required}")

    def test_ratings_reach_the_drawing(self):
        """
        The drawing exists to carry the numbers. If a rating the sizing
        stage computed does not appear on the sheet, the sheet is decorative.
        """
        from ensys.diagram import sld

        system, peak, dkw, tkw = self._cases()["commercial_3p"]
        d = self._design(system, peak, **dkw)
        svg = sld.render(d, title="ratings", **tkw)

        pv = d["pv"]
        self.assertIn(f"{pv['ac_protection']['rating_a']:,.0f} A", svg)
        self.assertIn(f"{pv['strings']['string_voc_cold_v']:,.0f} V", svg)
        self.assertIn(f"{pv['inverter']['total_ac_kw']:,.1f} kW", svg)
        self.assertIn(f"{d['battery']['pcs']['rated_kw']:,.0f} kW", svg)
        self.assertIn(f"{d['ev']['chargers']:,.0f} x", svg)
        # every protective device in the schedule, by rating
        for row in d["schedules"]["protection"]:
            self.assertIn(f"{row['rating_a']:,.0f} A", svg,
                          f"{row['circuit']} rating missing from the drawing")

    def test_standby_set_is_not_paralleled_with_the_utility(self):
        """
        A generator behind a change-over must not also appear on the
        busbar. Drawing it on both is an inter-connection the scheme is
        specifically designed to prevent, and it is an easy mistake to make
        by treating every source as a bus feeder.
        """
        from ensys.diagram import sld, topology as tp

        system, peak, dkw, tkw = self._cases()["pv_with_genset"]
        d = self._design(system, peak, **dkw)
        spec = tp.build(d, **tkw)
        self.assertTrue(spec.has_sub_db)
        svg = sld.render(d, title="genset", **tkw)
        self.assertIn("NO INTER-CONNECTION", svg)
        self.assertIn("CHANGE-OVER SWITCH", svg)
        self.assertIn("STANDBY GENSET", svg)

    def test_medium_voltage_designs_get_a_step_up_and_a_warning(self):
        from ensys.diagram import sld, topology as tp

        system, peak, dkw, tkw = self._cases()["utility_mv"]
        d = self._design(system, peak, **dkw)
        spec = tp.build(d, **tkw)
        self.assertTrue(spec.is_mv)
        self.assertGreater(spec.mv["transformer_kva"], 0)
        self.assertTrue(any("grid-code" in w or "network operator" in w
                            for w in spec.warnings))
        svg = sld.render(d, title="mv", **tkw)
        for required in ("STEP-UP TRANSFORMER", "MV BUSBAR",
                         "MV CIRCUIT BREAKER", spec.mv["vector_group"]):
            self.assertIn(required, svg)

    def test_browser_module_list_matches_the_package(self):
        """
        Pyodide has no directory listing, so web/boot.js names every module
        explicitly. A module added to the package and not to that list fails
        at import in the browser and nowhere else — which is to say, it
        fails only for the user.
        """
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        on_disk = set()
        for dirpath, _dirs, files in os.walk(os.path.join(root, "ensys")):
            for f in files:
                if not f.endswith(".py"):
                    continue
                rel = os.path.relpath(os.path.join(dirpath, f),
                                      os.path.join(root, "ensys"))
                on_disk.add(rel[:-3].replace(os.sep, "/"))

        with open(os.path.join(root, "web", "boot.js")) as fh:
            boot = fh.read()
        block = boot[boot.index("const MODULES = ["):]
        block = block[:block.index("]")]
        listed = set(re.findall(r"'([^']+)'", block))

        self.assertEqual(
            on_disk - listed, set(),
            "modules missing from web/boot.js; the browser build will fail",
        )
        self.assertEqual(
            listed - on_disk, set(),
            "web/boot.js lists modules that do not exist",
        )


class TestEVFleetAffectsResults(unittest.TestCase):
    """
    The defect that made this whole area worthless: five charge points and
    twenty gave byte-identical answers. The fleet size was baked into the
    profile at the moment it was drawn, the unit count was ignored by every
    method that read it, and the charge points were not a decision variable
    at all — the same mistake `mainWT.m` made, where an EV dimension was
    searched that `sim4.m` never read.
    """

    def _run(self, n_chargers, scenario="uncontrolled", tariff=None,
             profile=None):
        from ensys.models.evfleet import EVFleet

        ev = EVFleet(archetype="residential", scenario=scenario,
                     charger_kw=11, battery_kwh=50)
        grid = GridConnection(import_limit_kw=400, export_limit_kw=150)
        if tariff is not None:
            grid = GridConnection(import_limit_kw=400, export_limit_kw=150,
                                  import_price=tariff,
                                  export_price=[0.05] * HOURS_PER_YEAR)
        system = SystemConfig(
            pv=PVArray(capacity_kwp=25.0), n_pv=4,
            battery=Battery(nominal_energy_kwh=50, nominal_power_kw=25),
            n_battery=2, grid=grid, ev=ev, n_chargers=n_chargers,
        )
        res = {"load": demo_load(60.0), "pv_unit": demo_pv(1.0),
               "ev_profile": profile if profile is not None else ev.unit_profile()}
        r = dispatch.simulate(system, res)
        e = economics.evaluate(
            system, r, economics.EconomicParameters(project_years=20)
        )
        return system, r, e

    def test_more_chargers_means_more_energy_and_more_cost(self):
        seen = []
        for n in (5, 10, 20, 40):
            _s, r, e = self._run(n)
            seen.append((n, r.totals["ev_charge_kwh"], e["npc"]))
        for (n0, kwh0, npc0), (n1, kwh1, npc1) in zip(seen, seen[1:]):
            self.assertGreater(
                kwh1, kwh0 * 1.2,
                f"{n1} chargers took {kwh1:,.0f} kWh against {kwh0:,.0f} for "
                f"{n0}: the fleet size is not reaching the dispatch",
            )
            self.assertGreater(npc1, npc0)

    def test_the_fleet_takes_the_energy_the_vehicles_need(self):
        """
        A sanity check against arithmetic rather than against itself: the
        annual energy at the plug is the number of arrivals times the
        energy each one needs to reach its departure target. The model sat
        at 95% state of charge all year and drew a tenth of it, because
        every count in the profile was scaled to the fleet except the
        arrivals, so the blend that brings a depleted vehicle in was
        diluted by exactly that factor.
        """
        from ensys.models.evfleet import EVFleet

        ev = EVFleet(archetype="residential", scenario="uncontrolled",
                     charger_kw=11, battery_kwh=50)
        prof = ev.unit_profile()
        n = 20
        arrivals = sum(prof["just_arrived"]) * n
        mean_arrival_soc = (
            sum(a * j for a, j in zip(prof["arrival_soc"], prof["just_arrived"]))
            / max(1e-9, sum(prof["just_arrived"]))
        )
        expected = (
            arrivals * ev.battery_kwh * ev.vehicles_per_point
            * (ev.soc_departure_target - mean_arrival_soc) / ev.efficiency
        )
        _s, r, _e = self._run(n, profile=prof)
        got = r.totals["ev_charge_kwh"]
        self.assertGreater(
            got, expected * 0.6,
            f"the fleet drew {got:,.0f} kWh where the vehicles need about "
            f"{expected:,.0f} kWh",
        )
        self.assertLess(got, expected * 2.2)

    def test_smart_charging_beats_uncontrolled_on_a_time_of_use_tariff(self):
        """
        The point of the whole exercise. If shifting into the cheap hours
        does not reduce the bill, the control strategy is decorative.
        """
        tou = [0.28 if 17 <= (t % 24) < 22 else (0.08 if (t % 24) < 7 else 0.16)
               for t in range(HOURS_PER_YEAR)]
        _s, r0, e0 = self._run(20, "uncontrolled", tou)
        _s, r1, e1 = self._run(20, "v1g_smart", tou)
        self.assertLess(
            e1["items"]["grid"]["npc"], e0["items"]["grid"]["npc"] * 0.9,
            "smart charging saved less than a tenth of the grid bill",
        )
        # and it must not buy that saving by stranding drivers
        self.assertLessEqual(r1.totals["ev_unmet_departures"],
                             r0.totals["ev_unmet_departures"])

    def test_every_scenario_is_reachable_and_meets_the_departure_duty(self):
        from ensys.models.evfleet import SCENARIOS

        tou = [0.28 if 17 <= (t % 24) < 22 else (0.08 if (t % 24) < 7 else 0.16)
               for t in range(HOURS_PER_YEAR)]
        for sc in SCENARIOS:
            _s, r, _e = self._run(20, sc, tou)
            self.assertGreater(r.totals["ev_charge_kwh"], 0, sc)
            self.assertEqual(r.totals["ev_unmet_departures"], 0, sc)

    def test_the_fleet_is_a_demand_not_an_option(self):
        """
        The optimiser must not be able to make the problem easier by
        deleting the vehicles. Every objective improves if the fleet is
        allowed to go to zero, because the hardest part of the problem
        goes with it — so the count is fixed unless the study explicitly
        asks for the number of charge points to be sized.
        """
        from ensys.models.evfleet import EVFleet
        from ensys.optimise import SizingStudy

        ev = EVFleet(archetype="residential", charger_kw=11)
        system = SystemConfig(
            pv=PVArray(capacity_kwp=25.0), n_pv=1,
            grid=GridConnection(import_limit_kw=200),
            ev=ev, n_chargers=10,
        )
        res = {"load": demo_load(60.0), "pv_unit": demo_pv(1.0),
               "ev_profile": ev.unit_profile()}

        fixed = SizingStudy(system, res)
        i = fixed.space.keys.index("ev_fleet")
        self.assertEqual(fixed.space.bounds[i], (10, 10))

        sized = SizingStudy(system, res, size_ev_fleet=True)
        j = sized.space.keys.index("ev_fleet")
        self.assertEqual(sized.space.bounds[j][0], 1)
        self.assertGreater(sized.space.bounds[j][1], 10)

    def test_the_charge_points_are_a_decision_variable(self):
        from ensys.models.evfleet import EVFleet
        from ensys.optimise import SizingStudy

        ev = EVFleet(archetype="residential", charger_kw=11)
        system = SystemConfig(
            pv=PVArray(capacity_kwp=25.0), n_pv=1,
            grid=GridConnection(import_limit_kw=200),
            ev=ev, n_chargers=10,
        )
        study = SizingStudy(system, {
            "load": demo_load(60.0), "pv_unit": demo_pv(1.0),
            "ev_profile": ev.unit_profile(),
        })
        self.assertIn("ev_fleet", study.space.keys)
        # and the box must line up with the system's own decision order, or
        # a dimension drives the wrong asset
        self.assertEqual(study.space.keys, system.decision_keys())
        self.assertEqual(len(study.space.bounds), len(system.decision_keys()))


class TestDistributionVoltage(unittest.TestCase):
    """
    Fifteen megawatts of wind came out as 28,000 A on forty cables in
    parallel, with four of five protective devices non-compliant, because
    every feeder was sized for a whole technology at a fixed 400 V.
    """

    def _big(self):
        system = SystemConfig(
            pv=PVArray(capacity_kwp=1050.0), n_pv=6,
            wind=WindTurbine(rated_kw=3000), n_wind=5,
            battery=Battery(nominal_energy_kwh=1100, nominal_power_kw=550),
            n_battery=3,
            grid=GridConnection(import_limit_kw=5000, export_limit_kw=5000),
        )
        res = {
            "load": demo_load(3000.0), "pv_unit": demo_pv(1.0),
            "wind_unit": [0.3 + 0.25 * math.sin(t / 137.0)
                          for t in range(HOURS_PER_YEAR)],
        }
        r = dispatch.simulate(system, res)
        return system, size_system(system, r)

    def test_a_multi_megawatt_plant_is_connected_at_medium_voltage(self):
        _s, d = self._big()
        self.assertTrue(d["distribution"]["is_mv"])
        self.assertGreaterEqual(d["distribution"]["connection_voltage_v"], 3300)
        self.assertIn("step_up", d)
        self.assertGreater(d["step_up"]["transformers"], 0)
        self.assertTrue(d["distribution"]["reason"])

    def test_no_feeder_carries_an_impossible_current(self):
        from ensys.sizing import voltage as volt

        _s, d = self._big()
        for row in d["schedules"]["cables"]:
            if row.get("current_a") is None:
                continue
            self.assertLess(
                row["current_a"], 6300,
                f"{row['circuit']} carries {row['current_a']:,.0f} A",
            )
            runs = row.get("runs") or 1
            self.assertLessEqual(
                runs, volt.MAX_PARALLEL_RUNS + 2,
                f"{row['circuit']} needs {runs} conductors in parallel",
            )

    def test_every_protective_device_is_compliant(self):
        for system, d in (self._big(),):
            for p in d["schedules"]["protection"]:
                self.assertTrue(
                    p["compliant"],
                    f"{p['circuit']}: {p['rating_a']} A device does not "
                    f"protect its conductor",
                )

    def test_a_small_site_stays_at_low_voltage(self):
        system = SystemConfig(
            pv=PVArray(capacity_kwp=25.0), n_pv=6,
            battery=Battery(nominal_energy_kwh=50, nominal_power_kw=25),
            n_battery=3,
            grid=GridConnection(import_limit_kw=250, export_limit_kw=150),
        )
        r = dispatch.simulate(system, {"load": demo_load(60.0),
                                       "pv_unit": demo_pv(1.0)})
        d = size_system(system, r)
        self.assertFalse(d["distribution"]["is_mv"])
        self.assertNotIn("step_up", d)
        self.assertEqual(d["distribution"]["connection_voltage_v"], 400)

    def test_machines_are_counted_not_merged(self):
        """Five turbines are five feeders, not one enormous conductor."""
        system, d = self._big()
        self.assertEqual(d["wind"]["feeders"]["units"], system.n_wind)

    def test_inverters_share_a_station_but_turbines_do_not(self):
        """
        A transformer per machine is right for wind and wrong for PV. Fifty
        100 kWp inverters in one field are a handful of inverter stations,
        not fifty step-ups — and a drawing showing fifty says the tool does
        not know how a PV plant is put together.
        """
        system = SystemConfig(
            pv=PVArray(capacity_kwp=100.0), n_pv=50,
            wind=WindTurbine(rated_kw=500), n_wind=4,
            grid=GridConnection(import_limit_kw=4000, export_limit_kw=4000),
        )
        res = {
            "load": demo_load(2000.0), "pv_unit": demo_pv(1.0),
            "wind_unit": [0.3 + 0.25 * math.sin(t / 137.0)
                          for t in range(HOURS_PER_YEAR)],
        }
        d = size_system(system, dispatch.simulate(system, res))
        step = d["step_up"]
        groups = {g["name"]: g for g in step["groups"]}

        pv = groups["PV inverter station"]
        self.assertLess(pv["count"], system.n_pv,
                        "every inverter was given its own transformer")
        self.assertEqual(groups["Wind turbine"]["count"], system.n_wind,
                         "a turbine transformer stands at the base of its "
                         "own tower and cannot be shared")

        from ensys.sizing import voltage as volt
        for g in step["groups"]:
            self.assertLessEqual(
                g["kva_each"], volt.MAX_BLOCK_KVA * 1.2,
                f"{g['name']}: {g['kva_each']} kVA is beyond a standard "
                f"distribution transformer",
            )
        self.assertEqual(step["transformers"],
                         sum(g["count"] for g in step["groups"]))


class TestServerTransport(unittest.TestCase):
    """
    The interface can only be as reliable as the transport under it, and
    the failures here are the ones that produce a splash screen that never
    goes away: a script the browser refuses to run, an action reachable one
    way and not the other, a server on the port that is not this one.
    """

    @classmethod
    def setUpClass(cls):
        import threading
        import time as _time
        from http.server import ThreadingHTTPServer

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if root not in sys.path:
            sys.path.insert(0, root)
        import server as srv

        cls.srv_mod = srv
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
        cls.httpd.verbose = False
        cls.httpd.started = _time.time()
        cls.httpd.daemon_threads = True
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever,
                                      daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def _get(self, path):
        import urllib.request

        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}{path}", timeout=10
        ) as r:
            return r.status, dict(r.headers), r.read()

    def _post(self, path, obj):
        import urllib.request

        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(obj).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode("utf-8"))

    def test_scripts_are_served_as_javascript(self):
        """
        With X-Content-Type-Options: nosniff — which is right to send — a
        browser refuses to execute a script served as text/plain, and the
        application never starts. On Windows the content type comes from the
        registry unless the server states it, and a stray registry entry is
        enough to break the whole tool with no visible cause.
        """
        for name, expected in (("/app.js", "text/javascript"),
                               ("/boot.js", "text/javascript"),
                               ("/style.css", "text/css"),
                               ("/index.html", "text/html")):
            _s, headers, _b = self._get(name)
            self.assertTrue(
                headers["Content-Type"].startswith(expected),
                f"{name} served as {headers['Content-Type']}, "
                f"not {expected}",
            )
            self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")

    def test_health_identifies_this_installation(self):
        _s, _h, body = self._get("/api/health")
        health = json.loads(body.decode("utf-8"))
        self.assertTrue(health["ok"])
        self.assertEqual(health["pid"], os.getpid())
        self.assertEqual(
            os.path.normcase(health["root"]),
            os.path.normcase(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__)))),
        )

    def test_actions_are_reachable_by_get_as_well_as_post(self):
        """
        The GET route is the escape hatch for environments where something
        between the browser and the server stalls POSTs to loopback. It has
        to return the same thing, or the fallback trades one failure for
        another.
        """
        from urllib.parse import quote

        _s, by_post = self._post("/api/info", {})
        _s, _h, body = self._get("/api/info?payload=" + quote("{}"))
        by_get = json.loads(body.decode("utf-8"))
        self.assertTrue(by_post["ok"] and by_get["ok"])
        self.assertEqual(by_post["version"], by_get["version"])
        self.assertEqual(
            sorted(by_post["presets"]["technologies"]["groups"]),
            sorted(by_get["presets"]["technologies"]["groups"]),
        )

    def test_every_engine_request_has_a_deadline(self):
        """
        A request without a deadline is how the interface ends up on a
        splash screen for ever. `fetch` resolves when the response headers
        arrive, so the deadline has to cover reading the body as well - the
        stall we actually saw was mid-body, and a timeout that had already
        been cleared could not fire.
        """
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "web", "boot.js")) as fh:
            boot = fh.read()

        helper = boot[boot.index("async function timed("):]
        helper = helper[:helper.index("\n  }\n")]
        self.assertIn("await r.text()", helper,
                      "the body must be read while the abort signal is live")
        self.assertNotIn("clearTimeout", helper.split("await r.text()")[0],
                         "the deadline is cleared before the body is read")

        # The only other fetch is the Pyodide module loader, which reads
        # local files at a known path and cannot stall on a network.
        transport = boot[boot.index("async function serverCall"):
                         boot.index("async function bootPyodide")]
        self.assertNotIn("fetch(", transport,
                         "the server transport must go through timed()")

    def test_the_interface_is_never_hidden_behind_start_up(self):
        """
        The failure that cost the most time: the application markup carried
        `hidden` and was revealed only after a successful API round-trip, so
        any hiccup on that one call left a spinner turning over a blank
        page with no way to tell what had happened. The interface must be on
        screen from the first paint, whatever the engine is doing - as it is
        in the Earthing_System build this project is modelled on.
        """
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "web", "index.html")) as fh:
            html = fh.read()
        with open(os.path.join(root, "web", "app.js")) as fh:
            app = fh.read()

        self.assertIn('<div id="app">', html,
                      "the application container must not carry `hidden`")
        self.assertNotIn('id="app" hidden', html)
        # No loading screen in the markup at all: boot.js creates one only
        # for the browser-engine download and removes it again.
        self.assertNotIn('id="boot"', html)
        # And nothing in start-up may reveal the interface, because it was
        # never concealed.
        self.assertNotIn("$('#app').hidden = false", app)

    def test_unknown_action_reports_rather_than_hangs(self):
        import urllib.error
        from urllib.parse import quote

        try:
            self._get("/api/not_a_real_action?payload=" + quote("{}"))
            body = None
        except urllib.error.HTTPError as e:
            body = json.loads(e.read().decode("utf-8"))
        self.assertIsNotNone(body, "an unknown action should be refused")
        self.assertFalse(body["ok"])
        self.assertTrue(body["error"])


class TestApi(unittest.TestCase):

    def test_unknown_action_is_reported(self):
        from ensys import api

        r = api.handle("does_not_exist", {})
        self.assertFalse(r["ok"])
        self.assertIn("Unknown action", r["error"])

    def test_csv_parsing_picks_named_column(self):
        from ensys.api import parse_csv_column

        text = "time,load_kw,other\n0,10.5,x\n1,11.5,y\n"
        vals, note = parse_csv_column(text, "load_kw")
        self.assertEqual(vals, [10.5, 11.5])

    def test_csv_parsing_skips_comments(self):
        from ensys.api import parse_csv_column

        text = "# a header comment\n# another\ntime,v\n0,1\n1,2\n"
        vals, _ = parse_csv_column(text, "v")
        self.assertEqual(vals, [1.0, 2.0])

    def test_info_returns_presets(self):
        from ensys import api

        r = api.handle("info", {})
        self.assertTrue(r["ok"])
        self.assertIn("turbines", r["presets"])
        self.assertIn("objectives", r["presets"])


class TestOptimisationAlgorithms(unittest.TestCase):
    """
    A choice of algorithm is only worth having if the alternatives are
    genuinely interchangeable and genuinely find the same answer. Both
    halves are tested here: the contract each one honours, and agreement
    with an exhaustive search on a problem small enough to enumerate.
    """

    def _problem(self):
        base = SystemConfig(
            pv=PVArray(capacity_kwp=10.0, capital_cost=7000,
                       replacement_cost=6000, om_cost_per_year=90),
            battery=Battery(nominal_energy_kwh=20, nominal_power_kw=10,
                            capital_cost=5000, replacement_cost=4000),
            genset=Generator(rated_kw=25, capital_cost=8000,
                             replacement_cost=7000, om_cost_per_hour=0.5,
                             fuel_price=1.1),
            grid=GridConnection(import_limit_kw=40, export_limit_kw=10,
                                import_price=0.32, export_price=0.03),
        )
        res = {"load": demo_load(), "pv_unit": demo_pv()}
        space = SearchSpace(bounds=[(0, 9), (0, 9), (0, 3)],
                            keys=["pv", "battery", "genset"])
        return base, res, space

    def _run(self, algorithm, n_particles=12, n_iterations=10):
        base, res, space = self._problem()
        study = SizingStudy(base, res, seed=99, space=space,
                            constraints={"lpsp": ("<=", 0.05)},
                            algorithm=algorithm)
        return study.run(n_particles=n_particles, n_iterations=n_iterations,
                         screen_levels=3)

    def test_every_advertised_algorithm_runs(self):
        from ensys.optim import algorithms as alg

        for entry in alg.catalogue():
            with self.subTest(entry["key"]):
                r = self._run(entry["key"], n_particles=6, n_iterations=4)
                self.assertTrue(r.front, "returned an empty front")
                self.assertEqual(r.summary()["algorithm"], entry["key"])
                # Whatever ran, the report reads the same fields.
                for k in ("evaluations", "front_size", "recommended",
                          "algorithm_label", "exact"):
                    self.assertIn(k, r.summary())

    def test_an_unknown_algorithm_is_refused_not_substituted(self):
        base, res, space = self._problem()
        with self.assertRaises(ValueError):
            SizingStudy(base, res, space=space, algorithm="genetic-ish")

        from ensys import api

        r = api.handle("run_study", {"algorithm": "genetic-ish"})
        self.assertFalse(r["ok"])
        self.assertIn("genetic-ish", r["error"])

    def test_exhaustive_search_reports_itself_as_exact(self):
        # 10 x 10 x 4 = 400 designs; a budget of 400 covers all of them.
        r = self._run("grid", n_particles=40, n_iterations=10)
        self.assertTrue(r.summary()["exact"])
        self.assertTrue(
            any("exact" in n for n in r.summary()["warnings"]),
            "an exact front must say so in the run notes",
        )

    def test_a_grid_too_large_for_the_budget_says_so(self):
        r = self._run("grid", n_particles=5, n_iterations=5)
        self.assertFalse(r.summary()["exact"])
        self.assertTrue(
            any("not an exhaustive search" in n
                for n in r.summary()["warnings"]),
            "a coarse grid must not be presented as an exhaustive one",
        )

    def test_the_metaheuristics_agree_with_the_exhaustive_front(self):
        """
        The one test that can catch a broken optimiser.

        Every other check on a heuristic search compares it with itself.
        Here the true front is computed by enumeration and both
        metaheuristics are asked to find it on a fraction of the
        evaluations. A genetic operator that quietly clamps to a bound, or
        a swarm whose leader selection is wrong, still returns a plausible
        front - but not this one.
        """
        truth = {tuple(s.x) for s in self._run("grid", 40, 10).front}
        self.assertGreater(len(truth), 1, "the test problem has no trade-off")

        for alg in ("mopso", "nsga2"):
            with self.subTest(alg):
                r = self._run(alg)
                got = {tuple(s.x) for s in r.front}
                self.assertEqual(
                    got, truth,
                    f"{alg} disagreed with the exhaustive front: "
                    f"missed {sorted(truth - got)}, invented {sorted(got - truth)}",
                )
                self.assertLess(
                    r.summary()["evaluations"], 400,
                    f"{alg} used no fewer evaluations than enumeration, "
                    f"which defeats the point of a heuristic",
                )

    def test_nsga2_is_reproducible(self):
        a = self._run("nsga2").recommended().x
        b = self._run("nsga2").recommended().x
        self.assertEqual(a, b, "the same seed produced different designs")

    def test_exhaustive_search_ignores_the_seed(self):
        base, res, space = self._problem()
        out = []
        for seed in (1, 999999):
            study = SizingStudy(base, res, seed=seed, space=space,
                                constraints={"lpsp": ("<=", 0.05)},
                                algorithm="grid")
            r = study.run(n_particles=40, n_iterations=10, screen_levels=3)
            out.append(sorted(tuple(s.x) for s in r.front))
        self.assertEqual(out[0], out[1],
                         "an enumeration must not depend on a random seed")

    def test_nsga2_respects_the_search_bounds(self):
        """
        SBX and polynomial mutation are defined on the reals. Rounding them
        onto an integer lattice is easy to get wrong in a way that only
        shows at the edges — a child one unit outside the box is clamped
        back and looks fine, while a fixed dimension that moves at all is a
        charge-point count the user did not agree to.
        """
        from ensys.optim.nsga2 import NSGA2

        space = SearchSpace(bounds=[(2, 6), (0, 3), (4, 4)],
                            keys=["pv", "battery", "ev_fleet"])
        seen = []

        def fake(x):
            seen.append(list(x))
            return [float(x[0]), -float(x[1])], {"lpsp": 0.0}, True, 0.0

        NSGA2(space, fake, n_particles=8, n_iterations=12, seed=7).run()
        self.assertTrue(seen)
        for x in seen:
            for v, (lo, hi) in zip(x, space.bounds):
                self.assertTrue(lo <= v <= hi,
                                f"{x} is outside the search space")
            self.assertEqual(x[2], 4, "a fixed dimension was mutated")

    def test_the_algorithm_menu_is_served_to_the_interface(self):
        from ensys import api
        from ensys.optim import algorithms as alg

        r = api.handle("info", {})
        served = r["presets"]["algorithms"]
        self.assertEqual([a["key"] for a in served], alg.keys())
        for a in served:
            self.assertNotIn("class", a, "the API must not serve Python objects")
            self.assertTrue(a["label"] and a["summary"] and a["detail"])
        self.assertIn(r["presets"]["default_algorithm"], alg.keys())

    def test_the_interface_offers_every_algorithm_the_engine_has(self):
        """The menu is built from the engine's list, not a copy of it."""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "web", "app.js"), encoding="utf-8") as f:
            js = f.read()
        self.assertIn("p.algorithms", js,
                      "app.js must populate the menu from the engine")
        with open(os.path.join(root, "web", "index.html"),
                  encoding="utf-8") as f:
            html = f.read()
        self.assertIn('id="algorithm"', html)

    def test_the_analytic_prefilter_follows_the_decision_keys(self):
        """
        `analytic_filter` zips per-unit yields against the decision vector.
        Yields returned in a fixed four-technology order are credited to
        the wrong asset whenever the system has some other set, which
        rejects feasible designs before they are ever simulated.
        """
        from ensys.optim import screen

        system = SystemConfig(
            pv=PVArray(capacity_kwp=10.0),
            genset=Generator(rated_kw=25),
        )
        res = {"pv_unit": demo_pv(), "wind_unit": None}
        keys = ["genset", "pv"]
        y = screen.estimate_unit_yields(res, system, keys=keys)
        self.assertAlmostEqual(y[0], 25 * 8760)
        self.assertAlmostEqual(y[1], sum(demo_pv()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
