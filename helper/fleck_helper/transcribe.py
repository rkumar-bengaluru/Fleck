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


class StreamTranscriber:
    """Priority scheduler in front of one GPU worker (a job already running is never preempted)."""

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
            finally:
                job.done.set()

    def _whisper(self, audio: np.ndarray, final: bool) -> str:
        import mlx_whisper  # lazy: darwin-only

        out = mlx_whisper.transcribe(
            audio.astype(np.float32), path_or_hf_repo=self.model, language="en",
            condition_on_previous_text=False, verbose=None,
        )
        return out["text"].strip()
