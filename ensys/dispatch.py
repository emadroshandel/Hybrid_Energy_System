"""
Hourly energy management / dispatch engine.

One loop serves every technology. It knows about four behaviours —
non-dispatchable generation, dispatchable generation, storage and flexible
load — and nothing about which specific technology is present. Adding
hydrogen or a flywheel or a bus depot does not add a branch here, which is
what keeps the conservation guarantee intact as the library grows.

Each hour is resolved once, as a single energy balance with an explicit
priority order:

    surplus hour
      1. flexible loads take what they MUST take (departure obligations)
      2. flexible loads take what they WANT to take (soak up surplus)
      3. storage charges, in merit order
      4. export, up to the connection limit
      5. curtail the remainder

    deficit hour
      1. storage discharges, in merit order (cheap fast stores first)
      2. flexible loads discharge if bidirectional (V2G/V2H)
      3. FIRM SUPPLY, in least-cost order: grid import and every dispatchable
         generator ranked together by what the next kilowatt-hour costs
      4. flexible loads still take what they MUST, even at a bad price
      5. whatever remains is unserved load

Step 4 is the one that looks wrong and is not: an EV that must leave at 80%
is a service obligation, and buying that energy at the worst hour of the day
is the correct outcome, not a bug. A model that lets the fleet miss its
departure to save money is optimising the wrong thing.

Step 3 used to be two steps - import first, then generators with whatever was
left. Nothing was left. A firm connection covered every deficit before a
generator was ever asked, so a genset produced energy only when the grid
physically could not: during an outage, or above the import limit. At every
other hour it was capacity the study had paid for and could not use, which
made it strictly dominated, which is why the optimiser removed it from every
grid-connected design. The tariff never entered the decision, so a site
paying two dollars a kilowatt-hour still imported in preference to a diesel
costing thirty cents to run.

Ordering them together by marginal cost is what makes "should I install a
generator" a question the study can answer. It also makes the answer
sensitive to the fuel price and the tariff, which is where the answer
actually lives.

Every asset is assigned exactly one role per hour. The original MATLAB
assigned battery charge and discharge in the same branch, double-counting
the efficiency loss; the structure here makes that impossible to express.
"""

from __future__ import annotations

from .assets import Dispatchable, FlexibleLoad, NonDispatchable, Storage
from .timeseries import HOURS_PER_YEAR, month_index

LOAD_FOLLOWING = "load_following"
CYCLE_CHARGING = "cycle_charging"
TARIFF_ARBITRAGE = "tariff_arbitrage"
PEAK_SHAVING = "peak_shaving"

STRATEGIES = (LOAD_FOLLOWING, CYCLE_CHARGING, TARIFF_ARBITRAGE, PEAK_SHAVING)

# Where grid import sits when nothing has a price. The assets module has
# always carried this table; the dispatch simply never consulted it.
from .assets import DEFAULT_MERIT as _MERIT

GRID_MERIT = _MERIT["grid_import"]


def _dispatchable_marginal_cost(asset, n_units, output_kw, t=None, state=None):
    """
    What the next kilowatt-hour from this machine costs to produce.

    Three components, all of them real money and all of them already on the
    asset:

      fuel        the affine fuel curve at this operating point, which is
                  why the figure is evaluated at the output actually wanted
                  rather than at rating - the curve's intercept is charged
                  whether the machine is working or idling, so a lightly
                  loaded diesel is far dearer per kilowatt-hour than its
                  full-load figure suggests
      operating   `om_cost_per_hour`, spread over the hour's output
      wear        replacement cost divided by rated life in hours, which is
                  the part operators feel and spreadsheets forget: running
                  an engine consumes it, and a machine dispatched purely on
                  fuel price will always look cheaper than it is

    Returned per kWh, so it is directly comparable with an import tariff.
    Assets with no cost model return 0 and keep their static merit position.
    """
    if n_units <= 0 or output_kw <= 0:
        return 0.0

    hi = asset.available_kw(n_units, t, state)
    lo = min(asset.min_output_kw(n_units, t, state), hi)
    # The machine cannot produce less than its minimum, so a small deficit
    # is served at the minimum and costed there.
    actual = min(max(output_kw, lo), hi)
    if actual <= 0:
        return 0.0

    cost = 0.0
    try:
        cost += float(asset.marginal_cost(n_units, actual) or 0.0)
    except (TypeError, ValueError):
        pass

    om_h = float(getattr(asset, "om_cost_per_hour", 0.0) or 0.0)
    if om_h:
        cost += om_h * n_units / actual

    life_h = float(getattr(asset, "lifetime_hours", 0.0) or 0.0)
    repl = float(getattr(asset, "replacement_cost", 0.0) or 0.0)
    if life_h > 0 and repl > 0:
        cost += repl * n_units / life_h / actual

    return cost


