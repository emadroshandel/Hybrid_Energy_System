# HES — sizing review and corrections

Review of the sizing engine prompted by a result recommending **5.35 MWp PV +
2.40 MW wind + 1.60 MWh storage at USD 1.72/kWh**. This document explains how
that number was produced, what was changed, and what I would add next.

All 199 tests pass after the changes: 96 engine, 50 technologies, 53 new
plausibility tests.

---

## 1. Why the answer was so large

Four things compounded. Only the first is a mistake you made; the other three
are defects in the tool that made the mistake invisible.

### 1.1 The load was a factory, not a house — this is the dominant factor

You used **Build a profile** with peak 150 kW and the default load factor 0.45:

```
150 kW × 0.45 × 8760 h = 591,300 kWh/year
```

That is 591 MWh a year — a small factory, a hotel, or a campus building. A
house is 5–20 MWh. The engine sized correctly for a site **35 to 120 times
larger** than the one you had in mind.

This is confirmed independently by the screenshot's own numbers: NPC 9.99 M
÷ LCOE 1.72 /kWh, discounted at 8% over 20 years, back-solves to 592 MWh/yr
of energy served. The engine was internally consistent throughout.

Nothing in the interface said so. The peak field said "300" and then "150" and
the units were kW, and that was the entire feedback the tool gave.

**Fixed:** every load import now reports back, in words, what the engine thinks
it was handed — `Read as 150 kW peak, 67.5 kW average, 591 MWh a year — the
demand of a light-industrial site, a small factory, a hotel or a campus
building.` If that sentence is wrong, you know in five seconds.

### 1.2 PV yield was understated by the resource time-alignment bug

This is the real bug, and it is worth understanding even if you never repeat
the load mistake.

Every online provider — PVGIS, PVGIS TMY, NASA POWER, Open-Meteo — returns
hourly values **stamped in UTC**. `models/pv.solar_position()` works in **local
standard time**; it subtracts the zone meridian to reach solar time, which is
the correct and careful thing to do. The two were never reconciled. The
irradiance series and the sun were out of phase by the whole UTC offset.

The consequences are worse than a smearing of the daily profile:

- `output_series` zeroes any hour where the computed zenith is ≥ 90°. Real
  afternoon irradiance was assigned a night-time sun and deleted outright.
- The PVGIS readers rebuild GHI as `Gb(n)·cos(zenith) + Gd(h)`. That
  reconstruction used the misaligned zenith too, so the error was applied
  twice.

Measured against a physically self-consistent year:

| Site | UTC offset | PV yield modelled | Fraction of truth |
|---|---|---|---|
| Shiraz | +3:30 | 1,179 kWh/kWp | **0.75×** |
| Tehran | +3:30 | 1,265 kWh/kWp | **0.75×** |
| Adelaide | +9:30 | 105 kWh/kWp | **0.14×** |

At Shiraz the array is under-predicted by a quarter — so the optimiser buys a
third more PV than it needs, and every LCOE is a third too high. At Adelaide
the tool is unusable.

The error is proportional to the UTC offset, which is exactly why it survived
review: at the longitudes where this kind of code is usually written and
checked, it is a few per cent.

**Fixed.** `timeseries.shift_utc_to_local()` re-indexes a UTC year onto local
standard time, interpolating fractional offsets (+3:30, +9:30, +5:45) rather
than rounding them — rounding half an hour puts back a fifth of the error. It
is applied at the provider boundary in `resources/providers.py`, so nothing
downstream has to remember. The PVGIS readers now shift the components
**before** rebuilding GHI, so the zenith and the irradiance describe the same
instant. Every resource dict carries `time_reference` and
`utc_offset_applied_hours`.

After the fix, all three sites recover to **99–100%** of true yield. The
synthetic (offline) path was never affected — it is generated from the solar
model, so it was always self-consistent. That is why the offline tests looked
fine.

### 1.3 The search ceiling was set by a rule that ignored the grid

