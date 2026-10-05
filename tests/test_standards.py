"""
Regression tests for the standards review (October 2026).

Each test names the clause it holds the engine to. The references are the
documents in the project's References folder:

  IEC 60364-4-43 434.5      short-circuit protection: breaking capacity and
                            conductor withstand against let-through energy
  IEC 60364-5-52 T52.2      minimum conductor cross-section
  IEC 60364-5-53 T534.2     SPD continuous operating voltage
  IEC 62548, IEC TS 62257-7-1 (4.1.9, 5.3.4, Table 6)
                            PV string voltage window, string protection and
                            string cable current
  IEC TS 62257-7-3 (5.2.2, Table 1)
                            generator loading band and site derating
  IEC TS 62257-5 9.4.2.3    battery DC fault current, Ik = 10 C
  BS EN IEC 62933-2-1 (5.2.3, 5.2.4, 5.2.6)
                            storage efficiency at the POC, end-of-life
                            capacity, auxiliary consumption
  IEEE Std 1547.9-2022 5.2  Category B reactive capability for storage
  NFPA 855:2026 (Table 1.3, 9.5.1, 15.5)
                            ESS thresholds, groups, dwelling limits
  IEC 61400-1               wind turbine class against hub-height mean speed

Run with:  python -m pytest tests/test_standards.py
"""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ensys.models.genset import Generator
from ensys.models.pv import PVArray
from ensys.models.storage import BatteryStorage
from ensys.models.wind import WindTurbine
from ensys.resources.geo import PRESETS
from ensys.sizing import cables as cab
from ensys.sizing import protection as prot
from ensys.sizing import standards as std
from ensys.sizing.design import coordinate
from ensys.sizing.inverter import PVModule, design_strings, mppt_config


class TestShortCircuitCoordination(unittest.TestCase):

    def test_small_circuit_is_not_oversized_by_the_fault_level(self):
        """
        The old check applied I^2 x 0.4 s at the PCC fault level to every
        circuit, putting 50 mm2 on a 10 A inverter circuit. A miniature
        breaker clears in the first half cycle; the conductor sees its
        let-through energy (434.5.2).
        """
        i = cab.design_current(5.0, 400, 3, 0.95)
        c, p = coordinate(i, 25, 400, 3, fault_current_a=10_000, device="mcb")
        self.assertTrue(p["compliant"])
        self.assertLessEqual(c["csa_mm2"], 2.5)
        self.assertTrue(p["breaking"]["adequate"])

    def test_breaking_capacity_raises_the_device_class(self):
        """434.5.1: a 15 kA-class MCB is not installed on a 25 kA busbar."""
        c, p = coordinate(40.0, 20, 400, 3, fault_current_a=25_000, device="mcb")
        self.assertEqual(p["device"], "mccb")
        self.assertTrue(p["breaking"]["adequate"])
        # and the conductor withstands what that MCCB lets through
        k = cab.K_FACTOR[("copper", "xlpe")]
        self.assertGreaterEqual(c["csa_mm2"], math.sqrt(p["let_through_i2t"]) / k - 1e-9)

    def test_minimum_cross_section(self):
        """IEC 60364-5-52 Table 52.2: 1.5 mm2 copper for power circuits."""
        c = cab.size_cable(2.0, 5, 400, 3)
        self.assertGreaterEqual(c["csa_mm2"], 1.5)

    def test_volt_drop_uses_the_operating_current(self):
        a = cab.size_cable(17.4, 60, 700, phases=0, insulation="pv",
                           ambient_c=80, max_voltage_drop_pct=1.0)
        b = cab.size_cable(17.4, 60, 700, phases=0, insulation="pv",
                           ambient_c=80, max_voltage_drop_pct=1.0,
                           operating_current_a=13.2)
        # Checked at Imp, the volt-drop criterion no longer forces a
        # larger conductor than the ampacity check needs.
        self.assertLessEqual(b["csa_mm2"], a["csa_mm2"])
        self.assertLessEqual(b["voltage_drop_pct"], 1.0 + 1e-9)
        self.assertEqual(b["operating_current_a"], 13.2)

    def test_islanded_supply_that_cannot_trip_a_breaker_is_reported(self):
        c, p = coordinate(400.0, 30, 400, 3, fault_current_a=1500,
                          min_fault_current_a=900, device="mccb", curve="C")
        self.assertFalse(p["instantaneous_trip"])


