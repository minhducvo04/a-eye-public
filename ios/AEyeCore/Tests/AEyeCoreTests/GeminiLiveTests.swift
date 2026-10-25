import XCTest
@testable import AEyeCore

final class GeminiLiveTests: XCTestCase {
    func testDefaultAndTransportSelection() {
        XCTAssertTrue(LiveConfiguration.consent(nil))
        XCTAssertFalse(LiveConfiguration.consent(false))
        XCTAssertTrue(LiveConfiguration.consent(true))
        for mode in ["gemini-live", "general"] {
            XCTAssertTrue(LiveConfiguration.enabled(mode: mode, google: true, privateMode: false))
            XCTAssertFalse(LiveConfiguration.enabled(mode: mode, google: false, privateMode: false))
            XCTAssertFalse(LiveConfiguration.enabled(mode: mode, google: true, privateMode: true))
        }
        for mode in ["home", "memory", "pc_build"] {
            XCTAssertFalse(LiveConfiguration.enabled(mode: mode, google: true, privateMode: false))
        }
    }

    func testConsentAndURLIsolation() throws {
        let base = try XCTUnwrap(URL(string: "wss://example.test/ws2?token=abc&owner=xyz&mode=gemini-live&google=1"))
        XCTAssertNil(LiveConfiguration.url(base, live: true, google: false, privateMode: false))
        XCTAssertNil(LiveConfiguration.url(base, live: true, google: true, privateMode: true))
        let live = try XCTUnwrap(LiveConfiguration.url(base, live: true, google: true, privateMode: false))
        let items = URLComponents(url: live, resolvingAgainstBaseURL: false)!.queryItems!
        XCTAssertEqual(items.filter { $0.name == "mode" }.map(\.value), ["gemini-live"])
        XCTAssertTrue(items.contains(URLQueryItem(name: "token", value: "abc")))
        XCTAssertTrue(items.contains(URLQueryItem(name: "owner", value: "xyz")))
        let normal = LiveConfiguration.url(base, live: false, google: false, privateMode: true)!
        XCTAssertFalse(normal.absoluteString.contains("google="))
        XCTAssertFalse(normal.absoluteString.contains("mode="))
    }

    func testTransportMessages() throws {
        let hello = try XCTUnwrap(JSONSerialization.jsonObject(with: ClientMessage.hello(sessionId: "s", pack: "general", serverAudio: true, privateMode: false)) as? [String: Any])
        XCTAssertEqual(hello["pack"] as? String, "general")
        XCTAssertEqual(hello["tts"] as? String, "server")
        XCTAssertEqual(hello["stt"] as? String, "server")
        let bytes = Data([0, 0, 255, 127])
        let audio = try XCTUnwrap(JSONSerialization.jsonObject(with: ClientMessage.audioIn(sessionId: "s", seq: 3, pcm: bytes, final: true)) as? [String: Any])
        XCTAssertEqual(audio["sample_rate"] as? Int, 16000)
        XCTAssertEqual(audio["pcm16_b64"] as? String, bytes.base64EncodedString())
        XCTAssertEqual(audio["final"] as? Bool, true)
        let packet = Data(#"{"type":"audio","agent":"helper","utterance_id":"u","pcm16_b64":"AAD/fw==","final":true}"#.utf8)
        guard case .audio(let decoded) = ServerMessage.decode(packet) else { return XCTFail("PCM ignored") }
        XCTAssertEqual(decoded.pcm, bytes)
        XCTAssertTrue(decoded.final)
        XCTAssertEqual(ServerMessage.decode(Data(#"{"type":"audio","agent":"helper","utterance_id":"u","sample_rate":24000,"pcm16_b64":"AAD/fw=="}"#.utf8)), .ignored(type: "audio"))
    }

    func testTranscriptAndUtteranceBoundaries() {
        XCTAssertEqual(LiveConfiguration.transcript(Data(#"{"type":"turn","text":"Where is it?"}"#.utf8)), "Where is it?")
        var gate = LiveAudioGate()
        XCTAssertFalse(gate.consume(rms: 0, duration: 60))
        XCTAssertFalse(gate.isActive)
        XCTAssertFalse(gate.consume(rms: 0.1, duration: 0.1))
        XCTAssertTrue(gate.isActive)
        XCTAssertFalse(gate.consume(rms: 0, duration: 0.4))
        XCTAssertTrue(gate.consume(rms: 0, duration: 0.4))
        XCTAssertFalse(gate.isActive)
        XCTAssertFalse(gate.consume(rms: 0, duration: 1))
    }
}
