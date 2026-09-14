"""
Life-cycle economics.

Direct port of the original MATLAB `NPC.m`, `rep.m` and `Ecocnomic.m`, with
the same present-worth factors, extended to itemise every component so the
report can show where the money goes rather than only the total.

Three present-worth factors are used, matching the original:

  PWF1  replacement costs, discounted at each replacement year
  PWF2  annual O&M, an ordinary annuity over the project life
  PWF3  annual fuel and energy costs, an annuity with price escalation

The escalation handling deserves a note. When the discount rate equals the
escalation rate, the geometric series degenerates and PWF3 is simply the
project life n. The original code tested for that case; it is kept, because
the alternative is a division by zero at exactly the rate pair a user is
most likely to enter by accident (both 5%).

Sign convention: costs are positive, revenues negative, so NPC is the
number to minimise.
"""

from __future__ import annotations


def replacements(project_years, component_life_years):
    """
    Number of replacement events over the project life.

    Port of rep.m: fix(n/life) + (rem(n,life) > 0) - 1

    A component whose life equals the project life is never replaced. A
    component lasting 10 years in a 20-year project is replaced once, at
    year 10, not twice - the replacement at year 20 would be scrapped on the
    day it was installed.
    """
    n = float(project_years)
    life = float(component_life_years)
    if life <= 0:
        raise ValueError("component life must be positive")
    whole = int(n // life)
    remainder = 1 if (n % life) > 1e-9 else 0
    return max(0, whole + remainder - 1)


def pwf_replacement(interest_rate, component_life_years, n_replacements):
    """
    PWF1: present worth of a series of replacements.

        sum over y of 1 / (1 + ir)^(life * y)
    """
    ir = float(interest_rate)
    life = float(component_life_years)
    total = 0.0
    for y in range(1, int(n_replacements) + 1):
        total += 1.0 / (1.0 + ir) ** (life * y)
    return total


def pwf_annuity(interest_rate, project_years):
    """
    PWF2: present worth of one currency unit per year for n years.

        ((1+ir)^n - 1) / (ir * (1+ir)^n)
    """
    ir = float(interest_rate)
    n = float(project_years)
    if abs(ir) < 1e-12:
        return n
    return ((1.0 + ir) ** n - 1.0) / (ir * (1.0 + ir) ** n)


def pwf_escalating(interest_rate, escalation_rate, project_years):
    """
    PWF3: present worth of an annually escalating cost stream.

    Uses the real discount rate r = (1+ir)/(1+er) - 1, degenerating to n when
    the two rates are equal.
    """
    ir = float(interest_rate)
    er = float(escalation_rate)
    n = float(project_years)
    if abs(ir - er) < 1e-12:
        return n
    r = (1.0 + ir) / (1.0 + er) - 1.0
    if abs(r) < 1e-12:
        return n
    return ((1.0 + r) ** n - 1.0) / (r * (1.0 + r) ** n)


def capital_recovery_factor(interest_rate, project_years):
    """
    CRF: the annuity factor that converts a present sum into equal annual
    payments. The reciprocal of PWF2, and what turns NPC into LCOE.
    """
    ir = float(interest_rate)
    n = float(project_years)
    if abs(ir) < 1e-12:
        return 1.0 / n
    return (ir * (1.0 + ir) ** n) / ((1.0 + ir) ** n - 1.0)


def component_npc(
    n_units,
    capital_cost,
    replacement_cost,
    om_cost_per_year,
    annual_recurring_cost,
    life_years,
    project_years,
    interest_rate,
    escalation_rate,
):
    """
    Net present cost of one component type.

    Port of NPC.m:
        N * (capital + replacement*PWF1 + om*PWF2 + recurring*PWF3)

    `annual_recurring_cost` carries fuel or purchased energy - anything that
    escalates with time rather than staying flat in nominal terms.
    """
    reps = replacements(project_years, life_years)
    pwf1 = pwf_replacement(interest_rate, life_years, reps)
    pwf2 = pwf_annuity(interest_rate, project_years)
    pwf3 = pwf_escalating(interest_rate, escalation_rate, project_years)

    capital = n_units * capital_cost
    replacement = n_units * replacement_cost * pwf1
    om = n_units * om_cost_per_year * pwf2
    recurring = annual_recurring_cost * pwf3

    return {
        "units": n_units,
        "capital": capital,
        "replacement": replacement,
        "om": om,
        "recurring": recurring,
        "npc": capital + replacement + om + recurring,
        "n_replacements": reps,
        "life_years": life_years,
        "pwf1": pwf1,
        "pwf2": pwf2,
        "pwf3": pwf3,
    }


class EconomicParameters:
    """
    Project-level financial assumptions.

    `interest_rate` is the nominal discount rate. If the user prefers to work
    in real terms they should enter the real rate and set inflation to zero;
    mixing a nominal rate with real cash flows is the most common error in
    this kind of study and is worth about 20% on a 20-year NPC.
    """

    def __init__(
        self,
        project_years=20,
        interest_rate=0.08,
        escalation_rate=0.02,
        inflation_rate=0.0,
        currency="USD",
        unmet_load_penalty=0.0,
        emissions_price=0.0,
        load_growth_rate=0.0,
    ):
        self.project_years = int(project_years)
        self.interest_rate = float(interest_rate)
        self.escalation_rate = float(escalation_rate)
        self.inflation_rate = float(inflation_rate)
        self.currency = currency
        self.unmet_load_penalty = float(unmet_load_penalty)
        self.emissions_price = float(emissions_price)
        # Annual growth in demand over the project life. Zero reproduces the
        # previous behaviour, which assumed a site whose consumption never
        # changes for twenty years - a strong assumption stated nowhere, and
        # usually the optimistic one: a design sized on year-one demand is
        # already short by the time it is commissioned at a site adding load.
        self.load_growth_rate = float(load_growth_rate)

        if self.project_years <= 0:
            raise ValueError("project life must be positive")
        if self.interest_rate <= -1.0:
            raise ValueError("interest rate must exceed -100%")

    @property
    def real_discount_rate(self):
        """Discount rate net of inflation."""
        if self.inflation_rate == 0:
            return self.interest_rate
        return (1.0 + self.interest_rate) / (1.0 + self.inflation_rate) - 1.0

    @property
    def crf(self):
        return capital_recovery_factor(self.interest_rate, self.project_years)

    def to_dict(self):
        return {
            "project_years": self.project_years,
            "interest_rate": self.interest_rate,
            "escalation_rate": self.escalation_rate,
            "inflation_rate": self.inflation_rate,
            "real_discount_rate": self.real_discount_rate,
            "currency": self.currency,
            "crf": self.crf,
            "unmet_load_penalty": self.unmet_load_penalty,
            "emissions_price": self.emissions_price,
            "load_growth_rate": self.load_growth_rate,
        }


def evaluate(system, result, econ):
    """
    Full life-cycle cost of a simulated system.

    Returns a dict with the itemised NPC, the totals, LCOE and the annualised
    cost. `result` is a DispatchResult; `system` supplies the components and
    unit counts; `econ` the financial parameters.
    """
    n = econ.project_years
    ir = econ.interest_rate
    er = econ.escalation_rate
    tot = result.totals
    items = {}

    # ------------------------------------------------------------------ PV
    if system.pv and system.n_pv > 0:
        items["pv"] = component_npc(
            system.n_pv, system.pv.capital_cost, system.pv.replacement_cost,
            system.pv.om_cost_per_year, 0.0, system.pv.lifetime_years,
            n, ir, er,
        )

    # ---------------------------------------------------------------- wind
    if system.wind and system.n_wind > 0:
        items["wind"] = component_npc(
            system.n_wind, system.wind.capital_cost,
            system.wind.replacement_cost, system.wind.om_cost_per_year,
            0.0, system.wind.lifetime_years, n, ir, er,
        )

    # ------------------------------------------------------------- battery
    if system.battery and system.n_battery > 0:
        life = system.battery.expected_life_years(
            system.n_battery, tot["battery_discharge_kwh"]
        )
        life = max(1.0, min(life, float(n)))
        items["battery"] = component_npc(
            system.n_battery, system.battery.capital_cost,
            system.battery.replacement_cost, system.battery.om_cost_per_year,
            0.0, life, n, ir, er,
        )
        items["battery"]["throughput_life_years"] = life

    # -------------------------------------------------------------- genset
    if system.genset and system.n_genset > 0:
        g = system.genset
        summary = g.annual_summary(system.n_genset, result.genset)
        run_h = summary["run_hours"]
        life_years = (
            g.lifetime_hours / run_h if run_h > 0 else float(n)
        )
        life_years = max(1.0, min(life_years, float(n)))
        recurring = (
            summary["fuel_cost"] + summary["om_cost"] + summary["startup_cost"]
        )
        items["genset"] = component_npc(
            system.n_genset, g.capital_cost, g.replacement_cost, 0.0,
            recurring, life_years, n, ir, er,
        )
        items["genset"]["fuel_units_per_year"] = summary["fuel_units"]
        items["genset"]["run_hours"] = run_h
        items["genset"]["operating_life_years"] = life_years

    # ---------------------------------------------------------------- grid
    if system.grid:
        bill = system.grid.annual_cost(
            result.grid_import, result.grid_export, tot.get("months")
        )
        items["grid"] = component_npc(
            1, 0.0, 0.0, 0.0, bill["net_cost"], float(n), n, ir, er,
        )
        items["grid"]["annual_bill"] = bill

    # ------------------------------------------------------------ chargers
    if system.ev and system.n_chargers > 0:
        items["ev_infrastructure"] = component_npc(
            system.n_chargers,
            # `capital_cost` is the Asset-level field. The legacy shim also
            # exposes `capital_cost_per_charger`; reading that one first
            # broke every fleet built through the catalogue.
            getattr(system.ev, "capital_cost", 0.0),
            0.0, system.ev.om_cost_per_year, 0.0,
            system.ev.lifetime_years, n, ir, er,
        )

    # ----------------------------------------------------- converters etc.
    for key, conv in (system.converters or {}).items():
        items[key] = component_npc(
            1, conv.get("capital_cost", 0.0), conv.get("replacement_cost", 0.0),
            conv.get("om_cost_per_year", 0.0), 0.0,
            conv.get("lifetime_years", n), n, ir, er,
        )

    # ------------------------------------------------------------ penalties
    penalty = 0.0
    if econ.unmet_load_penalty > 0 and tot["unmet_kwh"] > 0:
        annual = tot["unmet_kwh"] * econ.unmet_load_penalty
        items["unmet_load_penalty"] = component_npc(
            1, 0.0, 0.0, 0.0, annual, float(n), n, ir, er
        )
        penalty = items["unmet_load_penalty"]["npc"]

    if econ.emissions_price > 0:
        kg = 0.0
        if "grid" in items:
            kg += items["grid"]["annual_bill"]["emissions_kg"]
        if system.genset and system.n_genset > 0:
            kg += system.genset.annual_summary(
                system.n_genset, result.genset
            )["emissions_kg"]
        if kg > 0:
            annual = kg / 1000.0 * econ.emissions_price
            items["emissions_cost"] = component_npc(
                1, 0.0, 0.0, 0.0, annual, float(n), n, ir, er
            )

    # ------------------------------------------------------------- totals
    npc = sum(v["npc"] for v in items.values())
    capital = sum(v["capital"] for v in items.values())
    crf = econ.crf
    annualised = npc * crf

    # Energy the project actually delivered, and the denominator of LCOE.
    #
    # `served_kwh` is already total demand less unserved, and total demand is
    # already the site load PLUS everything the flexible loads took - the
    # dispatch aggregates it that way so that an EV fleet's charging counts
    # as demand to be met rather than as a loss. Adding `ev_charge_kwh` on
    # top counted every kilowatt-hour delivered to a vehicle twice, which
    # divided the annualised cost by too large a number and understated the
    # levelised cost at exactly the sites where the fleet is the point of
    # the study. A site without EVs was unaffected, which is why it lasted.
    served = tot["served_kwh"]
    lcoe = (annualised / served) if served > 0 else float("inf")

    return {
        "items": items,
        "npc": npc,
        "initial_capital": capital,
        "annualised_cost": annualised,
        "lcoe": lcoe,
        "crf": crf,
        "served_kwh": served,
        "penalty_npc": penalty,
        "currency": econ.currency,
        "project_years": n,
        "interest_rate": ir,
        "escalation_rate": er,
    }


def payback_period(baseline_annual_cost, project_annual_cost, capital, max_years=50):
    """
    Simple payback in years against a do-nothing baseline.

    Returns None when the project never pays back, which is a legitimate and
    important answer, not an error.
    """
    saving = baseline_annual_cost - project_annual_cost
    if saving <= 0 or capital <= 0:
        return None
    years = capital / saving
    return years if years <= max_years else None


def discounted_payback(baseline_annual_cost, project_annual_cost, capital,
                       interest_rate, max_years=50):
    """Payback accounting for the time value of money."""
    saving = baseline_annual_cost - project_annual_cost
    if saving <= 0 or capital <= 0:
        return None
    cum = 0.0
    for y in range(1, int(max_years) + 1):
        cum += saving / (1.0 + interest_rate) ** y
        if cum >= capital:
            return y
    return None


def net_present_value(baseline_annual_cost, project_annual_cost, capital,
                      interest_rate, project_years):
    """NPV of the project relative to the baseline."""
    saving = baseline_annual_cost - project_annual_cost
    return saving * pwf_annuity(interest_rate, project_years) - capital


def internal_rate_of_return(baseline_annual_cost, project_annual_cost,
                            capital, project_years, tol=1e-6):
    """
    IRR by bisection on the NPV function.

    Bounded to [-0.99, 10.0]. Returns None when no root exists in range,
    which is the honest answer for a project that never returns its capital.
    """
    saving = baseline_annual_cost - project_annual_cost
    if saving <= 0 or capital <= 0:
        return None

    def npv(rate):
        return saving * pwf_annuity(rate, project_years) - capital

    lo, hi = -0.99, 10.0
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2.0
        f_mid = npv(mid)
        if abs(f_mid) < tol:
            return mid
        if f_lo * f_mid < 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2.0
