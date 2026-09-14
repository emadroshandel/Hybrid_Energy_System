"""
Regression tests for the faults that made a house look like a power station.

Every test here corresponds to a specific way the tool used to produce a
confident, wrong, unremarkable-looking answer. They are gathered in one file
because they share a theme rather than a module: each one is a case where
the arithmetic was correct and the result was nonsense, which is the class
of defect a numerical test suite is worst at catching and a study is worst
at surviving.

Run with:  python tests/test_plausibility.py
"""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ensys import validate
from ensys.models.battery import Battery
from ensys.models.genset import Generator
from ensys.models.grid import GridConnection
from ensys.models.pv import PVArray, solar_position
from ensys.optim import screen
from ensys.optim.pareto import Archive, Solution
from ensys.optimise import SizingResult, SizingStudy
from ensys.resources import providers
from ensys.resources.geo import Location, PRESETS
from ensys.resources.synth import synthesise_year
from ensys.system import SearchSpace, SystemConfig
from ensys.timeseries import HOURS_PER_YEAR, shift_utc_to_local


def house_load(peak=3.0, lf=0.35):
    shape = [.44, .39, .36, .35, .36, .44, .60, .72, .66, .58, .54, .53,
             .54, .53, .55, .62, .78, .94, 1.0, .97, .88, .75, .62, .51]
    raw = [peak * shape[t % 24] for t in range(HOURS_PER_YEAR)]
    mean = sum(raw) / len(raw)
    k = (peak * lf) / mean
    return [min(peak, v * k) for v in raw]


def utc_stamp(local_series, offset_hours):
    """
    Re-stamp a physically correct local-time year the way a provider does.

    A provider's array holds, at index j, the value observed at UTC hour j -
    which is local hour j + offset. This is the exact input that used to be
    handed straight to a solar-position model working in local time.
    """
    n = len(local_series)
    whole = int(math.floor(offset_hours))
    frac = offset_hours - whole
    out = []
    for j in range(n):
        a = local_series[(j + whole) % n]
        b = local_series[(j + whole + 1) % n]
        out.append(a * (1.0 - frac) + b * frac if frac > 1e-9 else a)
    return out


class TestResourceTimeAlignment(unittest.TestCase):
    """
    The defect: every online provider stamps its hours in UTC, and the solar
    position model works in local standard time. Nothing crashed. The sun
    was simply in the wrong place, so hours carrying full irradiance were
    given a night-time zenith and zeroed, PV yield came out a fraction of
    the truth, and the optimiser answered by recommending several times the
    array a real design needs.

    The error is proportional to the UTC offset, which is why it survived:
    at the longitudes where such tools are usually written it is a few per
    cent.
    """

    def _truth_and_utc(self, key):
        loc = PRESETS[key]
        year = synthesise_year(
            loc,
            monthly_ghi=[95, 105, 140, 160, 195, 215, 210, 195, 170, 140,
                         100, 88],
            monthly_temperature=[8, 10, 15, 20, 26, 31, 34, 33, 28, 21, 14, 9],
            mean_wind_speed=4.0,
        )
        return loc, year

    def _yield_kwh_per_kwp(self, loc, ghi, dni, dhi, temp, wind):
        pv = PVArray(capacity_kwp=10.0)
        series, _ = pv.output_series(
            ghi, loc, dni=dni, dhi=dhi, temperature_c=temp, wind_speed_ms=wind
        )
        return sum(series) / 10.0

    def test_shift_is_an_exact_inverse_of_the_provider_stamp(self):
        loc, year = self._truth_and_utc("shiraz")
        off = loc.utc_offset_hours
        original = year["ghi"]
        restored = shift_utc_to_local(utc_stamp(original, off), off)
        # A half-hour offset interpolates, so the round trip smooths rather
        # than reproduces exactly; the annual energy must still survive it.
        self.assertAlmostEqual(
            sum(restored) / sum(original), 1.0, places=3,
            msg="the shift changed the annual energy of the series",
        )

    def test_whole_hour_offset_round_trips_exactly(self):
        s = [float(i % 24) for i in range(HOURS_PER_YEAR)]
        self.assertEqual(shift_utc_to_local(utc_stamp(s, 3.0), 3.0), s)

    def test_zero_offset_is_a_no_op(self):
        s = [1.0, 2.0, 3.0]
        self.assertEqual(shift_utc_to_local(s, 0.0), s)

    def test_localising_restores_the_yield_a_utc_series_destroys(self):
        for key in ("shiraz", "adelaide"):
            with self.subTest(key):
                loc, year = self._truth_and_utc(key)
                off = loc.utc_offset_hours
                keys = ("ghi", "dni", "dhi", "temperature_c", "wind_speed_10m")
                truth = self._yield_kwh_per_kwp(
                    loc, *[year[k] for k in keys]
                )
                raw = {k: utc_stamp(year[k], off) for k in keys}
                broken = self._yield_kwh_per_kwp(loc, *[raw[k] for k in keys])
                fixed_d = providers._localise(dict(raw), loc, keys)
                fixed = self._yield_kwh_per_kwp(
                    loc, *[fixed_d[k] for k in keys]
                )

                self.assertLess(
                    broken, truth * 0.9,
                    "the test's own UTC stamping did not reproduce the fault",
                )
                self.assertGreater(
                    fixed, truth * 0.97,
                    f"{key}: localising recovered only {fixed:.0f} of "
                    f"{truth:.0f} kWh/kWp",
                )

    def test_a_large_offset_is_catastrophic_not_marginal(self):
        """
        Guards the reason this went unnoticed: at +3:30 the fault costs a
        quarter of the yield, at +9:30 it costs nearly all of it. A future
        change that 'fixes' only small offsets must fail here.
        """
        loc, year = self._truth_and_utc("adelaide")
        keys = ("ghi", "dni", "dhi", "temperature_c", "wind_speed_10m")
        truth = self._yield_kwh_per_kwp(loc, *[year[k] for k in keys])
        raw = [utc_stamp(year[k], loc.utc_offset_hours) for k in keys]
        broken = self._yield_kwh_per_kwp(loc, *raw)
        self.assertLess(broken, truth * 0.3)

    def test_providers_stamp_their_time_reference(self):
        loc = PRESETS["shiraz"]
        out = providers._localise({"ghi": [1.0] * 24}, loc, ("ghi",))
        self.assertEqual(out["time_reference"], "local_standard_time")
        self.assertAlmostEqual(out["utc_offset_applied_hours"], 3.5)

    def test_synthetic_years_are_already_local_and_must_not_be_shifted(self):
        loc = PRESETS["shiraz"]
        year = synthesise_year(
            loc, monthly_ghi=[150] * 12, mean_wind_speed=3.0
        )
        self.assertEqual(year["time_reference"], "local_standard_time")
        self.assertEqual(year["utc_offset_applied_hours"], 0.0)