class DispatchResult:
    """
    Hourly energy flows plus the annual aggregates.

    Per-asset series live in `assets`, keyed the same way as the registry,
    so a report can itemise any technology without the result object needing
    a named field for each one.
    """

    def __init__(self):
        self.load = []
        self.grid_import = []
        self.grid_export = []
        self.curtailed = []
        self.unmet = []
        self.assets = {}          # key -> {"output"/"charge"/"discharge"/"soc"}
        self.totals = {}
        self.config = {}
        self.notes = []
        self.heat_served = []
        self.heat_unmet = []

    # ---------------------------------------------------------- accessors

    def series(self, key, field="output"):
        rec = self.assets.get(key)
        if not rec:
            return []
        return rec.get(field, [])

    def by_technology(self, technology, field="output"):
        """Summed series across every asset of one technology."""
        out = None
        for key, rec in self.assets.items():
            if rec.get("technology") != technology:
                continue
            s = rec.get(field) or []
            if out is None:
                out = list(s)
            else:
                out = [a + b for a, b in zip(out, s)]
        return out or [0.0] * len(self.load)

    @property
    def renewable(self):
        out = [0.0] * len(self.load)
        for key, rec in self.assets.items():
            if rec.get("class") == "non_dispatchable":
                out = [a + b for a, b in zip(out, rec.get("output", []))]
        return out

    # Legacy accessors, so existing reports and tests keep working.
    @property
    def pv(self):
        return self.by_technology("pv")

    @property
    def wind(self):
        return self.by_technology("wind")

    @property
    def genset(self):
        return self.by_technology("genset")

    @property
    def battery_charge(self):
        return self.by_technology("battery", "charge")

    @property
    def battery_discharge(self):
        return self.by_technology("battery", "discharge")

    @property
    def battery_soc(self):
        for key, rec in self.assets.items():
            if rec.get("technology") == "battery":
                return rec.get("soc", [])
        return [0.0] * len(self.load)

    @property
    def ev_charge(self):
        return self.by_technology("ev_fleet", "charge")

    @property
    def ev_discharge(self):
        return self.by_technology("ev_fleet", "discharge")

    @property
    def ev_soc(self):
        for key, rec in self.assets.items():
            if rec.get("technology") == "ev_fleet":
                return rec.get("soc", [])
        return [0.0] * len(self.load)

    def balance_error(self):
        """
        Largest hourly violation of conservation of energy.

        supply + discharge + import + unmet
            == load + charge + export + curtailed

        Checked rather than assumed. A non-trivial value means the dispatch
        has a bug, and the test suite fails on it.
        """
        worst = 0.0
        worst_hour = None
        n = len(self.load)
        for t in range(n):
            supply = self.grid_import[t] + self.unmet[t]
            sink = self.load[t] + self.grid_export[t] + self.curtailed[t]
            for rec in self.assets.values():
                cls = rec.get("class")
                if cls in ("non_dispatchable", "dispatchable"):
                    supply += rec["output"][t]
                elif cls == "storage":
                    supply += rec["discharge"][t]
                    sink += rec["charge"][t]
                elif cls == "flexible_load":
                    supply += rec["discharge"][t]
                    sink += rec["charge"][t]
            err = abs(supply - sink)
            if err > worst:
                worst = err
                worst_hour = t
        return worst, worst_hour


