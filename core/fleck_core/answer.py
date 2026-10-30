import dataclasses
import json
import queue
import re
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass

import httpx

from .numbers import extract_figures
from .prompts import build_messages
from .types import Passage
from .validate import NOT_IN_MATERIAL, LibraryCheck, check_library_answer, split_sentences

MODEL = "qwen3:4b-q4_K_M"  # decision 72
OPTIONS = {"temperature": 0, "num_predict": 160}  # 3 short sentences fit in ~100 tokens
FIRST_TOKEN_S, DONE_S = 4.0, 8.0  # from started_wall; past either the answer is "Not in our material" (timeout)


class OllamaClient:
    def __init__(self, url: str = "http://localhost:11434", transport: httpx.BaseTransport | None = None):
        self._c = httpx.Client(base_url=url, timeout=120, transport=transport)

    def stream(self, messages: list[dict], model: str, keep_alive: int = -1, think: bool = False,
               options: dict | None = None) -> Iterator[str]:
        body = {"model": model, "messages": messages, "stream": True, "think": think, "keep_alive": keep_alive}
        if options:
            body["options"] = options
        with self._c.stream("POST", "/api/chat", json=body) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                d = json.loads(line)
                if "error" in d:
                    raise RuntimeError(f"ollama: {d['error']}")
                if t := d.get("message", {}).get("content"):
                    yield t
                if d.get("done"):
                    return

    def unload(self, model: str) -> None:
        self._c.post("/api/chat", json={"model": model, "messages": [], "keep_alive": 0}).raise_for_status()


@dataclass
class Delta:
    """Display text appended to sentence `index` while it streams."""
    index: int
    text: str


@dataclass
class Sentence:
    """Sentence `index` is complete and checked: its text replaces every Delta shown for that index."""
    index: int
    text: str
    ok: bool
    reason: str | None


@dataclass
class Done:
    check: LibraryCheck  # check.kind and check.citations are the answer's; not ok means show "Not in our material"
    first_words_ms: int | None
    full_ms: int


@dataclass
class Superseded:
    """Emitted by the caller (Task 14), never by stream_library_answer: a speculative answer was abandoned."""


AssistUpdate = Delta | Sentence | Done | Superseded
_EOS = object()
_PARTIAL_MARK = re.compile(r"\s*\[\d*$")


def _display(raw: str) -> str:
    return _PARTIAL_MARK.sub("", re.sub(r"\s*\[\d+\]", "", raw)).lstrip()


def _pump(ollama, messages: list[dict], q: queue.Queue, stop: threading.Event) -> None:
    tokens = ollama.stream(messages, MODEL, options=OPTIONS)
    try:
        for t in tokens:
            if stop.is_set():
                return
            q.put(t)
        q.put(_EOS)
    except Exception as e:  # handed to the consumer's thread
        q.put(e)
    finally:
        tokens.close()  # closes the HTTP stream, so Ollama stops generating


def stream_library_answer(question: str, passages: list[Passage], client_names: list[str], ollama: OllamaClient,
                          started_wall: float, question_end_wall: float) -> Iterator[AssistUpdate]:
    """Stream a cited answer from `passages` through qwen3:4b, checking each sentence (spec 7.4 rules 1-3).

    Event contract for a consumer: show each Delta's text appended to sentence `index` (sentences are joined with
    one space). When Sentence(index, ...) arrives, replace everything shown for that index with Sentence.text,
    which is "Not in our material" when the sentence failed its check. A sentence gets Deltas only until a figure
    appears in it; the rest is held until the Sentence. Done ends the stream; if Done.check is not ok, replace the
    whole answer with "Not in our material" (kind not_in_material: NONE, no valid citation, or reason timeout).
    Times are wall clock (time.time()); first_words_ms and full_ms are measured from question_end_wall, the
    timeouts (FIRST_TOKEN_S to the first token, DONE_S to the end) from started_wall. Stopping iteration
    (generator close) cancels the Ollama request. An Ollama error is raised to the consumer."""
    q: queue.Queue = queue.Queue()
    stop = threading.Event()
    threading.Thread(target=_pump, args=(ollama, build_messages(question, passages), q, stop), daemon=True).start()
    text, pos, index, shown, held, got, decided = "", 0, 0, "", False, False, False
    first_words: int | None = None

    def ms() -> int:
        return round((time.time() - question_end_wall) * 1000)

    def emit(final: bool) -> Iterator[AssistUpdate]:
        nonlocal pos, index, shown, held, first_words
        pieces = split_sentences(text[pos:])
        n = len(pieces) if final else len(pieces) - 1
        if not final and n > 0 and pieces[-1].startswith("["):
            n -= 1  # "... end. [" : the citation marker is still arriving
        for piece in pieces[:n]:
            pos = text.index(piece, pos) + len(piece)
            checked = check_library_answer(piece, passages, client_names).sentences
            sc = checked[0] if checked else None
            first_words = first_words if first_words is not None else ms()
            yield Sentence(index, sc.display_text if sc else NOT_IN_MATERIAL, bool(sc and sc.ok), sc.reason if sc else "none")
            index, shown, held = index + 1, "", False
        disp = _display(text[pos:])
        if held or final:
            return
        if extract_figures(disp):
            held = True
        elif len(disp) > len(shown) and disp.startswith(shown):
            first_words = first_words if first_words is not None else ms()
            yield Delta(index, disp[len(shown):])
            shown = disp

    def none_reply() -> bool:
        return re.match(r"NONE\b", text.lstrip()) is not None

    try:
        while True:
            wait = started_wall + (DONE_S if got else min(FIRST_TOKEN_S, DONE_S)) - time.time()
            try:
                tok = q.get(timeout=max(wait, 0)) if wait > 0 else q.get_nowait()
            except queue.Empty:
                yield Done(dataclasses.replace(check_library_answer("", passages), reasons=["timeout"]), first_words, ms())
                return
            if isinstance(tok, Exception):
                raise tok
            if tok is _EOS:
                if none_reply():
                    break
                yield from emit(final=True)
                yield Done(check_library_answer(text, passages, client_names), first_words, ms())
                return
            got, text = True, text + tok
            if not decided:
                s = text.lstrip()
                if s.startswith("N") and len(s) < 6:
                    continue  # might still be NONE
                decided = True
                if none_reply():
                    break
            yield from emit(final=False)
        yield Done(check_library_answer("NONE", passages), None, ms())
    finally:
        stop.set()


def prefill(passages: list[Passage], question_hint: str, ollama: OllamaClient) -> None:
    """Warm Ollama's prompt cache with the system text and passages stream_library_answer will send (spec 7.1
    item 2): the later answer only reads the tokens after `question_hint`'s shared prefix. Blocks ~1 s, so run
    it on a thread. num_predict 1 because Ollama treats 0 as unlimited."""
    for _ in ollama.stream(build_messages(question_hint, passages), MODEL, options={"num_predict": 1}):
        pass
