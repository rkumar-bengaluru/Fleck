"""One canonical form for spoken and written figures (spec 7.4)."""
import re
from datetime import datetime

from text_to_num import alpha2digit

_CUR = {"$": "USD", "₹": "INR", "€": "EUR", "£": "GBP"}
_CODES = "USD|INR|EUR|GBP"
_WORDS = {"dollars": "USD", "dollar": "USD", "rupees": "INR", "rupee": "INR", "euros": "EUR", "euro": "EUR",
          "pounds": "GBP", "USD": "USD", "INR": "INR", "EUR": "EUR", "GBP": "GBP"}
_NUM = r"\d+(?:,\d{3})*(?:\.\d+)?"
_MONTHS = "January February March April May June July August September October November December".split()
_MON = "|".join(_MONTHS)
_TIME = "days?|weeks?|months?|years?|hours?|minutes?|mins?"
_DMY = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)? ({_MON}),? (\d{{4}})\b")
_MDY = re.compile(rf"\b({_MON}) (\d{{1,2}})(?:st|nd|rd|th)?,? (\d{{4}})\b")


def _iso(d: int, m: str, y: int) -> str:
    return datetime(y, _MONTHS.index(m) + 1, d).date().isoformat()


def _plain(n: str) -> str:
    return n.replace(",", "")


def normalize_numbers(text: str) -> str:
    t = alpha2digit(text, "en")
    t = _DMY.sub(lambda m: _iso(int(m[1]), m[2], int(m[3])), t)
    t = _MDY.sub(lambda m: _iso(int(m[2]), m[1], int(m[3])), t)
    t = re.sub(rf"([$₹€£])\s?({_NUM})", lambda m: f"{_plain(m[2])} {_CUR[m[1]]}", t)
    t = re.sub(rf"\b({_CODES})\s?({_NUM})", lambda m: f"{_plain(m[2])} {m[1]}", t)
    t = re.sub(rf"({_NUM})\s?({'|'.join(_WORDS)})\b", lambda m: f"{_plain(m[1])} {_WORDS[m[2]]}", t)
    t = re.sub(rf"({_NUM})\s?(?:%|per ?cent|percent)", lambda m: f"{_plain(m[1])} %", t)
    return re.sub(r"\d{1,3}(?:,\d{3})+(?!\d)", lambda m: _plain(m[0]), t)


_FIG = re.compile(rf"\d{{4}}-\d{{2}}-\d{{2}}|(\d+(?:\.\d+)?)(?: (%|(?:{_CODES}|{_TIME})\b))?")


def _canon_unit(u: str | None) -> str | None:
    return u.lower().rstrip("s") + "s" if u and u[0].islower() else u


def extract_figures(text: str) -> set[str]:
    out = set()
    for m in _FIG.finditer(normalize_numbers(text)):
        if m[1] is None:
            out.add(m[0])  # ISO date
            continue
        num = m[1].rstrip("0").rstrip(".") if "." in m[1] else m[1]
        u = _canon_unit(m[2])
        out.add(f"{num} {u}" if u else num)
    return out
