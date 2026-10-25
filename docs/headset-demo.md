# Vision Pro browser demo — no app install

1. On Vision Pro, open `https://<the A-eye hostname>/agent.html?device=headset` in Safari. Keep the flag: Safari can identify itself as a Mac.
2. Sign in with the same account used by your remembered phone.
3. Leave the phone's A-eye camera page open on its stand, pointed at the object. If it was reloaded, tap **Start** once; keep **Always use this phone as my camera** enabled.
4. On Vision Pro, choose **General** to start a call, then tap **Enable sound** to allow Safari audio playback. The waiting panel becomes **Phone camera connected** when the phone joins. If needed, tap **Pair phone · show QR** and scan with the phone.
5. Ask a question through the phone mic. Hear the answer on Vision Pro and see its drawings. The current server does not relay the phone picture to Safari, so drawings appear on a dark canvas. End the call when finished.

The headset never opens its own camera. Its mic is available from the mic button only while no phone is paired. Keep both pages open and reachable on the same A-eye server. Chromium emulation has been checked; physical Vision Pro Safari still needs a device run.