`bounds_from_load()` allowed each technology enough units to serve **1.5 ×
annual demand on its own**, at every site, whether or not there was a grid
connection.

That is far too loose for a grid-connected site (export is paid a fraction of
import and capped well below it, so nothing above ~1× annual demand has ever
been on the cost frontier) and too tight for a true island (where the December
deficit sets the size and several times annual nameplate is legitimate).

An over-generous box is not just wasted search time. Because reliability and
renewable fraction **saturate** — see 1.4 — designs at the ceiling are
genuinely non-dominated, so a loose box actively puts absurd systems on the
reported front.

**Fixed:** the ceiling now interpolates between a grid-connected headroom
(1.5×) and an islanded one (3.5×) along how much of the site's peak the
connection can actually carry, and a second, independent ceiling in **power**
caps installed capacity at 6× peak demand — which binds at poor-resource sites
where the energy rule alone demands implausible plant. Storage is bounded on
**usable** energy (`soc_max − soc_min`), not nameplate; the old rule was a fifth
too tight, in the direction that truncates a search silently.

### 1.4 "MOST RELIABLE" was reporting the largest system in the archive

This is the card in your screenshot, and its selection rule was:

```python
def most_reliable(self):
    pool = list(self.search.archive)          # includes INFEASIBLE designs
    return min(pool, key=lambda s: s.metrics.get("lpsp", 1.0))
```

LPSP is monotonically non-increasing in capacity. Once a design meets the load
in every hour, LPSP is exactly 0.0 and adding capacity cannot improve it. So a
plain `min()` returns whichever member of a large tied set the iteration order
reaches — reliably the biggest system the search ever tried, pinned to the top
corner of the box. `greenest()` had the same defect with renewable fraction.
Neither excluded designs that violated the study's own constraint.

The card was answering *"what is the largest thing you looked at?"*, not
*"what should I build?"*, and presenting the answer as a recommendation.

**Fixed:** all three selectors now consider only feasible designs, treat values
within a tolerance as tied (0.05% LPSP, 1 percentage point of renewable
fraction), and **break the tie on net present cost**. In the reproduction case
this moved "most reliable" from 1,075 kWp + 2,350 kWh (the box corner) to
550 kWp + 700 kWh — a real design.

A card can now legitimately show a small system, so each one carries a note
explaining how it was chosen: *"36 of the 50 designs on the front meet the load
in essentially every hour, so reliability cannot separate them. This is the
cheapest of those — buying more capacity would not make the supply any
firmer."*

---

## 2. Other defects found and fixed

### 2.1 LCOE double-counted EV charging energy

```python
served = tot["served_kwh"] + tot["ev_charge_kwh"]     # before
```

`served_kwh` is `total_demand_kwh − unmet_kwh`, and `total_demand_kwh` is
already `load_kwh + flex_charge` — the dispatch aggregates a flexible load's
demand into total demand so that vehicle charging counts as load to be met.
Adding it again counted every kilowatt-hour delivered to a vehicle twice,
dividing the annualised cost by too large a number.

The levelised cost came out low at exactly the sites where the fleet is the
point of the study. A site without EVs was unaffected, which is how it lasted.

### 2.2 The oversize filter treated a diesel as permanently at full output

`estimate_unit_yields()` credits a dispatchable unit with rated power for every
hour of the year. That is the right figure for the *feasibility* half of
`analytic_filter` — could this design meet the load? — and absurd for the
*oversize* half, where it asks whether a generator that could run flat out all
year would generate too much. The answer for any generator worth installing is
yes.

The old threshold (6× annual demand) sat above most search boxes, so the filter
removed nothing and the mistake stayed hidden. Tightening it to 2× immediately
deleted every design containing a generator — caught by your own
`test_the_metaheuristics_agree_with_the_exhaustive_front`, which is the single
most valuable test in the suite.

The oversize test now counts only must-take (non-dispatchable) generation.

### 2.3 Wind reference height defaulted to 80 m

