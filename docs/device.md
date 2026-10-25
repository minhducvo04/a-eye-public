# A-eye device: a home camera (or a clip-on) that talks to your own server

A small computer with a camera, a microphone, a speaker, a button and a light. It is a full A-eye call device,
like the phone page, but with no screen. Off keeps the camera and microphone closed. Explicit Armed mode
checks motion locally; Event briefly sends person-filtered stills to the home server for a text-only memory note.
Session is the existing user-started camera and microphone task call.

Status: built and tested on a Mac with a fake camera, a fake mic and a fake speaker against a real local server
(`docs/results/device.md`). Not yet run on a Raspberry Pi, a real button, a real LED or a real speakerphone.

## Everyday use

1. Press the button. The light comes on: the device is looking and listening.
2. Ask: "Where are my keys?" The answer comes from the speaker.
3. Press the button again to stop. If you forget, it turns itself off after 90 seconds with nothing new to see
   or hear, and never stays on longer than 15 minutes.

## Setup, once (about 30 minutes, nine steps)

You need: a Raspberry Pi 5 (4 GB is enough) with its 27 W power supply and the active cooler, a Camera Module 3,
a USB speakerphone (the Pi 5 has no headphone jack), a microSD card, a push button, an LED with a 330 ohm
resistor, and the Mac that runs the A-eye server.

1. **Put the system on the card.** On the Mac, open Raspberry Pi Imager, choose Raspberry Pi OS Lite (64-bit),
   and in the settings set the name `aeye`, user `pi`, your home Wi-Fi, and turn on SSH.