class TestPVStrings(unittest.TestCase):

    def test_mppt_maximum_is_checked_against_vmp_not_voc(self):
        """
        Voc against the MPPT ceiling shortened every string by about a
        fifth. The ceiling bounds the operating (Vmp) voltage.
        """
        m = PVModule()
        s = design_strings(100.0, m, mppt_v_min=200, mppt_v_max=800,
                           inverter_v_max=1000, t_min_c=-5, n_mppt=12,
                           inverter_i_max_per_mppt=40)
        self.assertTrue(s["valid"], s["errors"])
        self.assertLessEqual(s["string_vmp_cold_v"], 800 + 1e-6)
        self.assertLessEqual(s["string_voc_cold_v"], 1000 + 1e-6)
        self.assertGreater(s["string_voc_cold_v"], 800,
                           "Voc may exceed the MPPT ceiling; only Vmp may not")

    def test_module_maximum_system_voltage_caps_the_string(self):
        m = PVModule(max_system_voltage_v=1000)
        s = design_strings(500.0, m, mppt_v_min=500, mppt_v_max=1300,
                           inverter_v_max=1500, t_min_c=-10, n_mppt=16,
                           inverter_i_max_per_mppt=120)
        self.assertLessEqual(s["string_voc_cold_v"], 1000 + 1e-6)

    def test_large_inverters_are_1500_v_machines(self):
        self.assertEqual(mppt_config(1000)["v_max"], 1500.0)
        self.assertEqual(mppt_config(5)["v_max"], 1000.0)

    def test_string_fuse_follows_the_module_reverse_current_rating(self):
        """IEC 62548: protect when (Np - 1) Isc > I_MOD_MAX_OCPR."""
        self.assertFalse(prot.select_pv_string_fuse(9.0, 3, 20)["required"])
        self.assertTrue(prot.select_pv_string_fuse(9.0, 4, 20)["required"])

    def test_string_fuse_lies_in_the_iec_window(self):
        f = prot.select_pv_string_fuse(9.24, 22, 15)
        self.assertTrue(f["required"])
        self.assertGreaterEqual(f["rating_a"], 1.5 * 9.24)
        self.assertLessEqual(f["rating_a"], min(2.4 * 9.24, 15))
        # the reference study selects a 15 A fuse for this case
        self.assertEqual(f["rating_a"], 15)

    def test_unfused_string_cable_carries_the_reverse_current(self):
        """IEC 62548-1:2023 Table 5: K_I x Isc x (Npo - 1) when unfused."""
        self.assertAlmostEqual(prot.pv_string_cable_current(10.0, 1), 12.5)
        self.assertAlmostEqual(prot.pv_string_cable_current(10.0, 3), 25.0)
        # bifacial modules raise K_I (Annex F.7 b): K_corr = Isc,BNPI / Isc
        m = PVModule(isc=10.0, isc_bnpi=11.5)
        self.assertAlmostEqual(m.k_i, 1.25 * 1.15)
        self.assertAlmostEqual(
            prot.pv_string_cable_current(10.0, 3, k_i=m.k_i), 2 * 10 * 1.25 * 1.15)
        fuse = prot.select_pv_string_fuse(10.0, 6, 20)
        self.assertEqual(prot.pv_string_cable_current(10.0, 6, fuse),
                         fuse["rating_a"])