The UI's "Data measurement height" defaulted to 80 m, but ERA5, MERRA-2 and
PVGIS all supply 10 m wind. A 10 m series labelled 80 m gets sheared *down* to
a 30 m hub, throwing away most of the resource with no error. Default is now
10 m, with help text saying which sources are 10 m.

### 2.4 Grid outages were drawn independently, hour by hour

At 95% availability the old model produced ~440 separate one-hour
interruptions spread evenly through the year. Real networks fail perhaps twenty
times a year for several hours at a time. The two profiles have identical
availability and completely different consequences — an hour of storage rides
out the first and is irrelevant to the second — so the old model understated
backup requirements by roughly the mean repair time.

Replaced with a two-state Markov chain parameterised by availability and
**mean outage duration** (new `mean_outage_hours`, exposed in the UI). Same 95%
availability now gives, at your choice:

| Mean repair time | Events/year | Longest outage |
|---|---|---|
| 1 h | 456 | 1 h |
| 4 h | 115 | 17 h |
| 12 h | 38 | 87 h |

`GridConnection.outage_statistics()` reports what was actually generated.
Setting `mean_outage_hours=1` reproduces the old behaviour exactly.

### 2.5 PV degradation was modelled and never applied; there was no load growth

`PVArray.yield_after_years()` existed but was called from nowhere. Every study
modelled year-one output for all 20 years, and assumed a site consuming exactly
the same energy in year 20 as in year 1. Both assumptions are optimistic and
neither was stated.

Added `EconomicParameters.load_growth_rate` (default 0, so existing studies are
unchanged) and `SizingResult.end_of_life_check()`, which re-simulates the
recommended design in its final year with the degraded array and the grown
load. One extra dispatch run, not twenty — the worst year is the last one, so
it is the one to simulate. Example output for a house at 2%/yr growth:

```
end of life (year 20): pv factor 0.900, load factor 1.486
  Unserved energy rises from 0.39% in year one to 1.07% in year 20.
  The renewable share falls from 91% to 67%.
```

It also checks whether the design still meets the study's own LPSP constraint
at end of life, and says so if not.

---

## 3. New: input plausibility layer (`ensys/validate.py`)

The theme running through §1 is that every number is dimensionally ambiguous to
a computer and obvious to an engineer. A load series in watts is a valid list
of floats. Twelve monthly irradiation values given as daily averages are a
valid list of twelve numbers. A 25 kWp PV block is reasonable right up until the
site is a house.

`validate.py` states what the engine believes it was asked, and says so loudly
when the belief is implausible. Findings are `info` / `warning` / `error`, shown
next to the input that produced them. Nothing blocks a study — an unusual site
is still a site.

What it checks:

- **Load** — always describes the site back in words with an archetype; flags a
  probable watts/kW confusion, a load factor below 8% or above 95% (a flat
  profile has none of the day–night swing that sizes a battery), and metering
  gaps read as genuine zero demand.
- **Monthly GHI** — detects values in the 0.4–10 range as **daily averages**,
  which is how the Global Solar Atlas and most met stations report irradiation,
  and converts them. Uncorrected this understates the solar resource
  ~thirtyfold, and the study answers with an absurd array. This is the single
  most likely remaining way to get a wrong answer from the offline path.
- **PV specific yield** — reports kWh/kWp/yr prominently. An engineer knows this
  figure for their region to within 10%, so surfacing it turns a whole class of
  silent faults — misaligned timestamps, wrong hemisphere, tilt typed as 90 —
  into something visible at a glance. Outside 450–2,400 it is an `error` and
  names the likely causes.
- **Wind capacity factor** — flags below 2% or above 60%, pointing at hub-height
  and km/h-vs-m/s mistakes.
- **Component scale** — PV block larger than peak demand, battery block larger
  than daily consumption, C-rate above 2 or below 0.1, turbine or genset more
  than 2× peak, import limit below peak or more than 20× it. Each says what to
  use instead.
