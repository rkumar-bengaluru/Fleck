import threading
import time

import numpy as np

from fleck_helper.transcribe import StreamTranscriber

def test_client_final_runs_before_queued_you_jobs():
    order = []
    t = StreamTranscriber(_fn=lambda a, f: (order.append(len(a)), time.sleep(0.1), "")[2])
    blocker = threading.Thread(target=t.transcribe, args=(np.zeros(1), True, "you"))
    blocker.start(); time.sleep(0.02)  # worker busy with a You job
    ths = [threading.Thread(target=t.transcribe, args=(np.zeros(2), True, "you")),
           threading.Thread(target=t.transcribe, args=(np.zeros(3), False, "you")),
           threading.Thread(target=t.transcribe, args=(np.zeros(4), True, "client"))]
    for th in ths:
        th.start(); time.sleep(0.01)
    for th in ths + [blocker]:
        th.join()
    assert order == [1, 4, 2, 3]


def test_no_you_job_starts_while_client_active():
    started = []
    t = StreamTranscriber(_fn=lambda a, f: (started.append(len(a)), "")[1])
    t.set_client_active(True)
    th = threading.Thread(target=t.transcribe, args=(np.zeros(2), True, "you")); th.start()
    time.sleep(0.2)
    assert started == []
    t.transcribe(np.zeros(5), True, "client")  # client work still runs
    assert started == [5]
    t.set_client_active(False); th.join(2)
    assert started == [5, 2]


def test_stale_partial_dropped():
    ran = []
    t = StreamTranscriber(_fn=lambda a, f: (ran.append(len(a)), time.sleep(0.1), "t")[2])
    threading.Thread(target=t.transcribe, args=(np.zeros(1), True, "you")).start(); time.sleep(0.02)
    res = {}
    def go(n, final):
        res[n] = t.transcribe(np.zeros(n), final, "client", "s1")
    ths = [threading.Thread(target=go, args=(n, f)) for n, f in [(2, False), (3, False), (4, True)]]
    for th in ths:
        th.start(); time.sleep(0.01)
    for th in ths:
        th.join()
    assert ran == [1, 4] and res[2] == "" and res[3] == "" and res[4] == "t"
