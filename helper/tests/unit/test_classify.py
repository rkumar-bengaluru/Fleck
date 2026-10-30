"""The four trigger tests (spec 7.2) at classifier level, on the reference transcripts; each prints its own count."""
import json
import time
from pathlib import Path

import pytest

from fleck_helper.classify import classify, triggers

FIX = Path(__file__).parents[3] / "fixtures"
CONV = json.loads((FIX / "conversation/reference.json").read_text())
NEG = json.loads((FIX / "negative/reference.json").read_text())

# Regression set: the Task 12 reviewer's probes and every example named in its findings (tag questions are
# confirmations, so they sit in SHOULD_NOT by policy, decision 93).
SHOULD_TRIGGER = [
    'I was wondering whether you have worked with insurance companies before.',
    "I was wondering whether you've done this kind of project before?",
    "I'm curious how you handle data security for client documents.",
    "I'm curious how you would staff this.",
    "I'd love to know how long the pilot would take.",
    'Could you share some examples of similar work',
    'What would the timeline look like',
    "What's your rate for a senior engineer? We have a fairly tight budget this year.",
    'How do you price this? Because our finance team will want a breakdown.',
    "Who owns the code? I'm asking because legal will ask me.",
    'Kindly tell us the pricing for the full project.',
    'What is the cost of one senior resource per month?',
    'Can you please share the rate card?',
    'What all services do you provide?',
    'How much time will it take to onboard the team?',
    'You can start from next month itself?',
    'Is it possible to start in two weeks?',
    'Do you have a team in Bengaluru?',
    'Is there any SLA for the critical issues?',
    'And the uptime, what SLA do you commit to?',
    'Any idea how much the whole thing will cost?',
    'Can you do it in under three months?',
    'Have you done anything similar for a bank?',
    'Would it be possible to get a fixed price quote?',
    'Do you guys do fixed bid projects?',
    'Will your team work in our time zone?',
    'Is the support available on weekends?',
    'Are your engineers certified on AWS?',
    'What about the IP, who owns it?',
    'And the warranty period?',
    'Tell me more about the legal document project you did.',
    'Could you walk us through the onboarding?',
    'Explain how the extraction pipeline works.',
    'How does your team handle the screen recording feature in our app?',
    'Can you share the slide deck with the case studies after the call?',
    'What audio formats can your transcription pipeline handle?',
    'Can you see any risk in moving our data to the cloud?',
    'Which of your case studies is closest to what we need?',
    'Did you deliver that project on time?',
    'Should we expect any extra costs for maintenance?',
    'Shall we expect the team to be onsite?',
    'Are there any hidden costs?',
    'Does the price include support?',
    "Doesn't that include testing as well?",
    "Isn't IP usually retained by the vendor?",
    'Where are your developers located?',
    'Is it fixed price or time and material?',
    'We were hoping to start in November. Is that feasible for you?',
    'Who owns the IP, because last time our vendor kept it',
    'Which team would do this',
    'Is it possible to get the rate card',
    'I was wondering whether you have worked with insurance companies before?',
    "I'm curious how you handle data security for client documents",
    'How much time will it take',
]

SHOULD_NOT = [
    "That's great to hear?",
    "We've been using that tool for two years now?",
    'So we are looking at about three months?',
    'Okay so that sounds good to me?',
    'I think we can start in January?',
    'We are fine with that?',
    'What we need is a faster review process.',
    'How we do it today is quite manual.',
    "What I'm trying to say is the budget is fixed.",
    'Who knows, maybe next quarter.',
    'When we did this last year it failed.',
    'How do you do, nice to meet you all.',
    "Do you know what, let's take this offline.",
    'Can you give me a second, my other line is ringing.',
    'Could you send me the invite for Thursday?',
    'Can you send the deck after the call?',
    'Would you like to go first?',
    'Can you repeat that?',
    'Sorry, could you say that again please?',
    'Sorry, you broke up there, can you say that again?',
    'Is everyone here, should we wait for Priya?',
    'Shall we take ten minutes break?',
    'Should we get started then?',
    'Shall we wrap up?',
    'Are you guys able to hear me properly?',
    'Is my voice coming clearly?',
    'Am I audible?',
    'Did you get my email from yesterday?',
    'Have you received the NDA I sent?',
    'Are you joining from the office today?',
    'How was your weekend?',
    'How are you doing today?',
    'How is the weather in Bengaluru?',
    'Where are you calling in from today?',
    'Can we start with the agenda?',
    'What time is it there for you?',
    'Is it okay if I record this call?',
    'Do you mind sharing your screen?',
    'Are you there?',
    'Hello, can you hear me now?',
    "You're on mute, Rupak.",
    'Will you send the minutes after?',
    'Can you hold on for one minute?',
    'Do you want to take a quick break?',
    'Would that be okay with you?',
    'Have you had lunch?',
    'Can we move to the next topic?',
    'Shall I share my screen?',
    'Can I quickly introduce my colleague?',
    'Who else is joining from your side?',
    'So the IP would belong to us, right?',
    "And you'd sign an NDA before we share data, correct?",
    'You support on-prem deployment, yes?',
    'How quickly could you',
    'Can you tell us',
    'Does Thursday work for the follow up?',
    'That works for us. Shall we take ten minutes at the end for next steps?',
    'Is it all right if I send you our playbook after this call?',
]

