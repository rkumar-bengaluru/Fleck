import pytest

from fleck_core.types import Passage
from fleck_core.validate import check_fallback, check_library_answer

# 1: real chunk of clients/_firm/rate-card.md (heading + Senior engineer row).
# 2: real text of clients/_firm/delivery-faq.md (SLA answer).
# 3: clients/_reference/photronics/outsourcing-agreement.pdf, Article 9 limitation of liability, clause (B), verbatim
#    (extracted with pypdf, whitespace joined).
P = [
    Passage(n=1, doc_id="firm-rate-card", chunk_id="c1", title="Northbeam Digital: rate card FY2026", client=None, kind="firm",
            text="## Time and materials, per person per month (USD)\n\n| Role | Offshore (Bengaluru) | Onsite (US/UK) |\n|---|---|---|\n| Senior engineer | 5,200 | 13,500 |"),
    Passage(n=2, doc_id="firm-delivery-faq", chunk_id="c2", title="Northbeam Digital: delivery FAQ", client=None, kind="firm",
            text="**What SLAs do you offer for support?**\nP1: response 30 min, restore 4 h, 24×7. P2: response 2 h, restore 1 business day. Service credits up to 10% of the monthly support fee."),
    Passage(n=3, doc_id="ref-photronics-outsourcing", chunk_id="c3", title="Photronics outsourcing agreement", client=None, kind="reference",
            text="AND (B) IN NO EVENT SHALL A PARTY’S CUMULATIVE LIABILITY ARISING OUT OF THIS AGREEMENT EXCEED THE AMOUNTS ACTUALLY PAID, PAYABLE, RECEIVED OR RECEIVABLE BY SUCH PARTY FOR THE PRODUCTS CONCERNED THEREWITH HEREUNDER PURSUANT TO THIS AGREEMENT DURING THE TWELVE (12) MONTHS PRIOR TO THE OCCURRENCE OF THE INITIAL EVENT FOR WHICH A PARTY RECOVERS DAMAGES HEREUNDER."),
]


def test_rejects_answer_without_citation():
    assert not check_library_answer("Rates start at $5,200.", P).ok


def test_rejects_out_of_range_citation():
    assert "citation_out_of_range" in check_library_answer("It is $5,200. [4]", P).reasons


def test_rejects_wrong_number_with_valid_citation():
    r = check_library_answer("A senior engineer is $5,500 a month offshore. [1]", P)
    assert r.sentences[0].ok is False and r.sentences[0].reason == "figure_not_in_cited_passage"
    assert r.sentences[0].display_text == "Not in our material" and not r.ok


def test_accepts_spoken_form_of_cited_figure():
    assert check_library_answer("Five thousand two hundred dollars a month. [1]", P).ok


def test_accepts_currency_against_real_rate_card_row():
    r = check_library_answer("A senior engineer is $5,200 a month offshore. [1]", P)
    assert r.ok and r.citations[0].doc_id == "firm-rate-card" and r.sentences[0].display_text == "A senior engineer is $5,200 a month offshore."


def test_rejects_unit_mismatch():
    assert not check_library_answer("It is ₹5,200 a month. [1]", P).ok


def test_one_bad_sentence_does_not_sink_good_one():
    r = check_library_answer("Senior engineers are $5,200 a month. [1] Discounts are 40%. [1]", P)
    assert r.ok and [s.ok for s in r.sentences] == [True, False]


def test_reference_only_answer_kind():
    r = check_library_answer("Liability is capped at the amounts paid in the 12 months before the event. [3]", P)
    assert r.ok and r.kind == "reference"


def test_none_reply_is_not_in_material():
    assert check_library_answer("NONE", P).kind == "not_in_material"


def test_fallback_blocks_price():
    assert not check_fallback("Projects like this usually cost $200,000.", []).ok


def test_fallback_blocks_percent_and_rates():
    assert not check_fallback("Expect 99.9% uptime at $50 per hour.", []).ok