class TestSurgeProtection(unittest.TestCase):

    def test_ac_spd_uc_is_rms_1_1_u0(self):
        s = prot.select_spd(system_voltage_v=400, earthing="TN-S")
        self.assertAlmostEqual(s["uc_required_v"], 1.1 * 400 / math.sqrt(3), places=6)
        self.assertEqual(s["uc_v"], 275.0)

    def test_it_system_needs_line_voltage(self):
        s = prot.select_spd(system_voltage_v=400, earthing="IT")
        self.assertGreaterEqual(s["uc_v"], 400.0)

    def test_dc_spd_covers_the_cold_open_circuit_voltage(self):
        s = prot.select_spd(dc_side=True, array_voltage_v=1080)
        self.assertGreaterEqual(s["uc_v"], 1080)


class TestGenerator(unittest.TestCase):

    def test_iec_62257_7_3_derating_table(self):
        d = std.genset_site_derating(40.0, 1500.0, 30.0)
        self.assertAlmostEqual(d["temperature_derate"], 0.075, places=9)
        self.assertAlmostEqual(d["altitude_derate"], 0.12, places=9)
        self.assertAlmostEqual(d["factor"], 1 - 0.195, places=9)
        hum = std.genset_site_derating(35.0, 0.0, 80.0)
        self.assertAlmostEqual(hum["humidity_derate"], 0.01, places=9)

    def test_units_are_committed_one_at_a_time(self):
        g = Generator(rated_kw=50.0, min_load_ratio=0.3)
        out, forced = g.dispatch_output(4, 10.0)
        self.assertAlmostEqual(out, 15.0)          # one set at 30 %
        self.assertAlmostEqual(forced, 5.0)
        one = g.fuel_rate(4, 40.0)
        self.assertAlmostEqual(one, g.f0 * 50.0 + g.f1 * 40.0)

    def test_derated_set_delivers_less(self):
        g = Generator(rated_kw=100.0, site_derate=0.8)
        self.assertAlmostEqual(g.available_kw(2), 160.0)

    def test_loading_band(self):
        rec = std.genset_loading_check([60.0] * 100 + [10.0] * 10, 100.0, 1)
        self.assertEqual(rec["status"], std.PASS)
        rec = std.genset_loading_check([95.0] * 100, 100.0, 1)
        self.assertEqual(rec["status"], std.WARN)


class TestStorage(unittest.TestCase):

    def test_round_trip_is_measured_at_the_point_of_connection(self):
        b = BatteryStorage(chemistry="lithium_lfp", pcs_efficiency=0.98)
        self.assertAlmostEqual(b.round_trip_efficiency, (0.97 * 0.98) ** 2)

    def test_auxiliary_consumption_is_a_standing_loss(self):
        a = BatteryStorage(chemistry="lithium_lfp", auxiliary_w_per_kwh=0.0)
        b = BatteryStorage(chemistry="lithium_lfp", auxiliary_w_per_kwh=3.0)
        self.assertAlmostEqual(
            b.self_discharge_per_hour - a.self_discharge_per_hour, 0.003)

    def test_end_of_life_capacity_defaults(self):
        self.assertEqual(BatteryStorage(chemistry="lithium_lfp").eol_capacity_fraction, 0.80)
        self.assertGreater(BatteryStorage(chemistry="flow_vanadium").eol_capacity_fraction, 0.9)

    def test_battery_dc_fault(self):
        f = std.battery_dc_fault(10.0, 2, 50.0)
        self.assertAlmostEqual(f["ik_unit_a"], 2000.0)
        self.assertAlmostEqual(f["ik_bus_a"], 4000.0)

    def test_reactive_capability(self):
        self.assertEqual(std.ess_reactive_capability(100, 100)["status"], std.WARN)
        self.assertEqual(std.ess_reactive_capability(100, 112)["status"], std.PASS)