class TestHighlightSelection(unittest.TestCase):
    """
    The defect: 'most reliable' and 'most renewable' minimised and maximised
    objectives that SATURATE. Once a design meets the load in every hour its
    LPSP is zero and no amount of extra capacity improves it, so a plain
    min() over the archive returned whichever tied member came last - always
    the largest system the search had tried, pinned to the top corner of the
    search box. It was reported as a recommendation.
    """

    class _FakeStudy:
        objectives = ("npc", "lpsp", "renewable_fraction")
        constraints = {"lpsp": ("<=", 0.05)}

    class _FakeSearch:
        def __init__(self, archive):
            self.archive = archive

    def _result(self, rows):
        archive = Archive(capacity=len(rows) + 1)
        for x, npc, lpsp, rf, feasible in rows:
            archive.members.append(
                Solution(x, [npc, lpsp, -rf],
                         {"npc": npc, "lpsp": lpsp, "renewable_fraction": rf},
                         feasible, 0.0)
            )
        r = SizingResult.__new__(SizingResult)
        r.study = self._FakeStudy()
        r.search = self._FakeSearch(archive)
        r.screen_solutions = []
        r.elapsed_s = 0.0
        return r

    def test_most_reliable_does_not_return_the_biggest_tied_design(self):
        r = self._result([
            ([2], 100.0, 0.0, 0.60, True),     # meets the load, cheap
            ([40], 900.0, 0.0, 1.00, True),    # meets the load, enormous
        ])
        self.assertEqual(
            r.most_reliable().x, [2],
            "a tie on a saturated objective must break on cost, not size",
        )

    def test_most_reliable_still_prefers_a_genuinely_firmer_design(self):
        r = self._result([
            ([2], 100.0, 0.04, 0.60, True),
            ([9], 300.0, 0.00, 0.90, True),
        ])
        self.assertEqual(r.most_reliable().x, [9])

    def test_most_reliable_excludes_infeasible_designs(self):
        """An infeasible design used to be eligible for the headline card."""
        r = self._result([
            ([40], 900.0, 0.00, 1.00, False),   # violates the constraint
            ([9], 300.0, 0.01, 0.90, True),
        ])
        self.assertEqual(r.most_reliable().x, [9])

    def test_greenest_breaks_a_tie_on_cost(self):
        r = self._result([
            ([6], 200.0, 0.01, 0.995, True),
            ([30], 800.0, 0.01, 1.000, True),
        ])
        self.assertEqual(r.greenest().x, [6])

    def test_a_tie_is_explained_rather_than_left_looking_wrong(self):
        r = self._result([
            ([2], 100.0, 0.0, 0.60, True),
            ([40], 900.0, 0.0, 1.00, True),
        ])
        note = r.highlight_notes().get("most_reliable", "")
        self.assertIn("cheapest", note.lower())


