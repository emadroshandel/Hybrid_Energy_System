# Disclaimer

HES is an engineering aid, not an authority.

A sizing result is a statement about a model, not about a site. Every number
it produces is conditional on what it was given: the demand profile, the
resource data, the equipment parameters, the prices and the discount rate.
Change the load by a factor of two and the answer changes by roughly a factor
of two; it will not warn you that the load was wrong, only that it looks
unusual.

Four things in particular deserve suspicion before anything here is built.

* **The demand profile.** This is the single largest influence on the answer,
  and the easiest thing to get wrong by an order of magnitude. A synthetic
  profile built from a peak and a load factor is a placeholder for a measured
  one, never a substitute. Check the annual energy against a bill.
* **The resource data.** Satellite-derived irradiation carries a bias of a
  few per cent at best, and considerably more in coastal, mountainous and
  dusty locations. Wind is worse: a hub-height speed extrapolated from a
  10 m reanalysis series is an estimate of an estimate, and a wind project
  sized from it has not been assessed. Measure.
* **The prices.** Capital costs, fuel prices and tariffs move faster than any
  library shipped with a program. The regional cost library here is a
  starting point for a screening study; a decision needs quotations.
* **The dispatch.** One year of hourly simulation with perfect foresight of
  nothing is a reasonable model of a rule-based controller. It is not a model
  of a real plant with forecast error, communications faults, maintenance
  outages, or an operator.

Before anything computed here is built:

* the demand profile must come from measurement, over a period long enough to
  contain the seasonal extremes;
* the solar and wind resource must be verified against ground measurement, or
  the design must carry the uncertainty explicitly;
* the electrical design — conductors, protection, earthing, protection
  coordination, and the grid connection agreement — must be carried out
  separately; the single-line and three-line diagrams this program draws are
  a sizing aid, not a construction drawing;
* the design must be reviewed and approved by an engineer competent in the
  applicable standards and qualified in the relevant jurisdiction.

The plausibility checks built into the program are a floor, not a ceiling.
They catch inputs and results that are outside published ranges. Passing
every check means nothing was obviously wrong; it does not mean the study is
right.

The authors accept no liability for any use of this software. See the LICENSE
file: the software is provided "as is", without warranty of any kind.