class TestNFPA855(unittest.TestCase):

    def _by_clause(self, recs, clause):
        return next(r for r in recs if r["clause"] == clause)

    def test_dwelling_unit_limit(self):
        recs = std.nfpa855_check(27.0, 27.0, "lithium_nmc", dwelling=True)
        self.assertEqual(self._by_clause(recs, "15.5.1")["status"], std.FAIL)
        recs = std.nfpa855_check(27.0, 13.5, "lithium_nmc", dwelling=True)
        self.assertEqual(self._by_clause(recs, "15.5.1")["status"], std.PASS)

    def test_dwelling_aggregate_by_location(self):
        recs = std.nfpa855_check(54.0, 13.5, "lithium_lfp", dwelling=True,
                                 location="utility_closet")
        self.assertEqual(self._by_clause(recs, "Table 15.5.2")["status"], std.FAIL)
        recs = std.nfpa855_check(54.0, 13.5, "lithium_lfp", dwelling=True,
                                 location="attached_garage")
        self.assertEqual(self._by_clause(recs, "Table 15.5.2")["status"], std.PASS)

    def test_threshold_and_groups(self):
        li = std.nfpa855_check(15.0, 15.0, "lithium_lfp")
        self.assertEqual(li[0]["status"], std.PASS)
        pb = std.nfpa855_check(60.0, 20.0, "lead_acid_agm")
        self.assertEqual(pb[0]["status"], std.PASS)   # 70 kWh threshold
        big = std.nfpa855_check(500.0, 25.0, "lithium_lfp")
        g = self._by_clause(big, "9.5.1.1-9.5.1.3")
        self.assertEqual(g["values"]["groups"], 10)


class TestWind(unittest.TestCase):

    def test_availability_and_losses_are_applied(self):
        t = WindTurbine(rated_kw=100, hub_height_m=30, rotor_diameter_m=20)
        full, _ = t.output_series([10.0] * 100, availability=1.0, wake_loss=0.0)
        net, info = t.output_series([10.0] * 100)
        # `availability=1.0` overrides only the availability; the 2 %
        # electrical loss stays.
        self.assertAlmostEqual(sum(net) / sum(full), 0.97, places=6)
        self.assertEqual(info["availability"], 0.97)

    def test_iec_class(self):
        self.assertEqual(std.wind_class_check(8.0)["values"]["required_class"], "II")
        self.assertEqual(std.wind_class_check(9.0, "III")["status"], std.FAIL)
        self.assertEqual(std.wind_class_check(7.0, "IIA")["status"], std.PASS)


class TestPVInverter(unittest.TestCase):

    def test_dispatch_series_is_ac(self):
        """IEC 61724-1 measures yield at the AC output."""
        loc = PRESETS["shiraz"] if "shiraz" in PRESETS else next(iter(PRESETS.values()))
        ghi = [max(0.0, 900 * math.sin((h % 24 - 6) / 12 * math.pi)) for h in range(8760)]
        a = PVArray(capacity_kwp=10, inverter_efficiency=0.96, dc_ac_ratio=None)
        ac, info = a.output_series(ghi, loc)
        self.assertAlmostEqual(sum(ac), 0.96 * info["annual_dc_kwh"], delta=1e-6)
        b = PVArray(capacity_kwp=10, inverter_efficiency=0.96, dc_ac_ratio=1.5)
        ac2, _ = b.output_series(ghi, loc)
        self.assertLessEqual(max(ac2), 10 / 1.5 + 1e-9)


