# -*- coding: utf-8 -*-
"""
Translation catalogue.

Persian is a first-class output language here, not a wrapper around English
strings. Reports for Iranian clients are written in Persian, read
right-to-left, and use Persian technical vocabulary — an interface that
translates the chrome but leaves "Net present cost" and "Loss of power
supply probability" in English is not usable in a submission.

Three things beyond word substitution are handled:

  Direction    `dir` is "rtl" for Persian, and the report and interface
               mirror their layout from it.

  Digits       Persian text conventionally uses Persian-Indic digits
               (۰۱۲۳۴۵۶۷۸۹). `localise_number` converts them, and keeps the
               decimal separator and thousands grouping correct for the
               locale rather than pasting Persian digits into an
               English-formatted number.

  Terminology  Technical terms follow Iranian engineering usage rather than
               literal translation, e.g. کلید مدار for circuit breaker and
               دیاگرام تک‌خطی for single-line diagram. Where a term is
               genuinely used in English by Iranian engineers — اینورتر,
               الکترولایزر — the loanword is kept, because inventing a
               Persian coinage nobody uses helps no one.

Missing keys fall back to English rather than showing a raw key, so a
partially translated build degrades legibly.
"""

from __future__ import annotations

LANGUAGES = {
    "en": {"label": "English", "native": "English", "dir": "ltr",
           "digits": "latin"},
    "fa": {"label": "Persian", "native": "فارسی", "dir": "rtl",
           "digits": "persian"},
}

PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"

