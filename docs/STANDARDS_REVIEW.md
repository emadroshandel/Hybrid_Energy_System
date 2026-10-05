# HES — review against the reference standards

October 2026. The engine and the web interface were checked clause by clause
against the documents in `References/`. Defects were fixed in place, and the
checks a reviewer would make with those standards in hand are now run on every
design and listed on the **Design** page (and in the exported report) under
**Standards compliance**.

All 256 tests pass: the 207 that existed before, and 49 in
`tests/test_standards.py`, each named after the clause it enforces. Section 5
covers the second pass, against the documents added in
`References/New References/`.

A copy of the code as it was before this review is in
`_backup_before_standards_review_2026-10-02/`.

---

## 1. Which references bear on a sizing tool

| Reference | Used for |
|---|---|
| IEC TS 62257-7-1:2010 (PV generators) | String voltage limits at the coldest ambient (4.1.9), string protection (5.3.4), string-cable current (Table 6), PV cable at ambient + 40 °C, 5 % array volt-drop guidance |
| IEC TS 62257-7-3:2008 (generator sets) | 50–80 % loading band (5.2.2), site derating for temperature, altitude, humidity (Table 1), alternator VA |
| IEC TS 62257-5:2015 (protection) | Battery DC fault current Ik = 10 × C (9.4.2.3); fault protection in islanded systems |
| IEC TS 62257-9-2:2006 (microgrids) | Voltage-drop limits (Table 1: 6 % main line, 1 % service) |
| IEC TS 62257-1/-2/-3/-4/-6/-9-x | Context for off-grid system selection, acceptance and O&M; no numerical rule the engine was breaking |
| BS EN IEC 62933-2-1:2018 | Round-trip efficiency at the point of connection incl. conversion (5.2.3); initial capacity planned for end-of-life (5.2.4); auxiliary power (5.2.6) |
| IEC TS 62933-3-2:2023, IEC 63056, IEC 62619, UL 9540:2023 | Storage design for power-intensive use; cell and system safety listing |
| IEEE Std 2030.2.1-2019 | PCS design: reactive capability, DC voltage window, unbalance ≤ 2 % |
| IEEE Std 1547.9-2022 | Storage interconnection: Category B reactive power (±0.44 pu ⇒ PF 0.90 at full output), Category III ride-through |
| NFPA 855:2026 | Table 1.3 thresholds by chemistry; 50 kWh groups 0.9 m apart (9.5.1); dwelling limits 20 kWh per unit, Table 15.5.2 by location, 600 kWh per property (Ch. 15) |
| Solar PV cable-sizing design report | (Np − 1)·Isc > I_MOD_MAX_OCPR rule; fuse window 1.5–2.4 Isc; transformer-limited LV fault S/(√3·V·Z); 3 % accumulated DC+AC drop; ≤ 1.5 % cable losses |
| IEC 61400-26-1 / IEC TS 61400-28 / IEC 61400-1 | Turbine availability; class versus hub-height mean wind speed |

---

## 2. Defects found and fixed

### 2.1 Every conductor was sized for a 0.4 s fault at the PCC — serious

`size_system` passed the PCC fault level (default 10 kA) with a fixed 0.4 s
to the adiabatic check of **every** circuit. 0.4 s is the IEC 60364-4-41
*disconnection* time for TN final circuits, a shock-protection limit, not the
time a breaker takes to clear a bolted fault.

| 8 kWp house, 10 kA PCC | Before | After |
|---|---|---|
| PV inverter AC cable | 50 mm² (governed by short circuit) | 1.5 mm² |
| Battery PCS AC cable | 50 mm² | 1.5 mm² |
| Main load feeder | 50 mm² | 1.5 mm² |

Fixed per IEC 60364-4-43 434.5: the conductor must withstand the
**let-through energy of its own protective device** (MCB energy-limiting
class 3; MCCB and fuses at half-cycle clearing; ACB with a 0.3 s short-time
delay), and the device's **breaking capacity** must cover the prospective
fault — an MCB is raised to an MCCB or ACB when it does not.

### 2.2 The fault level ignored where the fault current comes from

The prospective fault is now built from its sources (IEC TS 62257-7-1 4.1.8):
the network (through the step-up transformer's impedance for MV-connected
plant, as in the reference study), inverters at 1.5 pu, generators at
1/x″d. In an **islanded** system there is no network fault infeed at all;
the minimum fault current (battery inverter alone) is compared with each
breaker's magnetic trip, and a warning is raised when a breaker would not
trip instantaneously — the classic inverter-microgrid protection problem.

