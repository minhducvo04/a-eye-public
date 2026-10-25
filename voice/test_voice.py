"""Voice adapter tests. asyncio.run only; pytest-asyncio is not installed."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from contracts import validate
from voice.adapter import VoiceAdapter, make_adapter
from voice.cartesia import CARTESIA_VERSION, TTS_URL, CartesiaAdapter
from voice.commands import match_command
from voice.fake import FakeAdapter


def test_fake_say_yields_chunks_then_spoken():
    events = []
    adapter = FakeAdapter(on_event=events.append)

    async def run():
        chunks = []
        async for c in adapter.say("u7", "hello there"):
            assert isinstance(c, (bytes, bytearray))
            chunks.append(c)
        return chunks

    chunks = asyncio.run(run())
    assert len(chunks) >= 3
    assert all(len(c) > 0 for c in chunks)
    spoken = [e for e in events if e.get("type") == "spoken"]
    assert len(spoken) == 1
    validate("voice_spoken", spoken[0])
    assert spoken[0]["utterance_id"] == "u7"


def test_stop_mid_utterance():
    events = []
    adapter = FakeAdapter(on_event=events.append)

    async def run():
        agen = adapter.say("u7", "a" * 80)
        first = await agen.__anext__()
        barge = adapter.stop("u7")
        rest = []
        async for c in agen:
            rest.append(c)
        return first, rest, barge

    first, rest, barge = asyncio.run(run())
    assert isinstance(first, (bytes, bytearray))
    assert rest == []
    validate("voice_barge_in", barge)
    assert barge["utterance_id"] == "u7"
    assert barge["at_ms"] >= 0
    assert not any(e.get("type") == "spoken" for e in events)


def test_transcribe_valid(monkeypatch):
    monkeypatch.delenv("FAKE_TRANSCRIPT", raising=False)
    adapter = FakeAdapter()
    event = asyncio.run(adapter.transcribe(b"\x00\x00"))
    validate("voice_transcript", event)
    assert event["text"] == "which way does it go"
    assert event["final"] is True
    assert event["ts_ms"] >= 0


def test_transcribe_from_env(monkeypatch):
    monkeypatch.setenv("FAKE_TRANSCRIPT", "check again")
    adapter = FakeAdapter()
    event = asyncio.run(adapter.transcribe(b""))
    validate("voice_transcript", event)
    assert event["text"] == "check again"


def test_make_adapter_fake_without_env(monkeypatch):
    monkeypatch.delenv("CARTESIA_API_KEY", raising=False)
    monkeypatch.delenv("VOICE", raising=False)
    adapter = make_adapter()
    assert isinstance(adapter, FakeAdapter)
    assert isinstance(adapter, VoiceAdapter)


def test_make_adapter_cartesia_when_env_set(monkeypatch):
    monkeypatch.setenv("CARTESIA_API_KEY", "sk_test_dummy")
    monkeypatch.setenv("VOICE", "cartesia")
    adapter = make_adapter()
    from voice.cartesia_ws import CartesiaWsAdapter
    assert isinstance(adapter, CartesiaWsAdapter)


COMMAND_TABLE = [
    ("which way does it go", "which_way"),
    ("Which way?", "which_way"),
    ("say again", "say_again"),
    ("Say that again!", "say_again"),
    ("check again", "check_again"),
    ("Check it again.", "check_again"),
    ("next", "next"),
    ("Next step", "next"),
    ("stop", "stop"),
    ("please stop talking", "stop"),
    ("power is off", "power_off"),
    ("it's off", "power_off"),
    ("unplugged", "power_off"),
    ("The PSU is off.", "power_off"),
    ("where is the ram", None),
]


@pytest.mark.parametrize("text,want", COMMAND_TABLE)
def test_match_command(text, want):
    assert match_command(text) == want


def test_cartesia_request_building(monkeypatch):
    monkeypatch.setenv("CARTESIA_API_KEY", "sk_test_dummy")
    monkeypatch.setenv("CARTESIA_VOICE_ID", "voice-id-1")
    captured = {}
    pcm = bytes(3200)

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["header_names"] = sorted({k.lower() for k in request.headers})
        captured["authorization_prefix"] = request.headers.get("authorization", "")[:7]
        captured["cartesia_version"] = request.headers.get("cartesia-version")
        captured["content_type"] = request.headers.get("content-type")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, content=pcm)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = CartesiaAdapter(client=client)

    async def run():
        chunks = []
        async for c in adapter.say("u7", "Switch the PSU off and unplug it."):
            chunks.append(c)
        await client.aclose()
        return chunks

    chunks = asyncio.run(run())
    assert b"".join(chunks) == pcm
    assert captured["url"] == TTS_URL
    assert captured["method"] == "POST"
    for name in ("authorization", "cartesia-version", "content-type"):
        assert name in captured["header_names"]
    assert captured["authorization_prefix"] == "Bearer "
    assert captured["cartesia_version"] == CARTESIA_VERSION
    body = captured["body"]
    assert body["transcript"] == "Switch the PSU off and unplug it."
    assert body["voice"] == "voice-id-1"
    assert body["model_id"] == "sonic-3.6"
    assert body["output_format"] == {
        "container": "raw",
        "encoding": "pcm_s16le",
        "sample_rate": 16000,
    }
    validate("voice_spoken", adapter.last_event)


def test_cartesia_transcribe_not_implemented():
    adapter = CartesiaAdapter()

    async def run():
        await adapter.transcribe(b"\x00")

    with pytest.raises(NotImplementedError, match="STT runs on the GPU host; see TODO"):
        asyncio.run(run())
