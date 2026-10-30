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
