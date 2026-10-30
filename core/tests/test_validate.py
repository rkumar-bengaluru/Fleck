import pytest

from fleck_core.types import Passage
from fleck_core.validate import check_fallback, check_library_answer

# 1: real chunk of clients/_firm/rate-card.md (heading + Senior engineer row).
# 2: real text of clients/_firm/delivery-faq.md (SLA answer).
# 3: clause 7(a) is verbatim from clients/_reference/kubient/msa-part1.pdf; the 12-month cap sentence is
#    NOT in that PDF and was added so the reference-kind test has a figure to check.
P = [
    Passage(n=1, doc_id="firm-rate-card", chunk_id="c1", title="Northbeam Digital: rate card FY2026", client=None, kind="firm",
            text="## Time and materials, per person per month (USD)\n\n| Role | Offshore (Bengaluru) | Onsite (US/UK) |\n|---|---|---|\n| Senior engineer | 5,200 | 13,500 |"),
    Passage(n=2, doc_id="firm-delivery-faq", chunk_id="c2", title="Northbeam Digital: delivery FAQ", client=None, kind="firm",
            text="**What SLAs do you offer for support?**\nP1: response 30 min, restore 4 h, 24×7. P2: response 2 h, restore 1 business day. Service credits up to 10% of the monthly support fee."),
    Passage(n=3, doc_id="ref-kubient-msa", chunk_id="c3", title="Kubient MSA", client=None, kind="reference",
            text="7. Limitation of Liability. (a) EXCEPT WITH RESPECT TO THE PARTIES' LIABILITY FOR INDEMNIFICATION, OR LIABILITY FOR BREACH OF CONFIDENTIALITY, NEITHER PARTY SHALL BE LIABLE FOR ANY CONSEQUENTIAL, INCIDENTAL, INDIRECT, PUNITIVE, SPECIAL OR OTHER SIMILAR DAMAGES. Aggregate liability is capped at the fees paid in the 12 months before the claim."),
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
    assert check_library_answer("Liability is capped at 12 months of fees. [3]", P).kind == "reference"


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
