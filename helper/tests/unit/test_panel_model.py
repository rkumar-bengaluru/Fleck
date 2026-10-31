"""The answer panel's model: cards from assist events, history, and the source label copy (plan Global Constraints)."""
from fleck_core.events import AssistEvent
from fleck_core.types import Citation
from fleck_helper.panel import PanelModel

RATE = Citation(doc_id="d1", chunk_id="d1-0", title="Rate card FY2026", client=None, kind="firm")
KUB = Citation(doc_id="d9", chunk_id="d9-3", title="Kubient MSA", client=None, kind="reference")


def ev(aid, phase, **kw):
    return AssistEvent(type="assist", meeting_id="m1", assist_id=aid, segment_id=kw.pop("seg", "client-61000"),
                       phase=phase, question=kw.pop("q", f"Question {aid}?"), **kw)


def test_panel_model_newest_on_thinking():
    m = PanelModel()
    assert m.current is None
    m.apply(ev("a", "thinking"))
    m.apply(ev("a", "done", kind="general", text="Answer a."))
    m.apply(ev("b", "thinking", q="How fast can you start?"))
    v = m.current
    assert (v.question, v.kind, v.label, v.text, v.index, v.total) == ("How fast can you start?", "thinking", "Thinking…", "", 2, 2)
    m.prev()
    m.apply(ev("b", "delta", sentence_index=0, text="Two"))
    assert m.current.question == "Question a?"  # events for the newest card don't move the reader off an old one
    m.apply(ev("c", "thinking"))
    assert (m.current.question, m.current.index, m.current.total) == ("Question c?", 3, 3)
    assert m.current.asked_ms == 61000


def test_panel_history_navigation():
    m = PanelModel()
    for a in "abc":
        m.apply(ev(a, "thinking"))
        m.apply(ev(a, "done", kind="general", text=f"Answer {a}."))
    assert m.current.text == "Answer c." and m.current.index == 3
    m.next()  # already newest
    assert m.current.index == 3
    m.prev(), m.prev(), m.prev()
    assert (m.current.text, m.current.index) == ("Answer a.", 1)
    m.next()
    assert m.current.text == "Answer b."
    # a superseding answer for the same turn reuses the card (same assist_id), from its thinking on
    m.apply(ev("b", "thinking", q="Question b, longer?"))
    assert (m.current.question, m.current.text, m.current.kind, m.current.total) == ("Question b, longer?", "", "thinking", 3)


def test_panel_labels_per_kind():
    m = PanelModel()
    m.apply(ev("lib", "thinking"))
    m.apply(ev("lib", "delta", sentence_index=0, text="$5,200 a"))
    assert (m.current.label, m.current.text) == ("Thinking…", "$5,200 a")  # not labelled before its checks are done
    m.apply(ev("lib", "sentence", sentence_index=0, text="$5,200 a month offshore.", citations=[RATE]))
    m.apply(ev("lib", "done", kind="library", citations=[RATE], text="$5,200 a month offshore."))
    assert (m.current.label, m.current.source_text, m.current.text) == ("Our material", "Rate card FY2026", "$5,200 a month offshore.")

    m.apply(ev("gen", "thinking"))
    m.apply(ev("gen", "sentence", sentence_index=0, text="Ground every suggestion.", kind="general"))
    assert (m.current.label, m.current.source_text) == ("General knowledge", "Not from our material")

    m.apply(ev("ref", "thinking"))
    m.apply(ev("ref", "done", kind="reference", citations=[KUB], text="Fees paid in the prior 12 months."))
    assert (m.current.label, m.current.source_text) == ("Reference contract", "Not our work: Kubient MSA")

    m.apply(ev("nim", "thinking"))
    m.apply(ev("nim", "sentence", sentence_index=0, text="Roughly $90k.", kind="general"))
    m.apply(ev("nim", "done", kind="not_in_material", text=""))  # replaces what was shown
    assert (m.current.label, m.current.source_text, m.current.text) == ("Not in our material", "", "")


def test_panel_shows_only_the_question_of_the_turn():
    m = PanelModel()
    m.apply(ev("a", "thinking", q="that is roughly what we had in mind. Before we go further, let me ask about the team. "
                              "What does a senior engineer cost per month? Offshore."))
    assert m.current.question == "What does a senior engineer cost per month?"
    m.apply(ev("b", "thinking", q="We close our budget cycle in March. Tell me about your support model."))
    assert m.current.question == "Tell me about your support model."  # no "?": the last sentence


def test_panel_reset_per_meeting_and_foreign_events_ignored():
    m = PanelModel()
    m.reset("m1")
    m.apply(ev("a", "thinking"))
    m.reset("m2")
    assert m.current is None  # the next meeting starts empty
    m.apply(ev("a", "done", kind="general", text="Late answer from meeting 1."))  # an old answer thread finishing late
    m.apply(ev("b", "thinking"))  # ev() says meeting m1
    assert m.current is None
    m.apply(AssistEvent(type="assist", meeting_id="m2", assist_id="c", segment_id="client-0", phase="thinking", question="Q?"))
    assert m.current.question == "Q?" and m.current.total == 1


def test_saved_panel_position_is_kept_on_a_visible_screen():
    from fleck_helper.panel import place
    screens = [((0, 0), (1440, 875))]  # visibleFrame: origin, size
    default = (450.0, 859.0)  # top-centre, 16 below the menu bar
    assert place(None, screens, 540) == default
    assert place((100.0, 600.0), screens, 540) == (100.0, 600.0)
    assert place((3000.0, 600.0), screens, 540) == default  # a display that is gone
    assert place((1200.0, 600.0), screens, 540) == (900.0, 600.0)  # partly off the right edge: pulled in