STRINGS = {
    # ================================================== application chrome
    "app.title": {
        "en": "HES — Hybrid Energy System Sizing",
        "fa": "انرسیس — طراحی و تعیین اندازه سامانه‌های ترکیبی انرژی",
    },
    "app.tagline": {
        "en": "Hybrid system sizing", "fa": "تعیین اندازه سامانه ترکیبی",
    },
    "nav.site": {"en": "Site", "fa": "موقعیت"},
    "nav.load": {"en": "Load", "fa": "بار"},
    "nav.components": {"en": "Components", "fa": "تجهیزات"},
    "nav.economics": {"en": "Economics", "fa": "اقتصادی"},
    "nav.optimise": {"en": "Optimise", "fa": "بهینه‌سازی"},
    "nav.results": {"en": "Results", "fa": "نتایج"},
    "nav.design": {"en": "Design", "fa": "طراحی"},
    "nav.diagrams": {"en": "Diagrams", "fa": "دیاگرام‌ها"},
    "nav.report": {"en": "Report", "fa": "گزارش"},
    "btn.next": {"en": "Next", "fa": "بعدی"},
    "btn.back": {"en": "Back", "fa": "قبلی"},
    "btn.run": {"en": "Run study", "fa": "اجرای مطالعه"},
    "btn.generate": {"en": "Generate report", "fa": "تولید گزارش"},
    "btn.download": {"en": "Download", "fa": "دانلود"},

    # ============================================================ location
    "loc.latitude": {"en": "Latitude", "fa": "عرض جغرافیایی"},
    "loc.longitude": {"en": "Longitude", "fa": "طول جغرافیایی"},
    "loc.elevation": {"en": "Elevation", "fa": "ارتفاع از سطح دریا"},
    "loc.terrain": {"en": "Terrain", "fa": "نوع زمین"},
    "loc.site_name": {"en": "Site name", "fa": "نام سایت"},
    "loc.timezone": {"en": "UTC offset", "fa": "اختلاف با زمان جهانی"},

    # ============================================================ resource
    "res.title": {"en": "Resource data", "fa": "داده‌های منابع انرژی"},
    "res.ghi": {"en": "Global horizontal irradiance",
                "fa": "تابش کل افقی"},
    "res.dni": {"en": "Direct normal irradiance", "fa": "تابش مستقیم عمودی"},
    "res.dhi": {"en": "Diffuse horizontal irradiance", "fa": "تابش پخشیده افقی"},
    "res.wind_speed": {"en": "Wind speed", "fa": "سرعت باد"},
    "res.temperature": {"en": "Air temperature", "fa": "دمای هوا"},
    "res.provider": {"en": "Data source", "fa": "منبع داده"},
    "res.synthetic": {"en": "Synthetic data", "fa": "داده مصنوعی"},
    "res.annual_irradiation": {"en": "Annual irradiation", "fa": "تابش سالانه"},

    # ================================================================ load
    "load.title": {"en": "Load profile", "fa": "پروفیل بار"},
    "load.peak": {"en": "Peak demand", "fa": "حداکثر تقاضا"},
    "load.average": {"en": "Average demand", "fa": "میانگین تقاضا"},
    "load.annual": {"en": "Annual demand", "fa": "مصرف سالانه"},
    "load.factor": {"en": "Load factor", "fa": "ضریب بار"},
    "load.base": {"en": "Base load", "fa": "بار پایه"},
    "load.duration_curve": {"en": "Load duration curve", "fa": "منحنی تداوم بار"},

    # ========================================================= technologies
    "tech.pv": {"en": "Solar PV", "fa": "فتوولتائیک خورشیدی"},
    "tech.pv_array": {"en": "PV array", "fa": "آرایه خورشیدی"},
    "tech.wind": {"en": "Wind turbine", "fa": "توربین بادی"},
    "tech.battery": {"en": "Battery storage", "fa": "ذخیره‌ساز باتری"},
    "tech.genset": {"en": "Diesel generator", "fa": "ژنراتور دیزلی"},
    "tech.grid": {"en": "Grid connection", "fa": "اتصال به شبکه"},
    "tech.ev_fleet": {"en": "EV charging", "fa": "شارژ خودروی برقی"},
    "tech.hydro": {"en": "Hydro", "fa": "برق‌آبی"},
    "tech.run_of_river_hydro": {"en": "Run-of-river hydro",
                                "fa": "برق‌آبی جریانی"},
    "tech.reservoir_hydro": {"en": "Reservoir hydro", "fa": "برق‌آبی مخزنی"},
    "tech.biomass": {"en": "Biomass", "fa": "زیست‌توده"},
    "tech.csp": {"en": "Concentrating solar power",
                 "fa": "نیروگاه خورشیدی متمرکز"},
    "tech.geothermal": {"en": "Geothermal", "fa": "زمین‌گرمایی"},
    "tech.tidal": {"en": "Tidal stream", "fa": "جریان جزر و مدی"},
    "tech.wave": {"en": "Wave energy", "fa": "انرژی امواج"},
    "tech.fuel_cell": {"en": "Fuel cell", "fa": "پیل سوختی"},
    "tech.chp": {"en": "Combined heat and power",
                 "fa": "تولید همزمان برق و حرارت"},
    "tech.pumped_hydro": {"en": "Pumped hydro storage",
                          "fa": "ذخیره‌سازی تلمبه‌ذخیره‌ای"},
    "tech.flywheel": {"en": "Flywheel", "fa": "چرخ طیار"},
    "tech.supercapacitor": {"en": "Supercapacitor", "fa": "ابرخازن"},
    "tech.caes": {"en": "Compressed air storage",
                  "fa": "ذخیره‌سازی هوای فشرده"},
    "tech.thermal_storage": {"en": "Thermal storage", "fa": "ذخیره‌ساز حرارتی"},
    "tech.hydrogen": {"en": "Hydrogen storage", "fa": "ذخیره‌سازی هیدروژن"},
    "tech.electrolyser": {"en": "Electrolyser", "fa": "الکترولایزر"},

    # ============================================================== metrics
    "metric.npc": {"en": "Net present cost", "fa": "ارزش فعلی خالص هزینه"},
    "metric.lcoe": {"en": "Levelised cost of energy",
                    "fa": "هزینه تراز شده انرژی"},
    "metric.capital": {"en": "Initial capital", "fa": "سرمایه‌گذاری اولیه"},
    "metric.annualised": {"en": "Annualised cost", "fa": "هزینه سالانه معادل"},
    "metric.lpsp": {"en": "Loss of power supply probability",
                    "fa": "احتمال قطع تأمین بار"},
    "metric.unmet": {"en": "Unserved energy", "fa": "انرژی تأمین‌نشده"},
    "metric.renewable_fraction": {"en": "Renewable fraction",
                                  "fa": "سهم انرژی تجدیدپذیر"},
    "metric.self_sufficiency": {"en": "Self-sufficiency", "fa": "خودکفایی"},
    "metric.self_consumption": {"en": "Self-consumption", "fa": "خودمصرفی"},
    "metric.curtailment": {"en": "Curtailment", "fa": "تولید هدررفته"},
    "metric.emissions": {"en": "Annual CO₂ emissions",
                         "fa": "انتشار سالانه دی‌اکسید کربن"},
    "metric.capacity_factor": {"en": "Capacity factor", "fa": "ضریب ظرفیت"},
    "metric.payback": {"en": "Payback period", "fa": "دوره بازگشت سرمایه"},
    "metric.irr": {"en": "Internal rate of return", "fa": "نرخ بازده داخلی"},
    "metric.npv": {"en": "Net present value", "fa": "ارزش فعلی خالص"},
    "metric.cycles": {"en": "Battery cycles per year",
                      "fa": "تعداد چرخه باتری در سال"},
    "metric.soc": {"en": "State of charge", "fa": "وضعیت شارژ"},
    "metric.dod": {"en": "Depth of discharge", "fa": "عمق دشارژ"},
    "metric.longest_shortfall": {"en": "Longest shortfall",
                                 "fa": "طولانی‌ترین قطعی"},
    "metric.peak_import": {"en": "Peak import", "fa": "حداکثر توان دریافتی"},
    "metric.peak_export": {"en": "Peak export", "fa": "حداکثر توان تزریقی"},

    # ============================================================ economics
    "econ.project_life": {"en": "Project life", "fa": "عمر پروژه"},
    "econ.discount_rate": {"en": "Discount rate", "fa": "نرخ تنزیل"},
    "econ.escalation": {"en": "Energy price escalation",
                        "fa": "نرخ رشد قیمت انرژی"},
    "econ.inflation": {"en": "Inflation rate", "fa": "نرخ تورم"},
    "econ.currency": {"en": "Currency", "fa": "واحد پول"},
    "econ.capital_cost": {"en": "Capital cost", "fa": "هزینه سرمایه‌ای"},
    "econ.replacement_cost": {"en": "Replacement cost", "fa": "هزینه تعویض"},
    "econ.om_cost": {"en": "Operation and maintenance",
                     "fa": "بهره‌برداری و نگهداری"},
    "econ.fuel_cost": {"en": "Fuel cost", "fa": "هزینه سوخت"},
    "econ.replacements": {"en": "Replacements", "fa": "تعداد تعویض"},
    "econ.breakdown": {"en": "Cost breakdown", "fa": "تفکیک هزینه‌ها"},

    # ============================================================== design
    "design.inverter": {"en": "Inverter", "fa": "اینورتر"},
    "design.pcs": {"en": "Power conversion system", "fa": "مبدل توان"},
    "design.transformer": {"en": "Transformer", "fa": "ترانسفورماتور"},
    "design.dc_ac_ratio": {"en": "DC/AC ratio", "fa": "نسبت توان DC به AC"},
    "design.clipping": {"en": "Clipping loss", "fa": "تلفات محدودسازی"},
    "design.strings": {"en": "Strings", "fa": "رشته‌ها"},
    "design.modules_per_string": {"en": "Modules per string",
                                  "fa": "تعداد ماژول در هر رشته"},
    "design.string_voltage": {"en": "String voltage", "fa": "ولتاژ رشته"},
    "design.mppt": {"en": "MPPT input", "fa": "ورودی ردیاب نقطه توان بیشینه"},
    "design.cable_schedule": {"en": "Cable schedule", "fa": "جدول کابل‌ها"},
    "design.protection_schedule": {"en": "Protection schedule",
                                   "fa": "جدول تجهیزات حفاظتی"},
    "design.isolation": {"en": "Isolation and safety",
                         "fa": "قطع‌کننده‌ها و ایمنی"},
    "design.circuit": {"en": "Circuit", "fa": "مدار"},
    "design.conductor": {"en": "Conductor size", "fa": "سطح مقطع هادی"},
    "design.parallel_runs": {"en": "Parallel runs", "fa": "تعداد رشته موازی"},
    "design.length": {"en": "Length", "fa": "طول"},
    "design.design_current": {"en": "Design current", "fa": "جریان طراحی"},
    "design.ampacity": {"en": "Current capacity", "fa": "ظرفیت جریان‌دهی"},
    "design.voltage_drop": {"en": "Voltage drop", "fa": "افت ولتاژ"},
    "design.governed_by": {"en": "Governed by", "fa": "معیار تعیین‌کننده"},
    "design.device": {"en": "Device", "fa": "تجهیز"},
    "design.rating": {"en": "Rating", "fa": "جریان نامی"},
    "design.curve": {"en": "Trip curve", "fa": "منحنی عملکرد"},
    "design.compliant": {"en": "Compliant", "fa": "منطبق"},
    "design.breaker": {"en": "Circuit breaker", "fa": "کلید مدار"},
    "design.mccb": {"en": "Moulded case circuit breaker",
                    "fa": "کلید اتوماتیک کامپکت"},
    "design.mcb": {"en": "Miniature circuit breaker", "fa": "کلید مینیاتوری"},
    "design.fuse": {"en": "Fuse", "fa": "فیوز"},
    "design.rcd": {"en": "Residual current device",
                   "fa": "کلید جریان نشتی"},
    "design.spd": {"en": "Surge protective device", "fa": "برق‌گیر"},
    "design.isolator": {"en": "Isolator", "fa": "کلید جداکننده"},
    "design.earth": {"en": "Protective earth", "fa": "اتصال زمین حفاظتی"},
    "design.copper_estimate": {"en": "Estimated conductor mass",
                               "fa": "جرم تقریبی هادی"},

    # ============================================================ diagrams
    "diagram.single_line": {"en": "Single-line diagram", "fa": "دیاگرام تک‌خطی"},
    "diagram.three_line": {"en": "Three-line diagram", "fa": "دیاگرام سه‌خطی"},
    "diagram.busbar": {"en": "AC busbar", "fa": "شینه جریان متناوب"},
    "diagram.load": {"en": "Load", "fa": "بار"},

    # ======================================================== EV scenarios
    "ev.scenario": {"en": "Charging scenario", "fa": "سناریوی شارژ"},
    "ev.uncontrolled": {"en": "Uncontrolled charging",
                        "fa": "شارژ کنترل‌نشده"},
    "ev.v1g_smart": {"en": "Smart unidirectional charging",
                     "fa": "شارژ هوشمند یک‌طرفه"},
    "ev.v2g": {"en": "Vehicle-to-grid", "fa": "خودرو به شبکه"},
    "ev.v2h": {"en": "Vehicle-to-home", "fa": "خودرو به ساختمان"},
    "ev.price_responsive": {"en": "Price-responsive charging",
                            "fa": "شارژ متناسب با قیمت"},
    "ev.scheduled": {"en": "Scheduled charging", "fa": "شارژ زمان‌بندی‌شده"},
    "ev.chargers": {"en": "Charge points", "fa": "تعداد نقاط شارژ"},
    "ev.departure_target": {"en": "Departure SOC target",
                            "fa": "شارژ هدف هنگام خروج"},
    "ev.missed_departures": {"en": "Departures below target",
                             "fa": "خروج‌های زیر حد هدف"},
    "ev.residential": {"en": "Residential", "fa": "مسکونی"},
    "ev.workplace": {"en": "Workplace", "fa": "محل کار"},
    "ev.depot": {"en": "Depot fleet", "fa": "ناوگان پایانه"},
    "ev.public_fast": {"en": "Public fast charging",
                       "fa": "شارژ سریع عمومی"},
    "ev.bus_fleet": {"en": "Bus fleet", "fa": "ناوگان اتوبوس برقی"},

    # ============================================================== report
    "report.title": {"en": "Hybrid energy system study",
                     "fa": "مطالعه سامانه ترکیبی انرژی"},
    "report.generated": {"en": "Generated", "fa": "تاریخ تولید"},
    "report.recommended": {"en": "Recommended system", "fa": "سامانه پیشنهادی"},
    "report.energy_balance": {"en": "Energy balance", "fa": "تراز انرژی"},
    "report.assumptions": {"en": "Assumptions and limitations",
                           "fa": "مفروضات و محدودیت‌ها"},
    "report.attention": {"en": "Items requiring attention",
                         "fa": "موارد نیازمند بررسی"},
    "report.component": {"en": "Component", "fa": "تجهیز"},
    "report.size": {"en": "Size", "fa": "ظرفیت"},
    "report.quantity": {"en": "Quantity", "fa": "مقدار"},
    "report.pareto": {"en": "Trade-off analysis", "fa": "تحلیل مصالحه"},
    "report.disclaimer": {
        "en": "This study is a design aid. It does not replace a qualified "
              "engineer's review, a site survey, or compliance sign-off "
              "against the local wiring rules.",
        "fa": "این مطالعه یک ابزار کمک‌طراحی است و جایگزین بررسی مهندس ذی‌صلاح، "
              "بازدید میدانی از سایت، یا تأییدیه انطباق با مقررات ملی "
              "سیم‌کشی نمی‌شود.",
    },
    "report.cost_caveat": {
        "en": "Cost figures are published benchmarks, not quotations. "
              "Replace them with real quotes before any commitment.",
        "fa": "ارقام هزینه، مقادیر مرجع منتشرشده هستند و حکم پیش‌فاکتور ندارند. "
              "پیش از هرگونه تعهد، آن‌ها را با استعلام واقعی جایگزین کنید.",
    },
    "report.synthetic_caveat": {
        "en": "This resource year is synthetic. It reproduces the correct "
              "monthly totals but not the real sequence of weather, so "
              "storage sizes derived from it will be optimistic.",
        "fa": "داده‌های منابع این مطالعه مصنوعی هستند. مجموع ماهانه صحیح است "
              "اما توالی واقعی شرایط جوی بازتولید نشده؛ بنابراین ظرفیت "
              "ذخیره‌ساز به‌دست‌آمده خوش‌بینانه خواهد بود.",
    },

    # ================================================================ units
    "unit.kw": {"en": "kW", "fa": "کیلووات"},
    "unit.kwh": {"en": "kWh", "fa": "کیلووات‌ساعت"},
    "unit.mwh": {"en": "MWh", "fa": "مگاوات‌ساعت"},
    "unit.kwp": {"en": "kWp", "fa": "کیلووات پیک"},
    "unit.kva": {"en": "kVA", "fa": "کیلوولت‌آمپر"},
    "unit.volt": {"en": "V", "fa": "ولت"},
    "unit.amp": {"en": "A", "fa": "آمپر"},
    "unit.mm2": {"en": "mm²", "fa": "میلی‌متر مربع"},
    "unit.metre": {"en": "m", "fa": "متر"},
    "unit.hour": {"en": "h", "fa": "ساعت"},
    "unit.year": {"en": "years", "fa": "سال"},
    "unit.degree": {"en": "°", "fa": "درجه"},
    "unit.celsius": {"en": "°C", "fa": "درجه سلسیوس"},
    "unit.tonne": {"en": "t", "fa": "تن"},
    "unit.percent": {"en": "%", "fa": "درصد"},
    "unit.per_kwh": {"en": "per kWh", "fa": "به ازای هر کیلووات‌ساعت"},
    "unit.per_year": {"en": "per year", "fa": "در سال"},

    # =============================================================== months
    "month.1": {"en": "January", "fa": "ژانویه"},
    "month.2": {"en": "February", "fa": "فوریه"},
    "month.3": {"en": "March", "fa": "مارس"},
    "month.4": {"en": "April", "fa": "آوریل"},
    "month.5": {"en": "May", "fa": "مه"},
    "month.6": {"en": "June", "fa": "ژوئن"},
    "month.7": {"en": "July", "fa": "ژوئیه"},
    "month.8": {"en": "August", "fa": "اوت"},
    "month.9": {"en": "September", "fa": "سپتامبر"},
    "month.10": {"en": "October", "fa": "اکتبر"},
    "month.11": {"en": "November", "fa": "نوامبر"},
    "month.12": {"en": "December", "fa": "دسامبر"},

    # ============================================================== common
    "common.yes": {"en": "Yes", "fa": "بله"},
    "common.no": {"en": "No", "fa": "خیر"},
    "common.review": {"en": "Review", "fa": "بررسی شود"},
    "common.total": {"en": "Total", "fa": "مجموع"},
    "common.none": {"en": "None", "fa": "ندارد"},
    "common.optional": {"en": "optional", "fa": "اختیاری"},
    "common.warning": {"en": "Warning", "fa": "هشدار"},
    "common.note": {"en": "Note", "fa": "توضیح"},
    "common.source": {"en": "Source", "fa": "منبع"},
}