2. **Connect the parts.** Camera ribbon into the CAM port, speakerphone into a blue USB port, button and LED as in
   [Wiring](#wiring-button-and-led). Plug in the power.
3. **Log in from the Mac:** `ssh <PC_USER>@<PC_NAME>`.
4. **Install what it needs** (one line):
   `sudo apt install -y python3-venv python3-picamera2 python3-opencv libportaudio2`
5. **Copy A-eye to the Pi** from the Mac, in the repo folder:
   `rsync -a --exclude .venv --exclude data ./ <PC_USER>@<PC_NAME>:a-eye/`
   Then on the Pi: `cd a-eye && python3 -m venv --system-site-packages .venv && .venv/bin/pip install -r device/requirements.txt sounddevice`
6. **Tell it where the server is.** `sudo mkdir -p /etc/a-eye && sudo cp device/device.example.toml /etc/a-eye/device.toml`,
   then `sudo nano /etc/a-eye/device.toml`: set `url` to `ws://<the Mac's address>:8000/ws2`, `button = "gpio"`,
   `kind = "gpio"` under `[indicator]`, and for explicit Armed startup on a home camera `motion = true`.
7. **Add the password, if the server has one.** `sudo nano /etc/a-eye/device.env` with the line
   `A_EYE_TOKEN=<the server's token>`, then `sudo chmod 600 /etc/a-eye/device.env`. Skip this on an open home server.
8. **Try it once by hand:** `.venv/bin/python -m device --config /etc/a-eye/device.toml`. Press the button, ask a
   question, hear the answer. Stop with Ctrl-C.
9. **Start it on every boot:** `sudo cp device/aeye-device.service /etc/systemd/system/ && sudo systemctl enable --now aeye-device`.
   The log: `journalctl -u aeye-device -f`.

## Wiring: button and LED

Pin numbers are the physical header pins; GPIO numbers are what the config uses.

| Part | Connect | To |
|---|---|---|
| Push button | one leg to GPIO17 (pin 11) | other leg to GND (pin 9) |
| LED | GPIO27 (pin 13) through a 330 ohm resistor to the LED's long leg | LED's short leg to GND (pin 14) |

The button uses the Pi's internal pull-up, so it needs no resistor. The LED is a software state indicator, not a hardware camera cutoff.

## Network: LAN, Tailscale, or a phone hotspot

| Where | Server URL in `device.toml` | Notes |
|---|---|---|
| Home, same Wi-Fi | `ws://<mac-lan-ip>:8000/ws2` | Simplest. Find the Mac's IP in System Settings, Wi-Fi, Details. |
| Anywhere, Tailscale | `wss://<mac-name>.<tailnet>.ts.net/ws2` (Mac runs `tailscale serve --bg 8000`), or `ws://<mac-tailscale-ip>:8000/ws2` | On the Pi: `curl -fsSL https://tailscale.com/install.sh \| sh`, then `sudo tailscale up` and sign in to the same tailnet. Traffic stays on WireGuard between your devices. |
| Venue Wi-Fi blocks devices from reaching each other | the Mac's address on the hotspot | Put the Mac and the Pi on a phone hotspot (add it on the Pi with `sudo nmcli dev wifi connect <name> password <password>`), or use Tailscale, which usually still works. |
| Cloudflare Access URL | `wss://<your-access-host>/ws2` | Create an Access service token and a Service Auth policy for it; put `CF_ACCESS_CLIENT_ID` and `CF_ACCESS_CLIENT_SECRET` in `device.env`. The server accepts the Access JWT that Cloudflare adds. Not yet tried with a service token. |

## Power and heat (Board hardware notes, estimates unless marked)

- Pi 5 as a 1 fps camera client: about 4 to 5 W, so about 5 to 7 hours on a 10,000 mAh power bank (estimate). It
  wants 5 V at 5 A; a 3 A bank limits the USB current for the speakerphone.
- Published Pi 5 figures: about 3 W idle, about 8.8 W on all cores. Without a fan it throttles within about 90 s of
  sustained heavy work. Camera duty is light, but use the active cooler anyway.
- A Pi 5 with a bank weighs about 250 to 300 g: fine on a table, too heavy for a necklace. A necklace would be a
  Pi Zero 2 W (about 1 to 1.5 W, 15 to 20 hours on 10,000 mAh, estimate) as a camera only.
- Lead form: a plugged-in home camera by the door, aimed at the key table, with the motion trigger. Not a hat:
  a hat camera points at faces.
- No vision model on the Pi: a small vision model takes 8 to 29 s per frame there. The Pi only decides when to
  capture; the Mac and the GPU PC do the seeing and talking.

## Technical details

Code: `device/` (Python 3.11+, `websockets` and Pillow; optional `sounddevice`, OpenCV below 5, `gpiozero`,
`picamera2`). Run with `python -m device --config <file>`. Settings: `device/device.example.toml`. Credentials come
only from the environment (`A_EYE_TOKEN`, or `CF_ACCESS_CLIENT_ID` and `CF_ACCESS_CLIENT_SECRET`), never from the
config file, and are never logged.

**On-device rules** (`device/trigger.py`, unit tested in `device/test_trigger.py`):

- Off by default: camera and microphone closed, no application socket.
- `motion = true` explicitly starts Armed once at boot. It does not silently re-arm after Off or the cap.
- Terminal commands: `arm`, `remember this`, `session`; Enter stops Armed/Event/Session, or starts Session
  from Off. GPIO17 is the Session/Off button; optional GPIO22 to ground is the Remember button.
- Armed: requests 64×48 camera output at 1 fps, compares grey pixels in `trigger.zone` (normalized rectangle).
  It retains a five-second grey RAM pre-roll and one comparison thumbnail, no microphone or network connection.
  USB camera drivers may ignore resolution/fps requests; actual sensor duty must be measured.
- `trigger.armed_cap_s` defaults to 43200 (12 h), including Event capture and upload; expiry cancels the Event,
  clears its buffers and closes the camera. No automatic re-arm at expiry.
- Event: five further seconds at up to 640 px; inspect every candidate with the device person detector and send
  at most three safe JPEG stills. Unknown/unavailable detector or a detector error fails closed. Tiny pre-roll
  is discarded before transmission because HOG cannot reliably inspect 64×48 frames. No cloud fallback.
- Memory must already have both consents and Remembering enabled for this device's authenticated owner.
  `/api/memory/event` first checks every still with the server's local person detector. A person, unknown result
  or detector failure refuses the whole Event before inference. It then asks the configured home `OLLAMA_URL`
  model “Worth noting?” once, then writes zero or one
  note (12 words, max 200 characters). Notes are searchable by the existing Find path. No crops are saved.
- Event buffers clear on success, refusal, cancellation and error; return to Armed after success, Off on stop.
  An application disconnect cannot recall a request already received by the server; the server clears its own
  frames after the bounded inference finishes. Python reference release is not a secure RAM overwrite.
- Session keeps the 90 s idle / 15 min cap and existing audio behaviour. Remember during Session closes that
  session before opening Event. No always-listening wake phrase: use the Remember button or terminal command.
- Event and Session both require a successful on-device person check before sending images. Setting
  `drop_person_frames = false` disables the detector and therefore **blocks all frames in both states**;
  it never permits unfiltered images or a server-only fallback. Session audio remains available.
- HOG detects some full people, not every face/person. Synthetic person-drop tests prove transport gating,
  not reliable recognition of all people. No real-world “people can never leave” claim is justified yet.
- On turn-on it takes a burst of 3 frames and sends the sharpest (edge variance), against motion blur.

**Session relay** (`device/client.py`) over the unchanged `/ws2` contract:

- `hello` with `pack: general`, `tts: server`, `stt: server`, `camera: true` and `private: true`. With
  `private = false` it sends `hello.cloud` with the three switches from `[privacy]` (Claude, Cartesia, TwelveLabs).
- Frames: upright JPEG, long side 640, quality 70, about 1 fps, increasing `seq`.
- Mic: 16 kHz mono PCM16 in 100 ms chunks. An energy voice detector starts an utterance (with 300 ms pre-roll),
  streams `audio_in` while you talk and sends `final: true` after 0.7 s of quiet (30 s cap per utterance).
- Reply: `audio` PCM16 chunks go to the speaker. By default the mic is muted while the speaker plays, because a
  speakerphone hears itself. With `barge_in = true` (a headset), speaking stops playback locally and sends
  `barge_in` with the utterance ID and the elapsed milliseconds.
- One socket per on-period; the socket closes normally (1000) at turn-off. Connection drops retry with backoff
  (1 to 30 s) while on. A 4401 close (wrong token) stops retries and turns the device off.

**Testing without hardware** (on a Mac; never opens the Mac's camera or mic):

```sh
.venv/bin/python -m pytest device -q             # trigger rules, auto-off, VAD, config, client vs in-process mock server
.venv/bin/python -m device.fake_turn             # full turn against a real local server on free ports
```

`fake_turn` starts the vision service (fake backend) and the server from the checkout on free ports with auth off
and a temporary memory folder, makes a synthetic key-table clip (no people) and a `say` WAV of "Where are my
keys?", presses the button once, and checks: listening with Private on, frames sent, utterance sent, turn
received, reply text, reply audio, and auto-off by idle. Speech recognition and Kokoro run on the GPU box; by
default it points the server's Ollama at a closed port so it never loads models on the box (`--box-ollama` to
use them). `--mock` uses `A_EYE_MOCK=1` instead (no box at all).

## Private capture state signals and measurement

| State | GPIO27 PWM LED | Terminal strip |
|---|---|---|
| Off | dark | `[Off]` |
| Armed | slow four-second pulse, 3–15% duty (never dark) | `[Armed]` |
| Event | solid 60% duty | `[Event]` |
| Session | solid full brightness | `[Session]` |

This single-color board distinguishes Session by brightness; an RGB LED is not implemented. GPIO PWM patterns
have fake-driver tests only. Swap/core dumps are outside Python's RAM-only guarantee: disable them on deployment
before claiming image bytes never reach any physical disk. No application capture code creates image files.

Run `PYTHONPATH=. python scripts/capture_power.py --fake` for 20 synthetic motion trials on Mac. Without `--fake`,
perform 20 placements and enter whether each Event appeared plus an optional USB meter watt reading. JSON lines
include state, event count, misses, battery percent (`pmset`) and Pi temperature (`vcgencmd`). Missing sensors
print `SKIP`. Redirect stdout to keep a measurement log. Fake trials do not measure power or real-world misses.
