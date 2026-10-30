# Hand-over notes

## Retraining the question classifier

The live trigger (spec §7.2, decisions 93–95) has two stages.

1. **Hard vetoes on the CPU** (`helper/fleck_helper/classify.py`). A line is stopped if it is under 4 words, call mechanics or small talk, a tag question, cut off, a request to send a document, or addressed to the client's own colleague.
2. **A learned score for everything else**, statements included. A logistic regression scores the qwen3-embedding:4b vector of the whole transcript under the instruction `CLASSIFY_TASK`.

On the live path, the 4b call runs in parallel with the qwen3-embedding:0.6b `Embedder.embed_query` call. That 0.6b vector is the one search and brief matching reuse; the library index stays on 0.6b. If the pair takes over 0.30 s, the CPU rules decide.

**Two embedding models are loaded while recording.** The 4b model at `num_ctx` 512 takes about 2.9 GB, and the 0.6b model about 0.5–1 GB, both with `keep_alive: -1`. `scripts/ensure_ollama.sh` pulls and pins both.

The model file is `helper/fleck_helper/question_model.json`. It stores:
- the weights and the threshold;
- the L2 strength;
- both embedding models with their digests;
- both instructions;
- the training counts and the cross-validation metrics.

If you change `CLASSIFY_TASK` or the classify model, retrain: the classifier refuses a model trained with another one.

1. **Add lines** to `fixtures/classifier/train.json`. Put real client questions the firm should answer under `"trigger"`, and everything else under `"no_trigger"`: small talk, logistics, requests to send things, and lines addressed to the client's own colleagues. Use one line per utterance, as Whisper writes it. Don't add lines from a file you will evaluate on.
2. **Train.** Ollama must be running with `qwen3-embedding:0.6b`. Run `uv run python scripts/train_classifier.py`. It prints:
   - the counts;
   - any trigger lines the shape gate rejects, which the model never sees;
   - 5-fold CV recall and false-positive rate at the chosen threshold (the lowest one with CV FPR ≤ 2%) and at 0.5;
   - the fit on all of train.

   It rewrites the model file and the CI embedding cache, `helper/tests/unit/data/question_embeddings.npz`. The cache holds 4b vectors only for the texts the CI tests use: the fixture transcripts plus `CI_EXTRA` in the script. A new CI test text goes into `CI_EXTRA`. Embedding all of train with 4b takes a few minutes. `--l2` changes the ridge strength. Compare CV metrics only, never a held-out file.
3. **Evaluate** on a held-out file of the same shape: `uv run python scripts/train_classifier.py --eval held.json`. It prints recall, false triggers, and the missed and false lines. Fix misses by adding similar (not identical) lines to train.json and retraining. Never tune on the held-out file.
4. **Check and commit.**
   - Run `uv run pytest helper/tests -q -m "not ollama"`. It runs the four trigger tests on the cache.
   - Run `uv run pytest helper/tests -q -m ollama`. It checks that the cache still matches the live embedder, and that the committed model fits train at ≥ 95%.
   - On the Mac, replay the fixture WAVs with `scripts/measure.py`.
   - Commit train.json, question_model.json and the cache together.

If the embedding model changes, retrain: the weights only fit the model named in the file. `FLECK_TRIGGER_MIN_CONFIDENCE` overrides the threshold, and empty means the file's threshold.