class Translator:
    """
    Look up strings for one language.

    Falls back to English, then to the key itself, so a missing translation
    shows readable text rather than a raw identifier in a client report.
    """

    def __init__(self, lang="en"):
        if lang not in LANGUAGES:
            lang = "en"
        self.lang = lang
        self.meta = LANGUAGES[lang]

    @property
    def dir(self):
        return self.meta["dir"]

    @property
    def is_rtl(self):
        return self.meta["dir"] == "rtl"

    def __call__(self, key, default=None):
        return self.t(key, default)

    def t(self, key, default=None):
        entry = STRINGS.get(key)
        if not entry:
            return default if default is not None else key
        return entry.get(self.lang) or entry.get("en") or key

    def tech(self, technology):
        return self.t(f"tech.{technology}", technology.replace("_", " "))

    def metric(self, name):
        return self.t(f"metric.{name}", name.replace("_", " "))

    def month(self, m):
        return self.t(f"month.{int(m)}")

    def months(self):
        return [self.month(m) for m in range(1, 13)]

    def number(self, value, decimals=0, group=True):
        return localise_number(value, self.lang, decimals, group)

    def money(self, value, currency="USD", decimals=0):
        from .costs import CURRENCIES

        sym = CURRENCIES.get(currency, {}).get("symbol", currency)
        n = self.number(value, decimals)
        # In right-to-left text the symbol reads better after the number.
        return f"{n} {sym}" if self.is_rtl else f"{sym}{n}"

    def percent(self, fraction, decimals=1):
        return f"{self.number(fraction * 100.0, decimals)}٪" if self.is_rtl \
            else f"{fraction * 100.0:.{decimals}f}%"

    def catalogue(self):
        """The whole catalogue for this language, for the browser build."""
        return {
            k: (v.get(self.lang) or v.get("en")) for k, v in STRINGS.items()
        }


