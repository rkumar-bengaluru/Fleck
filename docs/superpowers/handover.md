# Hand-over notes

## Retraining the question classifier

The live trigger (spec §7.2, decisions 93 and 94) works in two steps. First, a CPU question-shape gate passes or rejects each sentence (`helper/fleck_helper/classify.py`). Then a logistic regression scores the question text's embedding under the classification instruction `CLASSIFY_TASK` in classify.py. The live path fetches that vector in the same request as the `Embedder.embed_query` vector that search reuses. The model is `helper/fleck_helper/question_model.json`. It stores the weights, the threshold, the L2 strength, the embedding model and its digest, both instructions, the training counts and the cross-validation metrics. If you change `CLASSIFY_TASK`, retrain: the classifier refuses a model trained with another instruction.

1. **Add lines** to `fixtures/classifier/train.json`. Put real client questions the firm should answer under `"trigger"`, and everything else under `"no_trigger"`: small talk, logistics, requests to send things, and lines addressed to the client's own colleagues. Use one line per utterance, as Whisper writes it. Don't add lines from a file you will evaluate on.
2. **Train.** Ollama must be running with `qwen3-embedding:0.6b`. Run `uv run python scripts/train_classifier.py`. It prints:
   - the counts;
   - any trigger lines the shape gate rejects, which the model never sees;
   - 5-fold CV recall and false-positive rate at the chosen threshold (the lowest one with CV FPR ≤ 2%) and at 0.5;
   - the fit on all of train.

   It rewrites the model file and the CI embedding cache of classification vectors, `helper/tests/unit/data/question_embeddings.npz`. Keep the cache under 2 MB; it was 1.98 MB at 1,034 texts. If it grows past that, cut it to the texts the tests use and make the train-fit test an `ollama` test. `--l2` changes the ridge strength. Compare CV metrics only, never a held-out file.
3. **Evaluate** on a held-out file of the same shape: `uv run python scripts/train_classifier.py --eval held.json`. It prints recall, false triggers, and the missed and false lines. Fix misses by adding similar (not identical) lines to train.json and retraining. Never tune on the held-out file.
4. **Check and commit.**
   - Run `uv run pytest helper/tests -q -m "not ollama"`. It runs the four trigger tests on the cache, and the train-fit test needs ≥ 95%.
   - Run `uv run pytest helper/tests -q -m ollama`. It checks that the cache still matches the live embedder.
   - On the Mac, replay the fixture WAVs with `scripts/measure.py`.
   - Commit train.json, question_model.json and the cache together.

If the embedding model changes, retrain: the weights only fit the model named in the file. `FLECK_TRIGGER_MIN_CONFIDENCE` overrides the threshold, and empty means the file's threshold.