# Fix round 2: written before the positive-evidence scorer, as a check that it generalises beyond the lists above.
FRESH_SHOULD_TRIGGER = [
    'What would a three month pilot cost us?',
    'How many engineers would you put on this?',
    'Do you offer fixed price contracts?',
    'Have you built anything like this for an insurer?',
    'Can your team work from our office two days a week?',
    'How do you handle personal data in the documents?',
    'Are you ISO 27001 certified?',
    'What is your typical escalation path for a production incident?',
    'Is the model hosted in our cloud or yours?',
    'Who would own the trained model at the end?',
    'How long does a typical migration take?',
    'Does your platform integrate with SharePoint?',
    'What happens to our data if we end the contract?',
    'Would you consider a lower rate for a longer commitment?',
    'Which industries have you delivered automation for?',
    'Roughly what budget should we set aside for year one?',
    'By when could the first deliverable be ready?',
    'We need to understand how you secure the API keys.',
    'How would you measure the accuracy of the extraction?',
    'Can you support us outside UK business hours?',
    'What would your team actually do in the first two weeks?',
    'Do you have references from other law firms?',
]

FRESH_SHOULD_NOT = [
    'Is Priya going to join us later?',
    'Can you all see the whiteboard?',
    'How was the drive in this morning?',
    'Did everyone get the calendar update?',
    'Shall we push this to Friday afternoon?',
    'Would anyone like a quick coffee break?',
    'Is the connection better on your side now?',
    'Could you move your camera a little?',
    'Are you comfortable if we run five minutes over?',
    "Who's going to send round the summary?",
    "What's the plan for the rest of the afternoon?",
    'Have you been to Mumbai before?',
    'Did you watch the cricket last night?',
    'How long have you been in Bengaluru?',
    'Can you make it a bit louder?',
    'Is that your dog barking in the background?',
    'Do we need anyone else on this call?',
    'Where did you go for the holidays?',
    'Is it too early for you over there?',
    'Should I stop sharing now?',
    'Can we switch to the other document?',
    'When do you usually finish for the day?',
]


def _neg_triggers(categories):
    # every turn as if the client said it: the text alone must not trigger
    return [t["text"] for t in NEG["turns"] if t["category"] in categories and triggers(t["text"], "client")]


def test_negative_set_short():
    hits = _neg_triggers({"short"})
    print(f"negative_set_short: {len(hits)} triggers {hits}")
    assert not hits


def test_negative_set_medium():
    hits = _neg_triggers({"medium", "logistics"})
    print(f"negative_set_medium: {len(hits)} triggers {hits}")
    assert not hits


def test_conversation_false_triggers():
    hits = [t["text"] for t in CONV["turns"] if not t["question_id"] and triggers(t["text"], t["speaker"])]
    allowed = CONV["duration_ms"] / 600_000
    print(f"conversation_false_triggers: {len(hits)} (allowed {allowed:.2f}) {hits}")
    assert len(hits) <= allowed


def test_conversation_recall():
    asked = [t for t in CONV["turns"] if t["question_id"]]
    hit = [t["question_id"] for t in asked if triggers(t["text"], t["speaker"])]
    print(f"conversation_recall: {len(hit)}/{len(asked)} {hit}")
    assert len(asked) == 8 and len(hit) >= 0.9 * len(asked)


def test_logistics_beats_question_marks():
    v = classify("Can you see my screen? No, sorry… Can you see yours", "client")
    assert not v.is_question and v.confidence >= 0.9


def test_strong_and_weak_questions():
    assert classify("Ballpark, what would the whole project cost us?", "client").confidence >= 0.85
    assert classify("how quickly could you have a team working on this", "client").confidence >= 0.85  # no "?"
    assert classify("Walk me through your onboarding process.", "client").is_question
    weak = classify("I think we can start in January?", "client")
    assert weak.is_question and weak.confidence == 0.6
    assert not classify("It did, which was the plan.", "client").is_question
    assert not triggers("I did absolutely nothing. Which was the plan?", "client")  # seen from Whisper
    assert triggers("Which team would you put on this?", "client")
    assert not classify("Why?", "client").is_question  # under 4 words


def test_only_client_triggers():
    q = "What does a senior engineer cost per month?"
    assert triggers(q, "client") and not triggers(q, "you") and not triggers(q, "overlap")


def test_fast():
    t = time.perf_counter()
    for turn in NEG["turns"]:
        classify(turn["text"], "client")
    assert (time.perf_counter() - t) / len(NEG["turns"]) < 0.05


@pytest.mark.parametrize("text", SHOULD_TRIGGER + FRESH_SHOULD_TRIGGER)
def test_should_trigger(text):
    assert triggers(text, "client"), classify(text, "client")


@pytest.mark.parametrize("text", SHOULD_NOT + FRESH_SHOULD_NOT)
def test_should_not_trigger(text):
    assert not triggers(text, "client"), classify(text, "client")