def localise_number(value, lang="en", decimals=0, group=True):
    """
    Format a number for a locale.

    Persian output uses Persian-Indic digits and the Arabic thousands
    separator. Formatting the number in English and then swapping digit
    glyphs gives the wrong separators, so the grouping is applied first and
    the separators substituted along with the digits.
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)

    if group:
        s = f"{v:,.{decimals}f}"
    else:
        s = f"{v:.{decimals}f}"

    if LANGUAGES.get(lang, {}).get("digits") != "persian":
        return s

    out = []
    for ch in s:
        if ch.isdigit():
            out.append(PERSIAN_DIGITS[int(ch)])
        elif ch == ",":
            out.append("٬")        # Arabic thousands separator
        elif ch == ".":
            out.append("٫")        # Arabic decimal separator
        elif ch == "-":
            out.append("−")
        else:
            out.append(ch)
    return "".join(out)


def to_latin_digits(text):
    """Convert Persian or Arabic digits back to Latin, for parsing input."""
    out = []
    for ch in str(text):
        i = PERSIAN_DIGITS.find(ch)
        if i < 0:
            i = ARABIC_DIGITS.find(ch)
        if i >= 0:
            out.append(str(i))
        elif ch == "٫":
            out.append(".")
        elif ch == "٬":
            out.append(",")
        else:
            out.append(ch)
    return "".join(out)


def get(lang="en"):
    return Translator(lang)


def available():
    return [
        {"code": k, "label": v["label"], "native": v["native"],
         "dir": v["dir"]}
        for k, v in LANGUAGES.items()
    ]


def coverage():
    """
    How complete each language is.

    Reported honestly rather than assumed: a half-translated report is worse
    than an English one, because the reader cannot tell which half they are
    missing.
    """
    out = {}
    total = len(STRINGS)
    for code in LANGUAGES:
        have = sum(
            1 for v in STRINGS.values() if v.get(code)
        )
        out[code] = {
            "translated": have, "total": total,
            "fraction": have / total if total else 0.0,
        }
    return out
