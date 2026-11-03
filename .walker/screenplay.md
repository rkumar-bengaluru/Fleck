# Fleck: the right answer, in the room, while the deal is on the line

An investor pitch with the product demo (see `story.md`). Every sentence cites where its facts live; numbers that
change from shoot to shoot (latencies, costs, counts) are never said. The demo data is fictional; the call in act 3 is
staged (no real people), filmed on a real desktop with the real Fleck app, and the narration says so.

## The problem

```yaml
scene: title
type: title
heading: Fleck
subheading: Stay present, and Fleck remembers.
narration:
  - say: Every sales team knows the moment. The client asks the question that decides the deal, and the answer is somewhere in a folder.
    cite: ['docs/superpowers/specs/2026-10-30-fleck-mvp-design.md: "The answers exist, scattered across past proposals, SOWs, case studies and rate cards"']
  - say: Fleck puts that answer in front of the salesperson while the client is still on the line.
    cite: ['docs/superpowers/specs/2026-10-30-fleck-mvp-design.md: "recognises the client''s questions, and within seconds shows a short answer drawn from the firm''s own material"']
```

```yaml
scene: problem
type: title
heading: The problem
subheading: The answers exist, just not in the room.
narration:
  - say: In a presales meeting, the deciding questions are about rates and team size, proof of past results, data security, and how the work would be built.
    cite: ['docs/superpowers/specs/2026-10-30-fleck-mvp-design.md: "**commercial**: rates, team size, how fast you can start"', 'docs/superpowers/specs/2026-10-30-fleck-mvp-design.md: "**proof**: have you done this before"', 'docs/superpowers/specs/2026-10-30-fleck-mvp-design.md: "**delivery and risk**: IP ownership, data security"', 'docs/superpowers/specs/2026-10-30-fleck-mvp-design.md: "**technical**: how would you automate *our* flow"']
  - say: The answers live in past proposals, statements of work, case studies and rate cards, but not in the salesperson's head when the question lands.
    cite: ['docs/superpowers/specs/2026-10-30-fleck-mvp-design.md: "The answers exist, scattered across past proposals, SOWs, case studies and rate cards, but not in the salesperson''s head at the moment the question lands."']
  - say: A slow, vague or wrong answer costs credibility, and the client may well have its own AI checking the numbers.
    cite: ['docs/superpowers/specs/2026-10-30-fleck-mvp-design.md: "A slow, vague or wrong answer costs credibility and the deal."', 'docs/superpowers/specs/2026-10-30-fleck-mvp-design.md: "The client may well have their own AI checking the answers"']
```

```yaml
scene: what
type: title
heading: What Fleck is
subheading: An assistant that sits openly in the meeting, with no bot in the call.
narration:
  - say: Fleck is a desktop assistant that sits openly in the meeting with the salesperson.
    cite: ['docs/superpowers/specs/2026-10-30-fleck-mvp-design.md: "A desktop assistant that sits openly in the meeting with the salesperson."']
  - say: It listens without joining the call as a bot, so there is no awkward moment of explaining who the extra guest is.
    cite: ['docs/superpowers/specs/2026-10-30-fleck-mvp-design.md: "It listens to the call without joining it as a bot"', 'README.md: "No bot joins Zoom / Meet / Teams"']
  - say: Here is the product, end to end, on demo data.
    cite: ['story.md: "The demo data is fictional"']
```

## The firm's memory

```yaml
scene: library
type: clip
take: library
beats: [today, library]
narration:
  - say: A presales team signs in with Google, and everyone shares one library of past material.
    cite: ['docs/superpowers/specs/2026-10-30-fleck-mvp-design.md: "Every member of the presales team signs in with Google. The team shares one library of past material."']
  - say: It holds the firm's own material, from rate cards and capabilities to the case studies, proposals and statements of work of past client projects.
    cite: ['docs/superpowers/specs/2026-10-30-fleck-mvp-design.md: "**client** (our past work for that client), **firm** (rate card, capabilities, FAQ)"', 'footage/library/library.txt: "Our past work"']
```

