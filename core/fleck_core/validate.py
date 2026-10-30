"""Citation and number checks for live answers (spec 7.4, rules 2-4 and 6)."""
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from .numbers import _PERIOD_RX, _PERIODS, _TIME, CODES, extract_figures, normalize_numbers
from .types import Citation, Passage

NOT_IN_MATERIAL = "Not in our material"
_MARK = re.compile(r"\s*\[(\d+)\]")
_END = re.compile(r"[.!?]+(?:\s*\[\d+\])*(?=\s|$)")
_ABBREV = {"e.g", "i.e", "etc", "vs", "mr", "mrs", "ms", "dr", "approx", "inc", "ltd", "no", "st"}


def split_sentences(text: str) -> list[str]:
    """Split on sentence ends; a trailing [n] marker stays with its sentence; decimals and abbreviations don't split."""
    out, start = [], 0
    for m in _END.finditer(text):
        word = re.search(r"(\S+?)$", text[start : m.start()])
        if m[0][0] == "." and word and (word[1].lower() in _ABBREV or re.fullmatch(r"(?:\w\.)+\w", word[1])):
            continue
        out.append(text[start : m.end()].strip())
        start = m.end()
    if text[start:].strip():
        out.append(text[start:].strip())
    return out


_FIGURE = re.compile(r"(-?[\d.]+)(?: ([^/\s]+)(?:/(\w+))?)?")
_CODES = re.compile(rf"\b(?:{CODES})\b")
_CODE_SET = set(CODES.split("|"))
_PER_NOUN = re.compile(r"\bper\s+(?:person\s+(?:per\s+)?)?([a-z]+)\b", re.I)
_NOT_NOUN = {"the", "this", "that", "our", "your", "its", "their", "a", "an", "cent", "annum"}
_PERIOD_WORD = re.compile(rf"\b(?:per|an?|each|every)\s+(?:person\s+(?:per\s+)?)?({_PERIOD_RX})\b|(?<=/)({_PERIOD_RX})\b", re.I)
# A clause number at the start of a line or passage ("7. Limitation", "2.17 OF THE") is not a figure, unless a
# scale, unit or currency word follows ("1.5 Million users").
_NOT_HEADING = "|".join([*_TIME, "million", "billion", "thousand", "hundred", "lakhs?", "crores?", "grand", "k", "mn", "bn", "per", "percent", CODES])
_HEADING_NUM = re.compile(rf"(?m)^\d{{1,2}}(?:\.\d+)*\.?\s+(?!(?i:{_NOT_HEADING})\b)(?=[A-Z])")


def _periods(norm: str) -> set[str]:
    known = {_PERIODS[(a or b).lower()] for a, b in _PERIOD_WORD.findall(norm)}
    # any other "per <noun>" ("per seat", "per sprint") is a rate period too and must be stated by the passage
    return known | {_PERIODS.get(n.lower(), n.lower()) for n in _PER_NOUN.findall(norm) if n.lower() not in _NOT_NOUN}


def _parse(fig: str) -> tuple[Decimal | str, str | None, str | None]:
    if "-" in fig[1:]:
        return fig, None, None  # ISO date
    m = _FIGURE.fullmatch(fig)
    return Decimal(m[1]), m[2], m[3]


def _matches(fig: str, passage_figs: set[str], currencies: set[str]) -> bool:
    v, u, per = _parse(fig)
    for p in passage_figs:
        pv, pu, pper = _parse(p)
        if pv != v:
            continue
        if u is None:
            return True
        if pu == u:
            if per is None or pper is None or per == pper:
                return True
        elif pu is None and u in _CODE_SET:
            # bare table cell: the currency comes from the passage's heading
            if not currencies or u in currencies:
                return True
    return False


@dataclass
class SentenceCheck:
    text: str
    display_text: str
    cited: list[int]
    figures: set[str]
    ok: bool
    reason: str | None = None


@dataclass
class LibraryCheck:
    ok: bool
    sentences: list[SentenceCheck]
    citations: list[Citation]
    kind: Literal["library", "reference", "not_in_material"]
    reasons: list[str] = field(default_factory=list)


@dataclass
class FallbackCheck:
    ok: bool
    reasons: list[str]


