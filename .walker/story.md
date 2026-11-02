# Fleck: investor pitch with product demo

**Subject:** Fleck v0.2.0 (`67b588f`), which adds watching the call back and the demo panel setting. Walker plays its
own clone at that commit; rupak.k's checkout is never touched.

**Who it's for:** investors deciding whether to back Fleck. Not a developer walkthrough: no install, no terminal, no
architecture. It sells the problem, shows the product working end to end on demo data, says why it wins, and closes
with thank you and next steps.
The video closes with thank you and next steps. About 6½ minutes (~950 words, ~32 scenes; rupak.k chose to show every live answer).

**Logline:** In a sales call, the question that decides the deal lands before anyone can find the answer. Fleck sits
in the call without a bot, hears the client's question and shows the answer from the firm's own material, with its
source, in seconds.

## Acts

0. **Title and the problem** (title and image scenes). The salesperson in a presales call: commercial, proof,
   delivery and technical questions decide the deal; the answers are scattered across proposals, SOWs, case studies
   and rate cards (MVP spec §1). A slow or wrong answer costs credibility, and the client may have its own AI
   checking. Past clients are not named (rupak.k's rule): "our past client work".
1. **library**: the firm's memory (browser). Sign in with Google (the sign-in simulator, gsim), the Library: firm
   material (rate card, capabilities, delivery FAQ), past client work (case studies, a proposal, an SOW) and
   reference contracts, every one indexed. Message: Fleck is only as good as the firm's own material, and it
   uses that, not the internet.
2. **brief**: walking in prepared (browser). Today shows the calendar; the demo prospect's meeting is linked to its
   client (Harbourline Legal, fictional); Open brief: "What we know going in", likely questions, where our
   material is thin. Message: names are redacted before anything reaches Claude.
3. **live-desktop**: the moment that matters (the real desktop screen). A staged call (Walker's call page, titled
   "Meet – Presales: demo prospect", no real people) in real Chrome, and the real Fleck menu-bar app fed the recorded
   conversation. Fleck records the call window, and its own panel answers all 8 of the client's questions over the
   call, each with its own scene (rupak.k): rate, start time, code ownership and night support from our material;
   proof from a past case study; two general-knowledge answers, marked as such; and a ballpark price it declines as
   "Not in our material" instead of inventing one. The
   panel is filmed through a demo-only setting (`FLECK_PANEL_SHAREABLE=1`); in a real call it stays out of screen
   shares, and the narration says so. Message: no bot joins the call, the answer cites its source, and the latency
   gates (first words p95 ≤ 3 s) are a design goal written down, not a hope.
4. **after**: the team's memory (browser). The ready meeting: "Where Fleck helped", every question and what Fleck
   showed; the meeting plays back as **Video** (the staged call window Fleck recorded) or **Audio only**; the chat answers "What did they say about where
   documents can live?" from the transcript, with a timestamped citation that opens the line. Ask across meetings
   answers one question over every meeting.
5. **Why it wins** (title/image scenes over the docs): bot-free, privacy-first (redaction before Claude), answers
   from the firm's own material with a citation, a library, brief, live help and after-call memory in one product.
6. **Thank you and next steps** (outro): the live client-call pilot (handover), then what the feature spec lists
   next: Windows capture, Outlook calendar, Teams and other call apps, and the consent and retention questions
   left open for video.

## Not shown, and why

- **A real call with real people.** rupak.k chose a clean staged call: Walker's call page plays the recorded
  conversation in a Chrome window titled like a Meet call. The live act uses the helper's own replay source,
  which feeds a recorded conversation through the same pipeline as a live call.
- That pipeline is Whisper, question detection, retrieval and answers.
- **The web app's live transcript.** One desktop take films the whole call, because Fleck records the call window
  only while it is open; the transcript shows in the after-call scenes instead.
- The video in act 4 is the staged call window Fleck recorded. The api assembles it with the audio exactly as after a real call.
- **Market size, traction, team, the funding ask.** Not in the repo, and the narration may only say what a cited
  file says (rupak.k chose a product-only pitch). Nothing is invented.
- **Real client names** from the MVP spec: kept anonymous (rupak.k's rule). The demo data is fictional
  (Northbeam, Harbourline Legal, Larkspur Retail ...).
- Install, tests, architecture: not for this audience.

## The set and its time window

- The repo's Compose stack (`deploy/docker-compose.yml`) as Compose project `walker-fleck`, with a `.env` written
  off camera from `.env.example` (gsim sign-in, a generated `FLECK_SECRET_KEY`, the Anthropic key from `walker
  with-key`). Its ports are fixed in the compose file (5173, 8000, 8090, 8080, 50051, 5432, 9000, 9001), so
  rupak.k's own Fleck stack must be down during a shoot (`scripts/stop.sh`).
- Ollama on the host, as Fleck uses it (the three pinned models). Live answers run on the GPU: a GPU-heavy job
  running alongside slows the live act and may fail its "within seconds" wait. Shoot on an idle machine.
- gsim's demo meeting sits at the shoot's current time (`GSIM_DEMO_START` empty), so Today always has it; no
  other time window.
- The replay is about 8 minutes of conversation; act 3 films the first question's answer and cuts; the rest
  of the replay runs off camera before act 4.

## Paid calls (Anthropic, key `ANTHROPIC_API_KEY` via `walker with-key`)

Fleck's own Claude calls, read back from Fleck's `llm_calls` table after each take by a scenario script that
prints `WALKER_COST` (Fleck prices each call itself):

| Step | Model | Input | Estimate |
|---|---|---|---|
| brief: the demo prospect's brief | claude-sonnet-5-5 | one event, redacted, library passages | $0.10 |
| live: the replay's general-knowledge answers | claude-haiku-5-5 | a few questions the library can't answer | $0.05 |
| after: the meeting summary and notes | claude-sonnet-5-5 | one 8-minute transcript | $0.15 |
| after: one chat question, one ask-across question | claude-sonnet-5-5 | transcript and library passages | $0.10 |
| **One full shoot** | | | **$0.40, rounded up to $0.50** |

Take-by-take shooting plays each paid take about twice; with the final full shoot, about **$2.00** in total,
inside Walker's $4.00 cap. Walker's own rendering uses the local narration voice (no paid call expected).
