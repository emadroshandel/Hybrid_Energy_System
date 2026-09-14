# Publishing HES to GitHub and GitHub Pages

Everything needed is already in this folder: `.github/workflows/pages.yml` (the
deploy workflow), `.github/workflows/tests.yml` (the test workflow),
`.nojekyll`, `.gitignore`, `LICENSE`, and the root `index.html` that forwards
to `web/`.

> **First, check the workflows are actually there.** Open `.github\workflows\`
> and confirm it holds `pages.yml` and `tests.yml`. Windows hides folders
> beginning with a dot from some file pickers, and remote tools are not
> permitted to write into `.github\workflows\` at all — a deliberate safety
> boundary, since that folder is executable CI configuration. If the folder or
> either file is missing, create the folders and save the two files there by
> hand; they were delivered alongside this document.

---

## 1. Create the repository on GitHub

Go to https://github.com/new and create an **empty** repository — no README,
no .gitignore, no licence (this folder already has all three).

Name it **`Hybrid_Energy_System`** if you want the demo URL in the README to be correct:

    https://emadroshandel.github.io/Hybrid_Energy_System/

If you choose a different name, edit the two demo links at the top of
`README.md`, the `git clone` line under *Install*, the BibTeX `url`, and the
`repository-code` line in `CITATION.cff`.

> **Why the project is not called `EnerSys`.** `EnerSys` is a listed
> company (NYSE: ENS) selling industrial batteries and stored-energy systems
> — the same field. A repository or a program under that name is invisible:
> every search for it returns them. The name was changed everywhere before
> the first release rather than after somebody had linked to it.

## 1a. Fill in the About panel

Press the gear beside **About** on the repository page. This is the text that
appears under the title, in GitHub search results, and in every link preview,
so it is worth more than its size suggests.

**Description:**

> Multi-objective sizing of hybrid renewable energy systems — PV, wind,
> storage, diesel and grid dispatched hour by hour over a full year, returning
> a Pareto front rather than a single design. Python, standard library only,
> runs in the browser.

**Website:** `https://emadroshandel.github.io/Hybrid_Energy_System/`

**Topics** — these drive GitHub's own search harder than the README does:

```
renewable-energy  microgrid  energy-storage  multi-objective-optimization
pareto-front  particle-swarm-optimization  photovoltaic  wind-energy
techno-economic-analysis  energy-system-modeling  pyodide  python
```

## 2. Check what is about to be published

Two things in this folder are worth a look before the first push.

**The spreadsheets.** `Data.xlsx`, `WindStates.xlsx` and `WindTen.xlsx` are
about 2.7 MB of working data from the original MATLAB study. They are not
used by the application. Either delete them, or move them into a `data/`
folder and say in the README what they are — a reader who finds three
unexplained workbooks in the root of a repository will assume the program
needs them.

**`Claude outputs/`.** Excluded by `.gitignore` already. Nothing to do, but
worth knowing it will not be published.

Everything else — `ensys/`, `web/`, `tests/`, `docs/`, `examples/`, the
launchers and the documents — is meant to be there.

## 3. Push this folder

Open a terminal **in this folder** (in File Explorer: type `cmd` in the
address bar and press Enter), then:

```
git init -b main
git add .
git commit -m "HES 0.1.0 - multi-objective sizing of hybrid renewable energy systems"
git remote add origin https://github.com/emadroshandel/Hybrid_Energy_System.git
git push -u origin main
```

If `git` is not recognised, install it from https://git-scm.com/download/win
and reopen the terminal.

`__pycache__/`, `startup_log.txt`, `outputs/` and `Claude outputs/` are
excluded automatically by `.gitignore`.

## 4. Turn on Pages

The workflow asks for `enablement: true`, so on the first push it switches
Pages on by itself and you should not have to touch this. Check
Settings → Pages → **Build and deployment** afterwards and confirm it says
**Source: GitHub Actions**.

If the first run failed with

    Get Pages site failed. Please verify that the repository has Pages
    enabled and configured to build using GitHub Actions

then Pages was off and the workflow could not turn it on — set
**Source: GitHub Actions** by hand and re-run the job from the Actions tab.
That message is the only thing this error ever means; it is not a problem
with the site.

> Alternative, if you prefer no workflow at all: set **Source: Deploy from a
> branch**, branch `main`, folder `/ (root)`. That also works — `.nojekyll`
> is there for it.

## 5. Watch it deploy

The **Actions** tab shows two runs: *Tests* (three or four minutes, the full
207-check suite on Linux and Windows, Python 3.9 and 3.12) and *Deploy
HES to GitHub Pages* (about a minute). When Pages goes green the site is
at

    https://<your-username>.github.io/<repo-name>/

The root page forwards to `web/`, which finds no server, and starts the
Python engine inside the visitor's browser through Pyodide.

## 6. What visitors see

The first load fetches Pyodide (about 10 MB) and shows a progress splash;
after that the browser caches it. Every calculation then runs locally in
their browser — no demand profile, no site and no result is uploaded
anywhere, because there is nowhere to upload it to.

Two differences from the local version, both worth knowing before you send
the link to anyone:

* **Resource data still needs the internet**, because PVGIS, Open-Meteo and
  NASA POWER are remote services. Some of them do not send the CORS headers a
  browser requires, so an online fetch can fail in the browser-only version
  while working perfectly from the local server. This is why every worked
  example in `examples/` uses the *Monthly averages* mode: those run with no
  network at all.
* **A long study is slower.** Pyodide runs at roughly a third to a half of
  native speed. A 24-particle, 40-iteration MOPSO run that takes seven
  seconds locally takes twenty or so in the browser. Reduce the iterations if
  a visitor's machine struggles.

---

## Updating later

```
git add .
git commit -m "what changed"
git push
```

Both workflows run again on every push to `main`.

If you change anything in `web/app.js` that touches the project format,
regenerate `examples/*.json` by opening each one, pressing **Save project
(JSON)**, and replacing the file. `tests/test_plausibility.py` fails if the
examples stop matching the format.

## Troubleshooting

**The Actions run fails with a permissions error.** Settings → Actions →
General → Workflow permissions → select *Read and write permissions*.

**The Tests workflow fails on Windows but passes on Linux.** Almost always a
path separator or a file encoding: open the log, find the failing test name,
and run that one test locally with `python -m unittest tests.test_engine -v`.

**The site loads but stays on the splash.** Open the browser console. If it
cannot reach `cdn.jsdelivr.net`, the network is blocking the CDN — the local
Windows launchers are unaffected.

**404 at the repository URL.** The deployment has not finished, or Pages is
still set to a branch that does not exist. Check the Actions tab first.

**The demo works but "Get resource data" fails.** Expected in browser-only
mode for some providers; see section 6. Switch to *Monthly averages*, or run
the local server.
