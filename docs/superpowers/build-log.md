# Build log

One dated entry per task: what ran, numbers, what changed.

## 2026-10-30 Task 1: workspace, compose stack, pinned models

- `make up`: postgres 16, MinIO, Weaviate 1.27.0, gsim stub, api all healthy; `GET /api/health` returns ok for postgres, minio, weaviate, ollama.
- `make ensure-ollama`: both pinned models present, IDs `500a1f067a9f` (qwen3:8b-q4_K_M) and `ac6da0dfba84` (qwen3-embedding:0.6b) match.
- `api/tests/test_health.py`: RED with the stack down (503, three checks erroring), GREEN with the stack up (200 all ok; closed Ollama port gives 503 with `checks.ollama` starting `error`).
- Changed: `minio/minio` is no longer pullable from Docker Hub or quay.io (401), so compose and CI use `cgr.dev/chainguard/minio` (no shell, so no container healthcheck; the api health check covers it).
- Spec edit (`demo/larkspur-original` reference): already removed before this task; nothing to change.
