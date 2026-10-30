import dataclasses
import json
import logging
import queue
import re
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass

import httpx

from .numbers import extract_figures
from .prompts import build_messages
from .types import Citation, Passage
from .validate import NOT_IN_MATERIAL, LibraryCheck, check_library_answer, split_sentences

MODEL = "qwen3:4b-q4_K_M"  # decision 72
OPTIONS = {"temperature": 0, "num_predict": 160}  # 3 short sentences fit in ~100 tokens
MAX_SENTENCES = 3  # spec 7.4 rule 1: at most 3 short cited sentences; the stream is closed after the third
log = logging.getLogger("fleck.answer")
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
    """Sentence `index` is complete and checked: its text replaces every Delta shown for that index. Empty text
    (reason "uncited") removes the index. `citations` are the passages it cites, for the failed-sentence link."""
    index: int
    text: str
    ok: bool
    reason: str | None
    citations: list[Citation] = dataclasses.field(default_factory=list)


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
_PARTIAL_MARK = re.compile(r"\s*[\[(【][\d,\s]*$")
_NUMBER_WORD_TAIL = re.compile(r"\s*\b(?:one|two|an?)\s*$", re.I)  # "need one" becomes a figure once "engineer" comes
# replies that mean NONE: handed to the fallback like NONE itself
_NONE_FORMS = ("none of the passages", "the passages do not", "the passages don't", "the passages does not",
               "the provided passages do not", "no passage", "not in the passages")


def _norm_marks(t: str, n_max: int) -> str:
    """[1, 2] -> [1][2]; 【1】 -> [1]; " (n)" at a sentence end -> [n] only when n is a passage number and the
    sentence has no bracket marker ("team leads (3)." with 2 passages stays a count)."""
    t = re.sub(r"【\s*(\d+)\s*】", r"[\1]", t)
    t = re.sub(r"\[(\d+(?:\s*,\s*\d+)+)\]", lambda m: "".join(f"[{n}]" for n in re.findall(r"\d+", m[1])), t)

    def paren(m: re.Match) -> str:
        start = max(t.rfind(e, 0, m.start()) for e in (". ", "! ", "? "))
        ok = 1 <= int(m[1]) <= n_max and not re.search(r"\[\d", t[start + 1 : m.start()])
        return f" [{m[1]}]" if ok else m[0]

    return re.sub(r" \(([1-9])\)(?=\s*(?:[.!?]|$))", paren, t)


def _display(raw: str) -> str:
    return _PARTIAL_MARK.sub("", re.sub(r"\s*\[\d+\]", "", raw)).lstrip()


def _none_state(text: str, final: bool) -> str:
    """'none', 'wait' (could still become a NONE form) or 'no'."""
    s = text.lstrip()
    low = s.lower()
    if re.match(r"NONE\b", s) or any(low.startswith(f) for f in _NONE_FORMS) or (final and re.fullmatch(r"none\W*", low)):
        return "none"
    if not final and ((s.startswith("N") and len(s) < 6) or any(f.startswith(low) for f in _NONE_FORMS)):
        return "wait"
    return "no"


def _pump(ollama, messages: list[dict], q: queue.Queue, stop: threading.Event) -> None:
    tokens = None
    try:
        tokens = ollama.stream(messages, MODEL, options=OPTIONS)
        for t in tokens:
            if stop.is_set():
                return
            q.put(t)
        q.put(_EOS)
    except Exception as e:  # handed to the consumer's thread
        q.put(e)
    finally:
        if tokens is not None:
            tokens.close()  # closes the HTTP stream, so Ollama stops generating


