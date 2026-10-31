# Hand-over notes

For rupak.k. Fleck's MVP is built on branch `feat/mvp`. This note says what is running, what was measured, what only you can check, and what is known not to work. Every call made without you is in the [decision log](decisions.md) (decisions 1–147, each with the critique agent's verdict). What happened, run by run, is in the [build log](build-log.md). To use Fleck, follow the [user guide](../user-guide.md).

## 1. What's running and where

The stack is up on your Mac, in Docker, on the simulated Google (`FLECK_GOOGLE_MODE=gsim`).

| Service | Where | What it does |
|---|---|---|
| web | http://localhost:5173 | the web app |
| api | http://localhost:8000 | REST and WebSockets; `GET /api/health` checks the rest |
| worker | (no port) | background jobs: indexing, briefs, processing, chat index |
| postgres | localhost:5432 | data, traces, Claude costs |
| minio | localhost:9000 (console 9001) | recordings and documents |
| weaviate | localhost:8080, 50051 | search |
| gsim | http://localhost:8090 | the Google simulator: sign-in and calendar |
| ollama | localhost:11434, native on the Mac | `qwen3:4b-q4_K_M`, `qwen3-embedding:0.6b`, `qwen3-embedding:4b` |

Everything is bound to `localhost`. Keep it that way: pairing codes have no attempt limit (decision 115i).

**The demo.** Sign in at http://localhost:5173 with **Sign in with Google** > **rupak@northbeam.test** > **Allow**. Then:
- the library has 15 documents, all indexed (3 firm, 6 reference, 6 client);
- the event **Presales: demo prospect** on Today is linked to the client **Harbourline Legal** and has its brief;
- **Meetings** > **Presales: demo prospect** is the test conversation, replayed through the real helper pipeline: the recording, the transcript, 8 helped items with their used markers, the notes, and chat.