```yaml
scene: library-scroll
type: clip
take: library
beat: library-scroll
narration:
  - say: Every document is indexed for search, and reference contracts are kept apart, quoted for their terms but never passed off as the firm's own work.
    cite: ['footage/library/library.txt: "Indexed"', 'docs/superpowers/specs/2026-10-30-fleck-mvp-design.md: "Reference documents may be quoted for their terms but are never presented as the firm''s past work."']
  - say: The library also counts how often each document is used in answers, so the team sees which material earns its place.
    cite: ['footage/library/library.txt: "Used in answers"']
```

## Walking in prepared

```yaml
scene: today
type: clip
take: brief
beats: [today, link-client]
narration:
  - say: Before the call, the team's Google Calendar appears in Fleck, read only.
    cite: ['docs/superpowers/specs/2026-10-30-fleck-mvp-design.md: "The user''s Google Calendar appears in Fleck, read-only"']
  - say: Fleck suggests the client from the guests, and choosing it links the meeting to them.
    cite: ['footage/brief/today.txt: "Suggested: Harbourline Legal"']
```

```yaml
scene: brief
type: clip
take: brief
beats: [open-brief, brief-wait]
narration:
  - say: Linking the client builds a brief, starting with what we know going in and the questions this client is likely to ask.
    cite: ['footage/brief/brief-wait.txt: "What we know going in"', 'footage/brief/brief-wait.txt: "Likely questions"']
  - say: Before anything reaches Claude, names, companies, emails and links are redacted.
    cite: ['docs/superpowers/specs/2026-10-30-fleck-mvp-design.md: "attendee names, client names (and the guests'' company, from their email domain), emails, links and long digit runs are redacted"']
```

```yaml
scene: brief-scroll
type: clip
take: brief
beat: brief-scroll
narration:
  - say: Each likely question comes with an answer from our own material and the document behind it.
    cite: ['footage/brief/brief-wait.txt: "Our material"', 'footage/brief/brief.png']
  - say: Where the library has nothing, the brief marks a gap instead of making something up, so the salesperson knows where to tread carefully.
    cite: ['footage/brief/brief-wait.txt: "gaps"']
```

## In the call

```yaml
scene: live-title
type: title
heading: In the call
subheading: The moment that matters.
narration:
  - say: Now the moment that matters.
    cite: ['story.md: "the moment that matters"']
  - say: On your desktop, Fleck hears the call, transcribes it on the machine, spots the client's questions and finds the answer.
    cite: ['story.md: "That pipeline is Whisper, question detection, retrieval and answers."']
  - say: For this video, a recorded presales conversation is replayed through that same pipeline.
    cite: ['story.md: "which feeds a recorded conversation through the same pipeline as a live call"']
```

```yaml
scene: live-call
type: clip
take: live-desktop
beats: [join, wait-1]
trim: [0, 137]                  # keeps the join and the call starting; the long wait is cut
narration:
  - say: Here is a staged video call on your desktop, with the real Fleck app running in the menu bar.
    cite: ['takes/live-desktop.yaml: "A staged call"', 'takes/live-desktop.yaml: "the real Fleck menu-bar app fed the recorded conversation"']
  - say: Fleck records the call window and listens, and no bot joins the call.
    cite: ['takes/live-desktop.yaml: "Fleck records the Chrome window whose title starts with"', 'README.md: "No bot joins Zoom / Meet / Teams"']
  - say: Its small panel sits at the top of the screen, listening, until the client asks something.
    cite: ['helper/fleck_helper/panel.py: "Fleck is listening"']
```

```yaml
scene: ans-rate
type: clip
take: live-desktop
beat: answer-1
narration:
  - say: Then the client asks what a senior engineer costs per month, offshore.
    cite: ['fixtures/conversation/reference.json: "What does a senior engineer cost per month, offshore?"']
  - say: Seconds later the panel answers, 5,200 a month, from our material, and names the rate card.
    cite: ['footage/after/meeting.txt: "A senior engineer costs 5,200 per month when working offshore."', 'footage/after/meeting.txt: "Rate card FY2026"']
```

```yaml
scene: ans-start
type: clip
take: live-desktop
beat: answer-2
narration:
  - say: How quickly could a team start? Within 10 working days, with a two-week overlap at our cost.
    cite: ['footage/after/meeting.txt: "How quickly could you have a team working on this?"', 'footage/after/meeting.txt: "within 10 working days of starting, with a two-week overlap at our cost"']
```