- **Economics** — discount rate at or below escalation; discount rate above 35%.
- **The result** — LCOE more than 4× the tariff, generation more than 8× peak
  demand, curtailment above 35%.

Run against your original inputs, this would have printed:

> ⚠ The PV block is 25 kWp but the site peaks at 3.8 kW…
> ⚠ The import limit (250 kW) is 86 times the site peak (3.8 kW)…
> ⚠ The design installs 25 kW of generation against a peak demand of 3.8 kW — 9 times over…

---

## 4. New: site-scale presets

The optimiser buys whole units, so unit sizes decide what answers it can
express at all. A 25 kWp block cannot describe a house: its options are
nothing, one block eight times larger than the site, or multiples of that.

The Load page now opens with **House / Small commercial / Industrial**, which
moves peak demand, profile shape, every unit size, the grid connection and every
per-unit cost together. Costs are derived from specific rates ($700/kWp,
$250/kWh, $1,800/kW wind, $300/kW genset — taken from your existing defaults) so
a 1 kWp block never keeps a 25 kWp block's price tag.

Relatedly: changing a unit **size** without changing its price was a quiet way
to get a wrong answer. Unit-size fields are now linked to their cost fields —
change the block from 25 kWp to 1 kWp and the three cost fields rescale by
1/25, with a note saying so. Overwrite them freely when a quote says otherwise.

---

## 5. What is sound

Most of the engine is good, and the failure mode was never arithmetic. Things I
checked and would defend:

- **Solar geometry.** Michalsky/NOAA with the equation of time and the zone
  meridian, evaluated at the hour midpoint. Correct, and better than most
  tools at this level. It was the *only* correct half of the alignment bug.
- **Transposition and thermal.** Hay–Davies rather than isotropic (right call
  for high-DNI sites), Faiman rather than NOCT (right call for hot climates),
  Erbs split with a conservation test. Well chosen and correctly implemented.
- **Dispatch.** One loop over four behaviours, with an explicit priority order
  and a `balance_error()` conservation check the tests assert on. The comment
  about the MATLAB assigning charge and discharge in the same branch is a real
  structural improvement.
- **Discrete unit sizing.** Sizing by integer unit counts rather than continuous
  capacity is the right decision and the reasoning in `system.py` is correct —
  half a turbine has no cut-in speed.
- **Life-cycle economics.** PWF1/PWF2/PWF3, the escalation degeneracy at
  `ir == er`, and the `rep.m` replacement count (a component whose life equals
  the project life is never replaced) are all handled properly.
- **Pareto machinery.** Non-dominated sort, crowding distance, knee point, the
  single sign flip in `objective_vector` for naturally-maximised objectives, and
  constraint-violation ranking rather than discarding infeasible designs.
- **`test_the_metaheuristics_agree_with_the_exhaustive_front`.** Computing the
  true front by enumeration and requiring both metaheuristics to reproduce it is
  the only test that can catch a broken optimiser, and it caught my own mistake
  within minutes. Keep it.
- **Electrical sizing.** String voltage limits at temperature extremes, cable
  derating, breaker/cable coordination, transformerless → Type B RCD. This part
  is better than it needs to be.
- **Determinism.** Seeded throughout, and asserted.

---

## 6. What I would add next, in priority order

### High value, low effort

**6.1 Cost the inverter and apply it to the energy balance.**
This is the largest remaining gap. `sizing/inverter.py` sizes the inverter and
computes clipping loss, and `diagram/` draws it — but `SystemConfig.converters`
is **never populated** by `api.py`, so inverter and PCS capital is absent from
every NPC. Meanwhile `dispatch` consumes `pv_unit`, which is DC power *before*
the inverter, with no conversion efficiency and no clipping. So the study is
currently optimistic on PV energy by a few per cent and low on capital by
roughly 8–12% of a PV system — and it cannot optimise the DC:AC ratio, which is
one of the few genuinely free design levers.