**Start and stop.**
- Start: `make up` (and `make ensure-ollama` if Ollama isn't running).
- Stop: `make down`. Your data stays.
- Wipe everything: `make reset`, then `make up`, `make seed-library` and `make seed-demo` to rebuild the demo (about 8 minutes).
- The helper app: `make helper-app`, then `open "dist/Fleck Helper.app"`. The user guide covers pairing and permissions. The stack was reset after the walk-through, so the app is not paired: get a code in **Settings** > **Get a pairing code** and run the command it shows (guide step 3c).

## 2. Measured numbers against the targets

All from Task 22 on this Mac (M5, 24 GB), with the demo brief built first. Details in the build log's Task 22 section.

**Latency (spec §5, decision 112): 3 fresh full-stack replays, pooled.**

| Measure | p95 | Target | Result |
|---|---|---|---|
| Library first words (n=15) | 2.28 s | 3.0 s | pass |
| Library full answer (n=15) | 2.71 s | 6.0 s | pass |
| Claude fallback first words (n=6) | 2.32 s | 5.0 s | pass |
| Not in our material | 1.20–1.26 s | reported only | |

**Question detection (spec §5, §7.2).**
- 3 replays: 24/24 triggers, 24/24 expected outcomes, 0 false triggers, 0 late answers, 0 breaker openings, 0 rules fallbacks.
- Through Whisper (`test_triggers_replay`): `negative_set_short` 0, `negative_set_medium` 0, conversation false triggers 0, recall 8/8, on both `conversation.wav` and `conversation-bleed.wav`.
- Classifier on the acceptance set (never trained or tuned on): recall 194/200, false triggers 2/200 (threshold 0.6653). The rules fallback alone: recall 29/200, false triggers 3/200.

**Under load: 1 replay.** A brief build overlapped the start and an outside `qwen3:4b` generation loop ran alongside. 8/8 triggers and 8/8 outcomes, no late answers, no breaker openings. Latency missed: library first words p95 3.49 s, full answer 3.84 s, Claude first words 4.51 s. Cause: Ollama runs one request at a time, so each live call waited behind the outside generation. Fleck's own background work already waits while a meeting records (decision 137).

**Local only (spec §6).** `test_local_only` passed with `FLECK_LIVE_FALLBACK=off`: no connection left the Mac, and no `huggingface.co` or `anthropic` line in the helper log.

**Panel not in a screen share.** Passed at your unlocked Mac: hidden_hits 0, control_hits 9774.

**Soak, 60 minutes (spec §7.7): soak 2 passes.** 10 loops, 80/80 triggered, 0 false triggers, 0 late answers, 0 breaker openings, 0 rules fallbacks, no thermal warning. Swap went from 0.7 to 1.8 GB.
- Drift: library first words p95 1.96 s in the first 10 minutes, 1.85 s in the last 10. Pass.
- Memory, by your ruling (decision 143): measured from after loop 1, the footprint grew 50 MB (3015 → 3065 MB). Pass. From 60 s it grew 573 MB, all during the first loop, as MLX filled its cache.
- Outcomes, by your ruling (decision 143): 0 wrong, with 1 Claude timeout (loop 5, `q_liability`, Haiku hit the 5 s limit). Pass, reported as `claude_timeouts`.

**Used markers (decision 118, threshold 0.52).** Correct on 7 of 8 cards in every run. `q_liability` scored 0.519, 0.526, 0.555 and 0.527 (load run), so it is falsely marked **Used** in 3 of 4 runs.

## 3. Checks only you can do

Claude cannot hold a real call (decision 124). Run these from `manual-checks.md` first. Build first: `make helper-app`, then `uv run --package fleck-helper fleck-helper set-claude-key --from-env .env` once, then `open "dist/Fleck Helper.app"`. Logs: `~/Library/Logs/Fleck/helper.log`.

1. **A real Google Meet call on speakers**, in the browser, with a colleague or a second device as the client. Ask two or three questions from `fixtures/conversation/reference.json`. Expect **Thinking…** about a second after the client finishes, then a labelled answer. Your own voice never triggers, and the transcript has no You line repeating the client. Share your full screen: the panel must not appear on the other device. Note any echoed You lines and the time to first words.
2. **A real Zoom call on speakers**, in the desktop app, the same way. Zoom's echo cancellation differs from the browser's.
3. **The notification's Record.** An event with a Meet link 6–10 minutes ahead; at "Starts in 5 min." press **Record**, then repeat with a click on the notification's body.
4. **⌥⌘F anywhere**, with Accessibility granted to Fleck Helper.
5. **Permissions after a rebuild**: Microphone, Screen & System Audio Recording and Notifications stay granted after `make helper-app`.
6. **Resume**: force-quit the app while recording, reopen it, **Start recording now**, and check **Resume** and **Start new**.
7. **Sleep**: while recording, `pmset -g assertions` lists "Fleck is recording a meeting"; after Stop it's gone.
8. **Real Google sign-in and calendar**: the Google Cloud setup is in the user guide, step 4b.

**The echo ratio.** The gate allows mic bleed up to 0.3× the client's level (`FLECK_GATE_BLEED_MAX`). The only hardware measurement so far is **0.138**, with afplay playing through the speakers into the signed app. No real call has been measured.

| Source | Echo ratio (mic / client) | Gate cut-off |
|---|---|---|
| afplay through speakers (Task 18) | 0.138 | 0.3 |
| Real Meet call (check 1) | *to fill in* | 0.3 |
| Real Zoom call (check 2) | *to fill in* | 0.3 |

Above 0.3, the client's speech is gated "overlap" and never triggers.

The older Task 2b and Task 11 checks are further down `manual-checks.md` (capture under the signed app, mid-call device changes, outage markers, notification sounds).

## 4. Known limits

**Answers**
- `q_liability` ("Would you cap liability at twelve months of fees?") never gets a **Reference contract** answer. The local model replies NONE, so Claude answers it as **General knowledge** (decision 101). Its used marker is also falsely **Used** in 3 of 4 runs (0.52–0.555 against the 0.52 threshold).
- When a client's turn is split, the second card carries only its own half of the question (decision 104).
- The number check matches figures by value, not by meaning (decisions 79, 100). "$13,500 offshore" passes when 13,500 is the onsite rate in the cited row, and "12 releases a year" passes against "12 months". A period stated without per/a/each/every ("over the year") isn't read.
- **Late** answers exist. When the Mac is busy and the rules said no, Fleck rechecks the line for up to 30 s. A late answer is tagged **Late** and answers an earlier question (decision 128). None happened in the measured runs.
- The brief's 3 s answer path has never run live: no scripted question matched a brief question.
- Library first words go over 3 s when another app uses the same Ollama (load run: p95 3.49 s).
- The live Claude fallback timed out on 1 of 40 calls in the soaks (2.5%). The card then reads **Not in our material**.

**Chat**
- Chat drops any sentence whose figures are not in the sources it cites. An honest sentence with an uncited number is dropped too (decision 145).
- A meeting's chat can cite another client's library document (decision 136).

**Names reaching Claude (redaction, decisions 113, 116, 129)**
- A person who is not an attendee (named in the call or the invite) is not redacted.
- A name Whisper writes in lower case or misspells is not redacted; the live transcript matches names as written.
- "Priyas" with no apostrophe is not redacted.
- Words under 4 letters of a client's name ("ACE") are not redacted.
- A company is redacted from the invite only if it is a library client or its people are guests with a company email. A company whose people aren't guests, or use public mail, is not.
- An unhyphenated domain gives one word: orionfreight.com gives "Orionfreight", so "Orion Freight" is not redacted.
- A client linked mid-recording doesn't reach the helper's live redaction (decision 144). The helper already redacts every library client.

**Other**
- The classifier's numbers come from fixed sets (the acceptance set and the scripted audio), not from new phrasings on real calls.
- Claude prices were checked against the claude-api skill's cached table, not a live page (decision 146).
- `fleck-helper replay` never unloads the models after it ends. The app does.
- A meeting left `recording` for over 3 hours stops blocking indexing and briefs (decision 137). One left for less still blocks them until it is stopped.
- A remote colleague on the call is heard through call audio and treated as **Client** (spec §7.2).

## 5. Retraining the question classifier

The live trigger (spec §7.2, decisions 93–95) has two stages.

1. **Hard vetoes on the CPU** (`helper/fleck_helper/classify.py`). A line is stopped if it is under 4 words, call mechanics or small talk, a tag question, cut off, or a request to send a document (the document noun ends the line or is followed by a delivery phrase such as "after the call"). Name-led lines ("Arjun, can you…", "Rupak, what would…") go to the model.
2. **A learned score for everything else**, statements included. A logistic regression scores the qwen3-embedding:4b vector of the whole transcript under the instruction `CLASSIFY_TASK`.

On the live path, the 4b call runs in parallel with the qwen3-embedding:0.6b `Embedder.embed_query` call. That 0.6b vector is the one search and brief matching reuse; the library index stays on 0.6b. The pair shares one deadline of 1 s (decision 128; over 0.30 s is logged as slow). Past it, the CPU rules decide, a breaker keeps them deciding for 10 s, and a rules "no" is rechecked for up to 30 s (the **Late** answers). The pipeline logs the run's fallback count, and measure.py prints it. At warm-up the pipeline compares both models' digests with Ollama's `/api/tags`; on a mismatch it logs an error and the rules decide for the whole session (run `scripts/ensure_ollama.sh`, or retrain).

**Two embedding models are loaded while recording.** The 4b model at `num_ctx` 512 takes about 2.9 GB, and the 0.6b model about 0.5–1 GB, both with `keep_alive: -1`. `scripts/ensure_ollama.sh` pulls and pins both.

**The answer model's prompt cache (start-early, decisions 72 and 98).** `fleck_core.answer.prefill(passages, question_hint, ollama)` sends the live prompt for qwen3:4b with `num_predict` 1. Ollama treats `num_predict` 0 as unlimited. The answer that follows then reads only the end of the question: TTFT drops from about 0.4–2 s to about 0.09 s. This assumes Ollama's default `OLLAMA_NUM_PARALLEL=1`, so the answer reuses the prefilled slot. With more parallel slots, an answer may land on a cold slot. The service's setting was not changed.

The model file is `helper/fleck_helper/question_model.json`. It stores:
- the weights and the threshold;
- the L2 strength;
- both embedding models with their digests;
- both instructions;
- the training counts and the cross-validation metrics.

If you change `CLASSIFY_TASK` or the classify model, retrain: the classifier refuses a model trained with another one.

1. **Add lines** to `fixtures/classifier/train.json`. Put real client questions the firm should answer under `"trigger"`, and everything else under `"no_trigger"`: small talk, logistics, requests to send things, and lines addressed to the client's own colleagues. Use one line per utterance, as Whisper writes it. Don't add lines from a file you will evaluate on.
2. **Train.** Ollama must be running with `qwen3-embedding:4b` and `qwen3-embedding:0.6b`. Run `uv run python scripts/train_classifier.py`. It prints:
   - the counts (train.json is deduplicated ignoring case and punctuation; the first line and label win);
   - any trigger lines the shape gate rejects, which the model never sees;
   - 5-fold CV recall and false-positive rate at the chosen threshold (the lowest one with CV FPR ≤ 2%) and at 0.5;
   - the fit on all of train.

   It rewrites the model file and the CI embedding cache, `helper/tests/unit/data/question_embeddings.npz`. The cache holds 4b vectors only for the texts the CI tests use: the fixture transcripts plus `CI_EXTRA` in the script. A new CI test text goes into `CI_EXTRA`. Embedding all of train with 4b takes a few minutes. `--l2` changes the ridge strength. Compare CV metrics only, never a held-out file.
3. **Evaluate** on a held-out file of the same shape: `uv run python scripts/train_classifier.py --eval held.json`. It prints recall, false triggers, and the missed and false lines. Add `--rules-only` to score the CPU rules fallback instead. Fix misses by adding similar (not identical) lines to train.json and retraining. Never tune on the held-out file.
4. **Check and commit.**
   - Run `uv run pytest helper/tests -q -m "not ollama"`. It runs the four trigger tests on the cache.
   - Run `uv run pytest helper/tests -q -m ollama`. It checks that the cache still matches the live embedder, and that the committed model fits train at ≥ 95%.
   - On the Mac, replay the fixture WAVs with `scripts/measure.py`.
   - Commit train.json, question_model.json and the cache together.

If the embedding model changes, retrain: the weights only fit the model named in the file. `FLECK_TRIGGER_MIN_CONFIDENCE` overrides the threshold, and empty means the file's threshold.
