# A-eye wake sensor: first wiring and upload

Use USB power only. Unplug the UNO before moving wires. This sensor wakes an already-open A-eye call; it does not turn on a camera, pair a phone, or bypass browser microphone permission.

## 1. Identify the parts

- UNO R3, its USB cable and the Mac USB-C adapter.
- HC-SR501 PIR: read the actual board's **VCC / OUT / GND** markings. Do not infer pin order from an online photo or from which way its white dome faces.
- Four-leg RGB LED: find the common leg from its packaging/pinout (often the longest, but that alone is not proof). The other three legs are red, green and blue. Pin order varies.
- One 220 Ω resistor (red-red-brown, often gold tolerance), breadboard and jumpers.

Breadboard holes in each short group of five are connected inside. Groups on opposite sides of the center trench are separate. Power rails may be split halfway down. Put each LED leg in a DIFFERENT numbered row so no legs share a connected strip. Use one half of each power rail throughout this sheet.

## 2. Find the LED type if the bag does not say

With the UNO unplugged, connect the suspected common leg **through the 220 Ω resistor** to GND. Connect just one color leg to 5V; leave the other two disconnected. Plug in briefly. If it lights, it is common cathode. If not, unplug and reverse: common through resistor to 5V, one color leg to GND. If that lights, it is common anode. Never test without the resistor. If neither works, recheck the pinout instead of guessing. Repeat one color at a time to identify R/G/B; unplug between changes.

## 3. Wire the circuit

1. UNO 5V → breadboard + rail. UNO GND → − rail (2 jumpers).
2. PIR VCC → + rail, PIR GND → − rail, PIR OUT → UNO D2 (3 jumpers).
3. LED red leg → UNO D9, green → D10, blue → D11 (3 jumpers).
4. LED common leg → one end of the 220 Ω resistor. Its other end → − rail for cathode, **or** + rail for anode (at most 1 extra jumper). Keep the resistor's two ends in separate breadboard strips.
5. Set `COMMON_ANODE` in the sketch to `false` for cathode or `true` for anode. The wiring and constant must agree.

```text
Mac USB-C ─ adapter ─ USB cable ─ UNO R3
                                  5V ───────────── + rail ── PIR VCC
                                 GND ───────────── - rail ── PIR GND
                                  D2 ◄────────────────────── PIR OUT

                                  D9 ───────── LED R
                                 D10 ───────── LED G
                                 D11 ───────── LED B
                                               LED COMMON
                                                    │
                                                  [220 Ω]
                                                    │
                          cathode: - rail / anode: + rail
                          (choose ONE, never connect both)
```

This uses 8–9 of the 10 jumpers. The sketch lights only one color at a time because the kit has one shared resistor. Do not change it to display mixed colors without giving each color its own resistor.

## 4. Set the PIR

With USB disconnected, put the retrigger jumper on **H**. Set the sensitivity/range knob toward minimum (usually counterclockwise) for the nearest few metres. Start the delay knob near minimum, then tune it so OUT stays high for about **30 seconds** after a single movement. Knob positions and ranges vary between boards: use elapsed time and `P1`/`P0`, not an assumed angle. H mode extends the high signal when it sees more movement. Allow about a minute to settle after power-on and aim across the arm's movement, away from heaters/direct sun.

A PIR detects changes in heat, not a stationary person's continued presence. An arm movement only helps if the PIR actually detects it. The software keeps short P1 pulses awake across gaps shorter than its quiet window. With a roughly 30 s hardware hold plus the default 30 s software quiet window, rearming may take roughly a minute after the last movement. [PIR timing and H-mode background](https://learn.adafruit.com/pir-passive-infrared-proximity-motion-sensor/testing-a-pir).

## 5. Upload

Open a terminal in this repository. Install Arduino CLI if needed, then:

```sh
arduino-cli core update-index
arduino-cli core install arduino:avr
arduino-cli board list
arduino-cli compile --fqbn arduino:avr:uno device/arduino/aeye_wake
arduino-cli upload --fqbn arduino:avr:uno --port /dev/cu.usbmodemYOUR_PORT device/arduino/aeye_wake
```

