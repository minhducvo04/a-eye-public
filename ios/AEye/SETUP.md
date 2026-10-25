# Running the A-eye iPhone app

The Xcode project is already set up: `ios/AEye/AEye.xcodeproj`. You need Xcode 26 or later, a real iPhone on iOS 17
or later, and a cable.

## What the project already has

- Target `AEye`, iPhone only, portrait only, iOS 17.0 minimum, bundle id `com.example.aeye`.
- `App/` is a synchronized folder, so new files you add there are built automatically.
- The local package `../AEyeCore` is linked to the target.
- Build settings: Swift 5 language mode, Default Actor Isolation = `nonisolated`, Approachable Concurrency = No.
- Info keys in `Config/Info.plist`: camera, microphone, speech recognition and local network usage strings,
  `NSAllowsLocalNetworking`, and `AEYE_SERVER_URL = $(AEYE_SERVER_URL)`.
- `Config/AEye.xcconfig` is the target's base config. It sets `AEYE_SERVER_URL` to empty and then includes
  `Local.xcconfig` if it exists.

## 0. Check the core package first

```sh
swift test --package-path ios/AEyeCore
```

If it says it cannot find XCTest, run `sudo xcode-select -s /Applications/Xcode.app` and try again.

## 1. Open and sign

1. Open `ios/AEye/AEye.xcodeproj`.
2. AEye target > Signing & Capabilities: pick your team. If Xcode says the bundle id is taken, change it to
   something like `com.<you>.aeye`.

## 2. First run on the iPhone

1. Plug the phone in, then open Xcode so it sees the phone.
2. **Developer Mode**: on the iPhone, Settings > Privacy & Security > Developer Mode > On. The phone restarts;
   confirm "Turn On" after the restart.
3. Pick the iPhone as the run destination and Run.
4. With a free personal team the app will not open the first time ("Untrusted Developer"). On the iPhone:
   Settings > General > VPN & Device Management > your Apple ID > Trust. Launch it again.

## 3. Server address

Either:

- in the app, tap the gear and type your Mac's address, for example `192.168.1.10:8000`; or
- copy `Config/Local.xcconfig.example` to `Config/Local.xcconfig`, set your Mac's LAN IP, and rebuild. Keep the
  `$()` trick: `AEYE_SERVER_URL = ws:/$()/192.168.1.10:8000/ws2`. `Local.xcconfig` is git-ignored; never commit it.

A value typed in the app wins over the xcconfig once it has been saved.

## If the first build fails

The sources were written without a compiler. Likely spots:

- If the compiler rejects `nonisolated final class` on `CaptureRig`, `SocketClient`, `SynthesizerDelegateProxy` or
  `RecognitionPipeline`, delete the `nonisolated` keyword from those declarations; with the build settings above
  they are already non-isolated. Do the same for `nonisolated` on `SpeechIn.authorize` and
  `SpeechIn.requestSpeechAuthorization` only if the compiler rejects them.
- If Xcode does not show `AEyeCore` under Package Dependencies: File > Packages > Reset Package Caches.

## 4. Device check: camera and mode ring

- The app opens straight onto the live **rear camera**, upright in portrait (not rotated or stretched).
- **PC Build is centred** in the ring at launch, with "PC Build" and "tap to start" above it.
- Swiping the ring snaps one item to the centre each time, with a haptic tick. The centre item has the white ring
  and is the largest; neighbours shrink and fade.
- Mechanic says "coming soon". Sink and Engineering start their deterministic guides.
- Settings > AEye > Camera off, relaunch: the "Camera access is off" notice with an Open Settings button appears,
  not a black screen.

## 5. Device check: call loop

On the Mac: `VISION_BACKEND=fake make dev`. Make sure the app points at that Mac's IP and port.

- At the first connection iOS shows the **Local Network** prompt. Tap **Allow**. If you tapped Don't Allow, turn it
  on in Settings > AEye > Local Network and relaunch.
- Start **General** (swipe to General, tap the centre). Within about 3 seconds there is no error, and the
  server's spoken greeting or answer appears in the caption card and is spoken aloud.
- Stop `make dev`, then start it again: the top shows "Reconnecting...", then returns to normal without
  leaving the call.
- End call (red button) returns to the camera screen with the same mode still selected, and no black flash.
- Gear > clear the server field: the camera screen shows the "Set server" chip, and tapping the centre of the
  ring opens Settings instead of starting a call.

## 6. Device check: draw overlay and presence

`VISION_BACKEND=fake make dev`, General mode, point at anything. When the server sends a `draw`, a green/blue box
animates in over the camera and lines up with the same region the web page would draw (open
`http://<mac>:8000/agent.html` on the Mac to compare). The presence dot changes as the agent thinks and speaks.

## 7. Device check: listening, typed fallback, barge-in

General mode: say "what am I looking at" → it appears as interim text, then a reply is spoken. While it's speaking
say "wait, stop" → speech stops within ~1 s. Let a long reply play in silence → it is **not** cut off by its own
voice. Turn off mic access in Settings > AEye → the call shows the text field and typing works. Airplane mode +
Wi-Fi only (no internet) still transcribes (proves on-device).

## 8. Device check: PC Build power-off gate and market sheet

PC Build: before tapping, ask anything → the server answers "Switch the power off and unplug it first…" and the
button is visible. Tap → "Thanks. What are we working on?" and the button hides. Restart `make dev` mid-call → the
button comes back. Ask "find me cheaper RAM that fits" → the shopper speaks and the sheet shows fixture offers with
chips.

## Known limits

- The microphone button mutes and unmutes listening. It is inactive (struck-through mic) when the call uses the
  typed field instead, because mic or speech permission is off or this iPhone has no on-device recognition.
- Speech recognition is on-device only. If the language has no on-device model the call uses typed input.

## Full rehearsal

It passes when all of these are true:

- cold launch reaches the camera in under 2 s;
- a PC Build call runs end to end: power-off, ask about a part, see a box, ask about price, see the sheet;
- a General call works;
- one Wi-Fi drop recovers;
- nothing is saved to Photos or Files.
