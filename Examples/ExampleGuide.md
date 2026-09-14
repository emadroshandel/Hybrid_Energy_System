# Worked examples

Three complete studies, ready to open. Each is a plain JSON file written by
the application's own **Save project** button, so anything you can do in the
interface you can also read, edit and share here.

## How to open one

Start HES, then press **Open project** at the top right of any page and
choose one of the files below. Opening a project restores the site, the
resource settings, the demand, the cost basis, every component field, the
economics and the search settings; it then retrieves the resource data and
rebuilds the demand profile for you and stops on the **Optimise** page.
Press **Run study** there.

Nothing in these three needs an internet connection. All of them use the
*Monthly averages* resource mode with twelve monthly irradiation and
temperature values, and a synthetic demand profile generated from a peak and
a load factor, so they run identically on a machine with no network and in
the browser-only version on GitHub Pages.

## The files

| File | What it is | Demand | Technologies |
|---|---|---|---|
| `01_house_rooftop.json` | A single dwelling in Shiraz with a grid connection | 5 kW peak, 0.35 load factor, ≈15 MWh/yr | PV + battery + grid |
| `02_grid_tied_commercial.json` | A shop, clinic or small office on the same site | 100 kW peak, 0.45 load factor, ≈394 MWh/yr | PV + battery + grid |
| `03_islanded_minigrid.json` | The same building supplied as an island | 100 kW peak, 0.45 load factor, ≈394 MWh/yr | PV + battery + diesel |

### 01 — Rooftop PV and battery for a dwelling

The smallest useful study, and the one to run first: it shows what a correct
answer looks like at household scale. With the 0.5 kWp module block and the
2.5 kWh battery block the search returns a system of a few kilowatts and a
few tens of kilowatt-hours — not megawatts. If a house study ever comes back
in MWp, the demand profile is the first thing to check, not the optimiser.

### 02 — Grid-connected commercial building

The first scenario of the tutorial. The grid is present and reliable, so
reliability is never binding and the trade-off that matters is cost against
renewable share. Look at the shape of the front: the last few percentage
points of renewable fraction are bought with storage, and storage is the
expensive end of the curve.

### 03 — Islanded mini-grid with diesel

The second scenario of the tutorial. The grid card is switched off, so every
hour must be met from PV, the battery or the generator, and the dispatch
decides between them by marginal cost each hour. This is the example to open
if you want to see the generator actually run; it is also the one where the
operating strategy changes the answer, so try it with *Load following* and
then with *Cycle charging* and compare the running hours.

## Making your own

Set a study up in the interface, then press **Save project (JSON)** on the
Report page. Two notes on the format:

* A demand profile loaded **from a file** cannot be stored inside a project —
  no web page is permitted to hand a file back to a file input. Projects
  saved in that mode restore everything else and ask for the CSV again. The
  examples here all use synthetic profiles for that reason.
* Only the technologies that are switched on, plus the five sizable ones and
  the grid, are written. A technology the file does not mention is left as
  the page has it, which is off.