```yaml
scene: ans-ip
type: clip
take: live-desktop
beat: answer-3
narration:
  - say: Who owns the code? The client owns all deliverables and code, on payment.
    cite: ['footage/after/meeting.txt: "Who owns the code you write for us?"', 'footage/after/meeting.txt: "The client owns all deliverables and code written for them on payment."']
```

```yaml
scene: ans-sla
type: clip
take: live-desktop
beat: answer-4
narration:
  - say: If something breaks at night, the response is 30 minutes, from the delivery FAQ.
    cite: ['footage/after/meeting.txt: "If something breaks at night, how fast do you respond?"', 'footage/after/meeting.txt: "30 minutes."', 'footage/after/meeting.txt: "Delivery FAQ"']
```

```yaml
scene: ans-case
type: clip
take: live-desktop
beat: answer-5
narration:
  - say: Asked for proof, it confirms a past redline project for a law firm, citing the case study.
    cite: ['footage/after/meeting.txt: "Have you automated redline review for a law firm before?"', 'footage/after/meeting.txt: "Yes, we have automated redline review for a law firm before."', 'footage/after/meeting.txt: "Case study: Calloway & Marsh"']
```

```yaml
scene: ans-general
type: clip
take: live-desktop
beat: answer-6
narration:
  - say: Some questions go beyond our material, like how to stop a model inventing contract clauses.
    cite: ['footage/after/meeting.txt: "How do you stop the model inventing clauses in a contract?"']
  - say: Fleck still answers, marked as general knowledge.
    cite: ['footage/after/meeting.txt: "General knowledge"', 'footage/after/meeting.txt: "Not from our material"']
```

```yaml
scene: ans-liability
type: clip
take: live-desktop
beat: answer-7
narration:
  - say: On capping liability at 12 months of fees, it gives the market view and the risks, again marked as general knowledge.
    cite: ['footage/after/meeting.txt: "would you cap liability at 12 months of fees?"', 'footage/after/meeting.txt: "A 12-months-of-fees cap is a common market default"', 'footage/after/meeting.txt: "General knowledge"']
```

```yaml
scene: ans-price
type: clip
take: live-desktop
beat: answer-8
narration:
  - say: And asked for a ballpark price for the whole project, Fleck says it is not in our material, instead of inventing a number.
    cite: ['footage/after/meeting.txt: "What would the whole project cost us?"', 'footage/after/meeting.txt: "Not in our material"']
```

```yaml
scene: panel-facts
type: clip
take: live-desktop
beat: finish
trim: [0, 44]
narration:
  - say: The design target is first words within 3 s of the client finishing the question, measured on every build.
    cite: ['docs/superpowers/specs/2026-10-30-fleck-mvp-design.md: "the first words of a library or brief answer appear within **3 s** of the client finishing the question"']
  - say: The panel floats over the call and never takes keyboard focus.
    cite: ['docs/superpowers/specs/2026-10-30-fleck-mvp-design.md: "The answer panel (§7.8) floats over the call, never takes keyboard focus"']
  - say: In a real call it stays out of screen shares and recordings; a demo setting lets this video film it.
    cite: ['helper/fleck_helper/panel.py: "left out of screen shares and recordings"', 'helper/fleck_helper/panel.py: "FLECK_PANEL_SHAREABLE"']
```

## The team's memory

```yaml
scene: after-title
type: title
heading: After the call
subheading: Every call becomes the team's memory.
narration:
  - say: When the call ends, the team works with Claude over the notes, the transcript and the library.
    cite: ['docs/superpowers/specs/2026-10-30-fleck-mvp-design.md: "After the meeting the team chats with Claude over the notes, transcript and library."']
```

```yaml
scene: meeting
type: clip
take: after
beat: meeting
narration:
  - say: The meeting page shows where Fleck helped, and the call plays back as video or audio only.
    cite: ['footage/after/meeting.txt: "Where Fleck helped"', 'footage/after/meeting.txt: "Audio only"']
  - say: So a sales lead can watch back how convincing an answer looked, not just read what was said.
    cite: ['docs/superpowers/specs/2026-11-01-meeting-video-design.md: "how convincing they looked"']
```

