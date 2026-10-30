"""Citation and number checks for live answers (spec 7.4, rules 2-4 and 6)."""
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from .numbers import extract_figures, normalize_numbers
from .types import Citation, Passage

NOT_IN_MATERIAL = "Not in our material"
_MARK = re.compile(r"\s*\[(\d+)\]")
_END = re.compile(r"[.!?]+(?:\s*\[\d+\])*(?=\s|$)")
_ABBREV = {"e.g", "i.e", "etc", "vs", "mr", "mrs", "ms", "dr", "approx", "inc", "ltd", "no", "st"}
_CURRENCIES = {"USD", "INR", "EUR", "GBP"}


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


def _parse(fig: str) -> tuple[Decimal | str, str | None]:
    if "-" in fig:
        return fig, None
    num, _, unit = fig.partition(" ")
    return Decimal(num), unit or None


def _matches(fig: str, passage_figs: set[str], passage_currencies: set[str]) -> bool:
    v, u = _parse(fig)
    for p in passage_figs:
        pv, pu = _parse(p)
        if pv != v:
            continue
        if u is None or pu == u:
            return True
        if pu is None and (u not in _CURRENCIES or not passage_currencies or u in passage_currencies):
            return True  # bare passage number inherits the passage's heading currency
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
            pfigs = set().union(*(extract_figures(p.text) for p in known))
            cur = set(re.findall(r"\b(?:USD|INR|EUR|GBP)\b", " ".join(normalize_numbers(p.text) for p in known)))  # also headings like "(USD)"
            if not all(_matches(f, pfigs, cur) for f in figs):
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


_BLOCK = [
    ("currency", re.compile(r"\d+(?:\.\d+)? (?:USD|INR|EUR|GBP)\b")),
    ("percent", re.compile(r"\d+(?:\.\d+)? %")),
    ("rate", re.compile(r"\bper\s+(?:hour|day|week|month|year|person|seat)\b|\b(?:an?|each)\s+(?:hour|day|month)\b", re.I)),
    ("sla", re.compile(r"\b(?:SLA|response time|uptime|restore)\b.*\d|\d.*\b(?:SLA|uptime)\b", re.I)),
]


def check_fallback(answer: str, client_names: list[str]) -> FallbackCheck:
    t = normalize_numbers(answer)
    reasons = [name for name, rx in _BLOCK if rx.search(t)]
    reasons += [f"client_name:{c}" for c in client_names if c and c.lower() in answer.lower()]
    return FallbackCheck(not reasons, reasons)