def stream_library_answer(question: str, passages: list[Passage], client_names: list[str], ollama: OllamaClient,
                          started_wall: float, question_end_wall: float) -> Iterator[AssistUpdate]:
    """Stream a cited answer from `passages` through qwen3:4b, checking each sentence (spec 7.4 rules 1-3).

    Event contract for a consumer: show each Delta's text appended to sentence `index` (sentences are joined with
    one space). When Sentence(index, ...) arrives, replace everything shown for that index with Sentence.text:
    "Not in our material" when the sentence failed its check (link it to Sentence.citations), or "" (reason
    "uncited") to remove the index. An uncited sentence's verdict waits for the next sentence: if that one is
    cited, the uncited one inherits its citations and is checked against them (one level only). A sentence gets Deltas only until a figure appears in it; the rest is held, and
    a trailing "one", "two", "a" or "an" waits for the next word. After MAX_SENTENCES cited sentences the stream
    is closed. Done ends the stream; if Done.check is not ok, replace the whole answer with "Not in our material"
    (kind not_in_material; reasons ["none"] for NONE, which hands over to the fallback, ["timeout"],
    ["ollama_error"], or the check's own). Times are wall clock (time.time()); first_words_ms and full_ms are
    measured from question_end_wall, the timeouts (FIRST_TOKEN_S to the first token, DONE_S to the end) from
    started_wall. Stopping iteration (generator close) cancels the Ollama request."""
    q: queue.Queue = queue.Queue()
    stop = threading.Event()
    threading.Thread(target=_pump, args=(ollama, build_messages(question, passages), q, stop), daemon=True).start()
    by_n = {p.n: p for p in passages}
    text, pos, index, shown, held, got, decided, cited_n = "", 0, 0, "", False, False, False, 0
    pending: tuple[int, str] | None = None  # a figure-free, claim-free uncited sentence waiting for citations
    kept: list[str] = []  # raw text (inherited markers added) of every sentence left on screen
    removed: list[str] = []  # "uncited" / "cap" per sentence taken off screen
    first_words: int | None = None

    def ms() -> int:
        return round((time.time() - question_end_wall) * 1000)

    def degraded(reason: str) -> Done:
        return Done(dataclasses.replace(check_library_answer("", passages), reasons=[reason]), first_words, ms())

    def verdict(i: int, raw: str, sc=None) -> Sentence:
        nonlocal cited_n
        sc = sc or check_library_answer(raw, passages, client_names, question).sentences[0]
        kept.append(raw)
        cits = [Citation(**by_n[c].model_dump(include=set(Citation.model_fields))) for c in dict.fromkeys(sc.cited) if c in by_n]
        cited_n += 1  # a shown sentence ("Not in our material" included) counts toward the cap
        return Sentence(i, sc.display_text, sc.ok, sc.reason, cits)

    def remove(i: int, reason: str) -> Sentence:
        removed.append(reason)
        return Sentence(i, "", False, reason)

    def emit(final: bool) -> Iterator[AssistUpdate]:
        nonlocal pos, index, shown, held, first_words, pending
        pieces = split_sentences(text[pos:])
        n = len(pieces) if final else len(pieces) - 1
        if not final and n > 0 and pieces[-1].startswith("["):
            n -= 1  # "... end. [" : the citation marker is still arriving
        for piece in pieces[:n]:
            pos = text.index(piece, pos) + len(piece)
            checked = check_library_answer(piece, passages, client_names, question).sentences
            sc = checked[0] if checked else None
            first_words = first_words if first_words is not None else ms()
            if not sc or not sc.cited:
                if pending:  # one level of carry only: the older uncited sentence goes
                    yield remove(pending[0], "uncited")
                    pending = None
                if sc and sc.reason is None and not sc.figures:
                    pending = (index, piece)  # its Deltas stay up until the next sentence ends
                else:  # a figure, a client name or past work never inherits a citation
                    yield remove(index, "uncited")
            else:
                if pending:  # it inherits this sentence's citations and is checked against them
                    yield verdict(pending[0], f"{pending[1]} " + "".join(f"[{c}]" for c in dict.fromkeys(sc.cited)))
                    pending = None
                    if cited_n >= MAX_SENTENCES:
                        yield remove(index, "cap")  # its Deltas may already be up
                        return
                yield verdict(index, piece, sc)
            index, shown, held = index + 1, "", False
            if cited_n >= MAX_SENTENCES:
                return
        if final and pending:
            yield remove(pending[0], "uncited")
            pending = None
        disp = _display(text[pos:])
        if held or final:
            return
        if extract_figures(disp):
            held = True
            return
        disp = _NUMBER_WORD_TAIL.sub("", disp)
        if len(disp) > len(shown) and disp.startswith(shown):
            first_words = first_words if first_words is not None else ms()
            yield Delta(index, disp[len(shown):])
            shown = disp

    def done() -> Done:
        """Checked on what the screen shows, so the trace's reasons match it, plus one reason per removed sentence."""
        chk = check_library_answer(" ".join(kept), passages, client_names, question)
        return Done(dataclasses.replace(chk, reasons=chk.reasons + removed), first_words, ms())

    try:
        while True:
            wait = started_wall + (DONE_S if got else min(FIRST_TOKEN_S, DONE_S)) - time.time()
            try:
                if wait <= 0:  # past the deadline even if tokens keep coming
                    raise queue.Empty
                tok = q.get(timeout=wait)
            except queue.Empty:
                yield degraded("timeout")
                return
            if isinstance(tok, Exception):
                log.warning("ollama failed during a live answer: %s", tok)
                yield degraded("ollama_error")
                return
            if tok is _EOS:
                if _none_state(text, final=True) == "none":
                    break
                yield from emit(final=True)
                yield done()
                return
            got, text = True, _norm_marks(text + tok, len(passages))
            if not decided:
                state = _none_state(text, final=False)
                if state == "wait":
                    continue
                decided = True
                if state == "none":
                    break
            yield from emit(final=False)
            if cited_n >= MAX_SENTENCES:
                yield done()
                return
        yield Done(check_library_answer("NONE", passages), None, ms())
    finally:
        stop.set()


def prefill(passages: list[Passage], question_hint: str, ollama: OllamaClient) -> None:
    """Warm Ollama's prompt cache with the system text and passages stream_library_answer will send (spec 7.1
    item 2): the later answer only reads the tokens after `question_hint`'s shared prefix. Blocks ~1 s, so run
    it on a thread. num_predict 1 because Ollama treats 0 as unlimited."""
    for _ in ollama.stream(build_messages(question_hint, passages), MODEL, options={"num_predict": 1}):
        pass