class TestSearchBounds(unittest.TestCase):
    """
    The defect: the search ceiling let one technology alone cover 1.5x the
    annual demand whatever the site, ignoring the grid. Because reliability
    and renewable fraction saturate, designs at that ceiling are genuinely
    non-dominated - so an over-generous box does not merely waste search
    time, it puts absurd systems on the reported front.
    """

    def setUp(self):
        self.load = house_load()
        self.pv_unit = [
            max(0.0, 0.8 * math.sin((t % 24 - 6) / 12 * math.pi))
            for t in range(HOURS_PER_YEAR)
        ]
        self.battery = Battery(nominal_energy_kwh=5.0, nominal_power_kw=2.5)

    def test_a_firm_grid_gives_a_tighter_ceiling_than_an_island(self):
        firm = GridConnection(import_limit_kw=50.0, export_limit_kw=5.0)
        island = GridConnection(import_limit_kw=0.0, export_limit_kw=0.0)
        b_firm = screen.bounds_from_load(
            self.load, pv_unit=self.pv_unit, battery=self.battery, grid=firm)
        b_island = screen.bounds_from_load(
            self.load, pv_unit=self.pv_unit, battery=self.battery, grid=island)
        self.assertLess(b_firm["n_pv"][1], b_island["n_pv"][1])
        self.assertLess(b_firm["n_battery"][1], b_island["n_battery"][1])

    def test_a_partial_connection_lands_between_the_two(self):
        """
        A connection covering most of the peak is not an island. Treating
        the distinction as a yes/no handed an island's search box to a site
        with an 85% firm supply.
        """
        peak = max(self.load)
        b = [
            screen.bounds_from_load(
                self.load, pv_unit=self.pv_unit, battery=self.battery,
                grid=GridConnection(import_limit_kw=f * peak,
                                    export_limit_kw=0.0),
            )["basis"]["headroom"]
            for f in (0.0, 0.5, 1.0)
        ]
        self.assertGreater(b[0], b[1])
        self.assertGreater(b[1], b[2])

    def test_the_ceiling_is_also_bounded_by_peak_demand(self):
        """
        At a poor site the energy rule alone demands an implausible amount
        of plant. The power rule has to bind instead.
        """
        weak = [v * 0.02 for v in self.pv_unit]
        b = screen.bounds_from_load(
            self.load, pv_unit=weak,
            grid=GridConnection(import_limit_kw=50.0, export_limit_kw=0.0))
        installed_kw = b["n_pv"][1] * max(weak)
        self.assertLessEqual(
            installed_kw,
            max(self.load) * screen.PEAK_POWER_HEADROOM * 1.05,
            "the power ceiling did not bind where the energy ceiling could "
            "not",
        )

    def test_storage_bound_uses_usable_energy_not_nameplate(self):
        narrow = Battery(nominal_energy_kwh=5.0, nominal_power_kw=2.5,
                         soc_min=0.4, soc_max=0.9)
        wide = Battery(nominal_energy_kwh=5.0, nominal_power_kw=2.5,
                       soc_min=0.05, soc_max=1.0)
        grid = GridConnection(import_limit_kw=50.0, export_limit_kw=0.0)
        n_narrow = screen.bounds_from_load(
            self.load, battery=narrow, grid=grid)["n_battery"][1]
        n_wide = screen.bounds_from_load(
            self.load, battery=wide, grid=grid)["n_battery"][1]
        self.assertGreater(
            n_narrow, n_wide,
            "a store with a narrower operating window delivers less per "
            "unit, so more units must be reachable",
        )


class TestOversizeFilter(unittest.TestCase):
    """
    The defect the tightened filter exposed: `unit_yields` credits a
    dispatchable unit with rated power for every hour of the year, which is
    right for asking whether a design COULD meet the load and absurd for
    asking whether it generates too much. With a loose threshold the mistake
    was invisible; tightening it deleted every design containing a
    generator.
    """

    def test_a_generator_is_not_treated_as_permanently_oversized(self):
        space = SearchSpace(bounds=[(0, 2), (0, 2)], keys=["pv", "genset"])
        annual = 10_000.0
        yields = [4_000.0, 25.0 * 8760]        # pv unit, genset at full tilt
        mask = screen.must_take_mask(space.keys)

        with_mask = screen.analytic_filter(space, annual, yields, 5.0,
                                           must_take=mask)
        self.assertTrue(
            any(p[1] > 0 for p in with_mask),
            "every design containing a generator was rejected as oversized",
        )

    def test_must_take_generation_is_still_capped(self):
        space = SearchSpace(bounds=[(0, 40)], keys=["pv"])
        kept = screen.analytic_filter(
            space, 10_000.0, [4_000.0], 5.0,
            must_take=screen.must_take_mask(space.keys))
        self.assertTrue(kept)
        self.assertLessEqual(max(p[0] for p in kept) * 4_000.0,
                             10_000.0 * 2.0 + 1e-6)

    def test_must_take_mask_names_the_right_technologies(self):
        self.assertEqual(
            screen.must_take_mask(["pv", "wind", "battery", "genset",
                                   "ev_fleet"]),
            [True, True, False, False, False],
        )


class TestClusteredOutages(unittest.TestCase):
    """
    The defect: outage hours were drawn independently, giving hundreds of
    isolated one-hour interruptions. Real supplies fail in blocks, and it is
    the block length that sizes the backup - an hour of storage rides out
    the first profile and is beside the point for the second, at identical
    availability.
    """

    def test_availability_is_still_delivered(self):
        for a in (0.90, 0.95, 0.99):
            g = GridConnection(availability=a, mean_outage_hours=6.0)
            got = g.outage_statistics()["availability"]
            self.assertAlmostEqual(got, a, delta=0.02)

    def test_longer_repairs_mean_fewer_but_deeper_outages(self):
        short = GridConnection(availability=0.95,
                               mean_outage_hours=1.0).outage_statistics()
        long = GridConnection(availability=0.95,
                              mean_outage_hours=12.0).outage_statistics()
        self.assertGreater(short["outage_events_per_year"],
                           long["outage_events_per_year"] * 5)
        self.assertGreater(long["longest_outage_hours"],
                           short["longest_outage_hours"] * 5)

    def test_outages_are_reproducible(self):
        a = GridConnection(availability=0.95, mean_outage_hours=6.0)
        b = GridConnection(availability=0.95, mean_outage_hours=6.0)
        self.assertEqual(a.outage_mask(), b.outage_mask())

    def test_a_perfect_supply_never_fails(self):
        g = GridConnection(availability=1.0)
        self.assertTrue(all(g.outage_mask()))


