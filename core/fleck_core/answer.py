import json
from collections.abc import Iterator

import httpx


class OllamaClient:
    def __init__(self, url: str = "http://localhost:11434", transport: httpx.BaseTransport | None = None):
        self._c = httpx.Client(base_url=url, timeout=120, transport=transport)

    def stream(self, messages: list[dict], model: str, keep_alive: int = -1, think: bool = False) -> Iterator[str]:
        body = {"model": model, "messages": messages, "stream": True, "think": think, "keep_alive": keep_alive}
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
