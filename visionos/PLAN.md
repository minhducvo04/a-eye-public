# visionOS app (AEyeVision): plan

Goal: the iPhone app's features on Apple Vision Pro, as a SwiftUI window app. Copy, do not redesign. Same `/ws2`
contract, same `AEyeCore` package, same words and colours (`ios/AEye/App/Views/Style.swift`).

## Step 0: the camera on visionOS

**Finding.** A regular visionOS app gets no frames from the headset's main (world-facing) camera. Main camera
access goes through ARKit `CameraFrameProvider`, which needs the Enterprise API entitlement
`com.apple.developer.arkit.main-camera-access.allow` plus an `Enterprise.license` file from Apple in the bundle.
Apple limits these to in-house apps or custom apps distributed privately through Apple Business Manager.

visionOS 26 changed one thing: before visionOS 26, `CameraFrameProvider` worked only inside an immersive space; from
26 it also works from a window. The entitlement and license requirement did not change.

Sources:
- https://developer.apple.com/documentation/visionos/accessing-the-main-camera (visionOS 26.0+ sample: "To use main
  camera access, you need to apply for the ... entitlement"; "Prior to visionOS 26, CameraFrameProvider is only
  available in an immersive space")
- https://developer.apple.com/documentation/visionos/building-spatial-experiences-for-business-apps-with-enterprise-apis
- WWDC24 session 10139, "Introducing enterprise APIs for visionOS"

`AVCaptureDevice` on visionOS gives only the Persona (virtual front) camera, which is not the user's surroundings.

**Options checked.**

| Option | Works without new entitlements? | Notes |
|---|---|---|
| (a) Vision frames from the phone or web client in the same session | No | `/ws2` keeps one session per socket (`server/ws2.py`, `websocket()` holds `session` as a local). A second client with the same `session_id` gets its own session, and a new `hello` resets it. Sharing would need a server change, which is out of scope. |
| (b) A still photo the user picks | **Yes** | `PhotosPicker` is a system UI; the app only gets the photo the user picks. No camera permission at all. |
| (c) Real main camera | Only with Apple's enterprise entitlement and license | Needs the team's yes and Apple paperwork. Not requested. |

**Chosen path: (b).** The headset does everything else the iPhone does (voice in and out, captions, mode ring, presence,
power gate, market sheet, settings). The picture A-eye looks at is a photo picked with `PhotosPicker`. While a call
runs the app sends that photo as a `frame` once a second (same loop, size and quality as the iPhone, `Tuning`), and
draws the server's overlay on it with the same aspect-fill mapping (`CoverTransform`). The photo lives in memory
only; nothing is written to disk.

If the team later gets the enterprise entitlement, `PhotoSource.latestJPEG()` is the one seam to replace with a
`CameraFrameProvider` source.

## What is copied, what differs

| iPhone | Vision Pro |
|---|---|
| Rear camera preview behind everything | The picked photo behind everything (aspect-fill), or a notice with "Pick a photo" |
| Camera permission notice | Notice explaining the headset camera is closed to apps, with "Pick a photo" |
| On-device speech in (`SFSpeechRecognizer`), typed fallback | Same code |
| System voice out (`tts:"browser"`) | Same by default. Settings also offers the server voices from `GET /api/voices` (Kokoro, Cartesia when keyed); picking one sends `hello.tts:"server"` and `hello.voice`, and the app plays the `audio` PCM. Asked for in the build brief; the iPhone app does not have it yet. |
| Haptics | None (visionOS has no haptics) |
| Portrait phone | One window, landscape-ish default size, glass background |

## Steps

1. Xcode project `visionos/AEyeVision`, builds for the visionOS simulator with `CODE_SIGNING_ALLOWED=NO`.
   Signing team only in git-ignored `Config/Local.xcconfig`. -> verify: `xcodebuild ... build` exit 0.
2. Socket, call state, speech in and out, barge-in (copied from `ios/AEye/App/Call`). -> verify: server log shows
   the headset's `hello` and a turn.
3. Views copied from iOS. -> verify: simulator screenshots of the ring and the call screen.
4. Overlay on the picked photo. -> verify: screenshot with a `draw` from the fake vision.

## Shared package

The only change outside `visionos/`: `.visionOS(.v1)` in `ios/AEyeCore/Package.swift` platforms. `AEyeCore` needed no
code changes. The two things it lacks for server voice (a `hello` with `tts:"server"` and `voice`, and decoding
`audio`) live in `visionos/AEyeVision/App/Call/ServerVoice.swift`.
