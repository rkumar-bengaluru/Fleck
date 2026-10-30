# Hand-over notes

## Retraining the question classifier

The live trigger (spec §7.2, decisions 93–95) has two stages.

1. **Hard vetoes on the CPU** (`helper/fleck_helper/classify.py`). A line is stopped if it is under 4 words, call mechanics or small talk, a tag question, cut off, or a request to send a document (the document noun ends the line or is followed by a delivery phrase such as "after the call"). Name-led lines ("Arjun, can you…", "Rupak, what would…") go to the model.
2. **A learned score for everything else**, statements included. A logistic regression scores the qwen3-embedding:4b vector of the whole transcript under the instruction `CLASSIFY_TASK`.

On the live path, the 4b call runs in parallel with the qwen3-embedding:0.6b `Embedder.embed_query` call. That 0.6b vector is the one search and brief matching reuse; the library index stays on 0.6b. If the pair takes over 0.30 s (one shared deadline), the CPU rules decide; the pipeline logs the run's fallback count, and measure.py prints it. At warm-up the pipeline compares both models' digests with Ollama's `/api/tags`; on a mismatch it logs an error and the rules decide for the whole session (run `scripts/ensure_ollama.sh`, or retrain).

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
