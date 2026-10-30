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

1. **Sign in with Google.** Every member of the presales team signs in with Google. The team shares one library of past material. Who counts as the team is an allow-list of email domains and addresses in `.env`; anyone else is refused at sign-in. **(Claude's call.)**
2. **Calendar sync.** The user's Google Calendar appears in Fleck. Each meeting can be linked to a client.
3. **Library.** The team uploads past material (PDF, Word, PowerPoint, Excel, Markdown), filed by client, plus firm-wide material and reference material. Fleck indexes it for search. Each document has a kind: **client** (our past work for that client), **firm** (rate card, capabilities, FAQ) or **reference** (third-party examples). Reference documents may be quoted for their terms but are never presented as the firm's past work. **(Claude's call.)**
4. **Pre-meeting brief.** Before a meeting with a known client: the questions this client is likely to ask, each with the best answer from the library and its source. **(Claude's call, accepted by rupak.k to review.)**
5. **Meeting kick-off.** A macOS notification 5 minutes before a meeting starts recording with one click; an ad-hoc **Start recording** for unplanned calls. Fleck captures the user's mic and the call audio, and transcribes both live on the Mac.
6. **Help during the meeting.** When the client asks a question, a short answer (2–3 lines plus the source) appears next to the live transcript, written by a local model from the library. When the library has nothing relevant, Claude answers, labelled **General knowledge, not from our material**.
7. **After the meeting.** The recording, full transcript and the user's own notes are saved. A summary keeps the user's notes word for word and adds what was discussed. The notes mark every moment Fleck helped: the question, the answer it showed, its source, and whether the salesperson's reply used it. **(The "used it" marker is Claude's call.)**
8. **Team chat on notes.** The team asks Claude questions about one meeting or across all meetings and the library; answers cite meetings, timestamps and documents.
9. **Testable end to end, and left running for review.** A scripted presales conversation, recorded as audio, drives the whole flow in tests. At hand-over the stack is up on rupak.k's Mac with that conversation already processed, so they can play the recording, read the notes and see where the model helped.

## 4. Non-goals (not in the MVP)

iOS, Android, Windows; Outlook calendar; billing, pricing tiers, admin console, SSO beyond Google; telling individual client speakers apart (the client side is one speaker, **Client**); CRM, Slack or Notion integrations; editing documents inside Fleck; languages other than English.

## 5. Anti-goals (failure even if it works)

