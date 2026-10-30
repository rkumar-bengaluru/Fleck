# Build log

One dated entry per task: what ran, numbers, what changed.

## 2026-10-30 Task 1: workspace, compose stack, pinned models

- `make up`: postgres 16, MinIO, Weaviate 1.27.0, gsim stub, api all healthy; `GET /api/health` returns ok for postgres, minio, weaviate, ollama.
- `make ensure-ollama`: both pinned models present, IDs `500a1f067a9f` (qwen3:8b-q4_K_M) and `ac6da0dfba84` (qwen3-embedding:0.6b) match.
- `api/tests/test_health.py`: RED with the stack down (503, three checks erroring), GREEN with the stack up (200 all ok; closed Ollama port gives 503 with `checks.ollama` starting `error`).
- Changed: `minio/minio` is no longer pullable from Docker Hub or quay.io (401), so compose and CI use `cgr.dev/chainguard/minio` (no shell, so no container healthcheck; the api health check covers it).
- Spec edit (`demo/larkspur-original` reference): already removed before this task; nothing to change.

## 2026-10-30 Task 2: latency budget on this Mac

`uv run --package fleck-helper python scripts/bench_latency.py` (30 runs each; a second thread transcribes a 60 s Rishi monologue in 3 s windows throughout; Ollama `qwen3:8b-q4_K_M`, 4096 ctx, 100% GPU, system prefix + 3 x ~260-token `_firm` passages + question).