```yaml
scene: recorded
type: clip
take: after
beat: play
narration:
  - say: This is the staged call's own window, exactly as Fleck recorded it during the call.
    cite: ['takes/live-desktop.yaml: "Fleck records the Chrome window whose title starts with"', 'footage/live-desktop/finish.log: "ready with video"']
  - say: Fleck joins it with the call's audio, exactly as it does after a real call.
    cite: ['story.md: "The api assembles it with the audio exactly as after a real call."']
```

```yaml
scene: audio-only
type: clip
take: after
beat: audio-only
narration:
  - say: A switch moves between the video and audio only.
    cite: ['docs/superpowers/specs/2026-11-01-meeting-video-design.md: "the user can switch to **Audio only**"']
```

```yaml
scene: helped
type: clip
take: after
beat: helped
narration:
  - say: Under the player, every question the client asked is listed with the answer Fleck showed and its source.
    cite: ['footage/after/meeting.txt: "Where Fleck helped"', 'web/e2e/full-journey.spec.ts: "Notes: every helped item, and the recording plays"']
```

```yaml
scene: chat
type: clip
take: after
beats: [chat, chat-answer]
narration:
  - say: Anyone on the team can ask about the meeting in plain words, here, what the client said about where documents can live.
    cite: ['takes/after.yaml: "What did they say about where documents can live?"']
  - say: The answer cites the exact moments in the transcript, and the meeting's summary.
    cite: ['footage/after/chat.png']
```

```yaml
scene: cite
type: clip
take: after
beat: cite
narration:
  - say: Clicking a citation opens the transcript at that line.
    cite: ['web/e2e/full-journey.spec.ts: "Chat answers from the transcript, with a timestamped citation that opens the line"']
```

```yaml
scene: ask-all
type: clip
take: after
beats: [ask-all, ask-all-answer]
narration:
  - say: Ask across meetings answers from every recorded meeting and the library at once.
    cite: ['takes/after.yaml: "Answers come from every recorded meeting and the library, with sources you can open."']
  - say: Asked what rate we quoted for a senior engineer, it finds the call and the rate card, and shows both as sources.
    cite: ['takes/after.yaml: "What rate did we quote for a senior engineer?"', 'footage/after/ask-all.png']
```

## Why Fleck wins, and what's next

```yaml
scene: why
type: title
heading: Why Fleck wins
subheading: No bot in the call, the firm's own answers with sources, before, during and after.
narration:
  - say: No bot joins the call, so there is nothing for the client to notice.
    cite: ['README.md: "No bot joins Zoom / Meet / Teams"']
  - say: Answers come from the firm's own material, with the source on screen, and names are redacted before anything reaches Claude.
    cite: ['docs/superpowers/specs/2026-10-30-fleck-mvp-design.md: "shows a short answer drawn from the firm''s own material, citing the document it came from"', 'docs/superpowers/specs/2026-10-30-fleck-mvp-design.md: "Before either goes to Claude, attendee names"']
  - say: And it covers the whole cycle in one product, from the library and the brief to live help and memory after the call.
    cite: ['story.md: "a library, brief, live help and after-call memory in one product"']
```

```yaml
scene: next
type: title
heading: Next steps
subheading: First real client calls, then more platforms.
narration:
  - say: The next step is Fleck's first real client calls; so far it has been measured on replays and test windows, not a real call.
    cite: ['docs/superpowers/handover.md: "No real call has been measured."', 'docs/superpowers/handover.md: "Measured on a Chrome test window titled"']
  - say: Then Windows capture, Outlook calendar, and Microsoft Teams with the other call apps.
    cite: ['README.md: "Windows system audio capture (WASAPI loopback)"', 'README.md: "Outlook Calendar OAuth + sync"', 'README.md: "Desktop conferencing: Zoom, Google Meet, Microsoft Teams, Slack Huddles, Webex"']
  - say: And for video, the consent and retention questions, settled with the firms who use it.
    cite: ['docs/superpowers/specs/fleck-mvp2.md: "Consent: a visible"', 'docs/superpowers/specs/fleck-mvp2.md: "Retention period and who can view the video."']
```

```yaml
scene: thanks
type: outro
cta: "Thank you. Next: Fleck's first real client calls."
narration:
  - say: Thank you for watching.
    cite: ['story.md: "The video closes with thank you and next steps."']
  - say: Fleck, the right answer, in the room, while the deal is on the line.
    cite: ['walker.yaml: "the right answer, in the room, while the deal is on the line"']
```