def test_fallback_blocks_library_client_name():
    assert not check_fallback("We did this for Larkspur Retail Group.", ["Larkspur Retail Group"]).ok


def test_fallback_allows_plain_advice():
    assert check_fallback("Ground each suggestion in the source clause and keep a lawyer approving changes.", ["Larkspur Retail Group"]).ok


def _ok(answer, n=None):
    return check_library_answer(answer, P).ok


@pytest.mark.parametrize("answer", [
    "Restore within 4 days. [2]",           # passage says 4 h
    "Response within 2 days. [2]",
    "Restore within 1 hour. [2]",           # the 1 in "P1" is not a figure
    "Liability is capped at 7 months of fees. [3]",
    "It costs $5,200 per year. [1]",        # heading says per month
    "Fees are $52,000. [1]",
    "Margin is -5%. [2]",
])
def test_rejects_unit_period_and_stray_figures(answer):
    assert not _ok(answer)


@pytest.mark.parametrize("answer", [
    "Response within 30 minutes. [2]", "Restore within 4 hours. [2]", "Restore within 240 minutes. [2]",
    "Senior engineers are $5,200 per month. [1]", "Senior engineers are 5.2k US dollars a month. [1]",
])
def test_accepts_equivalent_units(answer):
    assert _ok(answer)


def test_scale_words_one_value():
    from fleck_core.numbers import extract_figures
    assert extract_figures("$1.5 million") == {"1500000 USD"} == extract_figures("$1,500,000")
    assert extract_figures("$5.2k") == {"5200 USD"} and extract_figures("five lakh rupees") == {"500000 INR"}
    q = Passage(n=1, doc_id="d", chunk_id="c", title="t", client=None, kind="firm", text="Fees are $1 million; team of 1.5 FTE.")
    assert not check_library_answer("Fees are $1.5 million. [1]", [q]).ok


def test_sign_dates_and_us_dollars():
    from fleck_core.numbers import extract_figures
    assert extract_figures("-5%") == {"-5 %"} == extract_figures("minus 5 percent") != extract_figures("5%")
    assert extract_figures("five thousand two hundred US dollars") == {"5200 USD"}
    assert extract_figures("Oct 15, 2025") == extract_figures("15/10/2025") == {"2025-10-15"}


@pytest.mark.parametrize("answer", [
    "We did this for Larkspur.", "Larkspur Retail did it.", "Larkspur's team did it.",
    "It costs 200k.", "Budget fifty grand.", "It costs Rs 5,00,000.", "It costs five lakh rupees.",
    "costs ¥5000", "costs CAD 5000", "Rates are 40/hr.", "It saves 20 pc.", "It saves 20 per cent.",
    "Response within 4 hours.", "We restore in 30 minutes.", "A team of 6 engineers usually.", "Use 6 engineers.",
])
def test_fallback_blocks(answer):
    assert not check_fallback(answer, ["Larkspur Retail Group"]).ok


def test_fallback_allows_retry_advice():
    assert check_fallback("Restore the backup and retry 3 times.", ["Larkspur Retail Group"]).ok


@pytest.mark.parametrize("answer,ok", [
    ("It costs $5,200 annually. [1]", False), ("It costs $5,200 yearly. [1]", False), ("It costs $5,200 p.a. [1]", False),
    ("It costs $5,200 weekly. [1]", False), ("It costs $5,200 hourly. [1]", False),
    ("It costs $5,200 monthly. [1]", True),
])
def test_adverb_rate_periods(answer, ok):
    assert _ok(answer) is ok


def test_dozen():
    from fleck_core.numbers import extract_figures
    assert extract_figures("a dozen") == {"12"} and extract_figures("two dozen") == {"24"}


def _mk(text, kind="firm", n=1):
    return [Passage(n=n, doc_id="d", chunk_id="c", title="t", client=None, kind=kind, text=text)]


