# Fleck MVP: goals, problem and stack

Date: 2026-10-30. Author: rupak.k with Claude. Status: for rupak.k's review before implementation.

Supersedes `2026-10-24-fleck-phase1-design.md` (removed on this branch, still in git history). AEVIS, the old milestone docs and the `.walker/` golden are out of scope; rupak.k will make a Walker demo video of Fleck later with their Walker plugin.

Items marked **(Claude's call)** were decided by Claude, not rupak.k. Every such call, and every later one made during spec, plan and build, is recorded in [`docs/superpowers/decisions.md`](../decisions.md).

---

## 1. Problem

rupak.k sells AI automation consulting: their firm adds AI to clients' existing systems (for example, track-changes legal document flows for Joele Frank, an investigation agent for Sayari). In a presales meeting the client asks questions that decide the deal:

- **commercial**: rates, team size, how fast you can start, fixed price or T&M
- **proof**: have you done this before, for whom, with what result
- **delivery and risk**: IP ownership, data security, SLAs, warranty, what happens if someone leaves
- **technical**: how would you automate *our* flow, which models, where the data goes

The answers exist, scattered across past proposals, SOWs, case studies and rate cards, but not in the salesperson's head at the moment the question lands. A slow, vague or wrong answer costs credibility and the deal. The client may well have their own AI checking the answers, so a wrong number is caught.

## 2. What Fleck is

A desktop assistant that sits openly in the meeting with the salesperson. It listens to the call without joining it as a bot, recognises the client's questions, and within seconds shows a short answer drawn from the firm's own material, citing the document it came from. After the meeting the team chats with Claude over the notes, transcript and library.

## 3. Goals

1. **Sign in with Google.** Every member of the presales team signs in with Google. The team shares one library of past material. Who counts as the team is an allow-list in `.env` (`FLECK_ALLOWED_DOMAINS`, `FLECK_ALLOWED_EMAILS`); anyone else is refused at sign-in. The allow-list is only the sign-in gate, not a way to split teams: the MVP has exactly one team (rupak.k's rule). Everyone signed in sees the whole library and every meeting, since team chat spans all meetings; each meeting records who recorded it. **(Visibility: Claude's call.)**
2. **Calendar sync.** The user's Google Calendar appears in Fleck, read-only (`calendar.readonly`; Fleck never writes to it). The user links a meeting to a client by hand, with a dropdown of library clients on the event page; ad-hoc recordings are linked the same way. A meeting with no client still gets a brief, built from its title and description only. (Manual link: rupak.k's rule.) *(Amended: decisions 129, 130, 144.)* Before either goes to Claude, attendee names, client names (and the guests' company, from their email domain), emails, links and long digit runs are redacted. A meeting with a client gets its brief when the client is linked and again 30 minutes before the start; a meeting with no client gets one only when the user presses **Refresh brief**. An ad-hoc recording is linked from its meeting page.
3. **Library.** The team uploads past material (PDF, Word, PowerPoint, Excel, Markdown), filed by client, plus firm-wide material and reference material. Fleck indexes it for search. Each document has a kind: **client** (our past work for that client), **firm** (rate card, capabilities, FAQ) or **reference** (third-party examples). Reference documents may be quoted for their terms but are never presented as the firm's past work. **(Claude's call.)**
4. **Pre-meeting brief.** Before a meeting with a known client: the questions this client is likely to ask, each with the best answer from the library and its source. **(Claude's call, accepted by rupak.k to review.)**
5. **Meeting kick-off.** A macOS notification 5 minutes before a meeting starts recording with one click; an ad-hoc **Start recording** for unplanned calls. Fleck captures the user's mic and the call audio, and transcribes both live on the Mac.
6. **Help during the meeting.** When the client asks a question, a short answer (2–3 lines plus the source) appears in a floating panel on top of every window (§7.8), so the salesperson never switches window or tab. It is written by a local model from the library. When the library has nothing relevant, Claude answers from the question text alone, labelled **General knowledge, not from our material** (§6).
7. **After the meeting.** The recording, full transcript and the user's own notes are saved. A summary keeps the user's notes word for word and adds what was discussed. The notes mark every moment Fleck helped: the question, the answer it showed, its source, and whether the salesperson's reply used it (§7.3).
8. **Team chat on notes.** The team asks Claude questions about one meeting or across all meetings and the library; answers cite meetings, timestamps and documents.
9. **Testable end to end, and left running for review.** A scripted presales conversation, recorded as audio, drives the whole flow in tests. At hand-over the stack is up on rupak.k's Mac with that conversation already processed, so they can play the recording, read the notes and see where the model helped.

10. **User guide.** `docs/user-guide.md`, written for rupak.k to follow alone, step by step, from a clean Mac to a first real meeting:
    1. install prerequisites (Docker, `uv`, Ollama, the pinned models) and fill in `.env`
    2. start the stack and check it's healthy
    3. install the helper, grant Microphone and Screen Recording, pair it with Fleck
    4. sign in (simulated Google first, then real Google, including the Google Cloud setup)
    5. add material to the library
    6. replay the test conversation and read the result
    7. record a first real meeting: link it to a client, read the brief, record, use the live answers, stop, read the notes and the "used it" markers, chat
    8. troubleshooting: permissions, models not loaded, no audio, slow answers

    Every command and every on-screen label in the guide is copied from the product. Before hand-over Claude follows the guide from step 1 on a fresh stack and fixes the guide or the product wherever they differ. **(Claude's call: the guide is checked by walking through it, not by review.)**
11. **Build log.** `docs/superpowers/build-log.md`, kept as the work happens: a dated entry for each build and test run, with what was run, the measured numbers (latency, trigger counts, memory, soak results) and what was changed because of them. Failures and dead ends stay in, not only successes. It complements the decision log: decisions say *why*, the build log says *what happened*.

## 4. Non-goals (not in the MVP)

iOS, Android, Windows; Outlook calendar; several teams with separate data (one team, one tenant in the MVP); billing, pricing tiers, admin console, SSO beyond Google; telling individual client speakers apart (the client side is one speaker, **Client**); CRM, Slack or Notion integrations; editing documents inside Fleck; languages other than English.

## 5. Anti-goals (failure even if it works)

- **A made-up fact shown as ours.** Prices, rates, team sizes, dates, client names and results come only from the library, quoted with their source. If no document backs a number, the answer says so. Claude's fallback answers never contain prices or claims about the firm's past work.
- **Help that arrives too late.** Target (p95 over pooled replays of the test conversation, on rupak.k's Mac): the first words of a library or brief answer appear within **3 s** of the client finishing the question, and the full library answer within **6 s**; the first words of a Claude fallback answer within **5 s** (its timeout). The time to a "Not in our material" outcome is reported, not gated (rupak.k, 2026-10-31). *(Amended: decision 112.)* The gates are split by kind (library and brief, Claude fallback) and the p95 is pooled over every question of at least 3 real-time replays.
- **Missed or misheard questions.** Target: on the test conversation, at least 90% of the client's questions trigger an answer, and no more than one false trigger per 10 minutes.
- **Getting in the way.** Reading an answer must never need a window or tab switch, a click, or a sound. The answer panel (§7.8) floats over the call, never takes keyboard focus, and stays out of screen shares.

## 6. Constraints

- Development and use on rupak.k's Mac: Apple M5, 24 GB, macOS 26. The live path (capture, transcription, local model) runs entirely on it.
- **What leaves the Mac** (rupak.k's rule, with Claude's resolution of the fallback marked):
  - The transcript and the user's notes are meeting text. They go to Claude only for the summary and team chat (goals 7–8), after the meeting, never during it.
  - Library text goes to Claude for the summary and team chat. rupak.k is fine with this, including real client documents later.
  - **The live help path is local.** The helper detects the question, searches Weaviate, asks `qwen3:4b`, and runs the citation and number checks (§7.4), all on the Mac. The api (local Docker) only relays the result to the browser and stores it.
  - The one exception is the live fallback (goal 6). It sends **only the client's question text**, with no transcript and no library text, to Claude Haiku. `FLECK_LIVE_FALLBACK=claude|off` turns it off, making the live path fully local. **(Claude's resolution: rupak.k asked for both a Claude fallback and a local-only live path.)**
  - Test: with `FLECK_LIVE_FALLBACK=off`, a replay of the test conversation fails if anything in the live path opens a connection that leaves the Mac.
  - Audio never leaves the Mac except into Fleck's own storage.
- External secrets: `ANTHROPIC_API_KEY` (from rupak.k), and Google OAuth client id/secret for real Google sign-in. Both in `.env`, never committed.
- Real client documents (`clients/jf/`, Joele Frank) are out of the MVP: the MVP is built and tested on the demo library only. rupak.k adds the real material after accepting the MVP. They are never committed.
- One team, one Mac for the MVP. Several team members may sign in (from the same Mac or, later, a deployed instance) and all see the same data.
- Stable, labelled UI (accessible names on every control) and a deterministic replay mode, so rupak.k's Walker plugin can later drive it for the demo video.

## 7. Software stack (Claude's call)

```
 macOS, native                                    Docker Compose
 ┌────────────────────────────────┐              ┌────────────────────────────────────────┐
 │ fleck-helper (menu bar)        │  WebSocket   │ web (React)  ──▶  api (FastAPI)        │
 │  mic + call audio, or WAV file │─────────────▶│                    │  worker (same code)│
 │  live transcript (MLX Whisper) │  transcript  │ postgres   minio   weaviate   gsim     │
 │  question detection            │  + audio     └────────────────────────────────────────┘
 └────────────────────────────────┘                       │            │
 Ollama (native, Metal GPU): qwen3:4b, embeddings  ◀──────┘            └──▶ Claude API
```

| Part | Choice | Why |
|---|---|---|
| Desktop helper | Python 3.12, `rumps` + PyObjC menu-bar app, packaged with py2app | Python end to end; one language with the api |
| Call audio | ScreenCaptureKit (system audio); mic via AVAudioEngine with Apple voice processing (echo cancellation, since rupak.k uses speakers) | No bot joins the call |
| Live transcription | `mlx-whisper`, `large-v3-turbo`, on the Mac's GPU | Fast enough to keep up with speech, no network |
| Local model | Ollama, `qwen3:4b-q4_K_M` (pinned, §7.5), thinking mode off. Was `qwen3:8b`; measured too slow (§7.1, decision 72) | Qwen3 answers well from only the passages it's given; the 4B size meets the 3 s target where 8B didn't (decision 72); fits next to Whisper in 24 GB |
| Embeddings | Ollama `qwen3-embedding:0.6b`, called by the api; vectors pushed to Weaviate as self-provided vectors (no Weaviate vectoriser module) | Better retrieval for ~0.5 GB more (rupak.k's suggestion); one hop instead of two, and query-embedding time is measured in the api (rupak.k's suggestion) |
| Ollama placement | Native on macOS, not in Docker | Docker on macOS can't use the GPU |
| Vector search | Weaviate, hybrid (keyword + vector) search. Multi-tenancy on, with a single tenant `default`, created by the api at startup (idempotent), never on sign-in | Hybrid search catches exact terms like "SLA"; keeping the tenant means splitting teams later needs no query changes |
| Document parsing | `markitdown` for PDF, DOCX, PPTX, Markdown; a small `openpyxl` reader for XLSX (§7.5) | markitdown loses XLSX headers and number formats (tested, §7.5) |
| api + worker | FastAPI, SQLAlchemy, Alembic, `uv`; background jobs in a Postgres table (no Redis) | Fewer moving parts |
| Storage | Postgres (data, traces, cost); MinIO (audio, transcripts, original documents) | MinIO swaps for S3 in the cloud |
| Web | React + Vite + TypeScript, `pnpm` | |
| Claude | Fallback answers: Claude Haiku 5.5 (speed). Summary and chat: Claude Sonnet 5.5. Both set in `.env` | Fast where it's live, strong where it isn't |
| Google | Real Google OAuth + Calendar; `gsim`, a small Google simulator, for tests and the demo | Tests and the Walker video need a fixed sign-in and calendar |
| Test audio | Scripted conversation spoken with macOS `say` voices, stereo WAV: left = salesperson, right = client | Built in, no extra install; WAVs committed so tests run anywhere |
| Tests | pytest (api, worker, helper pipeline), Vitest (web), Playwright end to end | |

### 7.0 Audio routing

Live and replay differ only at the source. From the 16 kHz streams onward, the code path is the same.

```
 SOURCE (one per recording)                          SAME PATH FOR BOTH SOURCES
 ─────────────────────────                           ──────────────────────────────────────────────

 live                                                 "You" stream ─┐
  Mic ──▶ AVAudioEngine, voice processing ──▶ 16 kHz mono ──────────┤
          (Apple echo cancellation removes                          │   smoothed RMS energy
           the call heard through speakers)                         ├──▶ speaker gate (§7.2)
  Call audio ──▶ ScreenCaptureKit ──────────▶ 16 kHz mono ──────────┤   client / you / overlap
  (whole system output)                       "Client" stream       │
                                                                    ├──▶ VAD ──▶ mlx-whisper
 replay  (tests, demo, Walker)                                      │           partials (preview)
  stereo WAV, played at real-time speed                             │           finals (segment_id)
   left channel  ──▶ 16 kHz mono ──▶ "You" stream ──────────────────┤               │
   right channel ──▶ 16 kHz mono ──▶ "Client" stream ───────────────┘               ▼
                                                              Client final + gate = client
   bleed variant: right channel also mixed into left                     │
   (delayed, quieter, reverb) = call heard through                       ▼
   speakers, for tuning the gate and echo tests             classify() ──▶ live answer path
                                                            (§7.2)         (helper: Weaviate,
                                                                            qwen3:4b, checks §7.4)
                                                                                 │
  Both streams' audio, in chunks ─────▶ api ──▶ MinIO (recording)                ▼
  All finals + answers ───────────────▶ api ──▶ Postgres ──▶ browser (WebSocket)
```

Replay skips only Apple's echo cancellation, which works on live devices and can't be applied to a file. The bleed variant stands in for what leaks past it.

### 7.1 Memory and latency budget

All three models share one GPU on the Mac. Memory fits; latency is the tight part, because every generated token rereads the model's weights and the local model must read the retrieved passages before its first word.

| Model | Memory |
|---|---|
| Whisper `large-v3-turbo` | ~2 GB |
| `qwen3:4b` 4-bit + KV cache (4k context) (was `qwen3:8b`, ~6–7 GB) | ~3–4 GB |
| `qwen3-embedding:0.6b` | ~0.5–1 GB |
| `qwen3-embedding:4b` at `num_ctx` 512, the question classifier (decision 95) | ~2.9 GB |
| **Total** | **~8.5–10 GB** of 24 GB, leaving room for macOS, Docker, browser and the call app |

Time from the client finishing a question to the first words on screen (estimates, to be measured):

| Step | Estimate |
|---|---|
| End of speech detected (silence) | ~0.6 s |
| Whisper finalises the last window | 0.3–0.5 s |
| Question classified (no LLM, §7.2: `qwen3-embedding:4b` classify embed in parallel with the 0.6b search embed, decision 95) + hybrid search | ~0.11 s (1 s budget, then CPU rules; amended: decision 128) + ~0.15 s |
| `qwen3:4b` reads ~1,000 prompt tokens, first token out (measured p95 1.11 s; ~0.1 s when started early) | 1–1.5 s |
| **First words** | **~2.2–2.8 s** against the 3 s target |

To keep headroom: **(Claude's call)**

1. **Answer from the brief first.** The pre-meeting brief's answers are stored; a live question that matches one shows its answer at once, with no generation.
2. **Start early.** Search and the model's prompt reading start on Whisper's partial text when it looks like a question; the work is dropped if the final text differs. *(Amended: decision 103.)* They start on the client's pause instead: at each client final the turn so far is classified, and if it is a question, search and prompt reading start. The trigger at the turn's close reuses that work only when its text is the same; otherwise the work is dropped and search runs again.
3. **Small, warm prompt.** At most 3 passages (~800 tokens); a fixed instruction prefix so its cache is reused; all models loaded (`keep_alive`) from when recording starts.
4. **Fallback model.** If `qwen3:8b` misses the target when measured, switch to `qwen3:4b`.

Item 2 never shows anything on screen: an answer is shown only after the final-text trigger in §7.2.

**Classifier budget** *(amended: decision 128)*. The classify embed may take up to **1 s** (over 0.30 s is logged as slow). Past 1 s, or on an error, strict CPU rules decide, and a circuit breaker keeps the rules deciding for 10 s. A client turn the rules said no to is classified again on its own thread for up to 30 s. If it turns out to be a question, its answer is shown with a **Late** tag next to the source label (panel, live page and notes): it answers an earlier question.

**Measured (2026-10-30, M5, decision 72).** With fresh passages and a fresh question each time (1,045 prompt tokens), `qwen3:8b` took 2.59 s (p95) to its first token, putting first words at ~3.8 s, over the target; `qwen3:4b` took 1.11 s, giving ~2.4 s. Item 4 applied: the live model is `qwen3:4b`, and item 2 (start early) is mandatory, which cuts the first token to ~0.1 s when the passages were read on the partial text.

Measuring this budget on rupak.k's Mac is the first task of the build. If the measured numbers can't meet §5, work stops and goes back to rupak.k.

### 7.2 Question detection (rupak.k's design, with Claude's adjustments marked)

The pipeline is fixed: **VAD-flushed segment → speaker gate → local classifier → trigger.** No LLM call and no diarization.

1. **Segments, not streams.** Whisper is not truly streaming. "Partials" are re-inferences of the current audio chunk and are treated as an early preview only. A segment becomes final when VAD sees end of speech (300–700 ms of silence, counted in §7.1).
2. **Speaker gate.** Call audio is **Client** and mic audio is **You** by construction (separate streams). For triggering, smoothed RMS energy decides whether the segment is clean client speech:
   `client_speech = sys_energy > mic_energy × 1.5 AND mic_energy < silence_threshold`.
   *(Amended: decision 93.)* The built gate relies on echo cancellation, not loudness. Each 200 ms window of smoothed RMS counts as client only when `mic ≤ max(noise_floor, client × FLECK_GATE_BLEED_MAX)`, with `FLECK_GATE_BLEED_MAX=0.3` (a client-to-mic ratio of at least 3.3). The noise floor is twice the 10th percentile of mic RMS over the last 10 s, never below `FLECK_GATE_SILENCE`. **You** mirrors the rule, and 75% of the audible windows decide the segment. Weak echo cancellation (bleed above 0.3×) is gated overlap and never triggers.
   Both channels active means **overlap**: never a trigger. Multiplier and threshold are tuned on the **bleed** recording (call audio leaking into the mic through the speakers, after Apple's echo cancellation), not on clean audio. **(Tuning on bleed: Claude's adjustment.)**
3. **Classifier contract.**
   ```
   classify(transcript: str, speaker: Literal["client", "you", "overlap"]) -> QuestionVerdict
   QuestionVerdict: is_question: bool, confidence: float, reason: str
   ```
   It runs on CPU in under 50 ms, so it never competes with Whisper or Qwen for the GPU. *(Amended: decisions 94, 95, 128.)* It does not: hard vetoes run on the CPU (under 4 words, call mechanics and small talk, tag questions, cut-off lines, requests to send a document), and everything else is scored by a logistic regression on a `qwen3-embedding:4b` vector computed by Ollama on the GPU, in parallel with the search embed. The budget is 1 s (§7.1); past it, strict CPU rules decide. The pipeline triggers only when `speaker == "client"`, `is_question` and `confidence ≥` a configured threshold. The implementation behind the contract (rules plus a small CPU model) is replaceable without touching the pipeline.
4. **Trigger once, on final text.** Only final segments can trigger, and each `segment_id` triggers at most once. Speculative search and prompt reading on partials (§7.1, item 2) stay invisible. *(Amended: decision 103: the speculative work starts on client finals before the turn closes, not on partials.)*
5. **Precision first.** A false trigger interrupts the salesperson mid-sentence; a missed question can be asked again. **(rupak.k's text said both "false triggers cost more" and "prioritise recall"; Claude reads it as precision first.)**

**Negative-sample test (a blocker, not a description).** A committed negative set of 10–15 minutes, both speakers, generated like the test conversation (§8):
- very short segments under 1 s ("uh-huh", "okay", "got it", "hmm")
- 2–3 s small talk and confirmations
- meeting-logistics questions that are not for the library: "Can you see my screen?", "Can you hear me?", "Shall we move on?" **(Claude's addition: the most likely false triggers.)**

Assertion: **zero** triggers on the whole set, reported separately for short and medium segments. Any trigger fails the build. This is stricter than the ≤ 1 false trigger per 10 minutes in §5, which still applies to the full test conversation.

**Reported separately** (rupak.k's rule), each as its own named test with its own count in the output, so a pass on one never hides a failure on another:

| Test | Measure | Pass |
|---|---|---|
| `negative_set_short` | triggers on segments under 1 s | 0 |
| `negative_set_medium` | triggers on 2–3 s segments and logistics questions | 0 |
| `conversation_false_triggers` | triggers on the test conversation that match no scripted question | ≤ 1 per 10 min |
| `conversation_recall` | scripted client questions that triggered | ≥ 90% |

Each runs at two levels: the classifier on the reference transcripts (fast, every test run, including CI), and the full replay through Whisper on the Mac (the true measure, recorded in the hand-over note). **(Two levels: Claude's call.)**

**Known limit.** A colleague of the salesperson who joins the call remotely is heard through call audio and treated as **Client**.

### 7.3 The "used it" marker (rupak.k's design, with Claude's adjustments marked)

Each answer Fleck showed is marked **Used** or **Not used** in the notes.

- **Automatic value.** For each **You** final segment that starts within 15 s after the answer appeared, compute cosine similarity between that segment and the shown answer, using the same embedding model as the library. If the highest similarity is ≥ 0.75, the answer is **Used**. The 0.75 is a starting value, set from the test conversation.
  - *(Amended: decisions 118, 127.)* The threshold is **0.52** (`FLECK_USED_THRESHOLD`), set from the replayed test conversation: at 0.75, three of the four used answers were missed. A **You** segment counts when it *overlaps* the 15 s window after the answer appeared (it ends after the answer appeared and starts before the window closes), as long as it began no earlier than 2 s before the question ended. A salesperson who starts talking as the card appears is reading it.
  - Computed after the meeting, not live, so it never competes for the GPU during the call. **(Claude's adjustment.)**
  - Highest single segment, not the whole reply joined together, so one matching sentence in a long reply still counts. **(Claude's adjustment.)**
- **Manual override.** In the notes, each marker shows its value and whether it is automatic or set by hand. One click flips it; a manual value always wins, and recomputing never overwrites it.
- **Testable.** The test conversation's script marks, for each answered question, whether the salesperson uses the answer. It includes at least one answer that is ignored, and one where the reply is on-topic but doesn't use the answer. The automatic marker must agree with the script on every one of them.
- **Known limit.** Similarity measures topic, not correctness: a reply on the same subject with a different number can still be marked **Used**. The override covers this.
- *(Amended: Task 22 measurement, decision 159.)* The marker does not agree with the script on every answer: `q_liability`'s on-topic reply scores 0.519–0.555, so it reads **Used** in 3 of 4 runs where the script says not used. The threshold was not retuned; the hand-over lists it as a known limit.

### 7.4 Citations and anti-goal enforcement (rupak.k's rules, with Claude's adjustments marked)

The model never decides what a source is called. Rules enforced in code, not only in prompts:

**Library answers (`qwen3:4b`)**
1. The prompt gives at most 3 passages, numbered `[1]`–`[3]`, each with its document kind (client / firm / reference). Each claim must end with the number of the passage it comes from. If the passages don't answer the question, the model replies `NONE`, which hands over to the fallback.
2. The api maps each `[n]` to the Weaviate result's `doc_id` + `chunk_id`. **An answer with no valid citation, or citing a number it wasn't given, is rejected.** The source line on screen (document title, client, kind) is rendered from Weaviate metadata, never from the model's text. **(Numbered markers instead of the model writing `doc_id`s: Claude's adjustment, cheaper and streamable.)**
3. **Number check.** Every figure in a sentence (amounts, currencies, %, durations, team sizes, dates) must appear, after normalising ("6,900" = "6900" = "six thousand nine hundred"), in a passage that sentence cites. Sentences without figures stream to the screen token by token. Sentences with a figure are held until complete and checked. A sentence that fails is replaced by **Not in our material** with a link to the cited source. *(Amended: decisions 76, 123g: no link is shown; no event carries one.)* **(Claude's addition: a valid citation alone doesn't stop a wrong number.)**
4. An answer citing only **reference** documents is labelled **Reference contract, not our work**.

**Fallback answers (Claude Haiku)**
5. The system prompt states: no prices, rates, percentages, SLA figures, team sizes, client names or claims about the firm's past work; such output is blocked.
6. Code blocks any fallback answer matching: currency symbols or codes with a number, a number followed by `%`, rates ("per hour/day/month"), SLA or response-time figures, or the name of any client in the library. **A blocked answer is not retried live**; **Not in our material** is shown instead. **(No live retry, and client names taken from the library: Claude's adjustments.)**

**Number normalisation.** One shared function, `normalize_numbers(text)`, in the helper's live-path package, used by the number check (3), the fallback block (6) and the tests that compare Whisper output with the reference transcripts. It converts spoken and written forms to one canonical form: "six thousand nine hundred", "6,900", "$6,900" and "6900 dollars" → `6900 USD`; "five percent" and "5%" → `5 %`; "thirty days" → `30 days`. Words-to-number conversion inside sentences uses the `text2num` library (`alpha2digit`), not hand-written code. A table-driven unit test covers each form. **(Claude's call.)**

Note on where words appear: the number check compares the model's answer with library passages, and both are usually digits. Whisper `large-v3-turbo` mostly writes numbers as digits, but not always ("five percent", "six thousand"). So the same function is applied wherever Whisper text is compared with anything (rupak.k's point).

**Every rejection or block** is kept in the trace with its reason and shown nowhere else.

**Tests.** Unit tests feed the validators crafted bad answers: no citation, out-of-range citation, a correct citation with a wrong number, a fallback with a price, a fallback naming a library client. Each must be rejected. The test conversation's pricing question with no backing document must end in **Not in our material**.

### 7.5 Stack risks and how they're handled (rupak.k's review, with Claude's adjustments marked)

**macOS permissions.** Committed now in `helper/packaging/`:
- `Info.plist`: bundle id `ai.fleck.helper` (never changes), `LSUIElement` (menu bar only), `NSMicrophoneUsageDescription`, `NSScreenCaptureUsageDescription`.
- `entitlements.plist`: `com.apple.security.device.audio-input`, needed under the hardened runtime, and `com.apple.security.cs.disable-library-validation`: a self-signed identity has no Team ID, so library validation would refuse the bundled Python libraries (decision 68).
- **No screen-capture entitlement exists.** macOS grants Screen Recording per app through the privacy prompt, keyed to bundle id + signing certificate. So the stable bundle id and the one self-signed certificate (§9) are what keep the permission across rebuilds. **(Claude's correction of the suggested `com.apple.security.screen-capture`.)**
- The first build task proves all four edges together from the signed bundle: menu-bar app runs, mic records, system audio records, notification action fires.

**Document parsing, tested on 2026-10-30** with a rate card built as XLSX and PPTX:
- PPTX table: clean Markdown table, headers and values intact.
- XLSX: values intact, but a merged title row became the header (`Unnamed: 1`, `Unnamed: 2`), pushing the real header into the data, and number formats were dropped (`$9,800` → `9800`).
- PDF (CUAD contract): text clean; PDF tables not yet tested.

So: XLSX is read with `openpyxl` directly (already a markitdown dependency, no docling or unstructured): header row = first row with every cell filled, displayed (formatted) values kept, merged title kept as a caption. **Every table, from any format, is chunked by rows with the header repeated in each chunk**, so a passage like "Tech lead · Offshore (Bengaluru): $6,900" carries its own column meaning. A test fixture (XLSX with a merged title row and currency formats) guards this. **(Row chunking: Claude's call.)**

**Embeddings.** Computed in the api and pushed as vectors; Weaviate still does the keyword half of hybrid search. The suggested `text2vec-nomic` is moot since the model is now `qwen3-embedding:0.6b`.

**Ollama models pinned.** `make ensure-ollama` pulls these tags and fails unless the local model id matches the pinned manifest digest (Ollama shows the first 12 characters as the model ID). Ollama can't pull by digest, so the check runs after the pull. **(Check-after-pull: Claude's adjustment.)**

| Model | Tag | Manifest digest (registry, 2026-10-30) | Size |
|---|---|---|---|
| Answer model | `qwen3:4b-q4_K_M` | `sha256:2bfd38a7daaf4b1037efe517ccb73d1a3bbd4822cf89f1a82be1569050a114e0` | 2.6 GB |
| Embeddings | `qwen3-embedding:0.6b` | `sha256:ac6da0dfba84a81fdbfbaf330198c33cd77c4cdfc53e8bc50eb581914a15621d` | 0.64 GB |
| Question classifier (added: decision 95) | `qwen3-embedding:4b` | `sha256:df5bd2e3c74cd8d069d21dc038f1b359fcdc9458fce1c99bd43c9eb1518ff907` | |
| Former answer model (measured too slow, decision 72) | `qwen3:8b-q4_K_M` | `sha256:500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41` | 5.2 GB |

### 7.6 Ports (fixed, rupak.k's rule)

Fixed so Walker and the tests can rely on them. All bound to `localhost`.

| Service | Port |
|---|---|
| web (React) | 5173 |
| api (FastAPI, REST + WebSocket) | 8000 |
| weaviate | 8080 (HTTP), 50051 (gRPC) |
| gsim (Google simulator) | 8090 |
| postgres | 5432 |
| minio | 9000 (S3 API), 9001 (console) |
| ollama (native) | 11434 |

`deploy/docker-compose.yml` and `.env.example` use exactly these. **(Ports other than api, web and weaviate: Claude's call.)**

### 7.7 Long meetings (up to 1 hour, rupak.k's requirement)

Presales meetings run 30–60 minutes. The stack must stay steady for that long. **(Design below: Claude's call.)**

| Risk over an hour | Design |
|---|---|
| Ollama unloads idle models after 5 minutes by default; the next answer would wait seconds for a reload | While recording, every Ollama request sets `keep_alive: -1`; when recording stops, the models are released |
| Mac sleeps, or the ScreenCaptureKit stream stops | The helper holds a no-sleep assertion while recording. If the capture stream ends unexpectedly, it restarts it and marks the gap in the transcript. *(Amended: decision 139.)* The marker is a transcript line labelled **Fleck**, such as "Audio from the call was interrupted (m:ss–m:ss)" ("Your microphone…" for the mic) |
| Live prompts grow with the meeting | A live prompt holds only the question and ≤ 3 passages, never the running transcript, so answer latency doesn't depend on how long the meeting has run |
| Memory grows (1 hour of two 16 kHz streams ≈ 230 MB raw) | Audio is written in chunks to local disk and uploaded to MinIO, never accumulated in memory. Whisper works only on the current window |
| Connection between helper and api drops | The helper queues unsent segments and audio chunks and resends them in order on reconnect; the api ignores duplicates by `segment_id` and chunk number. The browser reloads the meeting state when it reconnects |
| Helper crashes mid-meeting | Everything sent is already stored. On restart the helper finds the meeting still `recording` and offers to resume it |
| GPU slows as the Mac heats up | Can't be designed away; measured by the soak run |

**Soak run.** The test conversation is looped through replay for 60 minutes on rupak.k's Mac, unattended. Pass:
- p95 time to first words in the last 10 minutes is within 20% of the first 10 minutes
- the helper's memory grows by less than 300 MB
- every scripted question in every loop gets its expected outcome

*(Amended: decision 143, rupak.k's rulings.)* Memory growth is measured from the first sample after loop 1, because MLX fills its buffer cache during the first loop and then stays flat; the helper's physical footprint (including Metal buffers) is measured, not RSS. A live-fallback Claude timeout that ends in **Not in our material** does not fail "expected outcome"; it is reported as `claude_timeouts`.

Results go into the hand-over note (§9).

### 7.8 The answer panel (rupak.k's requirement: no window switching; design: Claude's call)

A native macOS panel drawn by the helper (PyObjC `NSPanel`), not a browser window.

- **Always on top, never in the way.** A non-activating floating panel: shown over every app and every Space, including full-screen Zoom, Meet and Teams. It never takes keyboard focus or makes a sound.
- **Placement.** Top-centre, just under the camera, about 540 pt wide and as tall as the answer (mockups), so reading it keeps the eyes near the lens. It can be dragged, and remembers where it was put.
- **Content**, newest only:
  - while working: the client's question and **Thinking…**
  - then: the question (one line), the answer streaming in, and the source line: a label chip plus detail text, exactly as in the mockups: **Our material** + document title (from Weaviate metadata, §7.4); **General knowledge** + "Not from our material"; **Not in our material** (amended: decision 123g: no link to a closest document); **Reference contract** + "Not our work: <title>"
- **History.** ◀ ▶ step through this meeting's earlier answers; a new answer always jumps back to the newest.
- **Show / hide.** The global shortcut ⌥⌘F, or the menu-bar item. It appears automatically when recording starts and hides when recording stops.
- **Direct from the helper.** The helper produces the answer (§6), so the panel shows it with no trip through the api and browser. The same answer is also sent to the api for storage and the browser.
- **Screen sharing.** The panel is excluded from screen capture (`NSWindow.sharingType = .none`). Some macOS versions and sharing apps ignore this, so the manual check shares the full screen in Zoom and in Meet. If the panel leaks, the user guide says to share a single window instead.
- **Browser.** The meeting page keeps the full live transcript, the user's notes, and the answer history, for everything not needed mid-sentence.
- **Testable.** The panel's content is driven by the same events the browser receives. Tests assert the event sequence (question → thinking → answer tokens → source); the panel itself is checked by hand and by a screenshot in the build log.

## 8. The test conversation

A 6–8 minute presales call between rupak.k as salesperson (left channel) and a prospect (right channel), voiced by two different `say` voices. The prospect is a fictional firm wanting AI automation of an existing document workflow. **(Claude's call.)** The script includes, in order:

- small talk and agenda (no questions to answer, so no false triggers)
- questions the library answers exactly: rates, how fast a team can start, IP ownership, SLAs, a similar past project and its results
- a question the library covers only in general, answered by Claude and labelled as general knowledge
- a pricing question for which no document gives a number, where the answer must say so
- a few overlaps and interruptions, and spoken numbers and dates
- *(Added: decision 89.)* an eighth question, "Would you cap liability at twelve months of fees?", expected from reference material. *(Amended: decision 101.)* The local model replies NONE to it, so it goes to the Claude fallback and is labelled **General knowledge**: a known limit.

The script, the reference transcript and the expected outcome for each question (answered from library / Claude / "not in our material", and which document) are committed next to the WAV. They are the acceptance test for goals 5–7 and the anti-goal targets.

The demo library is the minimum needed to pass this test, about 10 pages in total (rupak.k's rule):
- `_firm/rate-card.md` (rates), `_firm/delivery-faq.md` (IP, SLA, warranty, start time) and `_firm/capabilities.md`: kept as they are
- `larkspur-retail/`: kept as it is
- three new one-page AI-automation case studies, each in its own client folder: a track-changes legal document flow, an investigation research agent, a contract-extraction pipeline **(the three topics: Claude's call)**
- the CUAD contracts in `clients/_reference/`, as reference material

Content work never blocks development: the case studies are written in the same task as the test conversation script.

## 9. Ready for rupak.k's review when

1. `docker compose up` plus the helper bring up the whole stack on rupak.k's Mac.
2. The test conversation has been replayed through the real pipeline into a meeting called **Presales: demo prospect**.
3. In the browser rupak.k can sign in, see the calendar, open that meeting, play the recording, read the transcript and the notes with every moment Fleck helped marked, and chat about it.
4. All automated tests pass, and the latency and question-detection targets in §5 and the 60-minute soak run (§7.7) are measured on the Mac and written into the hand-over note.
5. The decision log lists every call made without rupak.k, each with the critique agent's verdict.
6. The build log (goal 11) covers every build and test run, including the measurements in point 4.
7. The user guide (goal 10) has been followed end to end on a fresh stack, with no step that differs from the product.

The hand-over runs on `gsim` by default: the demo meeting is linked to a fixed event in the simulated calendar, so it works without any Google setup. Real Google sign-in and calendar are switched on by putting OAuth credentials in `.env`; the hand-over note gives the Google Cloud setup steps and rupak.k checks real sign-in by hand. **(Claude's call.)**

Live capture and the answer panel are checked by hand before hand-over: one real call on speakers in a browser (Google Meet) and one in a desktop app (Zoom), each with a full-screen share to confirm the panel doesn't appear in it, recorded with whole-system audio capture, with the results in the hand-over note. *(Amended: decisions 60, 124.)* Claude cannot hold a real call, so these calls, a real notification's **Record** and the ⌥⌘F shortcut with Accessibility are left to rupak.k, first in `manual-checks.md`. The automated stand-ins are a speaker test (afplay through the speakers into the signed app's capture) and a full-display capture that checks the panel stays out of it. The automated tests use replay. The helper is signed with a local self-signed certificate so macOS keeps its Screen Recording and Microphone permissions across rebuilds; no Apple Developer ID needed. **(Claude's call.)**

## 10. How the work runs after this is approved

- rupak.k approves this document. From then on Claude writes the detailed spec, the plan, the tests and the code without waiting for them.
- A **critique agent** stands in for rupak.k. It reviews each significant decision and each finished milestone against this document, and its verdict goes into the decision log with the decision.
- Claude stops and asks rupak.k only if a goal or anti-goal in this document can't be met as written.
- Work happens on feature branches with PRs. rupak.k reviews the result and the decision log at the end and corrects course.

## 11. Left to Claude

Milestone breakdown; data model; REST routes; UI layout and copy; prompt wording; classifier implementation behind the §7.2 contract and its thresholds; streaming window settings; chunk sizes; how "used it" is measured; compose service names.
