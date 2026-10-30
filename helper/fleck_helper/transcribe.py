import threading

import numpy as np

_gpu = threading.Lock()  # shortcut: one process-wide GPU lock, per-stream scheduling later


class StreamTranscriber:
    def __init__(self, model: str = "mlx-community/whisper-large-v3-turbo"):
        self.model = model

    def transcribe(self, audio: np.ndarray, final: bool) -> str:
        import mlx_whisper  # lazy: darwin-only

        with _gpu:
            out = mlx_whisper.transcribe(
                audio.astype(np.float32), path_or_hf_repo=self.model, language="en",
                condition_on_previous_text=False, verbose=None,
            )
        return out["text"].strip()