**6.2 Sensitivity / tornado on the recommended design.**
Perturb discount rate, capital cost, fuel price, tariff and load ±20% one at a
time, re-cost (no re-simulation needed for most), and rank the effect on NPC and
LCOE. Cheap, self-contained, and it turns a single number into a defensible
range — which is what a client actually needs. This is the highest-value single
addition for report credibility.

**6.3 Battery capacity fade within life.**
`expected_life_years` drives replacement timing, but the store delivers its full
nameplate right up to the day it is replaced. Real cells reach end of life at
~80% capacity. Fold a fade factor into `end_of_life_check()`, which now exists
and is the natural home for it.

**6.4 Roof/land area constraint.**
A house cannot fit 50 kWp regardless of economics. One field (available area,
m²) and one constraint using `module_efficiency` would close a whole class of
physically impossible answers.

### Higher value, more effort

**6.5 Inter-annual resource variability.**
The single largest source of uncertainty in the whole study, and currently
invisible: one TMY, one answer. Fetch 5–10 years from Open-Meteo (the archive
reaches 1940 and needs no key), re-simulate the *recommended* design against
each, and report the spread in LPSP and renewable fraction. Do not re-optimise
per year — size on the TMY, then report robustness. A design whose LPSP ranges
0.1%–4% across ten years is a different product from one that ranges 0.1%–0.3%,
and the client cannot see that today.

**6.6 Correlated load and weather.**
Load and resource are currently independent inputs. In your climate they are
strongly coupled: the cooling peak *is* the hot clear afternoon. A temperature
sensitivity on the load profile (kW per °C above a base) would capture it with
one parameter, and it changes storage sizing materially — the coincidence of
peak load and peak PV is favourable, the coincidence of peak load and a hot
derated array is not.

**6.7 Wind: wake losses and air density.**
`air_density_at()` exists and is tested, but multiple turbines are treated as
independent. Even a simple array-efficiency factor by spacing would stop
multi-turbine designs being systematically optimistic.

**6.8 Reserve margin in dispatch.**
Dispatch meets demand exactly, hour by hour, with no operating reserve. Real
islanded systems hold spinning or storage reserve against a forecast error or a
unit trip. A reserve requirement (percentage of load plus percentage of
instantaneous renewable output — the HOMER convention) would make islanded
results markedly more honest.

### Worth considering

**6.9 Multi-year dispatch rather than a factor.** The current treatment applies
degradation and growth analytically and checks the final year. Simulating all 20
years is the rigorous answer, at 20× the cost. Probably not worth it inside the
optimiser; possibly worth it as a final check on the chosen design.

**6.10 Tariff structures.** Demand charges, TOU bands and standing charges are
implemented and correctly applied. What is missing is a library of *real* tariff
shapes, which is a data problem rather than a code one.

**6.11 Report the search box in the UI.** `boundary_warnings()` already flags a
design pinned to a bound, but the bounds themselves are only in the payload.
Showing them next to the front would have made §1.3 obvious immediately.

---

## 6A. Second round: the diesel, the strategies, and the cost library

Prompted by "diesel is never considered, and I need to be able to adjust any
cost". Both turned out to be symptoms of larger defects.

### 6A.1 A generator could never run against a working grid

In a deficit hour the dispatch served, in fixed order: storage, V2G, **grid
import**, then dispatchable generation. The grid took the whole deficit up to
its limit, so the generator was asked for what remained — nothing.

A genset therefore produced energy only when the grid *physically could not*
supply it: during an outage, or above the import limit. At every other hour it
was capacity the study had paid for and could not use, which makes it strictly
dominated, which is why the optimiser removed it from every grid-connected
design. Measured before the fix, with a 200 kW connection:

| Tariff | Diesel share |
|---|---|
| $0.09/kWh | 0% |
| **$2.00/kWh** | **0%** |

At two dollars a kilowatt-hour — more than six times the diesel's ~$0.31/kWh
running cost — the model still imported every kilowatt-hour. The tariff never
entered the decision at all.