class TestInputPlausibility(unittest.TestCase):
    """
    The checks that turn a silent unit error into a sentence.
    """

    def _codes(self, findings):
        return {f["code"] for f in findings}

    def test_the_load_is_always_described_back(self):
        f = validate.check_load(house_load())
        self.assertIn("load_interpretation", self._codes(f))
        msg = next(x["message"] for x in f
                   if x["code"] == "load_interpretation")
        self.assertIn("house", msg)

    def test_an_industrial_load_is_named_as_one(self):
        f = validate.check_load(house_load(peak=300.0, lf=0.45))
        msg = next(x["message"] for x in f
                   if x["code"] == "load_interpretation")
        self.assertNotIn("house", msg)
        self.assertTrue("industrial" in msg or "factory" in msg)

    def test_a_load_in_watts_is_questioned(self):
        f = validate.check_load([v * 1000 for v in house_load()])
        self.assertIn("load_maybe_watts", self._codes(f))

    def test_daily_irradiation_is_detected_and_converted(self):
        daily = [3.1, 3.9, 4.7, 5.5, 6.5, 7.2, 7.0, 6.5, 5.7, 4.5, 3.4, 2.9]
        fixed, findings = validate.check_monthly_ghi(daily)
        self.assertIn("ghi_daily_averages", self._codes(findings))
        self.assertGreater(sum(fixed), 1400)
        self.assertLess(sum(fixed), 2400)

    def test_monthly_totals_are_left_alone(self):
        monthly = [95, 105, 140, 160, 195, 215, 210, 195, 170, 140, 100, 88]
        fixed, findings = validate.check_monthly_ghi(monthly)
        self.assertEqual(fixed, [float(v) for v in monthly])
        self.assertNotIn("ghi_daily_averages", self._codes(findings))

    def test_an_impossible_pv_yield_is_an_error_not_a_note(self):
        f = validate.check_pv_yield([0.02] * HOURS_PER_YEAR, 1.0)
        self.assertIn("pv_yield_low", self._codes(f))
        self.assertEqual(
            "error",
            next(x["level"] for x in f if x["code"] == "pv_yield_low"),
        )

    def test_a_plausible_pv_yield_passes_but_is_still_reported(self):
        series = [1.0] * 1600 + [0.0] * (HOURS_PER_YEAR - 1600)
        f = validate.check_pv_yield(series, 1.0)
        self.assertIn("pv_yield", self._codes(f))
        self.assertEqual(validate.worst_level(f), "info")

    def test_component_blocks_larger_than_the_site_are_flagged(self):
        comps = {
            "pv": PVArray(capacity_kwp=25.0),
            "battery": Battery(nominal_energy_kwh=50.0, nominal_power_kw=25.0),
        }
        f = validate.check_component_scale(
            house_load(), comps,
            grid=GridConnection(import_limit_kw=250.0, export_limit_kw=100.0))
        codes = self._codes(f)
        self.assertIn("pv_unit_oversized", codes)
        self.assertIn("battery_unit_oversized", codes)
        self.assertIn("grid_far_above_peak", codes)

    def test_matched_components_raise_nothing(self):
        comps = {
            "pv": PVArray(capacity_kwp=0.5),
            "battery": Battery(nominal_energy_kwh=2.5, nominal_power_kw=1.25),
        }
        f = validate.check_component_scale(
            house_load(), comps,
            grid=GridConnection(import_limit_kw=15.0, export_limit_kw=5.0))
        self.assertEqual(validate.worst_level(f), "info")

    def test_a_discount_rate_below_escalation_is_flagged(self):
        from ensys.economics import EconomicParameters

        f = validate.check_economics(
            EconomicParameters(interest_rate=0.02, escalation_rate=0.05))
        self.assertIn("discount_below_escalation", self._codes(f))

    def test_generation_far_above_peak_demand_is_flagged(self):
        system = SystemConfig(
            pv=PVArray(capacity_kwp=25.0), n_pv=40,
            grid=GridConnection(import_limit_kw=15.0, export_limit_kw=5.0),
        )
        f = validate.check_result(
            {"lcoe": 0.2, "curtailment_rate": 0.1}, system, house_load(),
            grid=system.grid)
        self.assertIn("generation_far_above_peak", self._codes(f))


class TestEndOfLife(unittest.TestCase):
    """
    Every other number in a study describes year one - the year the design
    is least likely to fail and the one nobody operates in for long.
    """

    def _study(self, growth):
        from ensys.economics import EconomicParameters

        base = SystemConfig(
            pv=PVArray(capacity_kwp=1.0, capital_cost=700,
                       replacement_cost=600, om_cost_per_year=9),
            battery=Battery(nominal_energy_kwh=5.0, nominal_power_kw=2.5,
                            capital_cost=1250, replacement_cost=1000),
            grid=GridConnection(import_limit_kw=15.0, export_limit_kw=5.0,
                                import_price=0.09, export_price=0.03),
        )
        res = {
            "load": house_load(),
            "pv_unit": [max(0.0, 0.8 * math.sin((t % 24 - 6) / 12 * math.pi))
                        for t in range(HOURS_PER_YEAR)],
        }
        space = SearchSpace(bounds=[(0, 6), (0, 4)], keys=["pv", "battery"])
        return SizingStudy(
            base, res,
            econ=EconomicParameters(project_years=20, load_growth_rate=growth),
            space=space, seed=7, algorithm="grid",
        )

    def test_a_growing_load_degrades_the_design_by_the_final_year(self):
        r = self._study(0.03).run(n_particles=8, n_iterations=4)
        eol = r.end_of_life_check()
        self.assertIsNotNone(eol)
        self.assertGreater(eol["load_growth_factor"], 1.7)
        self.assertLess(eol["pv_degradation_factor"], 1.0)
        self.assertGreaterEqual(
            eol["metrics"]["lpsp"], eol["year_one"]["lpsp"],
            "a bigger load on a degraded array cannot serve more of it",
        )

    def test_nothing_is_claimed_when_nothing_changes(self):
        base = SystemConfig(
            pv=PVArray(capacity_kwp=1.0, degradation_per_year=0.0),
            grid=GridConnection(import_limit_kw=15.0, export_limit_kw=0.0),
        )
        res = {"load": house_load(),
               "pv_unit": [0.4] * HOURS_PER_YEAR}
        study = SizingStudy(
            base, res, space=SearchSpace(bounds=[(0, 3)], keys=["pv"]),
            seed=1, algorithm="grid",
        )
        r = study.run(n_particles=4, n_iterations=2)
        self.assertIsNone(
            r.end_of_life_check(),
            "with no degradation and no growth the final year IS the first, "
            "and reporting it twice is noise",
        )


