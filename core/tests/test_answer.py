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


def test_stream_raises_on_midstream_error():
    body = json.dumps({"message": {"content": "a"}}) + "\n" + json.dumps({"error": "boom"})
    c = OllamaClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body.encode())))
    got = []
    try:
        for t in c.stream([], "m"):
            got.append(t)
    except RuntimeError as e:
        assert "boom" in str(e) and got == ["a"]
    else:
        raise AssertionError("no error raised")