**Fixed.** Grid import and every dispatchable unit are now ranked *together*
by what the next kilowatt-hour costs, and the deficit is served down that
list. `Generator.marginal_cost()` already existed and was never called; the
dispatch now calls it, and adds two costs that were being ignored:

- `om_cost_per_hour`, spread over the hour's output;
- **wear** — replacement cost ÷ rated life in hours. Running an engine
  consumes it. A machine dispatched purely on fuel price always looks cheaper
  than it is.

Cost is evaluated at the output the hour actually calls for, not at rating,
because the fuel curve's intercept is charged whether the machine is working
or idling: the same 100 kW diesel costs $0.276/kWh flat out and $0.346/kWh at
its 30% minimum. Ranking on the full-load figure would flatter it into running
when it should not.

Ties break on the static `DEFAULT_MERIT` table — which the assets module has
always carried and the dispatch never consulted — so a study with no prices
entered behaves exactly as before.

After the fix the crossover falls between $0.35 and $0.50/kWh, and the
optimiser selects a genset when, and only when, the economics justify it.

### 6A.2 `cycle_charging` was offered in the interface and did nothing

The name is in `STRATEGIES`, it is returned by `h_info` and it appears in the
strategy dropdown — and it never appeared anywhere in the dispatch loop.
Selecting it returned load-following's answer exactly.

This matters most for diesel: part load is where efficiency collapses, so the
strategy is to run a started machine hard and bank the surplus rather than
throttle back. Now implemented:

| Strategy | Genset | Run hours | Mean load | L/kWh |
|---|---|---|---|---|
| load following | 303,740 kWh | 7,662 | 40% | 0.322 |
| cycle charging | 352,700 kWh | 3,527 | 100% | 0.276 |

Same fuel cost, **half the run hours** — which more than doubles the engine's
life to overhaul. That is the real trade, and it was invisible.

`peak_shaving` had the same defect in milder form: it computed a target and
never used it. Now the grid is held below the target where the rest of the
design can cover the difference, falling back to import rather than failing to
serve the load.

### 6A.3 Fourteen technologies were unreachable

`api._build_components` hand-built five technologies with literal field reads.
`catalogue.build()` — a complete generic builder for all twenty, with field
mapping, constructor fixups and a cost library — was never called from the
request path.

So CSP, biomass, geothermal, fuel cell, CHP, both hydros, tidal, wave,
flywheel, supercapacitor, pumped hydro, CAES, hydrogen and thermal storage had
models, cost entries, translations and diagram symbols, and no way to select
any of them. A study cannot recommend what it cannot build.

Two supporting defects had to be fixed for them to work:

- `_derive_space()` mapped bounds for five named keys and gave everything else
  `(0, 0)`. That is not a neutral default — it pins a technology at zero units
  for the whole search, so the report reads "the optimiser did not choose one"
  when the optimiser was never allowed to. Bounds now come from the asset
  *class* (non-dispatchable / dispatchable / storage) through the generic
  interface.
- The catalogue offered `om_cost_per_year` for the genset, which charges O&M
  per running hour and whose constructor refuses the argument. The interface
  showed a box that crashed the build the moment anyone typed in it.