### 2.3 PV strings were a fifth shorter than they needed to be

The MPPT ceiling was compared with **Voc** at the coldest temperature; it
bounds **Vmp**. Voc is limited by the inverter's maximum DC voltage and, now
also, the module's maximum system voltage. The Vmp temperature coefficient is
estimated as γPmax − αIsc when the datasheet omits it, and the hot-cell
temperature comes from the hourly Faiman model rather than ambient + 25 °C.
Inverters above 150 kW are treated as 1500 V machines. Typical effect: 14 → 17
modules per string on a 1000 V inverter.

### 2.4 String fuses and string cables

The "three or more strings" shorthand was replaced with the module's own
rating, (Np − 1)·Isc > I_MOD_MAX_OCPR, with the fuse chosen inside
1.5–2.4 × Isc from the gPV series (the reference study's 9.24 A / 22-string
case now returns its 15 A fuse). The string cable is rated for the fuse, or
for 1.45·Isc·(Np − 1) when unfused (IEC TS 62257-7-1 Table 6), as EN 50618 PV
cable at ambient + 40 °C. Volt drop is checked at Imp, not at 1.25·Isc.

### 2.5 AC surge protector rated at 539 V on a 230/400 V system

`Uc` was computed as 1.1 × U × √2 / √3 × 1.5 — a peak value with an
unexplained factor. IEC 60364-5-53 Table 534.2: Uc ≥ 1.1·U0 (TN, TT), Uc ≥ U
(IT). A 230/400 V TN system now gets a 275 V device. The DC SPD is selected
at the coldest-day Voc from the standard Ucpv series.

### 2.6 PV energy was counted before the inverter

The 14 % `system_losses` is the PVWatts **DC** budget; the inverter was never
applied, so every PV kilowatt-hour reaching the load was 3–4 % high and the
hourly series was never clipped. The array now delivers AC (IEC 61724-1):
DC × inverter efficiency (default 0.97), clipped at kWp / DC-AC ratio
(default 1.2). Shiraz synthetic year: 1984 → 1904 kWh/kWp at the bus. The DC
series is kept for the inverter and string design.

### 2.7 Battery efficiency excluded the converter and auxiliaries

IEC 62933-2-1 defines round-trip efficiency at the point of connection,
through the PCS, with auxiliary consumption. The presets were cell figures:
LFP cycled at 94.1 %. It is now (cell × PCS)², 90.4 % with a 98 % PCS, plus
an auxiliary standing load (default 1 W/kWh) — both editable per project.

### 2.8 Storage was never checked at end of life

The end-of-life re-simulation degraded the PV and grew the load, but kept the
battery at nameplate. IEC 62933-2-1 5.2.4 requires the initial capacity to be
planned so the system meets its specification at end of service life. The
final-year check now runs the storage at its end-of-life capacity (80 %
lithium/lead-acid, 90–95 % flow).

### 2.9 A bank of generators behaved as one large machine

With N sets, the minimum load and the fuel intercept were applied to the whole
bank whenever any set ran: four 50 kW sets asked for 10 kW produced 60 kW and
burnt 20.8 L/h. Units are now committed one at a time: 15 kW, 5.2 L/h. Running
hours, O&M and overhaul life are counted per running unit.

### 2.10 Generators delivered nameplate output at any altitude and temperature

IEC TS 62257-7-3 Table 1 derating (2.5 %/5 °C above 25 °C, 3 %/300 m above
300 m, humidity above 60 % RH) is applied in the simulation, from the site's
elevation and the hottest 1 % of hours. Shiraz, 1500 m, 39 °C: 81 % of
nameplate. A user-entered factor overrides it.

### 2.11 The light-loading warning could never fire

`low_load_hours` counted hours below the minimum load the dispatch itself
enforces, so it was always zero. It now counts hours below 50 % of the running
sets, and a new check reports the share of running hours in the 50–80 % band
IEC TS 62257-7-3 5.2.2 recommends.

### 2.12 Wind turbines were available 100 % of the time

A default availability of 97 % (IEC 61400-26-1 range 95–98 %) and 2 %
electrical losses are applied; wake losses are an input. The turbine's IEC
61400-1 class is checked against the hub-height mean wind speed.

