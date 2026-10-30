import json

import httpx

from fleck_core.answer import OllamaClient


def test_stream_parses_ndjson():
    seen = {}

    def handler(req):
        seen.update(json.loads(req.content))
        lines = [{"message": {"content": "Hel"}}, {"message": {"content": "lo"}}, {"message": {"content": ""}, "done": True}]
        return httpx.Response(200, content="\n".join(map(json.dumps, lines)).encode())

    c = OllamaClient(transport=httpx.MockTransport(handler))
    assert list(c.stream([{"role": "user", "content": "hi"}], "m")) == ["Hel", "lo"]
    assert seen["think"] is False and seen["keep_alive"] == -1 and seen["stream"] is True