class TestEndOfLifeDoesNotLeak(unittest.TestCase):

    def test_faded_battery_does_not_replace_the_study_battery(self):
        """
        The end-of-life re-run swaps in a battery at its end-of-life
        capacity. The study's systems share one asset registry, so writing
        the faded copy into it would shrink the battery of every design
        evaluated afterwards.
        """
        from ensys.optimise import SizingStudy
        from ensys.system import SearchSpace, SystemConfig
        from ensys.models.grid import GridConnection

        bat = BatteryStorage(chemistry="lithium_lfp", nominal_energy_kwh=5.0,
                             nominal_power_kw=2.5, capital_cost=1000)
        base = SystemConfig(
            pv=PVArray(capacity_kwp=1.0, capital_cost=600),
            battery=bat,
            grid=GridConnection(import_limit_kw=10.0, export_limit_kw=2.0),
        )
        load = [0.6 + 0.4 * math.sin(h / 24 * 2 * math.pi) for h in range(8760)]
        res = {"load": load,
               "pv_unit": [max(0.0, 0.8 * math.sin((h % 24 - 6) / 12 * math.pi))
                           for h in range(8760)]}
        study = SizingStudy(base, res, space=SearchSpace(
            bounds=[(0, 4), (1, 3)], keys=["pv", "battery"]), seed=3,
            algorithm="grid")
        r = study.run(n_particles=4, n_iterations=2)
        eol = r.end_of_life_check()
        self.assertIsNotNone(eol)
        self.assertAlmostEqual(eol["battery_capacity_factor"], 0.8)
        self.assertEqual(base.battery.nominal_energy_kwh, 5.0)
        self.assertIs(base.battery, bat)


if __name__ == "__main__":
    unittest.main()


class TestDesignRecord(unittest.TestCase):
    """The full electrical design carries the compliance list."""

    def _design(self, grid):
        from ensys import dispatch
        from ensys.models.grid import GridConnection
        from ensys.sizing.design import size_system
        from ensys.system import SystemConfig

        loc = PRESETS["shiraz"] if "shiraz" in PRESETS else next(iter(PRESETS.values()))
        load = [3.0 + 2.0 * math.sin(h / 24 * 2 * math.pi) for h in range(8760)]
        pv = PVArray(capacity_kwp=1.0)
        ghi = [max(0.0, 900 * math.sin((h % 24 - 6) / 12 * math.pi)) for h in range(8760)]
        unit, info = pv.output_series(ghi, loc)
        sysc = SystemConfig(
            pv=pv, n_pv=8,
            battery=BatteryStorage(chemistry="lithium_lfp",
                                   nominal_energy_kwh=5.0, nominal_power_kw=2.5),
            n_battery=3,
            genset=None if grid else Generator(rated_kw=10.0), n_genset=0 if grid else 1,
            grid=GridConnection(import_limit_kw=20.0, export_limit_kw=5.0)
            if grid else GridConnection(import_limit_kw=0.0, export_limit_kw=0.0),
        )
        r = dispatch.simulate(sysc, {"load": load, "pv_unit": unit})
        return size_system(sysc, r, location=loc,
                           pv_dc_series=[p * 8 for p in info["dc_kw"]],
                           installation={"dwelling": True})

    def test_grid_connected_house(self):
        d = self._design(True)
        titles = {c["title"] for c in d["compliance"]}
        self.assertIn("Individual unit rating", titles)
        self.assertIn("Battery PCS reactive capability", titles)
        self.assertIn("grid", d)
        self.assertIn("interconnection", d["grid"])
        for row in d["schedules"]["cables"]:
            if row["circuit"] in ("PV inverter AC", "Battery PCS AC"):
                self.assertLessEqual(row["csa_mm2"], 6,
                                     "a house inverter circuit is not a 50 mm2 cable")

    def test_islanded_minigrid(self):
        d = self._design(False)
        self.assertTrue(d["faults"]["islanded"])
        titles = {c["title"] for c in d["compliance"]}
        self.assertIn("Automatic disconnection (minimum earth-fault current)", titles)
        self.assertIn("Short-circuit currents", titles)
        self.assertIn("Generator loading band", titles)
        self.assertIn(d["compliance_summary"]["worst"], ("pass", "warn", "fail"))


# ===================================================================
# Second review (October 2026): the "New References" folder.
# ===================================================================