### 2.13 Minimum conductor size

1.0 mm² could be selected; IEC 60364-5-52 Table 52.2 sets 1.5 mm² copper.

### 2.14 A regression introduced and caught during this review

The first version of the end-of-life storage check wrote the faded battery
into the asset registry, which `SystemConfig.with_decision` shares with the
study's base system — every later design was evaluated with a battery 20 %
smaller. Found in the end-to-end run, fixed by building a separate registry,
and pinned by `TestEndOfLifeDoesNotLeak`.

---

## 3. New checks on every design

The **Standards compliance** card lists each check with its status (pass,
review, fail, note), the clause and the finding:

- Short-circuit breaking capacity and conductor withstand (IEC 60364-4-43)
- Fault protection in islanded operation (IEC TS 62257-5)
- PV string voltage window and string protection/cable (IEC 62548, IEC TS 62257-7-1)
- Accumulated DC + AC voltage drop ≤ 3 % (reference study / IEC TS 62257-7-1)
- Annual cable losses ≤ 1.5 % of energy carried
- Surge protection Uc (IEC 60364-5-53, IEC 61643-31)
- Storage: NFPA 855 threshold, 50 kWh groups, dwelling unit/location/property
  limits; UL 9540 listing; DC fault current; RTE, auxiliaries and EOL capacity
  per IEC 62933-2-1
- Reactive capability for Category B (IEEE 1547.9) — the battery PCS is now
  rated in kVA for PF 0.90 at full power when grid-connected
- Default IEEE 1547 voltage/frequency trip settings and ride-through category
  at the PCC
- Generator site derating, loading band and alternator VA (IEC TS 62257-7-3)
- Wind turbine class (IEC 61400-1)
- V2G/V2H as an ESS (NFPA 855 15.11, IEEE 1547.9)

## 4. Interface

The Design page has a **Design basis** panel that the engine previously never
received: LV voltage, frequency, earthing system, network fault level, design
temperatures (blank = taken from the hourly site data), whether the storage is
at a dwelling and where, the PV module datasheet, and the cable route lengths.
**Recalculate design** re-runs the electrical design without re-optimising.
These inputs are saved in and restored from project files. New component
inputs: PV inverter efficiency, DC/AC ratio and degradation; wind
availability, electrical and wake losses and IEC class; battery PCS
efficiency, auxiliary consumption, end-of-life capacity and DC voltage;
generator site derating and humidity.

## 5. Second pass — the "New References" folder

Three of the new PDFs (IEC 60909-0:2026, IEEE 1013-2019, IEEE 1562-2021) are
scans and were read by OCR. ISO/IEC 27000 (information security), the CT
application guide and the duplicated IEC 61400 parts do not bear on sizing.

### 5.1 Cable ratings — IEC 60364-5-52:2009 Annex B

**Defect.** Every conductor was rated from one table: method C values for
**PVC with two loaded conductors** (Table B.52.2), labelled XLPE, applied to
three-phase circuits, with aluminium as 0.78 × copper and grouping that
ignored a circuit's own parallel runs.

**Now.** Tables B.52.2–B.52.5 (methods A1, A2, B1, B2, C, D1, D2) and
B.52.10–B.52.13 (E, F) are transcribed in `ensys/sizing/iec60364_5_52.py`
and selected by method, insulation (PVC/XLPE), material and number of loaded
conductors, with ambient air (B.52.14), ground temperature (B.52.15), soil
resistivity (B.52.16) and grouping (B.52.17–B.52.19) corrections. Parallel
runs of one circuit, and identical circuits on one route, form the group. The
minimum aluminium size is 10 mm² (Table 52.2). The method is chosen on the
Design page; "Automatic" uses C up to 125 A and F (single-core trefoil on a
ladder) above.

### 5.2 Short-circuit currents — IEC 60909-0:2026

`ensys/sizing/iec60909.py` replaces the multiplier estimate with the
equivalent-voltage-source method: network feeder from its fault level (6.7),
network transformer with K_T (6.3.3), generators with K_G (6.8.2),
converters as current sources (5.2.3, 6.10.2), voltage factors c_max/c_min
from Table 1, conductor resistance at 20 °C for maxima and at the end-of-fault
temperature (Formula 50) for minima, PV and wind left out of the minimum
(7.1.3 d), and the peak current with κ (Formula 60). Results:

