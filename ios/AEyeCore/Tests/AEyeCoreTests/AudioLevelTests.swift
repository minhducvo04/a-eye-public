import XCTest
@testable import AEyeCore

final class AudioLevelTests: XCTestCase {
    func testNormalisedLevelMapsDecibelsToZeroOne() {
        XCTAssertEqual(normalizedLevel(rms: 0), 0)
        XCTAssertEqual(normalizedLevel(rms: 1), 1)
        XCTAssertEqual(normalizedLevel(rms: 0.0001), 0)  // -80 dB: room silence
        let speech = normalizedLevel(rms: 0.05)          // about -26 dB: normal talking
        XCTAssertGreaterThan(speech, 0.5)
        XCTAssertLessThan(speech, 1)
        XCTAssertEqual(normalizedLevel(rms: .nan), 0)
    }
    func testSmoothingRisesAtOnceAndFallsSlowly() {
        XCTAssertEqual(smoothedLevel(previous: 0.2, next: 0.8), 0.8)
        let fall = smoothedLevel(previous: 0.8, next: 0)
        XCTAssertLessThan(fall, 0.8)
        XCTAssertGreaterThan(fall, 0.4)
    }
    func testAppendingLevelKeepsTheNewestAndDoesNotMutate() {
        let history = [0.1, 0.2, 0.3]
        let next = appendingLevel(history, 0.4, capacity: 3)
        XCTAssertEqual(next, [0.2, 0.3, 0.4])
        XCTAssertEqual(history, [0.1, 0.2, 0.3])
        XCTAssertEqual(appendingLevel([], 0.5, capacity: 3), [0.5])
    }
    func testVoiceThreshold() {
        XCTAssertFalse(isVoiceLevel(0.05))
        XCTAssertTrue(isVoiceLevel(0.6))
    }
}
