import XCTest
@testable import AEyeCore

final class VoiceRulesTests: XCTestCase {
    let line = "That is the CPU fan cable. It goes on CPU_FAN."
    func testEchoOfSpokenLineIsNotBargeIn() {
        XCTAssertFalse(isBargeIn(transcript: "it goes on CPU fan", isFinal: true, speakingText: line))
    }
    func testRealInterruptionIsBargeIn() {
        XCTAssertTrue(isBargeIn(transcript: "wait stop", isFinal: false, speakingText: line))
        XCTAssertTrue(isBargeIn(transcript: "stop", isFinal: true, speakingText: line))
    }
    func testOneWordInterimOrSilenceIsNot() {
        XCTAssertFalse(isBargeIn(transcript: "wait", isFinal: false, speakingText: line))
        XCTAssertFalse(isBargeIn(transcript: "wait stop", isFinal: true, speakingText: nil))
    }
    func testEchoMatchesWholeWordsNotSubstrings() {
        // "no" sits inside "know" but is not a word of the spoken line, so it is the user talking.
        XCTAssertTrue(isBargeIn(transcript: "no", isFinal: true, speakingText: "I know the answer"))
        XCTAssertFalse(isBargeIn(transcript: "know", isFinal: true, speakingText: "I know the answer"))
    }
    func testEchoWordsMayComeInAnyOrder() {
        XCTAssertFalse(isBargeIn(transcript: "fan cpu", isFinal: false, speakingText: line))
    }
    func testInputModeFallsBackToTyped() {
        XCTAssertEqual(inputMode(micGranted: true, speechGranted: true, onDeviceSupported: true), .voice)
        XCTAssertEqual(inputMode(micGranted: false, speechGranted: true, onDeviceSupported: true), .typed)
        XCTAssertEqual(inputMode(micGranted: true, speechGranted: false, onDeviceSupported: true), .typed)
        XCTAssertEqual(inputMode(micGranted: true, speechGranted: true, onDeviceSupported: false), .typed)
    }
}