def check_library_answer(answer: str, passages: list[Passage]) -> LibraryCheck:
    if re.fullmatch(r"\W*NONE\W*", answer.strip(), re.I):
        return LibraryCheck(False, [], [], "not_in_material", ["none"])
    by_n = {p.n: p for p in passages}
    sentences: list[SentenceCheck] = []
    for s in split_sentences(answer):
        cited = [int(n) for n in re.findall(r"\[(\d+)\]", s)]
        bare = _MARK.sub("", s).strip()
        figs = extract_figures(bare)
        reason = None
        if any(n not in by_n for n in cited):
            reason = "citation_out_of_range"
        elif figs and not cited:
            reason = "figure_without_citation"
        elif figs:
            known = [by_n[n] for n in cited]
            texts = [_HEADING_NUM.sub("", p.text) for p in known]
            pfigs = set().union(*(extract_figures(t) for t in texts))
            norm = " ".join(normalize_numbers(t) for t in texts)
            cur = set(_CODES.findall(norm))  # also headings like "(USD)"
            per = _periods(norm)
            # any rate period in the sentence ("billed per year", "Per year, ...") must be one the passage states
            if not all(_matches(f, pfigs, cur) for f in figs) or (per and (aper := _periods(normalize_numbers(bare))) and not aper <= per):
                reason = "figure_not_in_cited_passage"
        sentences.append(SentenceCheck(s, NOT_IN_MATERIAL if reason else bare, cited, figs, reason is None, reason))
    reasons = [s.reason for s in sentences if s.reason]
    good = {n for s in sentences if s.ok for n in s.cited}
    cits = [Citation(**by_n[n].model_dump(include=set(Citation.model_fields))) for n in sorted(good)]
    ok = bool(cits) and "citation_out_of_range" not in reasons
    if not ok and not reasons:
        reasons = ["no_valid_citation"]
    kind = "not_in_material" if not ok else "reference" if all(c.kind == "reference" for c in cits) else "library"
    return LibraryCheck(ok, sentences, cits if ok else [], kind, reasons)


_DUR = r"\d+(?:\.\d+)?\s?(?:%|(?:h|hrs?|hours?|mins?|minutes?|days?|weeks?|months?|years?)\b)"
_PEOPLE = r"engineers?|developers?|people|consultants?|members?|designers?|architects?|testers?|analysts?|staff|ftes?|resources|specialists?|heads"
_BLOCK = [
    ("currency", re.compile(rf"\d+(?:\.\d+)? (?:{CODES})\b|[$€£¥₹]|\bRs\b\.?")),
    ("scaled_amount", re.compile(r"\d\s?(?:k|m|bn|mn)\b|\b(?:grand|lakhs?|crores?|millions?|billions?)\b", re.I)),
    ("nines", re.compile(r"\b\w+ nines\b", re.I)),
    ("availability_figure", re.compile(r"\b(?:availability|uptime|SLA)\b(?:\W+\w+){0,3}?\W+(?:\d+\.\d+|9\d\b)", re.I)),
    ("spoken_decimal", re.compile(r"\b(?:\d|zero|one|two|three|four|five|six|seven|eight|nine|ten|ty|hundred)\s+point\s+(?:\d|zero|one|two|three|four|five|six|seven|eight|nine)\b", re.I)),
    ("figures", re.compile(r"\b(?:two|three|four|five|six|seven|eight|nine|\d+)[- ]figures?\b", re.I)),
    ("percent", re.compile(r"\d+(?:\.\d+)? %")),
    ("rate", re.compile(r"\bper\s+(?:hour|day|week|month|year|person|seat|head)\b|\b(?:an?|each)\s+(?:hour|day|month)\b|\d\s?/\s?(?:hr|hour|day|month|mo|yr|year|wk|week)\b", re.I)),
    # an SLA word within five words of a duration or percentage ("response within 240 min"); plain "retry 3 times" is fine
    ("sla", re.compile(rf"\b(?:SLA|respon\w*|restor\w*|resolv\w*|uptime|availability|turnaround)\b(?:\W+\w+){{0,5}}?\W+{_DUR}|{_DUR}(?:\W+\w+){{0,5}}?\W+\b(?:SLA|respon\w*|restor\w*|resolv\w*|uptime|availability)\b", re.I)),
    ("team_size", re.compile(rf"\b(?:team|pod|squad)\s+of\s+\d+|\b\d+\s+(?:{_PEOPLE})\b", re.I)),
]
_NAME_STOP = {"group", "retail", "inc", "llc", "ltd", "corp", "company", "partners", "limited", "the", "and"}


def check_fallback(answer: str, client_names: list[str]) -> FallbackCheck:
    t = normalize_numbers(answer)
    reasons = [name for name, rx in _BLOCK if rx.search(t) or rx.search(answer)]
    for c in client_names:
        tokens = [w for w in re.findall(r"[^\W\d_]+", c) if len(w) >= 4 and w.lower() not in _NAME_STOP]
        if any(re.search(rf"\b{re.escape(w)}(?:['’]s)?\b", answer, re.I) for w in tokens + [c]):
            reasons.append(f"client_name:{c}")
    return FallbackCheck(not reasons, reasons)
