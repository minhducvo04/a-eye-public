#if DEBUG
import AEyeCore
import Foundation
import UIKit

/// Debug builds only: lets a simulator run be driven from the command line, since visionOS simulator UI
/// cannot be scripted from `simctl`. Launch arguments (they land in `UserDefaults`):
///   -AEyeDebugPhoto /path/to/photo.jpg   use this file as the picked photo
///   -AEyeDebugStart general              start a call in that mode id
///   -AEyeDebugSay "what am I looking at" send this typed line once connected
///   -AEyeDebugTyped YES                  typed input, never ask for the mic (read in `CallController`)
///   -AEyeDebugVoiceUI YES                with -AEyeDebugTyped: show the voice controls, mic still closed
///   -AEyeDebugInject /path/messages.jsonl  once connected, feed these server messages one per line, as if
///                                        the server sent them; a line {"sleep_s": 1.5} pauses
///   -AEyeDebugRewind YES                 Rewind on before the call starts
///   -AEyeDebugForget today               press that Forget button 4 s into the call (last, today, or call)
///   -AEyeDebugEnd 8                      end the call after this many seconds
///   -AEyeDebugPair YES                   request a pairing code and open the Pair phone sheet (`CallScreen`)
/// Nothing here exists in a Release build.
@MainActor
enum DebugHooks {
    // Fixtures from contracts/examples.json, plus camera-off/leave transitions.
    static func checkPairing() {
        let offer = PairingMessage.decode(Data(#"{"v":2,"type":"pair","code":"4821","expires_in_s":300,"path":"/agent.html?pair=4821","future":true}"#.utf8))!
        let joined = PairingMessage.decode(Data(#"{"v":2,"type":"pair_status","state":"joined","role":"call","camera_on":true,"source":"camera"}"#.utf8))!
        let rejected = PairingMessage.decode(Data(#"{"v":2,"type":"pair_status","state":"rejected","role":"camera","detail":"Unknown or expired code."}"#.utf8))!
        assert(offer.code == "4821" && offer.expires_in_s == 300)
        assert(rejected.state == "rejected" && rejected.detail == "Unknown or expired code.")
        let url = offer.link(socketURL: URL(string: "wss://example.test/ws2?token=test%2Btoken%26value")!)!
        assert(url.scheme == "https" && url.path == "/agent.html")
        assert(URLComponents(url: url, resolvingAgainstBaseURL: false)!.queryItems!.last!.value == "test+token&value")
        var pairing = PairingState()
        pairing.receive(offer)
        assert(pairing.offer != nil && pairing.expiresAt != nil)
        pairing.receive(joined)
        assert(pairing.joined && pairing.cameraOn && pairing.offer == nil)
        pairing.receive(PairingMessage.decode(Data(#"{"v":2,"type":"pair_status","state":"camera","camera_on":false,"source":"camera"}"#.utf8))!)
        assert(pairing.joined && !pairing.cameraOn && pairing.status == "Phone camera off")
        pairing.receive(PairingMessage.decode(Data(#"{"v":2,"type":"pair_status","state":"left","source":"call"}"#.utf8))!)
        assert(!pairing.joined && pairing.status == "Phone camera left")
        pairing.receive(offer)
        pairing.receive(PairingMessage.decode(Data(#"{"v":2,"type":"pair_status","state":"expired"}"#.utf8))!)
        assert(pairing.offer == nil && pairing.status == "Pairing code expired")
        let request = try! JSONSerialization.jsonObject(with: PairingMessage.request(sessionId: "debug")) as! [String: Any]
        assert(request["type"] as? String == "pair_request" && request["session_id"] as? String == "debug")
        assert(request["v"] as? Int == 2)
        assert(PairSheet.qrCode(url.absoluteString) != nil)
        print("PAIRING_DEBUG_CHECKS_PASS")
    }

    static func run(controller: CallController, photo: PhotoSource) {
        checkPairing()
        let defaults = UserDefaults.standard
        if let path = defaults.string(forKey: "AEyeDebugPhoto"), let image = UIImage(contentsOfFile: path) {
            photo.use(image)
        }
        if defaults.bool(forKey: "AEyeDebugRewind") { controller.setRewind(true) }
        guard let id = defaults.string(forKey: "AEyeDebugStart"),
              let mode = Modes.all.first(where: { $0.id == id }) else { return }
        controller.start(mode)
        if let path = defaults.string(forKey: "AEyeDebugInject"),
           let text = try? String(contentsOfFile: path, encoding: .utf8) {
            Task {
                for _ in 0..<50 where controller.state.link != .live {
                    try? await Task.sleep(nanoseconds: 200_000_000)
                }
                try? await Task.sleep(nanoseconds: 1_000_000_000)
                for line in text.split(separator: "\n") where !line.isEmpty {
                    let data = Data(line.utf8)
                    if let pause = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["sleep_s"] as? Double {
                        try? await Task.sleep(nanoseconds: UInt64(pause * 1_000_000_000))
                    } else {
                        controller.debugReceive(data)
                    }
                }
            }
        }
        if let scope = defaults.string(forKey: "AEyeDebugForget") {
            Task {
                try? await Task.sleep(nanoseconds: 4_000_000_000)
                controller.forget(scope: scope == "call" ? nil : scope)
            }
        }
        let endAfter = defaults.double(forKey: "AEyeDebugEnd")
        if endAfter > 0 {
            Task {
                try? await Task.sleep(nanoseconds: UInt64(endAfter * 1_000_000_000))
                controller.end()
            }
        }
        guard let line = defaults.string(forKey: "AEyeDebugSay") else { return }
        Task {
            for _ in 0..<50 where controller.state.link != .live {
                try? await Task.sleep(nanoseconds: 200_000_000)
            }
            try? await Task.sleep(nanoseconds: 1_500_000_000)  // let a frame go out first
            controller.send(text: line)
        }
    }
}
#endif
