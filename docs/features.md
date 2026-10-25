# A-eye features (living list)

Every user-facing feature, what state it is in, how it was checked, which clients have it, and where it sits in the
UI. Update this file in the same commit as the feature. Words: **built** = code is on `main`; **verified** = a real
run named in the row; **not verified** = only tests or not run at all. Web = `client-web/agent.html` unless
named. iPhone = `ios/AEye`. Vision Pro = `visionos/AEyeVision` (photo instead of a live camera, see its RESULT.md).

Last updated 2026-10-24 (UI polish, `pkg/ui-polish`).

## Start and modes

| Feature | Status | Verified how | Web | iPhone | Vision Pro | Where in the UI |
|---|---|---|---|---|---|---|
| First run: what A-eye is, privacy line ("Video and pictures are not saved unless you turn on Rewind or the memory aid.") | built | Chromium screenshots 390, 1280, 1536, 1920 (`docs/ui-polish/`) | yes | no | no | Start screen, above the modes |
| Mode ring (General, PC Build, Cooking, Assembly, Memory, 3 coming soon) | built | browser smoke; screenshots | yes | yes (no Memory yet) | yes (no Memory yet) | Start screen, bottom |
| All modes in one row on desktop, ring on phones | built | Chromium and Brave, Playwright 1280 and 390 (8/8 modes in view at 1280) | yes | n/a | n/a | Start screen, bottom |
| Ring by wheel, drag and arrow keys | built | Playwright wheel and keys in Chromium and Brave; real trackpad not tried | yes | swipe | gaze/pinch | Start screen |
| Memory centred at launch (web); `?mode=<id>` picks another | built | Playwright Chromium and Brave 390 and 1280 (177d209) | yes | no (PC Build) | no | Start screen |

## Call screen

| Feature | Status | Verified how | Web | iPhone | Vision Pro | Where in the UI |
|---|---|---|---|---|---|---|
| Live camera, 1 fps frames to our own vision host | built | browser smoke (frames sent) | yes | yes | photo only | Full screen |
| Drawings on the picture (box, arrow, circle, path, label, pin) with tracking | built | browser smoke; node tests | yes | yes | yes (on the photo) | Over the camera |
| General answer points at one named object, with a box and arrow that expire after 2.5 s | implemented in `pkg/pointer` working tree; not on main | live local Qwen: 3/10 synthetic fixtures hit with cached caption, 0 wrong; uncached 0/10, all abstained; [report](results/pointer.md) | existing draw ops; tests pass | existing draw ops; Swift tests pass; device not checked | not checked | Over the camera |
| Voice in (browser speech or server STT), voice out (Cartesia or Kokoro), barge-in | built | node tests; real mic not checked in this pass | yes | yes | typed in simulator only | Mic button; listening strip |
| Tap to start turns the mic on; Mic on shows filled blue with "Hearing you" | built | Playwright Chromium 390 and 1280: on after start, off after the first press (b4e1855) | yes | n/a | n/a | Call bar mic |
| Hold to talk (button and Space) | built | browser smoke | yes | no | no | Call bar, hand icon (hidden in Memory mode) |
| Typed message | built | browser smoke | yes | when voice is off | yes | Above the call bar |
| Caption card (helper, shopper) and wait card with percent | built | screenshots; node tests | yes | yes | yes | Lower middle |
| Presence dot and top pill (mode, timer) | built | screenshots | yes | yes | yes | Top |
| One badge style: Private, Rewind on (+ Recap), Expert | built | screenshots 390 and 1280; Expert badge not seen live | yes | no | no | Under the top pill |
| Status line only for problems (timings with `?dev=1`) | built | screenshots | yes | n/a | n/a | Under the badges |
| PC Build power gate ("Power is off") | built | server tests; screenshots in `docs/ui-parity/` | yes | yes | yes | Above the typed bar |
| Shopper offers sheet (Fits / Does not fit / Unverified) | built | server tests | yes | yes | yes | Bottom sheet |
| Use my phone as camera and mic (QR pairing) | built | browser smoke (pair code shown) | yes | n/a | in code, not verified | Call bar phone icon (wide screens); first item in Settings |
| Upload a photo instead of the camera | built | not re-run in this pass | yes | no | photo picker | Settings, Photo |
| Camera off / permission notice | built | screenshot with camera denied | yes | yes | yes | Centre card or caption error |
| Projector scale (1.12x at 1200x760+, 1.4x at 1700x950+) | built | screenshots 1280x800, 1536x864, 1920x1080 | yes | n/a | n/a | Whole call and start screens |

## Memory aid

| Feature | Status | Verified how | Web | iPhone | Vision Pro | Where in the UI |
|---|---|---|---|---|---|---|
| Memory mode (calm look: larger words, fewer buttons, slower motion) | built | screenshots 390, 1280, 1920 | yes | no | no | Start screen, Memory |
| "Where did I leave my keys?" split answer: then frame boxed, live view with arrow | built | demo rehearsal `docs/results/demo-rehearsal.md` (stand-in TwelveLabs); screenshots with an injected hit | yes | no | no | Bottom sheet "Rewind" |
| Remembered count with Forget that / Forget today | built | node tests; screenshots | yes | no | no | Under the badges; tap the count |
| Caregiver page: two consents, things and their homes, rooms (bedroom and bathroom refused), keep days, stop switch | built | server tests (`docs/results/memory-aid.md`); screenshots | yes | no | no | `caregiver.html`; Settings, Memory aid |
| Local memory across calls (notes, tagged crops without people, local search) | built | server tests, see `docs/results/memory-aid.md` | server | server | server | No UI of its own; answers in the call |

## Privacy and Rewind

| Feature | Status | Verified how | Web | iPhone | Vision Pro | Where in the UI |
|---|---|---|---|---|---|---|
| Private mode (local brain, Kokoro, our STT, Rewind locked off) | built | server tests | yes | no | no | Settings, Privacy (first group after Phone camera); Private badge |
| Rewind: clips to TwelveLabs, ask about earlier moments, recap at the end | built | demo rehearsal with stand-in; real upload not run here | yes | no | no | Call bar Rewind button (not in Memory mode); Settings, Rewind; Rewind on badge |
| Spoken "forget that" deletes this call's clips | built | server tests | yes | yes (voice) | yes (voice) | Voice |

## Settings (web)

Simple, in this order: Phone camera, Privacy, Rewind, Voice, Photo, Memory aid (caregiver link). Advanced (one tap):
Devices (mic, camera, speaker, level), Microphone (sensitivity, recognition, room noise), Connection and server,
Transcript. The sheet scrolls with wheel or trackpad from anywhere while it is open, with keys after it opens, and with touch.

## Not verified in this pass

- Real trackpad and touch scrolling by hand, in Brave and Safari.
- Real phone pairing end to end (the code and QR appear; a phone did not join).
- Expert badge in a live expert turn.
- iPhone and Vision Pro do not have Memory mode, the badges or the first-run screen yet.