def simulate(system, resources, strategy=LOAD_FOLLOWING, dt_h=1.0):
    """
    Run one year of hourly dispatch.

    `system` must expose `.registry` (an AssetRegistry), `.counts` (a dict of
    key -> unit count) and `.grid`.
    """
    registry = system.registry
    counts = system.counts
    grid = system.grid

    load = resources["load"]
    n = len(load)

    r = DispatchResult()
    r.config = system.summary()

    # ------------------------------------------------- pre-compute output
    nd = []
    for key, asset in registry.non_dispatchable():
        c = counts.get(key, 0)
        if c <= 0:
            continue
        # A caller may supply the per-unit series directly — the optimiser
        # does, because computing it once and reusing it across thousands of
        # evaluations is most of the speed. Only fall back to asking the
        # asset to generate it when nothing was provided.
        unit = (
            resources.get(f"unit::{key}")
            or resources.get(f"{key}_unit")
        )
        if unit is None:
            unit, _info = asset.generate(resources, system.location)
            resources[f"unit::{key}"] = unit
        nd.append((key, asset, c, unit))
        r.assets[key] = {
            "technology": asset.technology, "class": "non_dispatchable",
            "name": asset.name, "units": c,
            "output": [0.0] * n, "curtailed": [0.0] * n,
        }

    disp = []
    for key, asset in registry.dispatchable():
        c = counts.get(key, 0)
        if c <= 0:
            continue
        disp.append((key, asset, c))
        r.assets[key] = {
            "technology": asset.technology, "class": "dispatchable",
            "name": asset.name, "units": c, "output": [0.0] * n,
        }

    stores = []
    for key, asset in registry.storage():
        c = counts.get(key, 0)
        if c <= 0:
            continue
        # A heat store cannot serve an electrical deficit. Letting it would
        # be a unit error the arithmetic cannot catch.
        serves = getattr(asset, "serves", "power")
        stores.append([key, asset, c, asset.soc_initial, serves])
        r.assets[key] = {
            "technology": asset.technology, "class": "storage",
            "name": asset.name, "units": c,
            "charge": [0.0] * n, "discharge": [0.0] * n, "soc": [0.0] * n,
        }

    flex = []
    for key, asset in registry.flexible_loads():
        c = counts.get(key, 0)
        if c <= 0:
            continue
        prof = (
            resources.get(f"profile::{key}")
            or resources.get(f"{key}_profile")
            or (resources.get("ev_profile") if asset.technology == "ev_fleet"
                else None)
        )
        if prof is None:
            prof = asset.profile(n_points=c)
            resources[f"profile::{key}"] = prof
        prof = _normalise_profile(prof, asset, c, n)
        flex.append([key, asset, c, 0.0, prof, 0])
        r.assets[key] = {
            "technology": asset.technology, "class": "flexible_load",
            "name": asset.name, "units": c,
            "charge": [0.0] * n, "discharge": [0.0] * n, "soc": [0.0] * n,
        }

    # ---------------------------------------------------------- grid setup
    grid_up = grid.outage_mask() if grid else [False] * n
    imp_price = grid.import_price if grid else [0.0] * n
    imp_limit = grid.import_limit_kw if grid else 0.0
    exp_limit = grid.export_limit_kw if grid else 0.0

    # Shared state passed to assets that need context beyond their own SOC.
    state = {
        "temperature_c": resources.get("temperature_c"),
        "heat_demand_kw": resources.get("heat_demand_kw"),
    }
    for key, asset, c in disp:
        if asset.technology == "csp":
            dni = resources.get("dni") or []
            state["csp_thermal_kw"] = asset.field_thermal_series(dni, c)
            state["csp_store_kwh"] = 0.0

    arb_lo = arb_hi = None
    if strategy == TARIFF_ARBITRAGE and grid and stores:
        prices = sorted(imp_price)
        arb_lo = prices[int(0.25 * (len(prices) - 1))]
        arb_hi = prices[int(0.75 * (len(prices) - 1))]
        best_rte = max(s[1].round_trip_efficiency for s in stores)
        if arb_hi * best_rte <= arb_lo:
            arb_lo = arb_hi = None
            r.notes.append(
                "Tariff arbitrage disabled: the price spread between the "
                "cheapest and dearest quartiles does not cover the round-trip "
                "efficiency loss of the best available store."
            )

    peak_target = None
    if strategy == PEAK_SHAVING:
        ordered = sorted(load, reverse=True)
        peak_target = ordered[int(0.05 * (len(ordered) - 1))]

    # A flexible load that can respond to price has to be told what the
    # prices are. Deriving "cheap" and "dear" from the tariff the study is
    # running against is the only way the thresholds can be right; an
    # absolute number set in a form is a guess that ages badly.
    for rec in flex:
        asset = rec[1]
        if hasattr(asset, "set_price_context"):
            asset.set_price_context(
                imp_price if grid else None,
                grid.export_price if grid else None,
            )

    # ================================================================ loop
    for t in range(n):
        demand = load[t]
        grid_ok = grid_up[t] if grid else False
        imp_cap = imp_limit if grid_ok else 0.0
        exp_cap = exp_limit if grid_ok else 0.0
        price = imp_price[t] if grid else 0.0

        # ---- non-dispatchable generation
        gen = 0.0
        for key, asset, c, unit in nd:
            p = unit[t] * c
            r.assets[key]["output"][t] = p
            gen += p

        surplus = gen - demand
        imported = exported = curtailed = unmet = 0.0

        # ---- flexible loads: the energy they MUST take this hour
        must_total = 0.0
        must = []
        for rec in flex:
            key, asset, c, soc, prof, missed = rec
            need = asset.required_kw(c, soc, t, prof)
            must.append(need)
            must_total += need

        # =============================================== surplus hour
        if surplus >= 0.0:
            rem = surplus

            # 1 + 2. flexible loads: obligation first, then preference
            for i, rec in enumerate(flex):
                key, asset, c, soc, prof, missed = rec
                take = min(must[i], asset.max_charge_kw(c, soc, t, prof))
                want = asset.preferred_charge_kw(
                    c, soc, t, prof, price=price, surplus_kw=max(0.0, rem - take)
                )
                total = min(
                    asset.max_charge_kw(c, soc, t, prof), max(take, want)
                )
                from_surplus = min(total, rem)
                shortfall = total - from_surplus
                rem -= from_surplus
                # Obligation beyond the surplus must come from the grid.
                from_grid = 0.0
                if shortfall > 0 and take > from_surplus:
                    from_grid = min(shortfall, take - from_surplus, imp_cap - imported)
                    from_grid = max(0.0, from_grid)
                    imported += from_grid
                charge = from_surplus + from_grid
                r.assets[key]["charge"][t] = charge
                rec[3] = asset.step(c, soc, charge, 0.0, t, prof, dt_h)

            # 3. storage, in merit order
            for rec in stores:
                if rem <= 0:
                    break
                key, asset, c, soc, serves = rec
                if serves == "heat":
                    continue
                room = asset.max_charge_kw(c, soc, t)
                take = min(room, rem)
                if take > 0:
                    r.assets[key]["charge"][t] = take
                    rec[3] = asset.step(c, soc, take, 0.0, dt_h)
                    rem -= take

            # 3b. arbitrage: buy cheap even without local surplus
            if arb_lo is not None and price <= arb_lo and imp_cap > imported:
                for rec in stores:
                    key, asset, c, soc, serves = rec
                    if serves == "heat":
                        continue
                    already = r.assets[key]["charge"][t]
                    room = asset.max_charge_kw(c, rec[3], t) - already
                    if room <= 0:
                        continue
                    extra = min(room, imp_cap - imported)
                    if extra <= 0:
                        break
                    r.assets[key]["charge"][t] = already + extra
                    rec[3] = asset.step(c, soc, already + extra, 0.0, dt_h)
                    imported += extra

            # 3b. bidirectional flexible loads may export in a dear hour.
            # This is the half of vehicle-to-grid that actually earns money.
            # Without it a V2G fleet can only ever displace on-site load,
            # which is vehicle-to-home by another name, and the two
            # scenarios return identical numbers — as they did.
            if grid_ok and exp_cap > 0:
                for rec in flex:
                    key, asset, c, soc, prof, missed = rec
                    if not getattr(asset, "can_export", False):
                        continue
                    if hasattr(asset, "price_now"):
                        asset.price_now(
                            grid.export_price[t] if grid else 0.0
                        )
                    room = exp_cap - rem
                    if room <= 0:
                        break
                    give = asset.preferred_discharge_kw(
                        c, soc, t, prof, price=price, export=True
                    )
                    give = min(give, room)
                    if give > 0:
                        r.assets[key]["discharge"][t] += give
                        rec[3] = asset.step(c, soc, 0.0, give, t, prof, dt_h)
                        rem += give

            # 4. export
            if rem > 0 and exp_cap > 0:
                exported = min(exp_cap, rem)
                rem -= exported

            # 5. curtail
            curtailed = max(0.0, rem)
            if curtailed > 0 and nd:
                # Attribute curtailment proportionally to the generators that
                # produced the surplus, so the report can say which asset is
                # being wasted rather than reporting a single site total.
                total_nd = sum(r.assets[k]["output"][t] for k, _, _, _ in nd)
                if total_nd > 0:
                    for key, _a, _c, _u in nd:
                        share = r.assets[key]["output"][t] / total_nd
                        r.assets[key]["curtailed"][t] = curtailed * share

        # =============================================== deficit hour
        else:
            deficit = -surplus

            # 1. storage discharge, merit order
            for rec in stores:
                if deficit <= 0:
                    break
                key, asset, c, soc, serves = rec
                if serves == "heat":
                    continue
                if arb_lo is not None and price < arb_hi and imp_cap > 0:
                    continue        # hold charge for a dearer hour
                avail = asset.max_discharge_kw(c, soc, t)
                give = min(avail, deficit)
                if give > 0:
                    r.assets[key]["discharge"][t] = give
                    rec[3] = asset.step(c, soc, 0.0, give, dt_h)
                    deficit -= give

            # 2. bidirectional flexible loads
            for i, rec in enumerate(flex):
                if deficit <= 0:
                    break
                key, asset, c, soc, prof, missed = rec
                if not asset.bidirectional:
                    continue
                give = asset.preferred_discharge_kw(
                    c, soc, t, prof, price=price, deficit_kw=deficit
                )
                if give > 0:
                    r.assets[key]["discharge"][t] = give
                    rec[3] = asset.step(c, soc, 0.0, give, t, prof, dt_h)
                    deficit -= give

            # 3. firm supply, cheapest first.
            #
            # The grid and every dispatchable unit are ranked together by
            # what the next kilowatt-hour costs, and the deficit is served
            # down that list. Whether a generator runs is now a question of
            # price, which is the only basis on which it should ever have
            # been decided.
            options = []
            if imp_cap > imported:
                options.append(("grid", None, None, price, GRID_MERIT))
            for key, asset, c in disp:
                # Cost is evaluated at the output this hour actually calls
                # for, because a generator's cost per kilowatt-hour falls
                # steeply with load - the fuel curve's intercept is charged
                # whether the machine is busy or idling. Ranking on
                # full-load cost would flatter a lightly loaded diesel.
                mc = _dispatchable_marginal_cost(asset, c, deficit, t, state)
                options.append(("gen", key, asset, mc, getattr(
                    asset, "default_merit", 70)))

            # Ties break on the static merit order, so that a study with no
            # prices entered behaves exactly as it did before.
            options.sort(key=lambda o: (round(o[3], 9), o[4]))

            for kind, key, asset, _mc, _merit in options:
                if deficit <= 0 and strategy != CYCLE_CHARGING:
                    break
                if kind == "grid":
                    if deficit > 0 and imp_cap > imported:
                        take = min(imp_cap - imported, deficit)
                        if peak_target is not None:
                            # Peak shaving: hold the import below the target
                            # where the rest of the design can cover it, and
                            # let the deficit fall through to plant that can.
                            headroom = max(0.0, peak_target - imported)
                            take = min(take, headroom)
                        imported += take
                        deficit -= take
                    continue

                c = counts.get(key, 0)
                if c <= 0:
                    continue
                want = deficit

                # Cycle charging: a running generator runs hard. Part load
                # is where a diesel's efficiency collapses, so the strategy
                # is to meet the deficit at full output and put the surplus
                # into storage rather than throttle back. This was named in
                # the strategy list, offered in the interface, and never
                # implemented - choosing it returned load-following's answer.
                if strategy == CYCLE_CHARGING:
                    already_running = want > 0
                    if not already_running:
                        continue
                    room = 0.0
                    for srec in stores:
                        if srec[4] == "heat":
                            continue
                        room += max(0.0, srec[1].max_charge_kw(
                            srec[2], srec[3], t) - r.assets[srec[0]]["charge"][t])
                    want = min(asset.available_kw(c, t, state), deficit + room)

                out, forced = asset.dispatch(c, want, t, state)
                if out <= 0:
                    continue
                r.assets[key]["output"][t] = out
                served = min(out, deficit)
                deficit -= served
                # Surplus above what the load needed - forced by the minimum
                # load ratio, or produced deliberately under cycle charging.
                # `forced` is not added to this: it is BY DEFINITION the part
                # of `out` that exceeds what was asked for, so it is already
                # inside (out - served). Adding both counts the same energy
                # twice and breaks conservation, which is what the balance
                # check exists to catch.
                spare = max(0.0, out - served)
                if spare > 0:
                    # Store it if anything will take it.
                    left = spare
                    for rec in stores:
                        if left <= 0:
                            break
                        skey, sasset, sc, ssoc, serves = rec
                        if serves == "heat":
                            continue
                        already = r.assets[skey]["charge"][t]
                        room = sasset.max_charge_kw(sc, rec[3], t) - already
                        take = min(max(0.0, room), left)
                        if take > 0:
                            r.assets[skey]["charge"][t] = already + take
                            rec[3] = sasset.step(sc, ssoc, already + take, 0.0, dt_h)
                            left -= take
                    curtailed += left

            # Peak shaving may have left a deficit the local plant could not
            # cover. Buying above the target beats failing to serve the load:
            # the target is an economic preference, the load is not.
            if deficit > 0 and imp_cap > imported:
                take = min(imp_cap - imported, deficit)
                imported += take
                deficit -= take

            # 4. flexible-load obligations, even now
            for i, rec in enumerate(flex):
                key, asset, c, soc, prof, missed = rec
                if must[i] <= 0:
                    continue
                already = r.assets[key]["charge"][t]
                need = min(
                    must[i] - already,
                    asset.max_charge_kw(c, soc, t, prof) - already,
                )
                if need <= 0:
                    continue
                take = min(need, max(0.0, imp_cap - imported))
                if take > 0:
                    imported += take
                    r.assets[key]["charge"][t] = already + take
                    rec[3] = asset.step(
                        c, soc, already + take, 0.0, t, prof, dt_h
                    )

            # 5. unserved
            unmet = max(0.0, deficit)

        # ---- flexible loads that neither charged nor discharged still age
        for rec in flex:
            key, asset, c, soc, prof, missed = rec
            ch = r.assets[key]["charge"][t]
            di = r.assets[key]["discharge"][t]
            if ch == 0.0 and di == 0.0:
                rec[3] = asset.step(c, soc, 0.0, 0.0, t, prof, dt_h)
            r.assets[key]["soc"][t] = rec[3]
            # Departure obligation check
            leaving = prof["about_to_leave"][t]
            if leaving > 0 and rec[3] < asset.soc_departure_target - 1e-6:
                rec[5] += 1

        for rec in stores:
            key, asset, c, soc, serves = rec
            if (r.assets[key]["charge"][t] == 0.0
                    and r.assets[key]["discharge"][t] == 0.0):
                rec[3] = asset.step(c, rec[3], 0.0, 0.0, dt_h)
            r.assets[key]["soc"][t] = rec[3]

        r.load.append(demand)
        r.grid_import.append(imported)
        r.grid_export.append(exported)
        r.curtailed.append(curtailed)
        r.unmet.append(unmet)

    # ------------------------------------------------------------ totals
    months = month_index() if n == HOURS_PER_YEAR else [1] * n
    ev_missed = sum(rec[5] for rec in flex)
    r.totals = _aggregate(r, system, months, ev_missed, dt_h)
    return r