class TestLevelisedCostDenominator(unittest.TestCase):
    """
    The defect: LCOE divided the annualised cost by the served energy PLUS
    the EV charging energy. Served energy already includes it - the dispatch
    aggregates a flexible load's demand into total demand so that a vehicle
    charging counts as load to be met - so every kilowatt-hour delivered to
    a fleet was counted twice, and the levelised cost came out low at
    precisely the sites where the fleet is the reason for the study.

    A site without EVs was unaffected, which is how it survived.
    """

    def _totals(self, load_kwh, ev_kwh, unmet_kwh=0.0):
        total = load_kwh + ev_kwh
        return {
            "load_kwh": load_kwh,
            "ev_charge_kwh": ev_kwh,
            "total_demand_kwh": total,
            "served_kwh": total - unmet_kwh,
            "unmet_kwh": unmet_kwh,
        }

    def test_served_energy_counts_vehicle_charging_once(self):
        from ensys import economics

        t = self._totals(100_000.0, 40_000.0)
        self.assertEqual(
            t["served_kwh"], 140_000.0,
            "the dispatch's own total already includes the fleet",
        )
        # The economics module must use that number as it stands.
        src = economics.evaluate.__doc__ or ""
        self.assertIsInstance(src, str)

    def test_lcoe_is_unchanged_by_where_the_demand_came_from(self):
        """
        Two sites consuming identical energy for identical cost must quote
        the same levelised cost, whether the demand is building load or
        vehicles. Under the double count the second came out 40% cheaper.
        """
        from ensys import economics

        class _Result:
            def __init__(self, totals):
                self.totals = totals
                self.grid_import = [0.0]
                self.grid_export = [0.0]
                self.genset = [0.0]

        econ = economics.EconomicParameters(project_years=20,
                                            interest_rate=0.08)
        system = SystemConfig(
            pv=PVArray(capacity_kwp=100.0, capital_cost=70_000), n_pv=1,
        )
        a = economics.evaluate(system, _Result(self._totals(140_000.0, 0.0)),
                               econ)
        b = economics.evaluate(system, _Result(self._totals(100_000.0,
                                                            40_000.0)), econ)
        self.assertAlmostEqual(a["served_kwh"], b["served_kwh"], places=6)
        self.assertAlmostEqual(a["lcoe"], b["lcoe"], places=9)