class TestIEC60364_5_52(unittest.TestCase):

    def test_tabulated_values(self):
        from ensys.sizing import iec60364_5_52 as t
        self.assertEqual(t.base_table("copper", "xlpe", "C", 3)[240], 500)   # B.52.5
        self.assertEqual(t.base_table("copper", "pvc", "C", 2)[2.5], 27)     # B.52.2
        self.assertEqual(t.base_table("aluminium", "xlpe", "D1", 3)[95], 154)
        self.assertEqual(t.base_table("copper", "xlpe", "F", 3)[630], 1088)  # B.52.12

    def test_ground_corrections(self):
        from ensys.sizing import iec60364_5_52 as t
        f = t.correction_factor("xlpe", "D2", ground_c=30, soil_resistivity=1.0,
                                n_grouped=3)
        self.assertAlmostEqual(f["temperature"], 0.93)   # B.52.15
        self.assertAlmostEqual(f["soil"], 1.50)          # B.52.16 direct
        self.assertAlmostEqual(f["grouping"], 0.65)      # B.52.18

    def test_parallel_runs_are_a_group(self):
        """B.52.17: four runs of one circuit derate each other."""
        c = cab.size_cable(1400, 20, 400, 3, method="C")
        self.assertGreater(c["parallel_runs"], 1)
        self.assertLess(c["correction_factors"]["grouping"], 1.0)
        self.assertGreaterEqual(c["ampacity_a"], 1400)

    def test_method_changes_the_answer(self):
        a = cab.size_cable(100, 10, 400, 3, method="A1")["csa_mm2"]
        f = cab.size_cable(100, 10, 400, 3, method="E")["csa_mm2"]
        self.assertGreater(a, f)

    def test_aluminium_minimum(self):
        self.assertGreaterEqual(
            cab.size_cable(5, 10, 400, 3, material="aluminium")["csa_mm2"], 10)


class TestIEC60909(unittest.TestCase):

    def test_network_feeder_reproduces_its_fault_level(self):
        from ensys.sizing import iec60909 as sc
        b = sc.Busbar(400)
        b.add_network(10.0)
        f = b.fault()
        self.assertAlmostEqual(f["ik3_max_a"], 10_000, delta=1)
        self.assertAlmostEqual(f["ik3_min_a"], 10_000 * 0.95 / 1.05, delta=1)
        self.assertGreater(f["ip_a"], math.sqrt(2) * 10_000)

    def test_transformer_correction_factor(self):
        from ensys.sizing import iec60909 as sc
        zk = sc.transformer(1000, 400, 6.0, 1.0, 1.05, True)
        z = sc.transformer(1000, 400, 6.0, 1.0, 1.05, False)
        xt = z.imag / (400 ** 2 / 1e6)
        self.assertAlmostEqual(abs(zk) / abs(z), 0.95 * 1.05 / (1 + 0.6 * xt), places=9)

    def test_fault_falls_along_a_cable(self):
        from ensys.sizing import iec60909 as sc
        b = sc.Busbar(400)
        b.add_network(10.0)
        end = b.at_circuit_end(16, 50)
        self.assertLess(end["ik3_max_a"], 10_000)
        self.assertLess(end["ik1_min_a"], end["ik3_min_a"])

    def test_pv_is_left_out_of_the_minimum(self):
        """7.1.3 d)"""
        from ensys.sizing import iec60909 as sc
        b = sc.Busbar(400)
        b.add_converter("PV", 100, 1.2, renewable=True)
        b.add_converter("BESS", 100, 1.5, renewable=False)
        f = b.fault()
        self.assertGreater(f["ik3_max_a"], f["ik3_min_a"])
        self.assertAlmostEqual(f["ik3_min_a"], 1.5 * 100e3 / (math.sqrt(3) * 400), delta=1)

    def test_disconnection(self):
        from ensys.sizing import iec60909 as sc
        self.assertTrue(sc.device_operates("mcb", 16, "C", 200, 0.4))
        self.assertFalse(sc.device_operates("mcb", 16, "C", 120, 0.4))
        self.assertEqual(sc.disconnection_time_s(32, True), 0.4)
        self.assertEqual(sc.disconnection_time_s(250, False), 5.0)