def _normalise_profile(prof, asset, n_units, n_hours):
    """
    Fill in any fields an older or hand-built profile is missing.

    A profile supplied by a caller may predate the fields the scenario logic
    needs (`just_arrived`, `about_to_leave`, `hours_to_departure`). Deriving
    them from the connection series rather than failing keeps saved projects
    and external inputs working.
    """
    if "just_arrived" in prof and "hours_to_next_departure" in prof:
        return prof

    p = dict(prof)
    connected = p.get("connected")
    if connected is None:
        present = p.get("n_present") or [0.0] * n_hours
        connected = [x > 0 for x in present]
        p["connected"] = connected
    if "n_present" not in p:
        p["n_present"] = [n_units if c else 0 for c in connected]
    if "arrival_soc" not in p:
        p["arrival_soc"] = p.get("soc_init") or [0.5] * n_hours

    n = len(connected)
    just = [0.0] * n
    leave = [0.0] * n
    remaining = [0] * n
    for t in range(n):
        if connected[t] and (t == 0 or not connected[t - 1]):
            just[t] = p["n_present"][t]
        if connected[t] and (t == n - 1 or not connected[t + 1]):
            leave[t] = p["n_present"][t]
    run = 0
    for t in range(n - 1, -1, -1):
        run = run + 1 if connected[t] else 0
        remaining[t] = max(0, run - 1)

    nxt = [0] * n
    countdown = 0
    for t in range(n - 1, -1, -1):
        if leave[t] > 0:
            countdown = 0
        elif connected[t]:
            countdown += 1
        else:
            countdown = 0
        nxt[t] = countdown

    p["just_arrived"] = just
    p["about_to_leave"] = leave
    p["hours_to_departure"] = remaining
    p["hours_to_next_departure"] = nxt
    p.setdefault("n_points", n_units)
    p.setdefault("scenario", getattr(asset, "scenario", None))
    return p