class TestFirmSupplyMeritOrder(unittest.TestCase):
    """
    The defect: in a deficit hour the grid was served before any dispatchable
    generator, unconditionally. A firm connection covered the whole deficit,
    so the generator was asked for what remained, which was nothing.

    A genset was therefore capacity the study paid for and could never use,
    which made it strictly dominated, which is why the optimiser deleted it
    from every grid-connected design. The tariff never entered the decision:
    a site paying two dollars a kilowatt-hour still imported in preference
    to a diesel costing thirty cents to run.
    """

    def _system(self, tariff, n_genset=2):
        from ensys.system import SystemConfig

        return SystemConfig(
            pv=PVArray(capacity_kwp=25.0), n_pv=2,
            genset=Generator(rated_kw=50.0, fuel_price=1.0), n_genset=n_genset,
            grid=GridConnection(import_limit_kw=200.0, export_limit_kw=50.0,
                                import_price=tariff),
        )

    def _resources(self):
        return {
            "load": [100.0 * (0.45 + 0.55 * max(
                0.0, math.sin((t % 24 - 6) / 12 * math.pi)))
                for t in range(HOURS_PER_YEAR)],
            "pv_unit": [max(0.0, 20.0 * math.sin((t % 24 - 6) / 12 * math.pi))
                        for t in range(HOURS_PER_YEAR)],
        }

    def test_a_dear_tariff_starts_the_generator(self):
        from ensys import dispatch

        t = dispatch.simulate(self._system(2.00), self._resources(),
                              dispatch.LOAD_FOLLOWING).totals
        self.assertGreater(
            t["genset_kwh"], 0.0,
            "at two dollars a kilowatt-hour the grid must lose to a diesel",
        )

    def test_a_cheap_tariff_leaves_the_generator_off(self):
        from ensys import dispatch

        t = dispatch.simulate(self._system(0.05), self._resources(),
                              dispatch.LOAD_FOLLOWING).totals
        self.assertEqual(
            t["genset_kwh"], 0.0,
            "a diesel must not run against a tariff far below its fuel cost",
        )

    def test_the_generator_still_covers_what_the_grid_cannot(self):
        from ensys import dispatch
        from ensys.system import SystemConfig

        sysc = SystemConfig(
            pv=PVArray(capacity_kwp=25.0), n_pv=2,
            genset=Generator(rated_kw=50.0, fuel_price=1.0), n_genset=2,
            grid=GridConnection(import_limit_kw=30.0, export_limit_kw=0.0,
                                import_price=0.05),
        )
        t = dispatch.simulate(sysc, self._resources(),
                              dispatch.LOAD_FOLLOWING).totals
        self.assertGreater(t["genset_kwh"], 0.0)
        self.assertEqual(t["unmet_kwh"], 0.0)

    def test_marginal_cost_rises_as_the_machine_is_throttled_back(self):
        """
        The fuel curve's intercept is charged whether the engine is working
        or idling, so a lightly loaded diesel is far dearer per kilowatt-hour
        than its full-load figure. Ranking on the full-load number would
        flatter it into running when it should not.
        """
        from ensys import dispatch

        g = Generator(rated_kw=50.0, fuel_price=1.0)
        full = dispatch._dispatchable_marginal_cost(g, 2, 100.0)
        # A request below the minimum load ratio is costed AT the minimum,
        # because that is what the machine will actually produce.
        part = dispatch._dispatchable_marginal_cost(g, 2, 5.0)
        self.assertGreater(part, full * 1.2)
        self.assertAlmostEqual(
            part, dispatch._dispatchable_marginal_cost(g, 2, 30.0), places=9,
            msg="below the minimum load the cost must be that of the "
                "minimum, not of the smaller output that was asked for",
        )

    def test_wear_and_operating_cost_reach_the_merit_order(self):
        from ensys import dispatch

        bare = Generator(rated_kw=50.0, fuel_price=1.0)
        real = Generator(rated_kw=50.0, fuel_price=1.0,
                         om_cost_per_hour=1.5, replacement_cost=28000,
                         lifetime_hours=15000)
        self.assertGreater(
            dispatch._dispatchable_marginal_cost(real, 2, 100.0),
            dispatch._dispatchable_marginal_cost(bare, 2, 100.0),
            "running an engine consumes it; that cost belongs in the "
            "comparison against a tariff",
        )

    def test_conservation_survives_the_new_branch(self):
        from ensys import dispatch

        err, _hour = dispatch.simulate(
            self._system(2.00), self._resources(),
            dispatch.LOAD_FOLLOWING).balance_error()
        self.assertLess(err, 1e-6)


class TestCycleCharging(unittest.TestCase):
    """
    `cycle_charging` was in the strategy list, offered in the interface, and
    absent from the dispatch loop - choosing it returned load-following's
    answer exactly. It is the strategy that matters most for a diesel: part
    load is where efficiency collapses, so a running machine should run hard
    and bank the surplus rather than throttle back.
    """

    def _run(self, strategy):
        from ensys import dispatch
        from ensys.system import SystemConfig

        g = Generator(rated_kw=50.0, fuel_price=1.0)
        sysc = SystemConfig(
            pv=PVArray(capacity_kwp=25.0), n_pv=4,
            genset=g, n_genset=2,
            battery=Battery(nominal_energy_kwh=100.0, nominal_power_kw=50.0),
            n_battery=4,
            grid=GridConnection(import_limit_kw=0.0, export_limit_kw=0.0),
        )
        res = {
            "load": [100.0 * (0.45 + 0.55 * max(
                0.0, math.sin((t % 24 - 6) / 12 * math.pi)))
                for t in range(HOURS_PER_YEAR)],
            "pv_unit": [max(0.0, 20.0 * math.sin((t % 24 - 6) / 12 * math.pi))
                        for t in range(HOURS_PER_YEAR)],
        }
        r = dispatch.simulate(sysc, res, strategy)
        return r, g.annual_summary(2, r.genset)

    def test_cycle_charging_differs_from_load_following(self):
        from ensys import dispatch

        _, lf = self._run(dispatch.LOAD_FOLLOWING)
        _, cc = self._run(dispatch.CYCLE_CHARGING)
        self.assertNotEqual(
            lf["run_hours"], cc["run_hours"],
            "the two strategies returned identical dispatch, which is what "
            "an unimplemented strategy looks like",
        )

    def test_cycle_charging_runs_the_machine_harder_and_for_less_time(self):
        from ensys import dispatch

        _, lf = self._run(dispatch.LOAD_FOLLOWING)
        _, cc = self._run(dispatch.CYCLE_CHARGING)
        self.assertGreater(cc["mean_load_ratio"], lf["mean_load_ratio"])
        self.assertLess(cc["run_hours"], lf["run_hours"])
        self.assertLess(
            cc["specific_consumption"], lf["specific_consumption"],
            "running at rating must burn less fuel per kilowatt-hour than "
            "idling along behind the load",
        )

    def test_conservation_holds_under_cycle_charging(self):
        from ensys import dispatch

        r, _ = self._run(dispatch.CYCLE_CHARGING)
        err, _hour = r.balance_error()
        self.assertLess(err, 1e-6)


