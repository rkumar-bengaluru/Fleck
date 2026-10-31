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


def test_warm_and_unload_use_an_endpoint_each_model_supports():
    import json as _json

    import httpx

    from fleck_core.answer import OllamaClient
    calls = []

    def handler(req):
        calls.append((req.url.path, _json.loads(req.content)))
        return httpx.Response(200, json={})
    c = OllamaClient(transport=httpx.MockTransport(handler))
    c.warm("qwen3:4b-q4_K_M")
    c.warm("qwen3-embedding:0.6b")  # an embedding model rejects /api/chat ("does not support chat")
    c.unload("qwen3-embedding:4b")
    c.unload("qwen3:4b-q4_K_M")
    assert calls == [("/api/chat", {"model": "qwen3:4b-q4_K_M", "messages": [], "keep_alive": -1}),
                     ("/api/embed", {"model": "qwen3-embedding:0.6b", "input": [], "keep_alive": -1}),
                     ("/api/embed", {"model": "qwen3-embedding:4b", "input": [], "keep_alive": 0}),
                     ("/api/chat", {"model": "qwen3:4b-q4_K_M", "messages": [], "keep_alive": 0})]