- **A made-up fact shown as ours.** Prices, rates, team sizes, dates, client names and results come only from the library, quoted with their source. If no document backs a number, the answer says so. Claude's fallback answers never contain prices or claims about the firm's past work.
- **Help that arrives too late.** Target: the first words of an answer appear within **3 s** of the client finishing the question; the full answer within **6 s** (p95, on the test conversation, on rupak.k's Mac).
- **Missed or misheard questions.** Target: on the test conversation, at least 90% of the client's questions trigger an answer, and no more than one false trigger per 10 minutes.
- **Getting in the way.** Nothing pops up over the call; answers appear in Fleck's own window, newest on top, and never make a sound.

## 6. Constraints

- Development and use on rupak.k's Mac: Apple M5, 24 GB, macOS 26. The live path (capture, transcription, local model) runs entirely on it.
- Meeting text and library text leave the machine only to the Claude API (fallback answers, summary, chat). rupak.k is fine with library text, including real client documents, going to Claude. Audio never leaves the machine except into Fleck's own storage.
- External secrets: `ANTHROPIC_API_KEY` (from rupak.k), and Google OAuth client id/secret for real Google sign-in. Both in `.env`, never committed.
- Real client documents (`clients/jf/`, Joele Frank) are out of the MVP: the MVP is built and tested on the demo library only. rupak.k adds the real material after accepting the MVP. They are never committed.
- Built so it can later be deployed for the whole team (multi-user safe), but the MVP runs on one Mac.
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
 Ollama (native, Metal GPU): qwen3:8b, embeddings  ◀──────┘            └──▶ Claude API
```

| Part | Choice | Why |
|---|---|---|
| Desktop helper | Python 3.12, `rumps` + PyObjC menu-bar app, packaged with py2app | Python end to end; one language with the api |
| Call audio | ScreenCaptureKit (system audio); mic via AVAudioEngine with Apple voice processing (echo cancellation, since rupak.k uses speakers) | No bot joins the call |
| Live transcription | `mlx-whisper`, `large-v3-turbo`, on the Mac's GPU | Fast enough to keep up with speech, no network |
| Local model | Ollama, `qwen3:8b` (4-bit, ~5 GB), thinking mode off | Best ~8B model at answering only from the passages it's given; fits next to Whisper in 24 GB |
| Embeddings | Ollama `nomic-embed-text`, called by Weaviate's `text2vec-ollama` module | Local, one model server for both |
| Ollama placement | Native on macOS, not in Docker | Docker on macOS can't use the GPU |
| Vector search | Weaviate, hybrid (keyword + vector) search, one tenant per team | Hybrid search catches exact terms like "SLA" and "BOPIS" |
| Document parsing | `markitdown` (PDF, DOCX, PPTX, XLSX → Markdown) | One library for every format the team uses |
| api + worker | FastAPI, SQLAlchemy, Alembic, `uv`; background jobs in a Postgres table (no Redis) | Fewer moving parts |
| Storage | Postgres (data, traces, cost); MinIO (audio, transcripts, original documents) | MinIO swaps for S3 in the cloud |
| Web | React + Vite + TypeScript, `pnpm` | |
| Claude | Fallback answers: Claude Haiku 5.5 (speed). Summary and chat: Claude Sonnet 5.5. Both set in `.env` | Fast where it's live, strong where it isn't |
| Google | Real Google OAuth + Calendar; `gsim`, a small Google simulator, for tests and the demo | Tests and the Walker video need a fixed sign-in and calendar |
| Test audio | Scripted conversation spoken with macOS `say` voices, stereo WAV: left = salesperson, right = client | Built in, no extra install; WAVs committed so tests run anywhere |
| Tests | pytest (api, worker, helper pipeline), Vitest (web), Playwright end to end | |

## 8. The test conversation

A 6–8 minute presales call between rupak.k as salesperson (left channel) and a prospect (right channel), voiced by two different `say` voices. The prospect is a fictional firm wanting AI automation of an existing document workflow. **(Claude's call.)** The script includes, in order:

- small talk and agenda (no questions to answer, so no false triggers)
- questions the library answers exactly: rates, how fast a team can start, IP ownership, SLAs, a similar past project and its results
- a question the library covers only in general, answered by Claude and labelled as general knowledge
- a pricing question for which no document gives a number, where the answer must say so
- a few overlaps and interruptions, and spoken numbers and dates

The script, the reference transcript and the expected outcome for each question (answered from library / Claude / "not in our material", and which document) are committed next to the WAV. They are the acceptance test for goals 5–7 and the anti-goal targets.

The demo library in `clients/` is rewritten so the fictional firm (Northbeam Digital) is an AI-automation consultancy: `_firm/` rewritten, Larkspur replaced by three fictional past projects (a track-changes legal document flow, an investigation research agent, a contract-extraction pipeline), each with proposal, SOW and case study. The CUAD contracts stay in `clients/_reference/` as reference material. **(Claude's call.)**

## 9. Ready for rupak.k's review when

1. `docker compose up` plus the helper bring up the whole stack on rupak.k's Mac.
2. The test conversation has been replayed through the real pipeline into a meeting called **Presales: demo prospect**.
3. In the browser rupak.k can sign in, see the calendar, open that meeting, play the recording, read the transcript and the notes with every moment Fleck helped marked, and chat about it.
4. All automated tests pass, and the latency and question-detection targets in §5 are measured on the Mac and written into the hand-over note.
5. The decision log lists every call made without rupak.k, each with the critique agent's verdict.

The hand-over runs on `gsim` by default: the demo meeting is linked to a fixed event in the simulated calendar, so it works without any Google setup. Real Google sign-in and calendar are switched on by putting OAuth credentials in `.env`; the hand-over note gives the Google Cloud setup steps and rupak.k checks real sign-in by hand. **(Claude's call.)**

Live capture is checked by hand before hand-over: one real call on speakers in a browser (Google Meet) and one in a desktop app (Zoom), recorded with whole-system audio capture, with the results in the hand-over note. The automated tests use replay. The helper is signed with a local self-signed certificate so macOS keeps its Screen Recording and Microphone permissions across rebuilds; no Apple Developer ID needed. **(Claude's call.)**

## 10. How the work runs after this is approved

- rupak.k approves this document. From then on Claude writes the detailed spec, the plan, the tests and the code without waiting for them.
- A **critique agent** stands in for rupak.k. It reviews each significant decision and each finished milestone against this document, and its verdict goes into the decision log with the decision.
- Claude stops and asks rupak.k only if a goal or anti-goal in this document can't be met as written.
- Work happens on feature branches with PRs. rupak.k reviews the result and the decision log at the end and corrects course.

## 11. Left to Claude

Milestone breakdown; data model; REST routes; UI layout and copy; prompt wording; question-detection method; streaming window settings; chunk sizes; how "used it" is measured; ports and service names.
