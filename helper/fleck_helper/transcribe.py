import threading

import numpy as np

# Lower number runs first: client final > client partial > you final > you partial.
_PRIO = {("client", True): 0, ("client", False): 1, ("you", True): 2, ("you", False): 3}


class _Job:
    def __init__(self, audio, final, speaker, segment):
        self.audio, self.final, self.speaker, self.segment = audio, final, speaker, segment
        self.prio = _PRIO[(speaker, final)]
        self.done = threading.Event()
        self.text = ""  # stays "" if the job is dropped as stale
        self.error: Exception | None = None


class StreamTranscriber:
    """Priority scheduler in front of one GPU worker (a job already running is never preempted).

    A queued partial dropped as stale returns "" (same as silence). A partial submitted after its
    segment's final may still run; callers must ignore partials once the final is out.
    An exception in the transcribe fn is re-raised in the calling thread; the worker survives."""

    def __init__(self, model: str = "mlx-community/whisper-large-v3-turbo", _fn=None):
        self.model, self._fn = model, _fn or self._whisper
        self._cv = threading.Condition()
        self._q: list[_Job] = []
        self._client_active = False
        threading.Thread(target=self._loop, daemon=True).start()

    def set_client_active(self, active: bool) -> None:
        with self._cv:
            self._client_active = active
            self._cv.notify_all()

    def transcribe(self, audio: np.ndarray, final: bool, speaker: str = "client", segment: str | None = None) -> str:
        job = _Job(audio, final, speaker, segment)
        with self._cv:
            if segment is not None:  # a newer partial or the final supersedes queued partials of the same segment
                for old in [j for j in self._q if j.segment == segment and j.speaker == speaker and not j.final]:
                    self._q.remove(old)
                    old.done.set()
            self._q.append(job)
            self._cv.notify_all()
        job.done.wait()
        if job.error:
            raise job.error
        return job.text

    def _loop(self):
        while True:
            with self._cv:
                while not (ready := [j for j in self._q if not (self._client_active and j.speaker == "you")]):
                    self._cv.wait()
                job = min(ready, key=lambda j: j.prio)  # min is stable: FIFO within a priority
                self._q.remove(job)
            try:
                job.text = self._fn(job.audio, job.final)
            except Exception as e:
                job.error = e
            finally:
                job.done.set()

    def _whisper(self, audio: np.ndarray, final: bool) -> str:
        import mlx_whisper  # lazy: darwin-only

        if not hasattr(self, "_path"):
            self._path = local_model(self.model)
        out = mlx_whisper.transcribe(
            audio.astype(np.float32), path_or_hf_repo=self._path, language="en",
            condition_on_previous_text=False, verbose=None,
        )
        return kept_text(out)


def local_model(repo: str) -> str:
    """The cached snapshot's folder, so loading Whisper never asks huggingface.co for the latest revision (spec: local
    only, Task 22). Only a model not yet on this Mac is fetched by its repo name."""
    from huggingface_hub import snapshot_download
    try:
        return snapshot_download(repo, local_files_only=True)
    except Exception:  # not cached yet (LocalEntryNotFoundError)
        return repo


MAX_COMPRESSION, MAX_NO_SPEECH = 2.4, 0.6  # Whisper's own thresholds, which it applies only to pick a retry


def kept_text(out: dict) -> str:
    """The transcript without Whisper's hallucinations: segments that repeat themselves ("2. 2. 2. …", compression
    ratio over 2.4) or that it thinks hold no speech (no_speech_prob over 0.6)."""
    segs = out.get("segments")
    if segs is None:
        return out["text"].strip()
    return " ".join(t for s in segs if s.get("compression_ratio", 0) <= MAX_COMPRESSION
                    and s.get("no_speech_prob", 0) <= MAX_NO_SPEECH and (t := s["text"].strip()))