Replace the port with the one shown by `board list`. Stop the bridge and close Serial Monitor before upload; only one process may own the serial port. On this Mac the installed AVR toolchain currently fails with `bad CPU type in executable`; use a compatible AVR toolchain/Arduino IDE installation (or the supported Intel-toolchain compatibility setup) before uploading. This work did not install system software or verify an upload.

Arduino IDE alternative: open `device/arduino/aeye_wake/aeye_wake.ino`, choose **Arduino Uno** under Boards and the USB port under Port, then click Upload. Open Serial Monitor at **115200 baud** with newline ending. Expect P0/P1 on transitions and once a second. Send `L ARMED`, `L EVENT`, `L SESSION`, `L OFF`: blue dim pulse, red, green, dark. Without another command within five seconds it goes dark; the running bridge sends keepalives.

## 6. Run with A-eye

Start the existing server and open an agent call. Pair the phone using the existing phone-camera flow. Grant microphone/audio permission with the normal call controls before testing unattended wake. Leave the microphone enabled for unattended listening: wake does not turn a user-off mic back on, but still speaks the reminder. A remote phone page needs the existing HTTPS flow; the Shortcut's server POST may use the Mac's LAN HTTP address.

Install Python dependencies in the project's virtual environment (`pyserial` is added for USB access). Set `A_EYE_TOKEN` to the same token as the server and `A_EYE_OWNER` to this call's capability. Do not paste either into logs or screenshots. In the active agent page's developer console, `await (await import('./call-owner.js')).callOwner()` retrieves the current capability locally. It changes for each new token-based call; a token alone intentionally cannot select somebody else's call. Access-authenticated callers instead use the same verified identity.

```sh
python scripts/wake_bridge.py --url http://127.0.0.1:8000 --zone entryway
```

Port detection refuses ambiguity; pass `--port /dev/cu.usbmodemYOUR_PORT` if needed. `--quiet 30` controls the quiet window; `--cooldown 5` is the minimum trigger interval. Close Serial Monitor first. Stop with Ctrl-C. USB unplug/replug requires restarting the bridge.

LED meanings: OFF = unavailable/disconnected, ARMED = live client waiting, EVENT = trigger delivered/pending listening acknowledgement, SESSION = client acknowledged listening within the presence window. Rearming changes the sensor indicator, not the user's mic/camera choices. Browser sleep, blocked permissions, or a closed call prevent reliable unattended wake. A paired phone's microphone must already be enabled; the wake does not remotely grant it permission.

Set a zone reminder through the existing authenticated `POST /api/memory/tag` with JSON `{"name":"entryway","kind":"place","label":"Keys are in the bowl."}` and the same owner header. This is an explicit local reminder, not a newly inferred object location. If no matching place label exists, the spoken line is “I’m here. How can I help?”

No-hardware check with an open call:

```sh
python scripts/wake_bridge.py --fake --quiet 1 --duration 4
```

Or run the self-contained synthetic-client, real-server check:

```sh
python scripts/wake_smoke.py
```

## 7. One-tap iPhone Shortcut

1. Keep the Mac server reachable on your LAN and the intended agent call open. Use its LAN address, for example `http://192.168.1.20:8000` (substitute your actual address).
2. In Shortcuts create **Wake A-eye**. Add **URL**: `http://192.168.1.20:8000/api/trigger`.
3. Add **Get Contents of URL**, method **POST**, request body **JSON**. Add text fields `source: iphone-shortcut`, `kind: shortcut`, `zone: entryway`.
4. Add headers `Content-Type: application/json`, `Authorization: Bearer YOUR_TOKEN`, and `X-Aeye-Owner: YOUR_CURRENT_CALL_CAPABILITY`. These are placeholders, not credentials. Store actual values only in your own Shortcut; do not share/export it with secrets included.
5. Optionally add **Show Result** while testing. Add the Shortcut to the Home Screen or Action Button for one tap. Allow local-network access when iOS asks.

Expected response starts at EVENT, then the status endpoint reports SESSION after the client acknowledges. 401 means credentials were refused; 409 means no live matching owner client; 429 means wait five seconds. With Access, the request must carry valid Access authentication for that same owner; a browser sign-in does not automatically sign in Shortcuts. LAN HTTP sends credentials without encryption, so use the existing HTTPS URL when the network is not trusted. Updating the owner capability is required when starting a new token-based call.
