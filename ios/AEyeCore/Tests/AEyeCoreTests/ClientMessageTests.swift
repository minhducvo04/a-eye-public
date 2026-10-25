import XCTest
@testable import AEyeCore

final class ClientMessageTests: XCTestCase {
    func obj(_ d: Data) -> [String: Any] { try! JSONSerialization.jsonObject(with: d) as! [String: Any] }

    func testHelloForGeneralHasNoBoard() {
        let m = obj(ClientMessage.hello(sessionId: "s1", pack: "general"))
        XCTAssertEqual(Set(m.keys), ["v", "type", "session_id", "pack", "client", "tts"])
        XCTAssertEqual(m["v"] as? Int, 2); XCTAssertEqual(m["type"] as? String, "hello")
        XCTAssertEqual(m["client"] as? String, "ios"); XCTAssertEqual(m["tts"] as? String, "browser")
    }
    func testHelloForPCBuildSendsDemoBoard() {
        let m = obj(ClientMessage.hello(sessionId: "s1", pack: "pc_build"))
        XCTAssertEqual(m["board_id"] as? String, "demo-b650")
    }
    func testFrameHasExactKeysAndBase64() {
        let m = obj(ClientMessage.frame(sessionId: "s1", seq: 3, tsMs: 1000, width: 360, height: 640, jpeg: Data([1, 2, 3])))
        XCTAssertEqual(Set(m.keys), ["v", "type", "session_id", "seq", "ts_ms", "w", "h", "jpeg_b64"])
        XCTAssertEqual(m["jpeg_b64"] as? String, "AQID")
    }
    func testUserTextIsFinalWithoutSource() {
        let m = obj(ClientMessage.userText(sessionId: "s1", text: "what is this"))
        XCTAssertEqual(Set(m.keys), ["v", "type", "session_id", "text", "final"])
        XCTAssertEqual(m["final"] as? Bool, true)
    }
    func testBargeInAndPowerOff() {
        XCTAssertEqual(Set(obj(ClientMessage.bargeIn(sessionId: "s1", utteranceId: "u1", atMs: 420)).keys),
                       ["v", "type", "session_id", "utterance_id", "at_ms"])
        let p = obj(ClientMessage.powerOff(sessionId: "s1"))
        XCTAssertEqual(p["type"] as? String, "confirm"); XCTAssertEqual(p["what"] as? String, "power_off")
        XCTAssertEqual(p["value"] as? Bool, true)
    }
}
