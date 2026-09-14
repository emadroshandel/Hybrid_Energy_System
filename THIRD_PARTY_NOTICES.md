# Third-party notices

HES redistributes no third-party code. The calculation engine, the web
server, the charts and the report generator are written against the Python
standard library and the browser's own APIs, with no bundled dependency of
any kind. What follows covers the services the program *calls*, and the one
library it loads at run time in browser mode.

---

## Run-time components

### Pyodide — loaded from a CDN in browser-only mode

The online version loads Pyodide (CPython compiled to WebAssembly) from
`cdn.jsdelivr.net` when the page finds no local server. Pyodide is released
under the Mozilla Public License 2.0. No Pyodide code is redistributed in
this repository, and the local desktop and server modes never fetch it.

Source: https://github.com/pyodide/pyodide

### NumPy — optional, never required

The engine runs on the standard library alone. NumPy, if it is present, is
used only to make large array operations faster, and the results are
identical without it. BSD 3-Clause License. Not redistributed here.

---

## Resource data services

HES retrieves solar and wind resource data from public services at the
moment you press *Get resource data*. It stores nothing of theirs in this
repository. **The licence of the data you retrieve is the licence of the
service you retrieved it from, and it travels with your study**, including
into any report or publication built on it. The provider and its licence are
recorded with every data set the program fetches and are printed on the Site
page and in the generated report.

### PVGIS — European Commission, Joint Research Centre

Hourly time series and typical meteorological years from the Photovoltaic
Geographical Information System. Free reuse with attribution; PVGIS asks that
work using its data acknowledge the source.

- https://re.jrc.ec.europa.eu/pvg_tools/en/
- Attribution: "Data source: PVGIS © European Union, 2001–2024."

### NASA POWER

Hourly meteorology from the Prediction of Worldwide Energy Resources project.
NASA data are in the public domain; attribution is requested, not required.

- https://power.larc.nasa.gov/

### Open-Meteo

Historical reanalysis (ERA5) through the Open-Meteo archive API. Released
under CC BY 4.0. Attribution is required.

- https://open-meteo.com/
- Attribution: "Weather data by Open-Meteo.com, CC BY 4.0."

### Global Solar Atlas — World Bank / Solargis

Long-term average GHI, DNI and PV output, used only as an independent
cross-check of the hourly series the program has just fetched. Released under
CC BY 4.0. Attribution is required.

- https://globalsolaratlas.info/
- Attribution: "© 2024 The World Bank, Solar resource data: Solargis."

### Renewables.ninja — non-commercial use only

Hourly wind speed and turbine power derived from MERRA-2. Requires a free
account and an API token, which you supply; HES ships none.

> **Renewables.ninja data are released under CC BY-NC 4.0. Non-commercial
> use only.** A study whose wind resource came from Renewables.ninja may not
> be used for a commercial purpose, and that restriction is not lifted by
> processing the data through this program. For commercial work, use PVGIS,
> Open-Meteo or measured data instead, or obtain a commercial licence from
> the provider of the underlying series.

- https://www.renewables.ninja/
- Pfenninger & Staffell (2016), *Energy* 114, 1251–1265.
- Staffell & Pfenninger (2016), *Energy* 114, 1224–1239.

---

## Standards and published methods

HES implements calculation methods published in the open literature and
in IEC and IEEE standards, and reproduces the numerical constants those
methods require — tabulated coefficients, model parameters and limit values —
each with a citation to the source it comes from. The principal ones are
listed in the *Licence and references* section of `README.md`.

It does **not** reproduce the text, figures, commentary or tables of any
standard, and it is **not** a substitute for them. Anyone using this software
for real design work must hold and read the applicable standards themselves.
No endorsement by IEC, IEEE, ISO or any other standards body is claimed or
implied.

## Cost library

The regional capital, replacement and operating costs shipped in
`ensys/costs.py` are order-of-magnitude figures compiled from public
benchmark reports — principally *IRENA Renewable Power Generation Costs*, the
*NREL Annual Technology Baseline* and IEA global weighted averages, each named
per entry with its reference year. They are indicative values for a screening
study. They are not quotations, they are
not current, and no figure in them is reproduced from a proprietary
database.