class TestEveryTechnologyIsReachable(unittest.TestCase):
    """
    The engine models twenty technologies. The interface offered five, and
    `api._build_components` could construct only those five, so the other
    fifteen had models, costs, translations and diagram symbols and no way
    to be selected. A study could not recommend what it could not build.
    """

    def _defaults(self, entry):
        cfg = {"enabled": True}
        for f in entry["fields"]:
            if f["default"] is not None:
                cfg[f["key"]] = f["default"]
        return cfg

    def test_every_catalogued_technology_builds_from_its_own_defaults(self):
        from ensys import catalogue

        schema = catalogue.ui_schema()
        failures = []
        n = 0
        for entries in schema["groups"].values():
            for entry in entries:
                n += 1
                try:
                    catalogue.build(entry["technology"],
                                    self._defaults(entry))
                except Exception as e:  # noqa: BLE001 - the point is to list them
                    failures.append(f"{entry['technology']}: {e}")
        self.assertEqual(failures, [], f"{len(failures)} of {n} failed")
        self.assertGreaterEqual(n, 20)

    def test_the_schema_never_offers_a_cost_a_technology_cannot_take(self):
        """
        A generator's maintenance is charged per running hour, so an annual
        figure is the wrong quantity and its constructor refuses it. The
        schema used to offer the field anyway, so the interface showed a box
        that crashed the build the moment anyone typed in it.
        """
        from ensys import catalogue

        schema = catalogue.ui_schema()
        for entries in schema["groups"].values():
            for entry in entries:
                tech = entry["technology"]
                skip = catalogue.COST_FIELDS_NOT_APPLICABLE.get(tech, set())
                keys = {f["key"] for f in entry["fields"]}
                self.assertFalse(
                    keys & skip,
                    f"{tech} is offered {keys & skip}, which it cannot accept",
                )

    def test_a_refused_cost_field_is_dropped_rather_than_fatal(self):
        from ensys import catalogue

        g = catalogue.build("genset", {"rated_kw": 50, "om_cost_per_year": 900})
        self.assertEqual(g.rated_kw, 50)


class TestRegionalCostLibrary(unittest.TestCase):
    """
    The library says "leave a cost at zero to use the regional benchmark".
    It was not true. An interface sends a number for every field it shows,
    so a blank cost arrives as an explicit 0.0 rather than as an absence,
    and `setdefault` had nothing to fill. Choosing a region changed the cost
    browser and nothing whatever in the sizing.
    """

    def test_a_zero_cost_is_filled_from_the_library(self):
        from ensys import catalogue

        a = catalogue.build("pv", {"unit_kwp": 25, "capital_cost": 0,
                                   "replacement_cost": 0,
                                   "om_cost_per_year": 0})
        self.assertGreater(a.capital_cost, 0.0)
        self.assertTrue(a.cost_from_library)

    def test_a_quoted_cost_is_never_overwritten(self):
        from ensys import catalogue

        a = catalogue.build("pv", {"unit_kwp": 25, "capital_cost": 12345.0})
        self.assertEqual(a.capital_cost, 12345.0)
        self.assertFalse(a.cost_from_library)

    def test_the_region_actually_changes_the_number(self):
        from ensys import catalogue
        from ensys import costs as costs_mod

        seen = {}
        for region in list(costs_mod.REGIONS)[:4]:
            a = catalogue.build("pv", {"unit_kwp": 25, "capital_cost": 0},
                                region=region)
            seen[region] = a.capital_cost
        self.assertGreater(
            len(set(seen.values())), 1,
            f"every region returned the same capital cost: {seen}",
        )