| Measure | p50 | p95 |
|---|---|---|
| (a) transcribe(final=True), 3.1 s question, under You-stream load | 1.49 s | 4.00 s |
| (a') same, no concurrent load (side check) | 0.35 s | 0.36 s |
| (b) TTFT, cold prefix | 2.61 s | 2.65 s |
| (b) TTFT, warm prefix | 0.08 s | 0.13 s |
| total generation, cold / warm | 4.67 / 2.11 s | 5.12 / 2.67 s |

Memory: helper process RSS 118 MB (+ Whisper weights on the unified GPU, not in RSS); `ollama ps`: qwen3:8b-q4_K_M 5.6 GB, 100% GPU, keep-alive forever.

Gate: 0.6 + 4.00 + 0.2 + 0.13 = **4.93 s > 3.0 s: FAIL** (with (a') instead of (a): 1.29 s).

Diagnosis: the miss is entirely the Whisper term. The single process-wide GPU lock makes the final-segment call queue behind an in-flight You-stream window. `qwen3:4b` only changes the 0.13 s warm-TTFT term, so it cannot fix this; not pulled. Cold TTFT (2.6 s) shows prefix warming is mandatory. Needs a decision (e.g. priority over partial/You windows, preempting or dropping stale windows, or a smaller Whisper) before Milestone B.

### Task 2 fix (decision 67): priority scheduler, re-measured

The first run's miss was a scheduling defect (FIFO lock), not model speed. `StreamTranscriber` now has a priority scheduler (client final > client partial > you final > you partial), `set_client_active(bool)` (no new You job starts while a Client segment is open), and stale queued partials are dropped when a newer partial or the final for the segment arrives. A running job is never preempted. Unit tests (no GPU): `test_client_final_runs_before_queued_you_jobs`, `test_no_you_job_starts_while_client_active`, `test_stale_partial_dropped`.

Re-run, 30 trials per scenario; `set_client_active` driven as the VAD would. Realistic: You speaks in turns, then client asks. Worst: a You 3 s window starts 0.2 s before the question.

| Measure | p50 | p95 |
|---|---|---|
| (a) transcribe final, realistic | 0.38 s | 0.41 s |
| (a) transcribe final, worst case | 0.39 s | 0.40 s |
| (b) TTFT cold prefix | 1.96 s | 2.55 s |
| (b) TTFT warm prefix | 0.05 s | 0.07 s |
| total generation cold / warm | n/a / 1.24 s | 3.93 / 1.56 s |

Memory: helper RSS 39 MB; `ollama ps` qwen3:8b-q4_K_M 5.6 GB, 100% GPU.

Gate (worst case): 0.6 + 0.40 + 0.2 + 0.07 = **1.27 s <= 3.0 s: PASS**. `qwen3:4b` not needed. Cold TTFT of 2.55 s makes prefix warming mandatory. Numbers were relayed by the controller from the finished run; the benchmark was not re-run by me.

### Task 2 review fix round 1: real contention trial

The task review found the "worst case" above never contended: its You window finished before the question's final arrived, so 0.40 s was really the solo figure. The bench now has a **contended** scenario: a client partial is already running (never preempted) when the final arrives, a You window has just started, and Qwen generates on the GPU throughout. Also fixed: an exception in the Whisper call no longer kills the worker thread (re-raised in the caller); an Ollama mid-stream `{"error": ...}` now raises. Unit tests: 9 passed, 1 deselected.

| Measure | p50 | p95 |
|---|---|---|
| (a) transcribe final, realistic | 0.39 s | 0.45 s |
| (a) transcribe final, contended (partial in flight + Qwen generating) | 1.43 s | 1.53 s |
| (b) TTFT cold prefix | 2.46 s | 2.69 s |
| (b) TTFT warm prefix | 0.05 s | 0.06 s |
| total generation cold / warm | 3.59 / 1.25 s | 4.08 / 1.52 s |

Memory: helper RSS 73 MB; qwen3:8b-q4_K_M 5.6 GB, 100% GPU.

Gate (contended): 0.6 + 1.53 + 0.2 + 0.06 = **2.39 s <= 3.0 s: PASS**, margin 0.6 s. The 1.27 s figure above is superseded. Whisper sharing the GPU with Qwen generation is the dominant cost; Task 11 should avoid running Whisper partials for the You stream while an answer is generating if the end-to-end p95 test comes in tight.

## 2026-10-30 Task 2b: signed helper bundle and capability probe

`make helper-app` builds `dist/Fleck Helper.app` (py2app) and signs it with the self-signed "Fleck Local Signing" identity (hardened runtime, bundle id `ai.fleck.helper`). The cert is imported but could not be trusted from the CLI (trust needs a GUI password); codesign signs fine with the untrusted identity. First run of sign.sh once hit `errSecInternalComponent`; an immediate retry passed. Launch failed with a Team ID library-validation error until `disable-library-validation` was added (decision 68). Unit tests: 7 passed.

Probe results (run twice, rebuilt once in between):
- menubar: true (rumps status item created).
- mic: authorized, voice processing on, mic_rms 0.0013 / 0.0061 / 0.0023. Permission persisted across the rebuild. Not tested with speech.
- system audio: **waiting on permission**. SCShareableContent returned -3801 "user declined TCCs" immediately; no prompt reached a person.
- notification: **waiting on permission**. `requestAuthorization` returned "Notifications are not allowed"; Record action not exercised.
No edge was found technically impossible; ScreenCaptureKit and UNUserNotificationCenter both load and answer from PyObjC under this signing. Steps for rupak.k are in `docs/superpowers/manual-checks.md`.

## 2026-10-30 Task 3: schema and Postgres job queue

- ORM for every table in the data model (`api/fleck_api/models.py`), one Alembic migration `0001` autogenerated against an empty database; `get_settings()` over the `.env` keys; `jobs.py` (`enqueue`, `@job`, `run_once` with `FOR UPDATE SKIP LOCKED`, 3 attempts, 5 s / 30 s backoff, `failed` + `last_error`); `python -m fleck_api.worker` polling loop (1 s) that survives errors. Decisions 69-71.
- Tests (`api/tests/test_jobs.py`, throwaway `fleck_test_*` database per test, `alembic upgrade head`, dropped after): 5 passed. The `worker` compose service reuses the api image. CI already had a Postgres service.

## 2026-10-30 Task 4: number normalisation and answer validators

`fleck_core.numbers` (`normalize_numbers` via text2num `alpha2digit` plus currency, percent, comma and ISO-date rules; `extract_figures`), `types.py` (`Passage`, `Citation`), `validate.py` (`split_sentences`, `check_library_answer`, `check_fallback`). Figures match by numeric value; a unit mismatches only when both sides state one, and a bare passage number inherits the currency named anywhere in the cited passage ("(USD)" heading). Full suite `uv run --all-packages pytest -m "not ollama" -q`: 44 passed.

### Milestone A critique follow-up: latency gate corrected, live model now qwen3:4b (decision 72)

The critique agent found the 2.39 s gate above was wrong: its "warm" first-token time resent an identical prompt, so Ollama had the passages and question cached, which never happens live. The contended-trial description above was also wrong: no You window runs in that trial (the code sets `stop` before the thread starts); the contention is the client partial in flight plus Qwen generating.

Re-measured with fresh passages and a fresh question each time (`prefill.py`, 12 trials, 1,045 prompt tokens):

| Model | First token, fresh prompt p50 / p95 | First token, passages read early (start-early) p95 |
|---|---|---|
| qwen3:8b-q4_K_M | 2.38 / 2.59 s | 0.19 s |
| qwen3:4b-q4_K_M | 1.06 / 1.11 s | 0.10 s |

`qwen3:8b` puts first words at 0.6 + 0.45 + 0.2 + 2.59 ≈ 3.8 s, missing 3 s. Spec §7.1 item 4 applies: live model switched to `qwen3:4b-q4_K_M` (`2bfd38a7daaf`), start-early made mandatory for Task 13. `bench_latency.py` now gates on the uncached case. Full run on qwen3:4b, 30 trials:

| Measure | p50 | p95 |
|---|---|---|
| (a) transcribe final, realistic | 0.40 s | 0.50 s |
| (a) transcribe final, contended | 1.15 s | 1.26 s |
| (b) TTFT, fresh passages + question | 1.15 s | 1.24 s |
| (b) TTFT, cached (start-early) | 0.03 s | 0.04 s |
| total generation fresh / cached | 1.91 / 1.03 s | 2.20 / 1.08 s |

Gate: 0.6 + 0.50 + 0.2 + 1.24 = **2.54 s <= 3.0 s: PASS**. Contended with start-early: 2.10 s. Worst untested combination (contended, start-early missed): ≈ 3.3 s, which Task 13's end-to-end p95 test will expose. Helper RSS 41 MB; qwen3:4b 3.3 GB on GPU. `qwen3:8b` unloaded.

### Task 2b follow-up: all four macOS edges proven

rupak.k ran the probe after granting Screen & System Audio Recording and notifications: `menubar` true, `mic_rms` 0.053 (speech), `system_rms` 0.338 (test tone via ScreenCaptureKit), `notification_authorized` true, `action_received` true. The earlier instant denials were un-granted permissions, not the self-signed certificate.

### Task 5: shared event schema

`core/fleck_core/events.py` holds the segment, assist and meeting_state models plus `parse_event` (pydantic discriminated union on `type`; unknown type raises `ValidationError`, a `ValueError`). `scripts/gen_event_types.py` writes `web/src/events.ts` from the JSON schema (`make gen-types`); CI regenerates and fails on `git diff --exit-code web/src/events.ts`. Defaults are marked required in the schema, since they are always present on the wire.

### Task 6: demo library content and parsing fixtures

Firm docs retitled `Rate card FY2026`, `Delivery FAQ`, `Capabilities` (firm name on the line below); rate-card table heading now "Time and materials (USD per person per month)", rows unchanged. Three case studies added (`calloway-marsh/`, `meridian-intel/`, `brightwater-insurance/`), `clients/README.md` updated. `api/tests/fixtures/make_xlsx.py` builds `rate-card.xlsx` (merged title row, `"$"#,##0` formats) and `rate-card.pptx` (title + 4-row rate table); both byte-identical across two runs. openpyxl and python-pptx added to fleck-api dev group.

### Task 4 review rounds: three adversarial passes on the number check

The first build (e225323, 44 tests) passed its own tests but an adversarial reviewer found false accepts, each now a regression test with the exact input:

- Round 1 (d97a36b, 96f44f2, b4143e1): passage units like `4 h` read as bare numbers ("Restore within 4 days" passed); clause numbers ("7.") read as figures; rate period ignored ("$5,200 per year" vs per month); "$1.5 million" split into two numbers; fallback missed partial client names, "200k", "fifty grand", lakh/₹, other currencies, "40/hr". 96f44f2 was committed with one test failing (an `| tail` hid the exit code); b4143e1 fixed it and later rounds check the exit code directly.
- Round 2 (cad4938): a "$" cell under a "per month" heading didn't inherit the period; a period word elsewhere in the sentence was missed; clause stripping ate "1.5" before "Million"; "$10 bn" didn't scale; the fallback blocked "ISO 27001", "SOC 2", "TLS 1.3" (over-blocking).
- Round 3 (a42ad90): "per quarter", "per seat" ignored; passage-side "per calendar month"/"pm" unread; the fallback blocked "an SLA with 3 severity levels" and missed "six figures" and "ninety nine point nine".

Result: 119 passed. Known limits accepted as decision 79. The Milestone B critique then found spoken "one" and "two" still unread ("start within two weeks" passed against "4 weeks"); fixed in the Milestone B follow-up below.

### Milestone B critique

Verdict: Agree with changes. Verified by the controller: "A team can start within two weeks of signature. [1]" passed against "within 4 weeks"; "Our SLA response time is two hours." and "Northbeam has done this before for many banks." passed the fallback. Job-queue flakiness reported by the critic (2 of 5 runs) did not reproduce in 5 runs here; host and Postgres clocks agree within 0.1 s, but `run_after` moves to server time anyway. Follow-up work: spoken small numbers, uncited-sentence checks, no first-person firm claims in the fallback, label-on-same-line check, job `on_fail` hook, server-time scheduling, worker environment. Doc drift fixed (decisions 6, 70; spec §7 "Why"; plan architecture line).

### Task 7: upload, parse, chunk, embed, index, hybrid search

`core/fleck_core/retrieval.py`: block types, `chunk_blocks` (text ~1400 chars with 200 overlap on paragraph boundaries; tables by 8 rows, first line `<title> · <caption/heading incl. unit>`, then `<row> · <col>: <value>` lines), `Embedder`, `ensure_schema`, `put_passages`, `Retriever` (nearVector distance gate at `FLECK_RETRIEVAL_MAX_DISTANCE` 0.55, then hybrid alpha 0.5; Weaviate over REST/GraphQL via httpx, no client library). `api/fleck_api/library.py`: markitdown/openpyxl parsing, MinIO storage, idempotent `index_document` job with `on_fail`, REST routes, `seed_library`. `validate.py` label binding now also reads `<row> · <col>: <value>` lines. Worker now imports `library` (found when the first seed failed every job with `KeyError: 'index_document'`).

Tests: `uv run --all-packages pytest -m "not ollama" -q` 160 passed (EXIT 0); `-m ollama` 3 passed (search finds the rate row; "capital of Peru" returns `[]`; plus the Task 1 Ollama test). API tests index into a throwaway Weaviate collection with a fake embedder; the Ollama tests use a real one.

`make seed-library` (host, against compose ports; 22 s): firm 3 documents / 8 chunks, reference 6 / 349, client 6 / 10, all indexed; a second run queued 0. Live search on the seeded index: "senior engineer offshore monthly cost" returns the Rate card chunks first; "capital of Peru" returns nothing.

### Task 7 fix round 2: Excel scaling, junk chunks, seed guard

Scaled/scientific/suffixed Excel formats render exactly (`#,##0,,"M"` -> 5M, `0.00E+00` -> 5.20E+03, `#,##0 "USD"` -> 5,200 USD; fractions fall back to the raw value). All-blank-header PDF grids are dropped, text chunks under 20 words or 100 letters are dropped, and text chunks now carry their section heading (`Rate card FY2026 · Other terms`); a section under 150 characters merges into the next. Delete removes the row before the passages; a bad Content-Length is 400; the seed refuses to run (and delete nothing) when no files are found. `scripts/seed_library.py --force` re-indexes everything (used once now: the chunking changed, hashes did not). Suites: `-m "not ollama"` 187 passed EXIT 0, `-m ollama` 3 passed EXIT 0. Seed after `--force`: firm 3 documents / 12 chunks, reference 6 / 338, client 6 / 31.

Live queries on the re-seeded library:
- "what is the capital of Peru" -> `[]`
- "do you have a case study in insurance" -> 3 Brightwater Insurance case-study passages (top 3)
- "what are your payment terms" -> 1: Rate card FY2026 · Other terms (firm, "Payment: net 45 days"), 2: a Cardlytics contract passage, 3: Larkspur SOW

### Task 7 fix round 3: query instruction, distance gate 0.60, form-grid tables

Queries are embedded with qwen3-embedding's instruction (`Instruct: Given a client's question in a sales meeting, retrieve passages from the firm's documents that answer it\nQuery: ...`), documents plain. Table chunks whose rows mostly lack a row label (PDF form grids) are dropped. Nearest-passage distances before/after: right answers sit at 0.26-0.57 (e.g. "senior engineer offshore" 0.261, "case study in insurance" 0.411, "liability" 0.398, "retrieval system for retail" 0.572); unrelated questions at 0.665 or more ("capital of Peru" 0.765, "world cup" 0.81, "weather" 0.669). `FLECK_RETRIEVAL_MAX_DISTANCE` default moved 0.55 -> 0.60 (Task 12 tunes further). Re-seeded with `--force` (after `make up`: the compose worker runs the code in its image and had indexed some documents with old chunking during the first attempt): firm 3 documents / 12 chunks, reference 6 / 334, client 6 / 31. Suites: `-m "not ollama"` 188 passed EXIT 0; `-m ollama` 4 passed EXIT 0 (new live-like test over all of clients/ except jf/).

Live results (passage, nearest-vector distance):
- "how fast can you start a team": Delivery FAQ 0.517; Larkspur proposal Team 0.517; Delivery FAQ fixed price 0.588
- "what does a tech lead cost onsite": Rate card rows Engagement manager..Tech lead (includes "Tech lead · Onsite") 0.415; Rate card ML engineer chunk 0.491; Delivery FAQ 0.551
- "what is the capital of Peru": [] (best 0.765)
- "do you have a case study in insurance": Brightwater case study 0.411, 0.485, 0.553
- "what are your payment terms": Rate card Other terms (Payment: net 45 days) 0.467; Larkspur SOW 0.470; Msa part2 0.527

## 2026-10-30 Task 8: gsim and Google sign-in

gsim (consent page, token, userinfo, calendar fixtures with an injectable clock) and api auth (login/callback/logout/me, allow-list, Fernet-encrypted tokens, sha256 session tokens, current_user on every /api route except health/login/callback). Placeholder no-op sync_calendar job until Task 9. Live stack via make up: rupak@northbeam.test signs in and /api/me returns 200; outsider@example.test redirected to /signin?error=not_allowed; /api/clients without session 401; sync_calendar job done. Suite -m 'not ollama': 196 passed EXIT 0.

### Task 7 Milestone C follow-up: seed through the worker, table substance, FAQ granularity, live budgets

- `scripts/seed_library.py` now only creates rows, uploads and enqueues; it polls until the compose worker finishes (timeout 600 s, `--timeout`), prints each removed path and the count, and `--force` re-queues everything. Re-seeded with `make up` + `--force`: firm 3 documents / 22 chunks, reference 6 / 322, client 6 / 40; removed 0.
- One unsupported-file message for the parse error and the 415. Table chunks drop value-less rows, grids that are mostly empty and unlabelled rows. Every markdown section is its own block under its own heading (short sections under a markdown heading are kept at 6 words). Delivery FAQ questions are `##` headings (answers unchanged), so each is its own passage.
- `Retriever.search` fetches 6, ranks firm/client before reference, and keeps at most 1 reference when any firm/client passage passed. `Embedder(url, keep_alive=-1, timeout=1.5)` and `Retriever(..., timeout=1.0)` raise `RetrievalTimeout`/`RetrievalError`; indexing uses a 120 s embed timeout.
- `DOC_TITLES` (Kubient MSA, part 1/2; Cardlytics maintenance agreement; HealthGate hosting agreement; Sphere 3D consulting agreement; Photronics outsourcing agreement); Larkspur SOW H1 is "SOW No. 2: order management". WebLibrary mockup rows now use the real titles.
- Suites: `-m "not ollama"` 202 passed EXIT 0; `-m ollama` 5 passed EXIT 0.

Live §8 questions (title, kind, nearest-vector distance):
- q_rate: Rate card FY2026 firm 0.218, 0.274, 0.439
- q_start: Delivery FAQ firm (in-house team 0.419, How fast can you start? 0.446, team member leaves 0.483)
- q_ip: Delivery FAQ "Who owns the IP?" firm 0.376; Delivery FAQ tools 0.542; GenAI 0.548 (no reference)
- q_sla: Delivery FAQ "What SLAs..." firm 0.420; SOW No. 2 hypercare service levels client 0.478; Delivery FAQ start 0.583
- q_case: Case study: Calloway & Marsh client 0.445, 0.502; Capabilities firm 0.520
- q_general: Photronics outsourcing agreement reference 0.592; Kubient MSA, part 2 reference 0.584 (nothing firm passes the gate, as intended)
- q_price: Proposal: Order management modernisation client 0.526; Rate card T&M firm 0.535; Rate card "Fixed price" firm 0.592

## 2026-10-30 Task 9: calendar sync, web shell, Today and client link

- `calendar_sync.py`: job `sync_calendar(user_id)` reads now-12 h..now+7 d (`singleEvents`, paged), skips cancelled, all-day and self-declined events, takes `meeting_link` from `hangoutLink`, `conferenceData` video entry point, or the first Zoom/Teams/Webex/Meet URL in location/description, upserts by (user, `google_event_id`) without touching `client_id`, and deletes rows in the window Google no longer returns. Expired access tokens are refreshed with the refresh-token grant and stored re-encrypted; a failed refresh logs and skips the user. The worker enqueues a sync for every user with tokens every 5 min unless one is queued or running for them.
- REST: `GET /api/events?range=today|upcoming&tz=<IANA>` (today = the user's local day; default the server's zone), `GET /api/events/{id}`, `PUT /api/events/{id}/client`, minimal `GET /api/devices` (migration 0004 adds `devices.last_seen`; Task 15 extends it).
- Web (`web/`, Vite 8 + React 19 + TS 7, pnpm): shell with nav, Sign in with Google, Today as in `WebToday.dc.html`, Settings (sign out). Compose service `web` (vite on 5173, `/api` proxied to `api:8000`, Origin kept). CI: pnpm install, `tsc --noEmit`, vitest. Playwright needs the whole stack and runs locally only.
- Suites: `uv run --all-packages pytest -m "not ollama" -q` 223 passed EXIT 0; vitest 3 passed; `tsc --noEmit` exit 0; Playwright `web/e2e/signin-today.spec.ts` 1 passed (1.1 s) against `make up` (sign in through gsim as rupak@northbeam.test, see Presales: demo prospect, link Harbourline Legal, reload, still linked).
- Screenshot of Today on the live stack: [docs/superpowers/screenshots/today.png](screenshots/today.png).
- Fix rounds 1-2:
  - Open brief and Record on every open meeting; containers use the host's `TZ` (from `.env` via `make up`), so the demo now starts at the next whole local hour.
  - Upcoming shows each recurring series once; events that are gone from Google but used are kept as cancelled.
  - The "New client" name comes from the guests' domain.
  - Central 401 handling sends the browser to sign-in with a return path, and failed loads and saves show inline errors.
  - Link extraction is anchored and unescaped, and unwraps Teams safelinks.
  - A Google 401 removes the tokens without retrying; one enqueue guard covers sign-in and the scheduler.
  - Day bounds are DST-safe; page titles are set; the dark nav has a light focus ring.
  - Suites: pytest `-m "not ollama"` 247 passed EXIT 0; vitest 9 passed; tsc 0; Playwright 1 passed. Screenshot re-taken.
- Milestone D follow-up:
  - The api refuses to start in Google mode without OAuth credentials; unreadable tokens are dropped, and Today shows "Calendar not synced. Sign in again".
  - gsim fixes the demo time at startup (`GSIM_DEMO_START` overrides it).
  - The first sign-in syncs inline (5 s, falling back to the job). Today polls events every 30 s and shows "Syncing your calendar…" while a sync runs.
  - Upcoming rows link to the event.
  - The client select hides reference-only clients and shows "Suggested: …" first and "New client: …" last.
  - WebToday mockup updated to match.
  - Suites: pytest 253 passed EXIT 0; vitest 11; tsc 0; Playwright 1 passed. Screenshot re-taken.

## 2026-10-30 Task 10: test conversation and negative set

- `make fixtures` (`uv run --script fixtures/build.py`, macOS `say`, numpy + pyyaml as inline script deps) ran three times; two consecutive runs gave identical SHA-1 for every WAV and reference.json.
- conversation.wav and conversation-bleed.wav: 382.5 s (6.37 min), 24,478,336 bytes each, 58 turns, 8 scripted questions, small talk about 95 s before the agenda ends.
- negative.wav: 750.0 s (12.50 min), 48,110,652 bytes, 230 segments (120 short, 80 medium, 30 logistics), gaps 1.5-2.4 s.
- Question end_ms (s): q_rate 155.72, q_start 174.03, q_ip 195.95, q_sla 224.04, q_case 247.53, q_general 292.24, q_liability 323.81, q_price 341.71.
- q_liability expects `reference`, doc "Photronics outsourcing agreement" (limited-liability clause, twelve-month cap). Found by markitdown text search of clients/_reference/ and confirmed in the seeded Weaviate library.

## 2026-10-30 Task 11: audio sources, VAD, speaker gate, transcription

- New: `audio.py` (ReplaySource, LiveSource with restart + Gap, AudioChunkWriter), `vad.py` (Segmenter: webrtcvad 2, Open/Partial/Final), `gate.py` (gate, frame_rms, drop_bleed_duplicates). `transcribe.py` unchanged: it already had `language="en"`, `condition_on_previous_text=False` and the decision-67 scheduler. New deps: `webrtcvad-wheels` (Linux and macOS wheels), `numpy`.
- Whisper on q_rate's clip (client channel of conversation.wav sliced by reference.json): "What does a senior engineer cost per month? Offshore." WER 0.000 after `normalize_numbers`.
- Full replay of conversation.wav (382.5 s) at realtime=False through both Segmenters + whisper-large-v3-turbo on every final, sequentially: 21.7 s total (whisper 21.5 s, VAD + WAV read 0.2 s), 56 finals for 58 scripted turns, 299 partials (not transcribed in this run), 0 empty finals.
- Live: mic capture from a terminal (AVAudioEngine voice processing, 48 kHz tap → 16 kHz) gave 3.00 s of audio in 3 s. System audio could not be checked outside the signed bundle (TCC -3801 for the terminal); added to manual-checks.md.
- Suites: `uv run --all-packages pytest -m "not ollama" -q` 271 passed, 5 deselected, EXIT 0; `pytest helper/tests -v` 21 passed.
- Fix: bleed() now uses a unit-energy impulse and calibrates to -14 dB on the left during client-only speech (measured -14.00 dB, asserted within 1 dB); only conversation-bleed.wav changed; temp say dir removed at exit.
- Fix round 1 (review of e9431f3):
  - LiveSource runs on the host clock: a 3 s stall of either stream loses none of the other stream and keeps skew under 40 ms (fake clock).
  - Added `Unavailable` and `Discard` events and `Gap.stream`.
  - The live audio queue is bounded at 250 buffers.
  - The mic engine restarts on a route change.
  - Resampling carries state between buffers: drift under 1 sample over 60 s of 1024-sample buffers at 22.05, 24, 44.1 and 48 kHz.
  - The gate counts dominant 200 ms windows, so a 300 ms mic blip during q_rate still gives "client".
  - `ReplaySource(loops=)` keeps t_ms increasing.
  - The chunk writer closes its open chunk on error.
  - Live mic from a terminal: mic buffers arrive 0.105–0.115 s after their host timestamp, on the same clock as `now()`. The system-audio start failed (TCC) and the source went on mic-only, emitting `Unavailable`.
  - Replay of conversation.wav is unchanged: 56 finals, 21.8 s.
  - Suites: `pytest -m "not ollama" -q` 283 passed, EXIT 0; `pytest helper/tests -v` 33 passed, EXIT 0.
  - Gap rule tightened: only a stopped/failed stream, a mic restart or a queue overflow (one Gap, stream "both") marks a gap; 10 s with no client buffers gives none. Suites: `pytest -m "not ollama" -q` 284 passed, EXIT 0; `pytest helper/tests -v` 34 passed, EXIT 0.
- Fix round 2:
  - Late or mis-stamped audio:
    - A stream that delivers late now widens the slot wait, up to 1 s, so client buffers arriving 0.4 s late keep at least 95% of their audio (it was 0%).
    - Timestamps more than 2 s off arrival fall back to arrival time, logged once per stream. Offsets of ±50 s and 1e6 s now keep the client audio.
    - Audio arriving after its slot went out is logged at most once every 10 s.
    - Zero-fill is capped at 5 s.
  - The queue never drops control messages.
  - Mic and system start/stop are serialized and only one mic restart runs at a time; "interrupted" is sent at notification time.
  - Live mic check: 250 frames, continuous after 0.8 s of startup, no late-drop logs.
  - Suites: `uv run pytest -m "not ollama" -q` 292 passed, EXIT 0; `uv run pytest helper/tests -q` 42 passed, EXIT 0.

## Task 11 review rounds (2026-10-30)
- Round 1 (7a1045e, 0ef0a54): host-clock slotting, bounded queue, Discard < 300 ms voiced, 75% gate dominance, bleed match ≥3 words / 0.6 length, stateful resampler, replay loops, gaps only on real outages. PyObjC completion-handler crash caught by the live check.
- Round 2 (a3d6b06): late / mis-stamped client audio (adaptive slot wait 0.3–1.0 s, >2 s stamps placed by arrival), control messages never dropped on overflow, serialized capture start/stop, "interrupted" at notification time.
- Controller fix: lag measured from buffer start; `_mic_again` under the lock. Reviewer fakes: 0.5 s buffers 0/150 client slots lost (was 37), 0.4 s late 0 lost, +50 s offset 0 lost (30 MB peak), mic stall and quiet client unchanged.
- Tests: 292 passed, 5 deselected; helper 42 passed (exit codes checked).
- Not verified: ScreenCaptureKit on hardware (manual-checks step 4 logs delay, offset and Final-minus-end_ms).

## 2026-10-30 Task 12: question classifier, first pipeline, trigger tests

- New: `classify.py` (rules, per sentence), `pipeline.py` (Segmenters → gate + Whisper → turn merge → classify → `Trigger`; no answers), `scripts/measure.py`, unit tests `test_classify.py` (the four tests on reference transcripts + logistics/strength cases) and `test_pipeline.py` (fake transcriber on real clips: paused question triggers once, unpunctuated segment, Gap mid-question, Unavailable pass-through, bleed duplicate), Mac `test_triggers_replay.py`.
- RED: `test_classify.py` failed to import `fleck_helper.classify`. First classifier run: `negative_set_medium` 1 ("Are you able to see this?"), fixed by widening the logistics pattern.
- First Whisper replay (gate 0.02 / 1.5): conversation.wav recall 7/8, 1 false trigger (Whisper wrote "…nothing. Which was the plan?"; q_ip missed because the merged previous segment came back unpunctuated); conversation-bleed.wav recall **0/8** (every client turn gated overlap: bleed at 0.2× the client level, smoothed mic RMS up to 0.065); negative.wav 0/0/0 (92 s for 750 s of audio).
- Gate grid on the bleed WAV's reference spans: silence 0.02 → 0/31 client turns; 0.04 → 27/31; 0.06–0.12 at ratio 1.5, 2 or 3 → 31/31 client and 27/27 You. Chose 0.08 / 2.0 (mid-plateau; own voice median 0.137). Confidence: non-questions ≤ 0.6, questions ≥ 0.85 on the bleed replay, threshold stays 0.7.
- Fixes: "which is/was/…" is a relative clause; turn segments are joined with a full stop for judging.
- Final replay (four counts × three WAVs):

| WAV | negative_set_short | negative_set_medium | conversation_false_triggers | conversation_recall |
|---|---|---|---|---|
| conversation.wav | n/a | n/a | 0 (allowed 0.64) | 8/8 |
| conversation-bleed.wav | n/a | n/a | 0 (allowed 0.64) | 8/8 |
| negative.wav | 0 | 0 | 0 (no triggers at all) | n/a |

- Classifier level (reference transcripts): 0 / 0 / 0 / 8 of 8.
- Seen, not fixed: on the bleed WAV the mic VAD hears the client, so You segments run across both speakers (11 overlap-gated, 5 client-gated) and repeat client text that `drop_bleed_duplicates` can't match (it compares against one client final). It doesn't affect triggers. After a Gap flush, webrtcvad's hangover opens one segment on silence, which is discarded.
- Fix round 1 (review of d4f6228):
  - The reviewer's probe run against the first classifier found 31 of 50 "should trigger" phrasings missed and 21 of 50 "should not" phrasings firing.
  - Classifier changes:
    - The reject list is now whole phrases: call mechanics, scheduling and small talk.
    - Any auxiliary opening plus `?` is strong.
    - Fragments score 0.6.
    - New patterns: who/which + verb at 0.8, and indirect questions at 0.85.
    - Tag questions do not trigger.
  - test_classify.py now has 54 should-trigger and 58 should-not cases; all pass. The 3 tag probes moved to should-not by policy.
  - Gate is now relative: client ≥ 2.0 × mic, or mic ≤ 0.01. A grid on the bleed WAV gives 31/31 client and 27/27 You for ratios 1.5–3 at every floor from 0.002 to 0.04; ratio 4 fails.
  - Turn speaker is the client share of the turn (≥ 0.75), and overlap segments add no text.
  - A client Gap drops the turn; a You Gap leaves it alone.
  - measure.py needs ≥ 0.5 word overlap to credit recall.
  - Replay, final code: conversation.wav and conversation-bleed.wav each 0 false triggers and 8/8 recall; negative.wav 0 short, 0 medium, no triggers.
  - Suites: `uv run --all-packages pytest helper/tests -q` 180 passed, EXIT 0; `uv run pytest -m "not ollama" -q` 430 passed, 5 deselected, EXIT 0.
- Fix round 2 (re-review 1):
  - Held-out phrasings fired on 15 of 20 small-talk lines, so the classifier now needs positive evidence: a business lexicon term or a vendor capability frame. People words don't count next to meeting or schedule words.
  - Before changing the scorer I wrote a fresh set of 22 should-trigger and 22 should-not lines. RED: 3 missed and 22 false triggers. GREEN: all pass, and the earlier regression lists still pass.
  - Gate (controller ruling): client only when mic ≤ max(adaptive floor, 0.3 × client). The floor is twice the 10th percentile of the mic over the last 10 s, with a minimum of 0.005.
  - Gate grid on the bleed WAV's reference spans: a cap of 0.3 gates 31/31 client turns; 0.25 gates 21/31.
  - Overlapped segments no longer veto a turn; they only drop out of its text.
  - Replay: conversation.wav and conversation-bleed.wav each 0 false triggers and 8/8 recall; negative.wav 0 short, 0 medium.
  - Suites: `uv run --all-packages pytest helper/tests -q` 225 passed, EXIT 0; `uv run pytest -m "not ollama" -q` 475 passed, 5 deselected, EXIT 0.
