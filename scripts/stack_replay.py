"""Full-stack replay (Task 22), shared by helper/tests/mac/test_targets.py, test_local_only.py and scripts/soak.py.

Signs in through gsim, pairs a helper with a throwaway keyring file (never the real keychain), replays a WAV through
`fleck-helper replay` into a new meeting on the running stack (`make up`), and scores the stored assists against
fixtures/conversation/reference.json. Latency gates are decision 112's, from scripts/measure_e2e.py; late answers
(decision 128) are reported apart and kept out of the latency numbers."""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlparse

import httpx

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from measure import _matches  # noqa: E402
from measure_e2e import GATES_S, KNOWN_LIMITS, PRICE, pct  # noqa: E402

API = os.environ.get("FLECK_API_URL", "http://localhost:8000")
ORIGIN = {"Origin": "http://localhost:5173"}  # cookie-authenticated writes need the web origin
WAV = ROOT / "fixtures/conversation/conversation.wav"
REF = json.loads((ROOT / "fixtures/conversation/reference.json").read_text())
with wave.open(str(WAV)) as _w:
    assert _w.getframerate() == 16000
    LOOP_MS = _w.getnframes() // 320 * 20  # ReplaySource drops each loop's trailing partial 20 ms frame


def sign_in(email: str = "rupak@northbeam.test") -> httpx.Client:
    c = httpx.Client(base_url=API, timeout=60)
    auth = urlparse(c.get("/api/auth/login").headers["location"])  # -> gsim's consent page
    q = dict(parse_qsl(auth.query))
    back = httpx.get(f"{auth.scheme}://{auth.netloc}/o/oauth2/v2/allow",
                     params={"email": email, "redirect_uri": q["redirect_uri"], "state": q["state"]}).headers["location"]
    c.get(back)  # the api's callback sets the session cookie
    assert c.get("/api/me").status_code == 200, "gsim sign-in failed"
    return c


def helper_env(c: httpx.Client, pythonpath: tuple[Path, ...] = (), **extra: str) -> dict:
    """Environment for `fleck-helper` with its own paired device (token in a throwaway keyring file) and the Claude key
    from .env in the environment (never printed or written anywhere)."""
    env = os.environ | extra | {
        "FLECK_TEST_KEYRING": str(Path(tempfile.mkdtemp(prefix="fleck-keyring-")) / "keyring.json"),
        "PYTHON_KEYRING_BACKEND": "file_keyring.FileKeyring",
        "PYTHONPATH": os.pathsep.join(map(str, (*pythonpath, ROOT / "web/e2e/support")))}
    code = c.post("/api/devices/pairing-code", headers=ORIGIN).raise_for_status().json()["code"]
    subprocess.run(["uv", "run", "--package", "fleck-helper", "fleck-helper", "pair", code], cwd=ROOT, env=env, check=True,
                   capture_output=True)
    if extra.get("FLECK_LIVE_FALLBACK") != "off":  # the key goes to the child's environment only, never to a file
        from fleck_helper.cli import _dotenv_key
        env.setdefault("ANTHROPIC_API_KEY", _dotenv_key(ROOT / ".env"))
    return env


def create_meeting(c: httpx.Client, title: str, event_id: int | None = None) -> int:
    return c.post("/api/meetings", json={"title": title, "event_id": event_id}, headers=ORIGIN).raise_for_status().json()["id"]


def start_replay(env: dict, mid: int, log: Path, loops: int = 1, *args: str) -> subprocess.Popen:
    """`fleck-helper replay` in the background, its stderr (the helper log) written to `log`."""
    log.parent.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen(["uv", "run", "--package", "fleck-helper", "fleck-helper", "replay", str(WAV), "--meeting", str(mid),
                             "--loops", str(loops), *args], cwd=ROOT, env=env, stdout=subprocess.DEVNULL,
                            stderr=log.open("w"), start_new_session=True)


def stop(c: httpx.Client, mid: int) -> None:
    c.post(f"/api/meetings/{mid}/stop", headers=ORIGIN).raise_for_status()


def wait_ready(c: httpx.Client, mid: int, timeout: float = 600) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        m = c.get(f"/api/meetings/{mid}").json()
        if m["meeting"]["status"] in ("ready", "failed"):
            return m
        time.sleep(3)
    raise TimeoutError(f"meeting {mid} not processed in {timeout:.0f} s")


def used_scores(mid: int) -> dict[str, float | None]:
    """assist_id -> used_score (decision 118), which the api does not serve."""
    out = subprocess.run(["docker", "exec", "fleck-postgres-1", "psql", "-U", "fleck", "-d", "fleck", "-Atc",
                          f"select assist_id, coalesce(used_score::text, '') from assists where meeting_id = {int(mid)}"],
                         capture_output=True, text=True, check=True).stdout
    return {a: float(s) if s else None for a, s in (ln.split("|") for ln in out.splitlines() if ln)}