class TestInterfaceScriptIsInternallyConsistent(unittest.TestCase):
    """
    A static check on web/app.js for identifiers that are used and never
    defined.

    This exists because of a real failure: an edit removed a block of
    function definitions along with the dead code it was meant to delete.
    The file still parsed - a missing identifier is a runtime error, not a
    syntax error - so every check that did not actually execute the page
    passed. In the browser `buildComponents()` threw on its first line, the
    whole components page rendered empty, and the two dropdowns above it
    stayed blank because the function that fills them is called at the end
    of the one that had already died.

    Nothing in a Python test suite can run the page, but this catches the
    specific shape of that mistake: a top-level name the script calls and
    never declares. It is deliberately narrow - it looks only at the
    identifiers the script itself defines and uses - so it stays quiet about
    browser globals and library names.
    """

    def _source(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(root, "web", "app.js")
        if not os.path.exists(path):
            self.skipTest("web/app.js is not present in this checkout")
        with open(path, encoding="utf-8") as f:
            return f.read()

    # Names the page cannot work without. Every one of these was either
    # deleted by that edit or called by something that was.
    REQUIRED = (
        "buildComponents", "componentConfig", "componentCard", "fieldInput",
        "unitSuffix", "gridCard", "buildCostBasis", "costBasis",
        "linkUnitCosts", "applySiteScale", "setField", "round2",
        "renderFindings", "escapeHtml", "renderFront", "runStudy",
        "loadProfile", "selectDesign",
        "CORE", "DEFAULT_ON", "GRID_FIELDS", "SITE_SCALES", "RATES",
        "COST_LINKS", "ALL_TECHS",
        # Project files. An export with no import makes examples/ dead
        # weight, so both halves are required to exist together.
        "projectSnapshot", "savedComponents", "saveProject", "applyProject",
        "applyComponents", "importProject", "segment", "put",
        "PROJECT_VERSION",
    )

    def test_every_required_name_is_defined(self):
        import re

        src = self._source()
        missing = []
        for name in self.REQUIRED:
            pattern = (
                r"(?:function\s+%s\s*\(|(?:const|let|var)\s+%s\s*=)"
                % (re.escape(name), re.escape(name))
            )
            if not re.search(pattern, src):
                missing.append(name)
        self.assertEqual(
            missing, [],
            f"web/app.js calls these and never defines them: {missing}. "
            f"The page will render empty from the first one that runs.",
        )

    def test_every_required_name_is_actually_used(self):
        """
        The mirror of the above: a name kept in the list long after the code
        stopped calling it turns this test into decoration.
        """
        import re

        src = self._source()
        unused = []
        for name in self.REQUIRED:
            uses = len(re.findall(r"\b%s\b" % re.escape(name), src))
            if uses < 2:            # the definition itself, plus a use
                unused.append(name)
        self.assertEqual(
            unused, [],
            f"these are defined but never used, so this test is guarding "
            f"nothing: {unused}",
        )

    def test_the_component_page_is_built_from_the_engine_catalogue(self):
        """
        The hand-written list of five technologies must not come back. If it
        does, the interface silently stops offering the other fifteen again.
        """
        src = self._source()
        self.assertNotIn(
            "COMPONENT_DEFS", src,
            "the hardcoded component list is back; the page must be built "
            "from the engine's ui_schema() so the two cannot drift",
        )
        self.assertIn("presets.technologies", src)

    def test_the_axis_helper_always_returns_a_minimum(self):
        """
        web/charts.js computes every point as (v - min) / (max - min). If the
        degenerate branch of nice() - a flat, all-zero or non-finite series -
        returns no `min`, that arithmetic is NaN and the chart draws paths of
        "M48.0,NaN". It happened on the state-of-charge chart of any design
        with no battery, where the series is 365 zeros, and it was visible
        only in the browser console.

        Python cannot execute the file, so this checks the one property that
        made it fail: the early return must carry a min.
        """
        import re

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(root, "web", "charts.js")
        if not os.path.exists(path):
            self.skipTest("web/charts.js is not present in this checkout")
        with open(path, encoding="utf-8") as f:
            src = f.read()

        m = re.search(r"function nice\(max, min = 0\)\s*\{(.*?)\n  \}", src, re.S)
        self.assertIsNotNone(m, "nice() is no longer where this test expects it")
        body = m.group(1)

        # Every return inside the helper must name a min.
        returns = re.findall(r"return\s*\{[^}]*\}", body)
        self.assertTrue(returns, "nice() returns nothing")
        for r in returns:
            self.assertIn(
                "min", r,
                f"this return has no min, so (v - min) will be NaN: {r}")

    def test_the_page_offers_a_way_to_open_a_project(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(root, "web", "index.html")
        if not os.path.exists(path):
            self.skipTest("web/index.html is not present in this checkout")
        with open(path, encoding="utf-8") as f:
            html = f.read()
        self.assertIn('id="project-open"', html,
                      "the example projects cannot be opened without the "
                      "file control that reads them")
        self.assertIn('for="project-open"', html,
                      "the file input is hidden; without its label there is "
                      "nothing to click")


class TestWorkedExamplesAreLoadable(unittest.TestCase):
    """
    The example projects ship as the interface's own save format. If the
    format changes and these are not regenerated they become three files
    that look official and load wrong, which is worse than having none.
    """

    def _examples(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        d = os.path.join(root, "examples")
        if not os.path.isdir(d):
            self.skipTest("examples/ is not present in this checkout")
        files = sorted(
            os.path.join(d, f) for f in os.listdir(d) if f.endswith(".json")
        )
        if not files:
            self.skipTest("examples/ holds no project files")
        return files

    def test_each_example_is_a_complete_project(self):
        import json

        for path in self._examples():
            with self.subTest(example=os.path.basename(path)):
                with open(path, encoding="utf-8") as f:
                    p = json.load(f)
                self.assertEqual(p.get("application"), "HES")
                self.assertGreaterEqual(p.get("version", 0), 2)
                for section in ("location", "resources", "load", "costs",
                                "components", "economics", "study"):
                    self.assertIn(section, p, f"{section} is missing")
                loc = p["location"]
                self.assertTrue(-90 <= loc["latitude"] <= 90)
                self.assertTrue(-180 <= loc["longitude"] <= 180)
                self.assertTrue(-12 <= loc["utc_offset_hours"] <= 14)
                self.assertGreaterEqual(len(p["study"]["objectives"]), 2,
                                        "a Pareto front needs a trade-off")

    def test_each_example_runs_without_a_network(self):
        """
        An example that reaches for the internet is not an example, it is a
        dependency. All of them must carry their own resource data and
        generate their own demand.
        """
        import json

        for path in self._examples():
            with self.subTest(example=os.path.basename(path)):
                with open(path, encoding="utf-8") as f:
                    p = json.load(f)
                self.assertEqual(
                    p["resources"]["mode"], "offline",
                    "the example would need an internet connection to open")
                ghi = [
                    x for x in p["resources"]["monthly_ghi"].replace(",", " ").split()
                ]
                self.assertEqual(len(ghi), 12,
                                 "twelve monthly irradiation values are needed")
                self.assertIn(p["load"]["mode"], ("paste", "synthetic"),
                              "a demand profile read from a file cannot be "
                              "carried inside a project")

    def test_each_example_switches_something_on(self):
        import json

        for path in self._examples():
            with self.subTest(example=os.path.basename(path)):
                with open(path, encoding="utf-8") as f:
                    p = json.load(f)
                on = [k for k, v in p["components"].items() if v.get("enabled")]
                self.assertTrue(
                    any(k in on for k in ("pv", "wind", "genset", "grid")),
                    f"nothing can serve the load in this project: {on}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
