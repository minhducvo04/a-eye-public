# voice

In-process TTS/STT adapter for the orchestrator. Event shapes: `voice_say`,
`voice_transcript`, `voice_barge_in`, `voice_spoken` in `contracts/schema.json`.

```python
from voice.adapter import make_adapter
from voice.commands import match_command

adapter = make_adapter()  # FakeAdapter unless CARTESIA_API_KEY is set and VOICE=cartesia
async for pcm in adapter.say("u7", "Switch the PSU off and unplug it."):
    ...
event = adapter.stop("u7")           # voice_barge_in, at_ms since this utterance started
transcript = await adapter.transcribe(pcm16)
cmd = match_command(transcript["text"])  # which_way | say_again | check_again | next | stop | power_off | None
```

## Fake vs Cartesia

| Env | Adapter |
|---|---|
| default | `FakeAdapter`: silence PCM sized to the text; transcript from `FAKE_TRANSCRIPT` (default `which way does it go`) |
| `CARTESIA_API_KEY` set **and** `VOICE=cartesia` | `CartesiaAdapter`: HTTP `POST /tts/bytes`, raw PCM s16le 16 kHz |

Cartesia receives only the AI's own sentences. `CARTESIA_VOICE_ID` selects the voice. The key is read from env and never logged.

`CartesiaAdapter.transcribe` raises `NotImplementedError` — STT runs on the GPU host.

## Commands

`match_command(text)` is case and punctuation insensitive. `power_off` matches "power is off", "it's off", "unplugged".