def score(m: dict, loops: int = 1) -> tuple[list[dict], list[dict]]:
    """(one row per scripted question per loop, assists matching no question). A row's `ok` is spec 8's outcome:
    kind and cited document as in reference.json (q_liability per decision 101), no figure shown for q_price."""
    segs = {s["segment_id"]: s for s in m["segments"]}
    client = [t for t in REF["turns"] if t["speaker"] == "client"]
    found, extra = {}, []
    for a in m["assists"]:
        s = segs.get(a["segment_id"])
        k = min(max(s["end_ms"] // LOOP_MS, 0), loops - 1) if s else 0
        hit = None
        if s:
            for kk in {k, max(k - 1, 0)}:  # a turn ending just after a loop boundary
                trig = SimpleNamespace(end_ms=s["end_ms"] - kk * LOOP_MS, question=a["question"])
                turn = min(client, key=lambda t: abs(t["end_ms"] - trig.end_ms))
                if _matches(trig, turn) and turn["question_id"]:
                    hit = (kk, turn["question_id"])
                    break
        if hit:
            found.setdefault(hit, a)  # the first card for a turn; a newer answer for the turn reuses its assist_id
        else:
            extra.append(a)
    rows = []
    for k in range(loops):
        for q in REF["questions"]:
            a = found.get((k, q["id"]))
            row = {"loop": k, "id": q["id"], "triggered": a is not None}
            if a:
                want = KNOWN_LIMITS.get(q["id"], {q["expect"]})
                docs = [c["title"] for c in a["citations"]]
                ok = a["kind"] in want and (a["kind"] in ("general", "not_in_material") or q["doc"] in docs)
                if q["expect"] == "not_in_material" and PRICE.search(a["answer"] or ""):
                    ok = False
                first = a["first_words_ms"] if a["first_words_ms"] is not None else a["full_ms"]
                cls = {"general": "claude", "not_in_material": "not_in_material"}.get(a["kind"], "library")
                row |= {"ok": ok, "kind": a["kind"], "docs": docs, "first": first, "done": a["full_ms"], "cls": cls,
                        "late": a["late"], "from_brief": a["from_brief"], "assist_id": a["assist_id"], "question": a["question"],
                        "answer": a["answer"]}
            rows.append(row)
    return rows, extra


def log_counts(text: str) -> dict:
    last = re.findall(r"question classifier: (\d+) rules fallbacks, late rechecks (\{.*?\}) this run", text)
    return {"slow_classify": len(re.findall(r"question classifier slow:", text)),
            "breaker_opened": len(re.findall(r"embedder breaker opened", text)),
            "rules_fallbacks": int(last[-1][0]) if last else None,
            "rechecks": last[-1][1] if last else None,
            "errors": len(re.findall(r" ERROR ", text))}


def gate_lines(rows: list[dict]) -> tuple[list[str], bool]:
    """Decision 112, pooled over every on-time row given; late answers are listed apart."""
    on_time = [r for r in rows if r.get("triggered") and r.get("done") is not None and not r["late"]]
    lines, ok = [], True
    for cls, gate in GATES_S.items():
        for what in ("first", "done"):
            xs = [r[what] for r in on_time if r["cls"] == cls and r[what] is not None]
            if not xs:
                continue
            (p50, p95), lim = pct(xs), gate.get(what)
            ok &= lim is None or p95 <= lim * 1000
            lines.append(f"pooled {cls} {what}: p50 {p50} ms p95 {p95} ms max {max(xs)} ms over {len(xs)} -> "
                         + ("reported, not gated" if lim is None else f"{'PASS' if p95 <= lim * 1000 else 'FAIL'} (target {lim:.1f} s)"))
    for r in rows:
        if r.get("late"):
            lines.append(f"late answer (not in the latency numbers): loop {r['loop']} {r['id']} first {r['first']} ms done {r['done']} ms")
    return lines, ok


def print_rows(rows: list[dict], extra: list[dict], scores: dict | None = None) -> None:
    for r in rows:
        if not r["triggered"]:
            print(f"loop {r['loop']} {r['id']:<12} NOT TRIGGERED")
            continue
        used = f" used_score {scores.get(r['assist_id']):.3f}" if scores and scores.get(r["assist_id"]) is not None else ""
        print(f"loop {r['loop']} {r['id']:<12} {'PASS' if r['ok'] else 'FAIL'} {r['kind']:<16} first {r['first']!s:>6} done {r['done']!s:>6}"
              f"{' LATE' if r['late'] else ''}{' BRIEF' if r['from_brief'] else ''} {r['docs']}{used} {r['answer'][:70]!r}")
    for a in extra:
        print(f"false trigger: {a['question']!r} kind {a['kind']}")
