import pytest

from fleck_core.numbers import extract_figures, normalize_numbers
from fleck_core.validate import split_sentences


@pytest.mark.parametrize("raw,canon", [
    ("six thousand nine hundred", "6900"), ("6,900", "6900"), ("$6,900", "6900 USD"),
    ("6900 dollars", "6900 USD"), ("five percent", "5 %"), ("5%", "5 %"),
    ("thirty days", "30 days"), ("USD 25,000", "25000 USD"), ("₹5,000", "5000 INR")])
def test_normalize_numbers(raw, canon):
    assert canon in normalize_numbers(raw)


def test_extract_figures():
    assert extract_figures("Net 45 days, 99.9% for $6,900 from 15 October 2025; ten people") == {
        "45 days", "99.9 %", "6900 USD", "2025-10-15", "10"}


@pytest.mark.parametrize("text,parts", [
    ("Uptime is 99.9% monthly. Cost is $6,900.", 2),
    ("Use tools, e.g. Jira, daily. It works.", 2),
    ("It is net 45 days. [1] Then pay. [2]", 2),
    ("One. [1][2] Two", 2),
])
def test_split_sentences(text, parts):
    assert len(split_sentences(text)) == parts


def test_marker_stays_with_preceding_sentence():
    assert split_sentences("Net 45 days. [1] Then pay.") == ["Net 45 days. [1]", "Then pay."]
