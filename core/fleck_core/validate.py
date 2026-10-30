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


def _table_cells(text: str):
    """Yield (row_label, column_label, cell_text) for the data cells of markdown tables."""
    header = None
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            header = None
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if all(re.fullmatch(r":?-+:?", c) for c in cells if c):
            continue
        if header is None:
            header = [re.split(r"[(/]", c)[0].strip().lower() for c in cells]
            continue
        for i, c in enumerate(cells[1:], 1):
            if i < len(header):
                yield cells[0].lower(), header[i], c


_LABELLED = re.compile(r"(.+) · (.+?): (.+)")


def _line_cells(text: str):
    """Yield (row_label, column_label, cell_text) from chunk lines "<row> · <column>: <value>" (line 1 is the caption)."""
    for line in text.splitlines()[1:]:
        if m := _LABELLED.fullmatch(line.strip()):
            yield m[1].strip().lower(), re.split(r"[(/]", m[2])[0].strip().lower(), m[3].strip()


def _labels_ok(sentence: str, figs: set[str], known: list[Passage], cur: set[str]) -> bool:
    """A table row or column label in the sentence binds its figures to that row and column of the passage table."""
    low = sentence.lower()

    def hit(label: str) -> bool:
        return bool(label) and re.search(rf"\b{re.escape(label)}s?\b", low) is not None

    for p in known:
        cells = [*_table_cells(p.text), *_line_cells(p.text)]
        rows = {r for r, _, _ in cells if hit(r)}
        cols = {c for _, c, _ in cells if hit(c)}
        rows = {r for r in rows if not any(r != o and r in o for o in rows)}  # "senior engineer" beats "engineer"
        if not rows and not cols:
            continue
        table = set().union(*(extract_figures(c) for _, _, c in cells)) if cells else set()
        bound = set().union(*(extract_figures(c) for r, k, c in cells if (not rows or r in rows) and (not cols or k in cols)))
        if any(_matches(f, table, cur) and not _matches(f, bound, cur) for f in figs):
            return False
    return True


_PAST_WORK = re.compile(
    r"\bwe(?:'ve|\s+have)?\s+(?:did|done|delivered|built|helped|worked|cut|reduced|saved|launched|migrated|implemented|completed)\b"
    r"|\bcut\b.{0,40}\bin half\b|\bour (?:clients?|customers?|past|previous)\b|\b(?:for|with) (?:a|one of our) (?:client|customer)s?\b", re.I)
_FIRST_PERSON = re.compile(r"\b(?:we|our|ours|us|northbeam)\b", re.I)


def _past_work(text: str) -> bool:
    return bool(_PAST_WORK.search(text))


def _client_hits(text: str, client_names: list[str]) -> list[str]:
    hits = []
    for c in client_names:
        tokens = [w for w in re.findall(r"[^\W\d_]+", c) if len(w) >= 4 and w.lower() not in _NAME_STOP]
        if any(re.search(rf"\b{re.escape(w)}(?:['’]s)?\b", text, re.I) for w in tokens + [c]):
            hits.append(c)
    return hits


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


def check_library_answer(answer: str, passages: list[Passage], client_names: list[str] | None = None) -> LibraryCheck:
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
        elif not figs and not cited and (_past_work(bare) or _client_hits(bare, client_names or [])):
            reason = "uncited_claim"  # an uncited sentence still may not name a client or claim past work
        elif figs:
            known = [by_n[n] for n in cited]
            texts = [_HEADING_NUM.sub("", p.text) for p in known]
            pfigs = set().union(*(extract_figures(t) for t in texts))
            norm = " ".join(normalize_numbers(t) for t in texts)
            cur = set(_CODES.findall(norm))  # also headings like "(USD)"
            per = _periods(norm)
            # any rate period in the sentence ("billed per year", "Per year, ...") must be one the passage states
            if (not all(_matches(f, pfigs, cur) for f in figs) or not _labels_ok(bare, figs, known, cur)
                    or (per and (aper := _periods(normalize_numbers(bare))) and not aper <= per)):
                reason = "figure_not_in_cited_passage"
        sentences.append(SentenceCheck(s, NOT_IN_MATERIAL if reason else bare, cited, figs, reason is None, reason))
    reasons = [s.reason for s in sentences if s.reason]
    good = {n for s in sentences if s.ok for n in s.cited}
    cits = [Citation(**by_n[n].model_dump(include=set(Citation.model_fields))) for n in sorted(good)]
    ok = bool(cits) and "citation_out_of_range" not in reasons
    if not ok and not reasons:
        reasons = ["no_valid_citation"]
    kind = "not_in_material" if not ok else "reference" if all(c.kind == "reference" for c in cits) else "library"
    if not ok:  # the panel links "the closest document": the best-ranked cited passage, else passage 1
        in_range = sorted(n for s in sentences for n in s.cited if n in by_n)
        best = by_n[in_range[0]] if in_range else passages[0] if passages else None
        cits = [Citation(**best.model_dump(include=set(Citation.model_fields)))] if best else []
    return LibraryCheck(ok, sentences, cits, kind, reasons)


_DUR = r"\d+(?:\.\d+)?\s?(?:%|(?:(?:business|working|calendar)\s+)?(?:h|hrs?|hours?|mins?|minutes?|days?|weeks?|months?|years?)\b)"
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
    reasons += [f"client_name:{c}" for c in _client_hits(answer, client_names)]
    if _FIRST_PERSON.search(answer) or _past_work(answer):  # general knowledge may not speak for the firm
        reasons.append("firm_claim")
    return FallbackCheck(not reasons, reasons)
