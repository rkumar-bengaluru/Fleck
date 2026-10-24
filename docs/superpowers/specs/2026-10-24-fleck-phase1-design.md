# Fleck Phase 1 (MVP): design

Date: 2026-10-24. Author: rupak.k with Claude. Status: draft for review.

Source material: [README.md](../../../README.md) (the full product feature list) and the AEVIS project at `C:\2026\rkumar-ava\AEVIS`, whose `milestone/step-*.md` files and `.walker/` folder are the model for how this project is delivered and documented.

Items marked **(Claude's call)** were decided by Claude during design, not by rupak.k. Check them while reading.

---

## 1. Intent

Fleck is a bot-free AI notepad for meetings. Phase 1 is the MVP rupak.k will use in real meetings on their own Mac:

- sign in with Google, see the calendar in Fleck
- get a desktop notification 5 minutes before a meeting and start recording from it
- watch a **live transcript** produced locally by MLX Whisper, next to their own typed notes
- after the meeting, get a Claude-written summary that keeps their notes word for word (black) and adds what Claude inferred (grey)
- chat with one meeting, or across all meetings, through Weaviate
- see what every Claude call cost and inspect its trace

Why local transcription: no network round trip, so the live transcript keeps up with speech. Why the AEVIS delivery style: every milestone is small, runs as a real stack, and is manually verified by rupak.k before the next one starts.

## 2. Goals, non-goals, anti-goals

**Goals (Phase 1)**
1. Live transcription of mic + system audio on macOS, transcribed on-device with MLX Whisper.
2. Google sign-in and Google Calendar sync.
3. A menu-bar desktop app that notifies 5 minutes before a meeting and starts/stops recording.
4. A meeting page with the user's notes and the live transcript side by side.
5. Audio and the full transcript saved to blob storage when the meeting ends.
6. Post-meeting summary by Claude, preserving the user's notes verbatim, with templates.
7. Weaviate-backed chat over a single meeting and across all meetings, with citations.
8. LLM traces and cost, stored in Postgres, with Fleck's own screens.
9. A complete, in-sync `.walker/` golden plus one milestone doc per milestone.

**Non-goals (Phase 2 or later)**: iOS, Android, Watch, Windows app; Outlook calendar; Spaces, sharing, comments; Notion, Slack, Zapier, CRM, Gmail, task-tool integrations; Briefs and morning digest; custom Recipes and a marketplace; MCP server, public API, webhooks; SSO, admin dashboard, billing, pricing tiers; real speaker diarization and voice prints; auto-start/auto-stop recording from the calendar; CSV/Markdown export; audio retention policies; handling audio device changes mid-call beyond not crashing.

**Anti-goals (would make Phase 1 a failure even if it works)**
- **The live transcript lags.** Target: partial text appears within **1.5 s** of speech; finalised text within **3 s** (p95, measured on the replay fixture on the M-series Mac).
- **The live transcript misses words.** Target: word error rate **≤ 10%** on the replay fixtures, and no speech lost at chunk boundaries (every fixture sentence appears complete in the final transcript).
- **Other people's words show up as yours.** Target: on the bleed fixture, at most 2% of **Others** words reappear as **You**, while your real speech stays at or under the 10% word error rate (§4.3, 3a).
- **Docs and golden drift from the product.** Any wording, label or button in a milestone doc that doesn't match the take and the source is a defect (§9).

Both transcript targets are part of sign-off for Milestone 3. If MLX Whisper cannot meet them, that milestone stops and the issue comes back to rupak.k before anything is built on top.

## 3. Constraints

- **Hardware**: development and use on rupak.k's Mac (Apple Silicon, 24 GB). The spec and plans are written on Windows; all building happens on the Mac.
- **macOS 13+** (ScreenCaptureKit system-audio capture). Needs Screen Recording and Microphone permissions.
- **Single user now, multi-user safe** (§5.8): built so a cloud deploy works for anyone who signs in with Google, but Phase 1 ships no sign-up, teams, billing or admin.
- **English only** in Phase 1. Whisper's language is fixed to English, with no per-window language detection.
- **Claude API** for summary and chat, key in `.env`. Google OAuth client id/secret in `.env` for real Google. These are the only external secrets.
- **Milestones of 1–5 tasks.** After each milestone rupak.k verifies the stack by hand before the next starts.
- **The `.walker/` golden follows the Walker standard** as practised in AEVIS's `.walker/` (`format: walker/0.1`). The Walker repo is not touched. When Walker later plays the golden, any deviation is a product defect.

## 4. Architecture

```
 macOS (native)                              Docker Compose (local now, cloud later)
 ┌──────────────────────────────┐           ┌──────────────────────────────────────────┐
 │ fleck-helper (menu bar)      │  HTTPS +  │ web (React)   ──/api──▶  api (FastAPI)   │
 │  capture: mic + system audio │ WebSocket │                          │   │   │       │
 │  source: live | replay WAV   │──────────▶│                worker ◀──┘   │   │       │
 │  MLX Whisper (streaming)     │  text +   │ (summary, indexing,          │   │       │
 │  notifications, Record/Stop  │  audio    │  calendar sync)              │   │       │
 └──────────────────────────────┘           │ postgres   minio(S3)   weaviate + t2v    │
                                            │ gsim (Google simulator, dev/test/walker) │
                                            └──────────────────────────────────────────┘
 Browser: meeting page shows notes + live transcript pushed by api over WebSocket.
 api ──▶ Claude API (summary, chat). api ──▶ Google (real) or gsim (simulated).
```

### 4.1 Components

| Component | What it is | Decided by |
|---|---|---|
| `helper/` | Native macOS menu-bar app, Python, `rumps` + PyObjC. System audio via ScreenCaptureKit, mic via AVFoundation. MLX Whisper `large-v3-turbo` (via `mlx-whisper`). Runs outside Docker. | Native helper: agreed. Libraries and model: Claude's call |
| `api/` | Python, FastAPI, `uv`, Postgres migrations. REST + WebSockets. | Claude's call |
| `worker` | Same codebase as api; runs summary generation, Weaviate indexing, calendar sync. | Claude's call |
| `web/` | React + Vite + TypeScript, `pnpm`. Visual design done with the frontend-design plugin. | Plugin: rupak.k. Stack: Claude's call |
| `postgres` | All structured data, including LLM traces and cost. | Traces in Postgres: rupak.k |
| `minio` | S3-compatible blob store for audio and transcripts. S3 in the cloud, no code change. | Blob storage: rupak.k. MinIO: Claude's call |
| `weaviate` | Vector store with the local `text2vec-transformers` module, so text is embedded on the machine. One Weaviate tenant per user. | Weaviate: rupak.k. Vectoriser and tenancy: Claude's call |
| `gsim/` | Google simulator: OAuth consent and token endpoints, userinfo, Calendar events list, from a fixed fixture calendar. Used by tests and the golden. | Agreed |

Ports, compose file layout and container names are left to the builder. Suggested: web 8080, gsim 8090, MinIO console 9001. Compose lives at `deploy/docker-compose.yml`, as in AEVIS.

### 4.2 Google: real vs simulated

The api reads Google's auth, token, userinfo and calendar base URLs from config. In the walker set, tests and local dev-without-Google, they point at `gsim`. On rupak.k's Mac for real use, they point at Google. **The Fleck UI is identical either way.** The sign-in page has one **Sign in with Google** button. In the walker set it leads to gsim's consent page, where the take picks the fixture account and presses Allow.

gsim fixture calendar (**Claude's call** on content, to be fixed in Milestone 1): fixture account `Rupak K <rupak@fleck.test>`; "Weekly sync with Acme" today with Sarah and Tom and a Google Meet link; a recurring daily "Standup" with a Zoom link in the location; an all-day "Offsite" block, which must not appear. Event times are relative to "now", so the golden is stable on any day.

Real Google is verified by rupak.k by hand in Milestone 1 (§8, manual checks).

### 4.3 Capture and live transcript pipeline

1. **Source.** `live`: mic and system audio as two streams (ScreenCaptureKit + AVFoundation), resampled to 16 kHz mono each. `replay`: a WAV file (stereo: left = mic/You, right = system/Others) fed at real-time speed through the *same* downstream path. Replay exists so the golden and tests are deterministic. It is not a separate code path past the source.
2. **Streaming transcription.** Rolling windows per stream with overlap. Partial hypotheses are emitted quickly and replaced; finalised segments are committed once stable. Overlap and de-duplication must not drop or double words at window boundaries. The exact windowing (window length, overlap, VAD) is left to the builder, constrained by the targets in §2.
3. **Speakers (Claude's call).** Mic segments are labelled **You**, system-audio segments **Others**. No diarization in Phase 1.
3a. **Echo: other people's voices leaking into the mic.** rupak.k doesn't wear headphones, so the mic also hears the call through the Mac's speakers. Without handling, the other people's words appear twice, as **You** and as **Others**. Three layers:
   - **Cancel.** Mic capture uses Apple's voice-processing mode (`AVAudioEngine` input with voice processing enabled), which removes the Mac's own speaker output from the mic signal. (Milestone 2.)
   - **Detect the route.** The helper checks the current output device through CoreAudio. On headphones or AirPods there is no echo, so cancellation and the echo check below are skipped. The meeting stores which mode it ran in. (Milestone 3.)
   - **Catch what leaks through.** Before a mic segment is finalised, it is compared with the system stream over the same window: audio cross-correlation within a ±500 ms delay range, and a text check that drops a **You** segment that closely matches an **Others** segment within ±2 s. A dropped segment is kept in the trace as `echo_suppressed` for debugging and never shown. When both people really speak at once, the mic segment is kept, so the text check must not delete real overlapping speech. (Milestone 3.) Thresholds are left to the builder and tuned on the bleed fixture (§8).

   Apple's cancellation works on the live audio devices, so a WAV file can't test it. The bleed fixture tests the third layer. Live cancellation is a manual check in Milestones 2 and 3: a real call on the speakers, with no **You** lines echoing the other side.
4. **Transport.** The helper holds an authenticated WebSocket to the api and sends segments `{meeting_id, stream, start_ms, end_ms, text, final}`. The api stores final segments and fans partials and finals out to the browser over the meeting's WebSocket. The helper uploads audio to the api in chunks during the meeting; the api writes them to MinIO.
5. **Stop.** From the menu bar or the web page's **Stop** (the api relays it to the helper). The helper flushes, the api assembles the final audio file and a transcript JSON in MinIO and marks the meeting `processing`, then the worker summarises and indexes it, then `ready`.

Meeting states: `scheduled` (from calendar) → `recording` → `processing` → `ready`, or `failed` with a reason.

### 4.4 Desktop helper behaviour

- **Pairing.** Web **Settings** → **Connect desktop app** shows a short one-time code. The helper's menu (or `fleck-helper pair <code>` on the command line) exchanges it for a per-device token. The helper stores the token in the macOS Keychain. The api may be remote, so the helper never relies on localhost trust.
- **Menu.** The next few meetings for today from the api; **Start recording** (ad-hoc, creates "Untitled meeting" with the start time); while recording, elapsed time and **Stop**.
- **Notification.** At T−5 minutes for each upcoming meeting with a meeting link or attendees: a macOS notification titled with the meeting name, "starts in 5 min", with a **Record** action. Pressing it starts recording linked to that event and opens the meeting page in the browser. **Risk**: action buttons may need a properly bundled app. Milestone 2 proves this first. If the action proves unreliable, clicking the notification body starts recording instead; the doc and golden then say so.
- **Packaging (Claude's call).** The helper ships as an ad-hoc-signed `.app` bundle built with py2app, with its own bundle id (`ai.fleck.helper`). That gives it its own Screen Recording and Microphone permission entries and a real notification identity, without needing an Apple Developer ID. Building the bundle is part of Milestone 2 task 2. The helper stays Python. Swift is only considered if Milestone 2 shows the notification action or ScreenCaptureKit can't work from PyObjC, and that would come back to rupak.k first.
- **Command line** (for tests, golden and debugging): `fleck-helper pair`, `fleck-helper replay <wav> --meeting <id>`, `fleck-helper status`.

### 4.5 Meeting page (layout A, agreed)

Top bar: meeting title, attendees, a red recording dot with elapsed time, **Stop**. Left half: **My notes**, a plain text editor (bullets by typing `-`), autosaved. Right half: **Live transcript**, final segments in the normal transcript colour with speaker and timestamp, the current partial in grey italics, auto-scrolling unless the user scrolls up.

After the meeting, the same page has **Notes**, **Transcript** and **Chat** tabs (mockup accepted in brainstorming; visual design comes later from the frontend-design plugin):

- **Notes** shows the summary: Summary, Key points, Decisions, Action items, Key quotes, Open questions. Text the user typed is **black and verbatim**; what Claude added is **grey**. A **Template** picker and **Regenerate**.
- **Transcript** shows the full transcript and an audio player. **(Claude's call)** Clicking a timestamp seeks the audio.
- **Chat**, §4.7.

Exact wording of every label and empty state is fixed when the milestone is built and becomes binding through the golden (§9).

### 4.6 Summary (hybrid notes)

- The worker sends the user's notes, the transcript and the chosen template's instructions to Claude, using tool use with a structured output schema. Each output item carries `user_text` (copied from the notes, may be empty) and `ai_text`.
- **Verbatim guard.** The api checks that every `user_text` is an exact substring of the user's notes. If not, it retries once, then falls back to showing that note line unchanged in black with the AI text separately. The user's phrasing is never rewritten.
- **Templates (Claude's call)**: General (default), 1:1, Standup, Sales call, Customer interview. Templates are prompt files in the repo, not user-editable in Phase 1. Picking one after the meeting and pressing **Regenerate** makes a new summary version; the previous one is kept.
- **Model**: from `.env` (`FLECK_CLAUDE_MODEL`), default the current Claude Sonnet. Target: summary ready in under 15 s for a 1-hour meeting.

### 4.7 Weaviate and chat

- **Indexing**: when a meeting becomes `ready`, the worker writes transcript chunks (with meeting id and start/end ms), note lines and summary sections to Weaviate under the user's tenant. Regenerating a summary re-indexes its sections.
- **Meeting chat**: the **Chat** tab, retrieval restricted to that meeting.
- **Library chat**: a **Chat** entry on the library page, retrieval across all of the user's meetings.
- **Answers cite their sources**: each citation names the meeting and timestamp and links to that line in the Transcript tab.
- **History (Claude's call)**: one chat thread per meeting and one library thread, kept in Postgres.

### 4.8 LLM traces and cost

- Every Claude call (summary, regenerate, chat) writes one row: time, purpose, model, prompt (system + messages), response, input/output tokens, cache tokens if any, latency, status/error, meeting id, cost.
- **Cost** = tokens × a price table kept in config (per model, per million tokens), so prices can be updated without code. Cost is stored on the row at call time.
- **Screens**: a cost line on each meeting ("Claude cost: $0.04 · 3 calls"); a **Usage** page with this month's and previous months' totals by purpose; a **Traces** list (filter by purpose and meeting) and a trace detail page showing the full prompt and response.
- Recording starts in Milestone 5 (first Claude call); the screens come in Milestone 7.

### 4.9 Data model (outline)

Every user-owned row carries `user_id`, and every query filters by the signed-in user. Tables, roughly: `users`, `google_accounts` (tokens encrypted at rest), `devices` (helper tokens), `pairing_codes`, `calendar_events`, `meetings`, `transcript_segments`, `notes`, `summaries` (versioned, template, structured items), `chat_threads`, `chat_messages`, `llm_calls`. Blob keys are prefixed by user id. Column-level design is left to the builder.

### 4.10 Calendar sync

On sign-in and then every 5 minutes (worker), fetch events from now to +7 days with recurring events expanded. Skip all-day events and declined events. Keep title, description, start/end, attendees, and the meeting link (Google Meet link, conference data, or a Zoom/Teams/Webex URL found in location or description). Web shows **Today** and **Upcoming**. Each event can be opened to see its details and its meeting page once recorded.

## 5. Cross-cutting decisions

5.1 **Auth.** Google sign-in only. Server-side session cookie for the browser; device tokens for the helper. The Google consent asks for `openid email profile` and `calendar.readonly`.

5.2 **Audio retention.** Audio is always saved, as rupak.k asked. The README's "no audio stored long-term" is consciously overridden for Phase 1. Retention settings come later.

5.3 **Config, not localhost.** Base URLs, Google endpoints, the S3 endpoint and the Claude model come from config, so the same images run locally and in the cloud.

5.4 **Tests.** pytest for api, worker, gsim and the helper's pipeline (with WAV fixtures and a stubbed recogniser for unit tests, real MLX for the latency and error-rate tests); Vitest for web; the golden check (§9.3). Suite counts go in each milestone doc, as in AEVIS.

5.5 **Claude in tests.** Unit tests stub Claude. The golden calls the real API, so takes check only stable text: section headings, the user's own black text, and labels. They never check Claude's wording. Spoken narration in the screenplay never states Claude's wording or any per-run number.

5.5a **CI (Claude's call).** GitHub Actions on Linux runs the api, worker, gsim and web suites, the helper's unit tests with the stubbed recogniser, and `scripts/check_golden.py`. The real-MLX latency and error-rate tests need Apple Silicon. They run on the Mac in the milestone's last task, and their results are recorded in the milestone doc's Automated suites section.

5.6 **Branches.** One branch per milestone (`feat/p1-NN-<name>`) with one PR, merged after rupak.k signs off. `milestone/README.md` is the index with status, as in AEVIS.

5.7 **Privacy.** Meeting text leaves the machine only to the Claude API. Embeddings are local. No training opt-ins.

5.8 **Multi-user safety.** Per-user filtering on every query, a Weaviate tenant per user, blob keys under the user id. An api test signs in two gsim accounts and proves neither can read the other's meetings, chat or traces.

## 6. Milestones

Each milestone is at most 5 tasks. The **last task of every milestone is always "Golden and doc"** (§9): play the stack fresh, write or update the takes and every `.walker/` file, write `milestone/step-NN-<name>.md`, and run the golden check. Then the milestone goes to rupak.k for manual verification.

| # | Milestone | Tasks |
|---|---|---|
| 0 | **Foundation** | 1. Compose stack (postgres, minio, weaviate + t2v, api, worker, web, gsim stub) with health checks and migrations · 2. gsim OAuth + Google sign-in, sessions, user record · 3. Web shell: sign-in page, empty home, user menu with **Sign out** · 4. Golden and doc |
| 1 | **Calendar** | 1. gsim Calendar API with the fixture calendar · 2. Sync worker (expand recurring, skip all-day/declined, extract meeting links) · 3. **Today** / **Upcoming** screens and event detail · 4. Golden and doc (manual check: real Google sign-in shows real events) |
| 2 | **Desktop helper and capture** | 1. Pairing (**Connect desktop app**, code, device token) · 2. Menu-bar app as a py2app bundle: upcoming meetings, **Start recording**, **Stop** · 3. T−5 notification with **Record** (prove the action works; fallback if not) · 4. System capture + mic capture with voice processing (echo cancellation), chunked audio upload to MinIO; replay source · 5. Golden and doc (manual checks: notification, real Zoom/Meet audio captured on speakers) |
| 3 | **Live transcript** | 1. Streaming MLX Whisper with partial/final segments, You/Others · 2. api + browser WebSockets; meeting page right half · 3. Echo handling: output-route detection and the cross-stream echo check (§4.3, 3a) · 4. Latency, error-rate and echo tests against the fixtures and anti-goal targets · 5. Golden and doc (manual check: a real call on speakers shows no echoed **You** lines) |
| 4 | **Notes and finish** | 1. **My notes** editor with autosave, layout A · 2. Stop from web or menu; final audio + transcript in MinIO; `processing` → `ready` · 3. Transcript tab with audio player and timestamp seek · 4. Golden and doc |
| 5 | **Summary** | 1. Claude client with trace + cost recording · 2. Structured summary, verbatim guard, black/grey rendering · 3. Templates, picker, **Regenerate**, versions · 4. Golden and doc |
| 6 | **Library and chat** | 1. Weaviate indexing per tenant · 2. Library page with search over meetings · 3. Meeting chat and library chat with citations linking to transcript lines · 4. Golden and doc |
| 7 | **Usage and traces** | 1. Per-meeting cost line · 2. **Usage** page · 3. **Traces** list and detail · 4. Multi-user isolation test across all data · 5. Golden and doc |

Milestone 2 is the riskiest (native capture, notification actions) and comes before anything depends on it.

## 7. Milestone doc format

`milestone/step-NN-<name>.md`, following AEVIS `milestone/step-01-project-and-target.md`, with these sections in this order:

1. **What this step delivers**: a short paragraph, then the screens, each as **Screen name** (`/route`, `web/src/...` source file).
2. **Prerequisites**
3. **Deploy**: exact commands and what `ps` and health checks show.
4. **Setup**: users, pairing, fixtures.
5. **Happy path**: a table with columns `# | Do this | You should see | Beat | Source`. Every UI string in **bold** is copied exactly from the product. **Beat** is the take beat id (`take#beat`). **Source** lists the file(s) that render the strings in that row.
6. **Negative checks**: same columns. Beat may be empty if the check isn't in the golden.
7. **Manual checks**: things the golden cannot drive (desktop notifications, real system audio, real Google), with exact expected text and source files.
8. **Automated suites**: commands and counts from the final run.
9. **Sign-off checklist**
10. **Known limits in this step**

Plus `milestone/README.md`: the index table of steps, branch, status.

## 8. The golden `.walker/`

After every milestone, `.walker/` is complete and usable on its own. It holds the same set of files as AEVIS, extended per milestone:

| File | Content |
|---|---|
| `walker.yaml` | `format: walker/0.1`; project block (name, logline, audience, repo_url, `walked_at` commit, title, runtime/narration/scene ranges); `needs` (tools including macOS arm64 and `uv`, ports, secrets by name only: `ANTHROPIC_API_KEY`; generated secrets; `redact` and `redact_screen` patterns for the API key, pairing codes and tokens); `set` (compose project, base_url, up/down, ready checks); `macros` (`signin` through **Sign in with Google** → gsim consent → fixture account → Allow; `signout`); the ordered take list |
| `takes/*.yaml` | `install`, `pair` (terminal), then per-milestone takes (`m0-signin`, `m1-calendar`, …). Browser takes use role + exact accessible name selectors, `expect_text`, `wait_for`, `beat`, `shot`, `capture_url`, as in AEVIS |
| `story.md` | Logline, audience, payoff, one act per milestone citing its milestone doc, on/off camera, said plainly |
| `screenplay.md` | Scenes per act with `narration` and `cite` entries whose quotes resolve in the repo or in takes |
| `gaps.md` | Same format as AEVIS. **Zero open gaps at sign-off** |
| `notes/doc-checks.md` | Values the docs promise, checked against what the product returns |
| `notes/docs-index.json` | Same schema as AEVIS: per milestone doc, its section line ranges, deploy commands, and every happy-path row (`id`, `do`, `see`, `role`, `typed`, `checkable`, `line`) |
| `scenarios/*` | Scripts takes call: starting a replay into a meeting, seeding meetings for library/chat |
| `.gitignore` | `footage/` and `.state/` |

Live transcript in the golden: a `run:` step (as AEVIS's `act4-benchmark.yaml` uses inside a browser take) calls `scenarios/start_replay.sh`, which starts `fleck-helper replay fixtures/audio/<name>.wav --meeting <id>` in the background and returns at once **(Claude's call)**, so the browser take can watch it, and the browser take `wait_for`s stable phrases from the fixture. Fixture WAVs live in `fixtures/audio/` with their reference transcripts.

**Fixture audio.**
- **You (left channel):** rupak.k's own 3–5 s recordings made for Piper training, with the transcripts that came with them as the reference text. rupak.k provides these.
- **Others (right channel):** at least two voices other than rupak.k's, generated with Piper TTS from scripted lines.
- **Building the fixtures:** a script in `fixtures/audio/build.py` stitches the lines into a 2–3 minute two-sided conversation with turn gaps, a few short overlaps, and spoken numbers and dates. It writes the WAV and the reference transcript.
- **Two variants:**
  - `clean`: used by the golden and the latency and error-rate tests
  - `bleed`: the right channel is mixed into the left at lower volume with a short delay and room reverb, so it sounds like speakers heard by the mic. It's used for the echo test, which expects no **Others** words to appear as **You**.
- **Limits (Claude's call to accept):** this audio is cleaner than a real Zoom call, which has codec artefacts. Real-call accuracy is a manual check, not an automated gate.
- **Privacy:** rupak.k's voice clips are committed to this repo. If the repo becomes public, rupak.k should decide whether they stay.

**Walker vocabulary.** Takes use only the keys AEVIS's takes and walker.yaml already use. If a step can't be expressed with them (for example running the replay concurrently), the builder records it in `gaps.md` and raises it with rupak.k. Inventing new keys is not allowed.

## 9. Keeping code, docs and golden in sync

This is the rule AEVIS lacked. Examples of AEVIS drift this rule prevents: the step 1 doc says tick **Add me as rag_engineer**, but the take never does; the doc says tab **Members** and field "username", but the take uses **Members and roles** and a **Person** dropdown.

9.0 **No Walker runner.** The builder never runs Walker. The golden is proven by playing every step by hand on a fresh stack and by the static check in §9.3. When Walker later plays it, any deviation is a defect in this repo.

9.1 **Order of work in the "Golden and doc" task**: bring up a fresh stack (`down -v`, `up`) → play every step by hand → write the take from what's on screen and the source strings → write the doc rows from the take → run the check → fix code, take or doc until it passes → hand over.

9.2 **What must match exactly**: every bold UI string in a milestone doc (buttons, tabs, labels, headings, toasts, empty states, error text) must appear verbatim in the cited source file **and** in the take row's `expect_text` / `wait_for` / selector names; every happy-path row has a beat; every take beat has a row; the order is the same.

9.3 **The golden check**: `scripts/check_golden.py`, run in CI and in each milestone's last task. It fails when:
- a bold string in a happy-path or negative row isn't found in that row's Source files;
- a happy-path row's bold strings aren't in its beat's steps, or a beat has no row, or the order differs;
- `notes/docs-index.json` doesn't match the doc (rows, text, line numbers, section ranges);
- a `cite` quote in `screenplay.md` doesn't resolve (`footage/` cites are skipped, because Walker produces footage);
- a take uses a key outside the walker/0.1 vocabulary (§8);
- `walker.yaml` lists a take file that doesn't exist, or a take file isn't listed;
- `gaps.md` has an open gap.

It writes its findings to `gaps.md` in the AEVIS gap format.

## 10. Left to the builder

Port numbers; compose service names; table columns; REST routes; streaming window, overlap and VAD settings within the §2 targets; Weaviate class schema and chunk sizes; prompt wording for summaries and templates; exact UI copy (once chosen, binding through §9); the scripted lines in the fixture conversation (within §8's fixture plan, with numbers and dates spoken, since the README stresses number accuracy); echo-check thresholds; how the golden check parses Markdown.
