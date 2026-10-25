# ios/ (the team)

Built by the team with Claude Code, 2026-10-23.

A camera-first SwiftUI iPhone app for A-eye. It opens straight onto the rear camera. A ring of modes sits at the
bottom; swipe to one and tap the centre to start a call. During the call the app sends camera frames, speaks the
agent's replies, listens to you, and draws the server's boxes, arrows, circles, paths, labels and pins over the live
picture.

## Modes

The ring shows the v2 skill packs, Gemini Live, and two placeholders:

| Mode | Pack | Status |
|---|---|---|
| Gemini Live | `general` + Live URL selection | centred at launch; Live with Google on and Private off |
| General | `general` | same Live transport and fallback as Gemini Live |
| PC Build | `pc_build` | works; "Power is off" gate before any step |
| Cooking | `cooking` | works |
| Assembly | `assemble` | works |
| Memory | `memory` | works; retains its existing flow |
| Sink | `home` | works; the app sends "sink" as soon as the helper first listens, like the web |
| Engineering | `general` + `mode=engineering` URL selection | sends "light an LED" once; deterministic USB-only guide |
| Mechanic | none | "coming soon" (tap shakes, no call) |

## Protocol

The app speaks the v2 contract (`contracts/schema.json`, mirror in `contracts/types.ts`) over `/ws2`. `hello` sends `client:"ios"`. Modes without Live use `tts:"browser"`, meaning the phone speaks the text itself and the server sends no audio.
Gemini Live and General use `/ws2?mode=gemini-live&google=1` (preserving access/owner query fields), with `pack:"general"`,
`tts:"server"` and `stt:"server"`. It captures and plays mono 16 kHz PCM16 in memory using the existing audio contracts.
`board_id:"demo-b650"` is added for PC Build only. Frames are JPEG, long side at most 640 px, one per second, and
carry no orientation field. Live frames are capped at 320 px; stale frames older than 1.5 seconds are dropped. Behaviour mirrors `client-web/agent.js` and `draw.js`.

## Gemini Live

**Gemini Live** is selected at launch. Google consent defaults on unless previously turned off.
General uses the same Live bridge. With Google off or Private on, either selection uses the existing pipeline. The notice explains that microphone
audio and downscaled camera video go through the server to Google. Revoking/changing Google consent ends an active
call; changing Private mode ends the current call. A new call uses the new settings. Server `LOCAL_ONLY=1` remains
a server-enforced block. No Google credentials belong on the phone.

Live requests microphone permission only; speech-recognition permission is unnecessary. With mic denied it still accepts
typed input and plays server audio. The existing waveform, caption card, mic/camera buttons and mode ring are reused.
Mic mute removes the capture tap. Camera off sends the existing `camera` message and stops frame transport. Playback
finishes after the last PCM buffer has actually played; server interruptions and disconnects clear queued playback.
A failed audio route falls back to typing; restart the call to restore capture. Other modes keep on-device recognition
and system speech after Live ends. Physical microphone/speaker behavior and real Google sessions still need rehearsal.

## Layout

```
ios/
  AEyeCore/        pure Swift package, no UIKit: runs `swift test` on a Mac
    Sources/AEyeCore/   message encoders and decoders, call state reducer, draw ops, aspect-fill mapping,
                        mode list, barge-in rules, tuning constants
    Tests/AEyeCoreTests/   tests for tuning, messages, draw state, cover transform, call state, modes, voice rules
  AEye/            the app
    AEye.xcodeproj the Xcode project (open this)
    App/           SwiftUI shell: camera, socket, speech in/out, call controller, views (synchronized folder)
    Config/        AEye.xcconfig, Info.plist, Local.xcconfig.example (Local.xcconfig is git-ignored)
    SETUP.md       first run on a phone and the device checks
```

`AEyeCore` holds everything that can be tested without a phone. `App/` only wires Apple frameworks (AVFoundation,
Speech, URLSessionWebSocketTask) to `CallState.reduce` and renders the result. No third-party packages.

## Test

```sh
swift test --package-path ios/AEyeCore
```

`make test` does not run this (the Makefile is not owned by `ios/`).

## Build and run

Needs Xcode 26 or later and a real iPhone on iOS 17 or later. Open `ios/AEye/AEye.xcodeproj`, pick your team under
Signing & Capabilities, choose the iPhone and Run. Details in [`AEye/SETUP.md`](AEye/SETUP.md).

## Point it at `make dev`

On the Mac run:

```sh
VISION_BACKEND=fake make dev
```

The server refuses any client that is not localhost unless it has credentials. On home Wi-Fi start it open
(`A_EYE_OPEN=1 make server`) and leave the app's token field empty, or set `A_EYE_TOKEN` in `.env` and type the
same value in the app's gear sheet (Access token; it is kept in the Keychain and sent as `?token=`).

Then give the app the Mac's address, either in `ios/AEye/Config/Local.xcconfig`
(`AEYE_SERVER_URL = ws:/$()/192.168.1.10:8000/ws2`) or at runtime in the gear sheet (Settings > server). The phone
and the Mac must be on the same Wi-Fi. Accepted forms: `192.168.1.10:8000` (becomes `ws://.../ws2`),
`http(s)://host` (becomes `ws(s)://host/ws2`), or a full `ws://` URL.

## Screenshots and rehearsal

`-aeye-start <mode id>` as a launch argument (Xcode scheme, or `xcrun simctl launch <sim> com.example.aeye
-aeye-start memory`) starts that call at once, like the web's `?mode=`.

## Privacy

- Frames and audio live in memory only. Nothing is written to disk, Photos or Files, and nothing is logged.
- In normal modes, speech recognition runs on the iPhone (`requiresOnDeviceRecognition = true`). If the language has no on-device
  model, or mic or speech access is off, the call falls back to a typed field.
- No API keys in the app. Frames go to our own server; Live explicitly authorizes forwarding microphone audio and downscaled video to Google.

## Limits

- Portrait, iPhone only.
- Modes without Live use the system voice (`tts:"browser"`); Live and General play server PCM audio when Google is enabled outside Private mode.
- Boxes are drawn where the server put them. There is no tracking between frames, so they can lag a moving object.
- Mechanic is a placeholder. Engineering's native mode wiring has core tests; physical-device rehearsal is pending.
- No app icon yet.
- The app is a helper, not a certified or medical tool, and it is not for mains, gas or PSU internals.
- Safari (`client-web/`) stays the committed demo unless native passes the same rehearsal.
