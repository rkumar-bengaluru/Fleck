"""Menu-bar status: the Claude key warning, degraded question detection, and the OLLAMA_NUM_PARALLEL check."""
import logging

from fleck_helper import menubar
from fleck_helper.menubar import parallel_slots, status_lines

PS = """\
78379 ollama serve
86298 /opt/homebrew/Cellar/ollama/0.40.1/libexec/lib/ollama/llama-server --model /x --port 49861 -c 4096 -np 1 --flash-attn auto
47210 /opt/homebrew/Cellar/ollama/0.40.1/libexec/lib/ollama/llama-server --model /y --port 63712 -c 1024 -np 4 --embedding
"""


def test_status_lines():
    assert status_lines("claude", has_key=True, degraded=False) == []
    assert status_lines("off", has_key=False, degraded=False) == []
    assert status_lines("claude", has_key=False, degraded=False) == ["No Claude key: general answers are off"]
    assert status_lines("claude", has_key=True, degraded=True) == ["Question detection degraded"]


def test_parallel_slots_read_from_the_runners():
    assert parallel_slots(PS) == [1, 4]
    assert parallel_slots("78379 ollama serve\n") == []


def test_parallel_warning_logged_not_changed(monkeypatch, caplog):
    monkeypatch.setattr(menubar, "_ps", lambda: PS)
    with caplog.at_level(logging.WARNING):
        menubar.check_parallel()
    assert "OLLAMA_NUM_PARALLEL" in caplog.text and "[1, 4]" in caplog.text
    caplog.clear()
    monkeypatch.setattr(menubar, "_ps", lambda: PS.replace("-np 4", "-np 1"))
    with caplog.at_level(logging.WARNING):
        menubar.check_parallel()
    assert caplog.text == ""
