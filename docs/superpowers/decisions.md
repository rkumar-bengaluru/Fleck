# Decision log

Every decision Claude made without rupak.k. rupak.k reviews this at the end and corrects course.

Columns: **What** was decided · **Options** considered · **Why** · **Critic**, the critique agent's verdict (from implementation onward).

| # | Date | What | Options | Why | Critic |
|---|---|---|---|---|---|
| 1 | 2026-10-30 | Demo contracts from CUAD v1 (6 IT-services contracts), kept in `clients/_reference/` as reference-only | GSA SOW samples (links 404); generic template sites | Only public, licensed (CC BY 4.0) set of real services contracts found | — |
| 2 | 2026-10-30 | Simulated firm "Northbeam Digital" instead of rupak.k's firm | Use the real firm name | Invented facts must not carry a real company's name | — |
| 3 | 2026-10-30 | Rewrite Northbeam as an AI-automation consultancy | Keep retail OMS story | Questions in the test conversation should match rupak.k's real meetings | — |
| 4 | 2026-10-30 | `clients/jf/` git-ignored | Commit it | Real client SOWs. MVP uses the demo library only; library text may go to Claude (both rupak.k's decisions) | — |
| 5 | 2026-10-30 | Pre-meeting brief added as a goal | Leave out | "Ahead of the game" is easiest before the call starts | — |
| 6 | 2026-10-30 | Local model `qwen3:8b` via Ollama, thinking off | Llama 3.1 8B, Mistral 7B, Gemma | Strongest ~8B at grounded answers; fits 24 GB with Whisper | — |
| 7 | 2026-10-30 | Claude Haiku 5.5 for live fallback, Sonnet 5.5 for summary and chat | One model for all | Speed where live, quality after | — |
| 8 | 2026-10-30 | Ollama native, not in Docker | In Docker | Docker on macOS has no GPU | — |
| 9 | 2026-10-30 | Background jobs in Postgres, no Redis | Redis + arq/Celery | One less service | — |
| 10 | 2026-10-30 | `markitdown` for document parsing | docling, unstructured, per-format libs | One small library for all formats | — |
| 11 | 2026-10-30 | Test voices from macOS `say` | Piper TTS, rupak.k's own recordings | Built in; WAVs committed | — |
| 12 | 2026-10-30 | Keep `gsim` Google simulator | Real Google only | Deterministic tests and Walker demo | — |
| 13 | 2026-10-30 | Client side is one speaker, **Client** | Diarization | Out of MVP scope | — |
| 14 | 2026-10-30 | "Used it" marker on each assist | Leave out | Shows where the model actually helped, as rupak.k asked | — |
| 15 | 2026-10-30 | Team = allow-list of email domains/addresses in `.env` | Anyone with Google; invite flow | Library holds rates and SOWs; simplest real boundary | — |
| 16 | 2026-10-30 | Document kinds: client / firm / reference; reference never cited as our work | Treat all alike | Real CUAD companies must not appear as our clients | — |
| 17 | 2026-10-30 | Three fictional AI-automation past projects replace Larkspur | Rewrite Larkspur only | Enough variety for the "similar project" questions | — |
| 18 | 2026-10-30 | Hand-over on `gsim`; real Google switched on via `.env`, checked by hand | Real Google only | Works without Google Cloud setup | — |
| 19 | 2026-10-30 | Whole-system audio capture; manual check in Meet (browser) and Zoom | Per-app capture | One path covers every app | — |
| 20 | 2026-10-30 | Helper signed with a local self-signed certificate | Ad-hoc signing; Apple Developer ID | Keeps macOS permissions across rebuilds, no paid account | — |
| 21 | 2026-10-30 | Embeddings `qwen3-embedding:0.6b` (rupak.k's suggestion) | `nomic-embed-text` | Better retrieval for ~0.5 GB more | — |
| 22 | 2026-10-30 | Latency measures: brief-answer match, start on partial text, ≤3 passages with warm prefix, `qwen3:4b` fallback | Rely on 8B speed alone | Estimated first-word time 2.2–2.8 s leaves little headroom under 3 s | — |
| 23 | 2026-10-30 | Measure the latency budget as the first build task | Measure at the end | Everything live depends on it | — |
| 24 | 2026-10-30 | Read rupak.k's "prioritise recall" as precision first (zero false triggers is the blocker) | Recall first | The same text says false triggers cost more than misses | — |
| 25 | 2026-10-30 | Speaker-gate thresholds tuned on the bleed recording | Tune on clean audio | On speakers the mic hears the client; clean audio would set thresholds that never fire | — |
| 26 | 2026-10-30 | Meeting-logistics questions added to the negative set | Short acknowledgements only | "Can you see my screen?" is the likeliest false trigger | — |
| 27 | 2026-10-30 | Speculative work on partials allowed, display only on final | Drop "start early" | Keeps the latency gain without repeated triggers | — |