All 20 now build from their own defaults, and the component page is generated
from `ui_schema()` — which is what the catalogue's own docstring always said
should happen ("rather than hand-written per technology and then drifting out
of step with the model"). The five core technologies plus the grid stay on the
main page; the rest are behind **Show all technologies**.

### 6A.4 "Leave a cost at zero to use the regional library" was not true

`costs.py` is 506 lines with 11 regions and 19 currencies, and it had **no
effect on any sizing result**.

An interface sends a number for every field it shows, so a cost the user left
blank arrives as an explicit `0.0`, not as an absence. `build()` then called
`kwargs.setdefault("capital_cost", library_value)` — and `capital_cost` was
already in `kwargs` as `0.0`, so `setdefault` did nothing. Choosing a region
changed the cost browser and nothing else.

Fixed by clearing zero-valued cost fields before applying the library. A cost
you type is never overwritten; a cost you leave at zero is now genuinely
filled from the regional benchmark. The component page has a **Cost basis**
card (region / currency / low-typical-high) and every cost field the engine
accepts is on screen and editable.

### 6A.5 Also fixed in this round

- The wind reference height was `reference_height_m` in the model and
  catalogue, `ref_height_m` in the API. Only one name was read, so the
  setting was silently ignored and every wind speed sheared from the wrong
  height. Both names are now accepted.
- A conservation bug I introduced myself while writing the merit order —
  counting the minimum-load surplus twice — was caught within minutes by the
  existing `test_energy_is_conserved` on CHP. That test is doing its job.

---

## 7. Files changed

| File | Change |
|---|---|
| `ensys/timeseries.py` | new `shift_utc_to_local()` with fractional-offset interpolation |
| `ensys/resources/providers.py` | `_localise()` at the provider boundary; PVGIS/TMY rebuild GHI after shifting; UTC offset in cache keys |
| `ensys/resources/synth.py` | stamps `time_reference` so the synthetic year is not shifted twice |
| `ensys/optimise.py` | feasible-only selectors with tolerance + NPC tie-break; `highlight_notes()`; `end_of_life_check()`; passes the grid to the bounds |
| `ensys/optim/screen.py` | grid-aware interpolated headroom; peak-power ceiling; usable-energy storage bound; oversize test restricted to must-take generation; `must_take_mask()` |
| `ensys/economics.py` | LCOE double-count fixed; `load_growth_rate` |
| `ensys/models/grid.py` | two-state Markov outages; `mean_outage_hours`; `outage_statistics()` |
| `ensys/validate.py` | **new** — the whole plausibility layer |
| `ensys/api.py` | findings threaded through `import_series`, `fetch_resources`, `run_study`; daily-GHI correction; `highlight_notes`; `end_of_life`; MTTR; `_build_components` routed through the catalogue so all 20 technologies build; wind reference height accepts both names |
| `ensys/optimise.py` (2) | `_bounds_for()` gives bounds to any asset class, not just the named five |
| `web/index.html` | site-scale card; findings containers; load-growth field; kW warning on the load page |
| `web/app.js` | `renderFindings()`; site-scale presets; linked unit-size↔cost fields; highlight notes; MTTR field; component cards now generated from the engine catalogue (all 20 technologies, every cost field), cost-basis selector |
| `web/index.html` (2) | Cost basis card (region / currency / level); "Show all technologies" section |
| `web/style.css` | findings and highlight-note styling, theme-aware |
| `web/boot.js` | registers `validate` for the Pyodide build |
| `ensys/dispatch.py` | least-cost merit order across grid and all dispatchable plant; `cycle_charging` implemented; `peak_shaving` target applied; `_dispatchable_marginal_cost()` |
| `ensys/catalogue.py` | zero costs fall through to the regional library; `COST_FIELDS_NOT_APPLICABLE` / `cost_fields_for()` |
| `tests/test_plausibility.py` | **new** — 53 regression tests, one per defect above |

---

## 8. Re-run your case

Same 150 kW site, after the fixes:

```
bounds        PV 0–54 units, wind 0–18, battery 0–78   (was 0–45 / 0–25 / 0–65
                                                        on an understated yield)
recommended   750 kWp + 1,600 kWh   NPC 1.23 M   LCOE 0.106   RF 84%
cheapest      450 kWp                NPC 1.06 M   LCOE 0.092   RF 37%
greenest      1,275 kWp + 2,100 kWh  NPC 1.55 M   LCOE 0.133   RF 100%
most reliable 600 kWp + 700 kWh      NPC 1.14 M   LCOE 0.098   RF 59%
```

And the same site entered as a house (3 kW peak, house-scale units):

```
recommended   9.5 kWp + 22.5 kWh   NPC 13,961   LCOE 0.093   RF 91%
```

Which is a house.