def _aggregate(r, system, months, ev_missed, dt_h):
    n = len(r.load)
    load = sum(r.load) * dt_h
    imported = sum(r.grid_import) * dt_h
    exported = sum(r.grid_export) * dt_h
    curtailed = sum(r.curtailed) * dt_h
    unmet = sum(r.unmet) * dt_h

    renewable = dispatchable = 0.0
    charge = discharge = 0.0
    flex_charge = flex_discharge = 0.0
    per_asset = {}

    for key, rec in r.assets.items():
        cls = rec["class"]
        if cls == "non_dispatchable":
            e = sum(rec["output"]) * dt_h
            renewable += e
            per_asset[key] = {"energy_kwh": e, "class": cls,
                              "technology": rec["technology"],
                              "curtailed_kwh": sum(rec.get("curtailed", [])) * dt_h}
        elif cls == "dispatchable":
            e = sum(rec["output"]) * dt_h
            dispatchable += e
            per_asset[key] = {"energy_kwh": e, "class": cls,
                              "technology": rec["technology"]}
        elif cls == "storage":
            c = sum(rec["charge"]) * dt_h
            d = sum(rec["discharge"]) * dt_h
            charge += c
            discharge += d
            per_asset[key] = {"charge_kwh": c, "discharge_kwh": d,
                              "losses_kwh": c - d, "class": cls,
                              "technology": rec["technology"],
                              "min_soc": min(rec["soc"]) if rec["soc"] else 0.0,
                              "max_soc": max(rec["soc"]) if rec["soc"] else 0.0}
        elif cls == "flexible_load":
            c = sum(rec["charge"]) * dt_h
            d = sum(rec["discharge"]) * dt_h
            flex_charge += c
            flex_discharge += d
            per_asset[key] = {"charge_kwh": c, "discharge_kwh": d,
                              "class": cls, "technology": rec["technology"]}

    total_demand = load + flex_charge
    served = total_demand - unmet
    renewable_used = max(0.0, renewable - curtailed - exported)

    soc_series = r.battery_soc

    return {
        "load_kwh": load,
        "ev_charge_kwh": flex_charge,
        "ev_discharge_kwh": flex_discharge,
        "total_demand_kwh": total_demand,
        "served_kwh": served,
        "renewable_kwh": renewable,
        "dispatchable_kwh": dispatchable,
        "pv_kwh": sum(r.pv) * dt_h,
        "wind_kwh": sum(r.wind) * dt_h,
        "genset_kwh": sum(r.genset) * dt_h,
        "imported_kwh": imported,
        "exported_kwh": exported,
        "battery_charge_kwh": charge,
        "battery_discharge_kwh": discharge,
        "battery_losses_kwh": charge - discharge,
        "curtailed_kwh": curtailed,
        "unmet_kwh": unmet,
        "unmet_hours": sum(1 for x in r.unmet if x > 1e-9),
        "ev_unmet_departures": ev_missed,
        "peak_load_kw": max(r.load) if r.load else 0.0,
        "peak_import_kw": max(r.grid_import) if r.grid_import else 0.0,
        "peak_export_kw": max(r.grid_export) if r.grid_export else 0.0,
        "peak_renewable_kw": max(r.renewable) if n else 0.0,
        "min_soc": min(soc_series) if soc_series else 0.0,
        "max_soc": max(soc_series) if soc_series else 0.0,
        "final_soc": soc_series[-1] if soc_series else 0.0,
        "renewable_used_kwh": renewable_used,
        "total_supply_kwh": renewable + dispatchable + imported,
        "per_asset": per_asset,
        "months": months,
    }
