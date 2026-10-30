"""One canonical form for spoken and written figures (spec 7.4)."""
import re
from datetime import datetime
from decimal import Decimal

from text_to_num import alpha2digit

CODES = "USD|INR|EUR|GBP|JPY|CNY|RMB|CAD|AUD|CHF|SGD|AED|NZD|HKD|SEK|NOK|DKK|ZAR|BRL|MXN|KRW"
_SYMBOLS = {"$": "USD", "₹": "INR", "€": "EUR", "£": "GBP", "¥": "JPY"}
_WORDS = {"us dollars": "USD", "us dollar": "USD", "dollars": "USD", "dollar": "USD", "rupees": "INR", "rupee": "INR",
          "euros": "EUR", "euro": "EUR", "pounds": "GBP", "yen": "JPY"}
_WORD_RX = "|".join(sorted(_WORDS, key=len, reverse=True))
_NUM = r"(?:(?<![\w.])-)?\d+(?:\.\d+)?"
_SCALES = {"hundred": 100, "thousand": 10**3, "k": 10**3, "grand": 10**3, "lakh": 10**5, "lakhs": 10**5,
           "million": 10**6, "mn": 10**6, "m": 10**6, "crore": 10**7, "crores": 10**7, "billion": 10**9, "bn": 10**9}
_SCALE_RX = re.compile(rf"({_NUM})(?:\s?(hundred|thousand|lakhs?|million|crores?|billion|grand)|(k|mn|bn|m))\b", re.I)
_MONTHS = "jan feb mar apr may jun jul aug sep oct nov dec".split()
_MON = r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sept?(?:ember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?"
_DMY = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)? ({_MON})\.?,? (\d{{4}})\b")
_MDY = re.compile(rf"\b({_MON})\.? (\d{{1,2}})(?:st|nd|rd|th)?,? (\d{{4}})\b")
_NUMERIC_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
# unit word -> (canonical unit, factor): hours become minutes, weeks days, years months, so values compare directly
_TIME = {"h": ("min", 60), "hr": ("min", 60), "hrs": ("min", 60), "hour": ("min", 60), "hours": ("min", 60),
         "min": ("min", 1), "mins": ("min", 1), "minute": ("min", 1), "minutes": ("min", 1),
         "d": ("days", 1), "day": ("days", 1), "days": ("days", 1),
         "wk": ("days", 7), "wks": ("days", 7), "week": ("days", 7), "weeks": ("days", 7),
         "mo": ("months", 1), "mos": ("months", 1), "month": ("months", 1), "months": ("months", 1),
         "yr": ("months", 12), "yrs": ("months", 12), "year": ("months", 12), "years": ("months", 12)}
_PERIODS = {"hour": "hour", "hr": "hour", "day": "day", "week": "week", "wk": "week", "month": "month", "mo": "month",
            "year": "year", "yr": "year", "annum": "year"}
_PERIOD_RX = "hour|hr|day|week|wk|month|mo|year|yr|annum"
_PER = rf"(?:\s?(?:per|a|an|each|every|/)\s?(?:person\s(?:per\s)?)?({_PERIOD_RX})\b)"


def _fmt(d: Decimal) -> str:
    return format(d.normalize(), "f")


def _iso(d: int, m: int, y: int, orig: str) -> str:
    try:
        return datetime(y, m, d).date().isoformat()
    except ValueError:
        return orig


def _scale(t: str) -> str:
    return _SCALE_RX.sub(lambda m: _fmt(Decimal(m[1]) * _SCALES[(m[2] or m[3]).lower()]), t)


def _numeric_date(m: re.Match) -> str:
    a, b, y = int(m[1]), int(m[2]), int(m[3])
    if a > 12:
        return _iso(a, b, y, m[0])
    if b > 12:
        return _iso(b, a, y, m[0])
    return m[0]  # ambiguous d/m vs m/d: leave as is


def normalize_numbers(text: str) -> str:
    t = re.sub(r"(?<=\d),(?=\d{3}\b|\d{2},\d)", "", text)  # 6,900 and lakh grouping 5,00,000
    t = _scale(t)
    t = alpha2digit(t, "en")
    t = _scale(t)
    t = re.sub(r"\bminus\s+(?=\d)", "-", t, flags=re.I)
    t = _NUMERIC_DATE.sub(_numeric_date, t)
    t = _DMY.sub(lambda m: _iso(int(m[1]), _MONTHS.index(m[2][:3].lower()) + 1, int(m[3]), m[0]), t)
    t = _MDY.sub(lambda m: _iso(int(m[2]), _MONTHS.index(m[1][:3].lower()) + 1, int(m[3]), m[0]), t)
    t = re.sub(rf"([$₹€£¥])\s?({_NUM})", lambda m: f"{m[2]} {_SYMBOLS[m[1]]}", t)
    t = re.sub(rf"\bRs\.?\s?({_NUM})", lambda m: f"{m[1]} INR", t)
    t = re.sub(rf"\b({CODES})\s?({_NUM})", lambda m: f"{m[2]} {m[1]}", t)
    t = re.sub(rf"({_NUM})\s?({_WORD_RX})\b", lambda m: f"{m[1]} {_WORDS[m[2].lower()]}", t, flags=re.I)
    t = re.sub(rf"({_NUM})\s?(?:%|per ?cent|percent|pc)(?!\w)", lambda m: f"{m[1]} %", t, flags=re.I)
    return t


_UNIT_WORD = "|".join(sorted(_TIME, key=len, reverse=True))
_FIG = re.compile(
    rf"(?<![\w.])(?:\d{{4}}-\d{{2}}-\d{{2}}|(-?\d+(?:\.\d+)?)"
    rf"(?: (%)| ({CODES})\b(?:{_PER})?"
    rf"|(?:\s?\(\d+\))?(?:\s(?!(?:to|or|and|of|per|in|a|the)\b)[A-Za-z]+)?\s?({_UNIT_WORD})\b)?)", re.I)


def extract_figures(text: str) -> set[str]:
    out = set()
    for m in _FIG.finditer(normalize_numbers(text)):
        if m[1] is None:
            out.add(m[0])  # ISO date
            continue
        v = Decimal(m[1])
        if m[2]:
            out.add(f"{_fmt(v)} %")
        elif m[3]:
            per = _PERIODS[m[4].lower()] if m[4] else None
            out.add(f"{_fmt(v)} {m[3].upper()}" + (f"/{per}" if per else ""))
        elif m[5]:
            unit, factor = _TIME[m[5].lower()]
            out.add(f"{_fmt(v * factor)} {unit}")
        else:
            out.add(_fmt(v))
    return out