@pytest.mark.parametrize("answer,text", [
    ("It costs $5,200 per year. [1]", "## Rates per person per month\n| Senior | $5,200 |"),
    ("It costs $5,200 per year. [1]", "Senior engineer, per month: $5,200"),
    ("It costs $5,200, billed per year. [1]", None),
    ("It costs $5,200 for each year. [1]", None),
    ("It costs $5,200/yr. [1]", None),
    ("It costs 5,200 dollars per annum. [1]", None),
    ("Per year, it costs $5,200. [1]", None),
    ("We onboarded 1 million users. [1]", "1.5 Million users onboarded."),
    ("Fees are $1 million. [1]", "Fees are high.\n2.5 Million USD total."),
    ("Liability is capped at 7 months of fees. [1]", "7. Limitation of Liability. (a) NEITHER PARTY SHALL BE LIABLE FOR CONSEQUENTIAL DAMAGES."),
])
def test_round2_false_accepts(answer, text):
    ps = P if text is None else _mk(text)
    assert not check_library_answer(answer, ps).ok


def test_clause_number_still_stripped_but_real_figure_kept():
    ps = _mk("7. Limitation of Liability. Cap is 12 months of fees.")
    assert check_library_answer("The cap is 12 months of fees. [1]", ps).ok


@pytest.mark.parametrize("text", ["$10 bn", "$2.5 mn", "3 k dollars"])
def test_spaced_scale_abbreviations(text):
    from fleck_core.numbers import extract_figures
    assert extract_figures(text) == {{"$10 bn": "10000000000 USD", "$2.5 mn": "2500000 USD", "3 k dollars": "3000 USD"}[text]}
    assert extract_figures("5 m long") == {"5"}


@pytest.mark.parametrize("answer", [
    "Most vendors are ISO 27001 certified.", "Review SOC 2 reports before signing.", "Use TLS 1.3 and OAuth 2.",
])
def test_fallback_allows_standards(answer):
    assert check_fallback(answer, []).ok


@pytest.mark.parametrize("answer", ["Costs 5000 bucks.", "Availability of 99.9.", "Aim for three nines.", "Aim for four nines."])
def test_fallback_more_blocks(answer):
    assert not check_fallback(answer, []).ok


@pytest.mark.parametrize("answer,text", [
    ("It costs $5,200 per quarter. [1]", "Senior: $5,200 per month."),
    ("It costs $5,200 per seat. [1]", "Senior: $5,200 per month."),
    ("It costs $5,200 per sprint. [1]", "Senior: $5,200 per month."),
    ("It costs $5,200 quarterly. [1]", "Senior: $5,200 per month."),
    ("It costs $5,200 per year. [1]", "Senior 5,200 USD per calendar month."),
    ("It costs $5,200 per year. [1]", "Senior 5,200 USD per person-month."),
    ("It costs $5,200 per year. [1]", "Senior: USD 5,200 pm."),
    ("It costs $5,200 per year. [1]", "Senior: USD 5,200 p.m."),
])
def test_round3_period_false_accepts(answer, text):
    assert not check_library_answer(answer, _mk(text)).ok


def test_round3_period_accepts():
    assert check_library_answer("It costs $5,200 per month. [1]", _mk("Senior 5,200 USD per calendar month.")).ok
    assert check_library_answer("It costs $5,200 a month. [1]", _mk("Senior: USD 5,200 pm.")).ok


@pytest.mark.parametrize("answer", ["Agree an SLA with 3 severity levels.", "Check availability in 2 regions.", "Uptime Kuma 2 is a monitoring tool."])
def test_fallback_availability_no_overblock(answer):
    assert check_fallback(answer, []).ok


@pytest.mark.parametrize("answer", ["Ninety nine point nine uptime.", "Cost is mid five figures.", "Six figures budget.", "Uptime of 99.9."])
def test_fallback_spoken_decimals_and_figures(answer):
    assert not check_fallback(answer, []).ok
