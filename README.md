**Hybrid renewable energy system sizing, from geographic resource data to a single-line diagram.**

Give it a location and an hourly load profile. It fetches the solar and wind
resource for that point, searches for the Pareto-optimal combination of PV,
wind, battery, EV charging, generator and grid capacity, then works the chosen
design up into a full electrical specification — inverters, string layouts,
cables, protection, isolation — and draws it.

**▶ Live demo — no installation:** https://emadroshandel.github.io/Hybrid_Energy_System/

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![Dependencies: none](https://img.shields.io/badge/dependencies-none-brightgreen.svg)](requirements.txt)
[![Tests](https://img.shields.io/badge/tests-206%20passing-brightgreen.svg)](tests/)
[![Technologies](https://img.shields.io/badge/technologies-20-0f766e.svg)](#13-twenty-technologies-four-behaviours-one-loop)

![The Pareto front for a 300 kW commercial site in Shiraz](docs/images/hero.png)

<sub>Sixteen designs that cannot be improved on one objective without giving something up on another — the output of a 30-second study on a 300 kW commercial site.</sub>

*Engineering and methods by **Emad Roshandel**. Software and interface by
**Claude (Opus)**. What that division means, and why it is worth stating, is
in [Authorship](#authorship).*

Python 3.9+, **standard library only**. No installation, no dependencies, no
build step. It runs as a desktop window, a local server, over your network, or
entirely client-side in a browser through Pyodide. 

---

## Contents

- [Install](#install)
- [Quick start](#quick-start)
- [Worked examples](#worked-examples)
- [What it does](#what-it-does)
- **[Part 1 — The concepts](#part-1--the-concepts)** — what hybrid sizing is, and why it is done this way
  - [1.1 What "sizing" actually means](#11-what-sizing-actually-means)
  - [1.2 Why the answer is a curve, not a number](#12-why-the-answer-is-a-curve-not-a-number)
  - [1.3 Twenty technologies, four behaviours, one loop](#13-twenty-technologies-four-behaviours-one-loop)
  - [1.4 The money: NPC and LCOE](#14-the-money-npc-and-lcoe)
  - [1.5 The service: LPSP and the four "renewable" numbers](#15-the-service-lpsp-and-the-four-renewable-numbers)
  - [1.6 Flexible demand: six ways to charge the same cars](#16-flexible-demand-six-ways-to-charge-the-same-cars)
  - [1.7 From kilowatts to copper](#17-from-kilowatts-to-copper)
- **[Part 2 — Using the software](#part-2--using-the-software)** — a page-by-page walkthrough
- **[Part 3 — A worked example, end to end](#part-3--a-worked-example-end-to-end)**
- **[Part 4 — Under the hood](#part-4--under-the-hood)** — layout, API, extending it
- [Relationship to the MATLAB study](#relationship-to-the-matlab-study)
- [The tutorial](#the-tutorial)
- [Validation and tests](#validation-and-tests)
- [Running it online](#running-it-online)
- [Limits worth knowing](#limits-worth-knowing)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [Authorship](#authorship)
- [Citation](#citation)
- [Licence and attribution](#licence-and-attribution)

---

## Install

There is nothing to install. The engine, the hourly dispatch, the optimisers,
the server, the charts and the report generator are written against the Python
standard library, so a clean Python 3.9 or newer is the whole requirement.

```bash
git clone https://github.com/emadroshandel/Hybrid_Energy_System.git
cd Hybrid_Energy_System
python server.py
```

Two optional extras, and the program says so when either is missing rather
than failing:

```bash
pip install numpy       # faster on large studies; identical results
pip install pywebview   # the native desktop window used by desktop.py
```

On Windows nothing needs a terminal at all: **`START_EnerSys.bat`** opens the
browser application and **`Run EnerSys (desktop).bat`** opens the native
window. If your security software blocks batch files, double-click
**`START_EnerSys.py`** instead. On Linux and macOS, `./run.sh`.

If anything is wrong with the installation, `python doctor.py` names it:

```bash
python doctor.py            # full report
python doctor.py --quiet    # only problems; exit code 1 if any
```

---

## Quick start

```bash
python server.py                 # localhost, opens a browser
python server.py --network       # also reachable from your LAN
python server.py --port 8080     # pick the port
python desktop.py                # native window (needs pywebview)
python -m unittest discover -s tests   # 206 regression tests
```

On Windows, `START_EnerSys.bat` does all of this from a double-click: it finds
Python, runs `doctor.py`, starts the server and keeps the window open so an
error is on screen rather than gone with a console that closed itself.

`--network` prints every address the machine is reachable on and warns that
there is no authentication — appropriate for a design office, not for the open
internet.

**No network at all?** Open `web/index.html` directly. The page loads Pyodide
and runs the identical engine in the browser. And with no internet either,
twelve monthly irradiation totals are enough to get a full study — see
[step 1](#step-1--site) below.

---

## Worked examples

Three complete studies ship in [`examples/`](examples/). Press **Open project**
at the top right of any page and choose one. Opening a project restores the
site, the resource settings, the demand, the cost basis, every component field,
the economics and the search settings, then fetches the resource and rebuilds
the demand for you and stops on the Optimise page. Press **Run study**.

| File | What it is | Demand | Technologies |
|---|---|---|---|
| `01_house_rooftop.json` | A single dwelling in Shiraz with a grid connection | 5 kW peak, ≈15 MWh/yr | PV + battery + grid |
| `02_grid_tied_commercial.json` | A shop, clinic or small office on the same site | 100 kW peak, ≈394 MWh/yr | PV + battery + grid |
| `03_islanded_minigrid.json` | The same building supplied as an island | 100 kW peak, ≈394 MWh/yr | PV + battery + diesel |

None of them needs an internet connection: each carries twelve monthly
irradiation and temperature values and generates its own demand profile, so
they behave identically on an offline machine and in the browser-only version.
Start with the house — it is the shortest run, and it shows what a correct
answer looks like at household scale. If a house study ever comes back in MWp,
the demand profile is the first thing to check.

Your own studies save the same way: **Save project (JSON)** on the Report page.
See [`examples/README.md`](examples/README.md) for the two limits of the
format.

---

## What it does

**Resource data.** Global Solar Atlas for long-term averages, PVGIS (TMY or a
specific year), NASA POWER, Open-Meteo/ERA5, and Renewables.ninja. Responses are
cached on disk. The Atlas figures are always fetched alongside the hourly data
as an independent cross-check: if the hourly year's annual GHI disagrees with
the long-term average by more than 10%, the report says so rather than
proceeding quietly.

**Technologies.** Twenty, in seven groups, all sharing one dispatch loop:

| Group | Technologies |
|---|---|
| Solar | PV (fixed, single- and two-axis, bifacial, floating), CSP with thermal storage |
| Wind | Onshore, offshore and small wind, with power-curve or cubic models |
| Hydro | Run-of-river, reservoir with pondage |
| Marine | Tidal stream, wave energy |
| Dispatchable | Diesel and gas gensets, biomass/biogas, CHP, geothermal, fuel cell |
| Storage | Lithium (NMC, LFP, LTO), sodium-ion, lead-acid, nickel-iron, flow, pumped hydro, flywheel, supercapacitor, compressed air, thermal, hydrogen |
| Flexible load | EV fleets, six charging scenarios, five archetypes |

**Costs, worldwide.** Thirty-four technologies benchmarked across eleven regions
and nineteen currencies, every figure carrying its source and year. They are
published benchmarks, not quotations — installed cost varies by more than a
factor of two between comparable projects — so the engine records which numbers
came from the library and which you supplied, and the report shows the
difference.

**Sizing.** A Pareto front across whichever objectives you choose — net present
cost, LCOE, loss of power supply probability, renewable fraction, emissions,
capital — subject to hard constraints, with a choice of three search
algorithms. Decision variables are integer unit counts, because you buy three
turbines, not 5.7 MW of turbine.

**Electrical design.** Distribution voltage chosen from the plant rating rather
than assumed. Inverter rating from the actual clipping curve rather than a
rule-of-thumb DC/AC ratio. String layouts checked against both the cold-day
open-circuit maximum and the hot-day MPPT minimum. Conductors sized against
ampacity, voltage drop and short-circuit withstand, with the governing
criterion named. Protection coordinated so both IEC 60364-4-43 conditions hold.

**Output.** Single-line and three-line diagrams as themed SVG with IEC 60617
symbols, generated from the same design record as the schedules, so the drawing
cannot drift out of step with the numbers. A self-contained HTML report in
English or Persian.

**Persian.** A full translation, not a veneer: 197 strings at 100% coverage,
right-to-left layout, Persian-Indic digits with Arabic separators (۱٬۲۳۴٫۵),
and Iranian engineering terminology — کلید مدار for circuit breaker, دیاگرام
تک‌خطی for single-line diagram, هزینه تراز شده انرژی for LCOE. Loanwords already
used by Iranian engineers (اینورتر, الکترولایزر) are kept rather than replaced
by coinages nobody says.

---