- maximum I″k for breaking capacity and conductor withstand, and the peak
  current against each device's making capacity (IEC 60947-2 n × Icu);
- **minimum line-to-earth current at the far end of every circuit**, checked
  against the IEC 60364-4-41 Table 41.1 disconnection time (0.4 s final
  circuits, 5 s otherwise). Islanded plants are where this fails, and the
  report says so with the remedy (RCDs, lower pickups, larger conductors).

### 5.3 PV — IEC 62548-1:2023 and IEC 61724-1:2021

- String-cable rating follows 62548-1:2023 Table 5: In + K_I·Isc·(Npo − 1),
  with **K_I = 1.25 × K_corr** (Annex F.4). Bifacial modules raise K_corr
  (F.7 b: I_SC,BNPI / I_SC,MOD) — a new datasheet field.
- The Voc design temperature follows F.1 b): the lowest temperature in hours
  with at least 100 W/m² of irradiance, unless the user enters a figure.
- New: the IEC 61724-1 yields and losses — Yr, Ya, Yf, capture loss Lc, BOS
  loss Ls, PR and the temperature-corrected PR′25°C (14.3.2.2) — as the
  expected values a monitoring system should be checked against.

### 5.4 Stand-alone systems — IEEE 1013-2019 and IEEE 1562-2021

Off-grid designs now get the deterministic check a reviewer would make:
battery capacity for the days of autonomy, adjusted for the usable window
(MDOD), end-of-life capacity, cold temperature (lead-acid) and a design
margin (IEEE 1013 6.3), and the array-to-load ratio in the worst month
against 1.1 (non-critical) or 1.3 (critical) (IEEE 1562 9.1). With a
generator present these are reported rather than failed.

### 5.5 EV charging — IEC 60364-7-722:2015

**Defect.** The EV feeder used a blanket 0.7 diversity for more than four
chargers. 722.311 requires a factor of 1 unless load control is available.
**Now:** 1 for uncontrolled charging; with load control (any managed
scenario) the feeder carries the highest simulated aggregate charging power
plus 10 %. Each connecting point gets its own dedicated final circuit
(722.314.01) and its own 30 mA RCD, type A with 6 mA DC detection or type B
(722.531.2.101).

### 5.6 Wind — IEC 61400-1:2019 and IEC 61400-12-1:2022

Air density is now computed **hour by hour** with Equation (12) (temperature,
pressure at hub height, humidity) instead of one annual mean; pitch-regulated
curves are corrected in wind speed (Eq. 14), stall-regulated in power
(Eq. 13). The class check uses Table 1 of 61400-1 (Vave 10 / 8.5 / 7.5 m/s).

### 5.7 Reliability — IEEE 1366-2022 and IEC 61703:2016

New indices for every design: interruptions per year (SAIFI), interruption
hours (SAIDI), mean interruption duration (CAIDI = mean down time), service
availability (ASAI), mean up time (IEC 61703 MUT), LOLE and EENS — on the
Results page and in the report.

### 5.8 Life-cycle cost — IEC 60300-3-3

**Defect.** The NPC had no disposal phase: no residual value for equipment
with life left at the end of the project, and no decommissioning cost. A
battery replaced in year 15 of 20 was charged in full and credited nothing.
**Now:** residual value (linear on remaining life, discounted) is credited by
default and a decommissioning cost can be entered as a fraction of capital
(Economics page).

## 6. Not done — recommended next

1. **Battery temperature and C-rate dependent ageing** (rainflow) in place of
   throughput + calendar life.
2. **Harmonics** (IEEE 519 / IEC 61000-3-12) at the PCC and the generator's
   capability to carry non-linear load (IEC TS 62257-7-3 5.2.2 note).
3. **Multi-year weather** (P50/P90) for resource uncertainty, in the spirit of
   IEC TS 61400-9's probabilistic approach.
4. **Grid-code presets** (EN 50549-1, AS/NZS 4777.2, Iranian TAVANIR rules)
   replacing the IEEE 1547 defaults on 50 Hz networks.
5. **Manufacturers' data** for converter fault current, MCB let-through
   and module temperature coefficients should replace the defaults on a
   final design; every default is stated in the compliance list.
6. **Line-to-earth faults in IT and TT systems** — the disconnection check
   models a TN loop; TT and IT are flagged for RCD / IMD design.