class TestPVPerformance61724(unittest.TestCase):

    def test_yields_and_pr(self):
        poa = [1000.0] * 10 + [0.0] * 14
        dc = [9.0] * 10 + [0.0] * 14
        ac = [8.5] * 10 + [0.0] * 14
        r = std.pv_yields_61724(10.0, poa, dc, ac, [25.0] * 24, -0.004)
        self.assertAlmostEqual(r["yr_h"], 10.0)
        self.assertAlmostEqual(r["yf_h"], 8.5)
        self.assertAlmostEqual(r["pr"], 0.85)
        self.assertAlmostEqual(r["pr_25c"], 0.85)      # cells at 25 C
        hot = std.pv_yields_61724(10.0, poa, dc, ac, [45.0] * 24, -0.004)
        self.assertGreater(hot["pr_25c"], hot["pr"])


class TestStandAloneSizing(unittest.TestCase):

    def test_ieee_1013_capacity(self):
        load = [1.0] * 8760                     # 24 kWh/day
        rec = std.stand_alone_check(load, [1.5] * 8760, battery_kwh=200,
                                    usable_fraction=0.8, eol_fraction=0.8,
                                    autonomy_days=2, design_margin=1.15,
                                    inverter_efficiency=0.96)
        need = 2 * 24 / 0.96 / 0.8 * 1.15
        self.assertAlmostEqual(rec["values"]["required_kwh"], need, places=6)
        self.assertEqual(rec["status"], std.PASS)
        self.assertAlmostEqual(rec["values"]["al_ratio"], 1.5, places=6)

    def test_low_array_to_load_ratio(self):
        rec = std.stand_alone_check([1.0] * 8760, [1.05] * 8760, 500, 0.8, 0.8,
                                    critical=True)
        self.assertEqual(rec["status"], std.WARN)


class TestWindDensity(unittest.TestCase):

    def test_equation_12(self):
        from ensys.models.wind import air_density_iec61400
        self.assertAlmostEqual(air_density_iec61400(15.0, 101325.0, 0.0), 1.225, places=3)
        self.assertLess(air_density_iec61400(15.0, 101325.0, 1.0),
                        air_density_iec61400(15.0, 101325.0, 0.0))

    def test_hourly_density_used(self):
        t = WindTurbine(rated_kw=100, hub_height_m=30, rotor_diameter_m=20)
        cold, i1 = t.output_series([8.0] * 24, temperature_c=[-10.0] * 24)
        hot, i2 = t.output_series([8.0] * 24, temperature_c=[40.0] * 24)
        self.assertGreater(sum(cold), sum(hot))
        self.assertIn("Eq. (12)", i1["density_model"])


class TestReliabilityIndices(unittest.TestCase):

    def test_ieee_1366_single_customer(self):
        from ensys.metrics import reliability_indices
        load = [10.0] * 100
        unmet = [0.0] * 100
        for h in (10, 11, 12, 50):
            unmet[h] = 5.0
        r = reliability_indices(load, unmet, hours_per_year=100)
        self.assertEqual(r["saifi"], 2)
        self.assertEqual(r["saidi_h"], 4)
        self.assertEqual(r["caidi_h"], 2)
        self.assertAlmostEqual(r["asai"], 0.96)


class TestLifeCycleCost(unittest.TestCase):

    def test_residual_value(self):
        """IEC 60300-3-3: a 15-year unit in a 20-year project is replaced
        at year 15 and has 10 of 15 years left at the end."""
        from ensys.economics import component_npc
        a = component_npc(1, 1000, 800, 0, 0, 15, 20, 0.0, 0.0, salvage=False)
        b = component_npc(1, 1000, 800, 0, 0, 15, 20, 0.0, 0.0, salvage=True)
        self.assertAlmostEqual(a["npc"] - b["npc"], 800 * 10 / 15, places=6)
        c = component_npc(1, 1000, 800, 0, 0, 15, 20, 0.0, 0.0,
                          decommissioning_fraction=0.05)
        self.assertAlmostEqual(c["npc"] - a["npc"], 50.0, places=6)
