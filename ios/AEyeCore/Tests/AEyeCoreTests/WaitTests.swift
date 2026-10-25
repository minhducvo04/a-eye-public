import XCTest
@testable import AEyeCore

final class WaitTests: XCTestCase {
    private func turn(_ id: String, _ stage: TurnStage, typicalMs: Int? = nil, detail: String? = nil) -> CallEvent {
        .server(.turn(id: id, stage: stage, typicalMs: typicalMs, detail: detail))
    }

    func testDecodesTurnWithTypicalMs() {
        let json = #"{"v":2,"type":"turn","turn_id":"t1","stage":"thinking","at_ms":5,"text":"hi","typical_ms":2400}"#
        XCTAssertEqual(ServerMessage.decode(Data(json.utf8)),
                       .turn(id: "t1", stage: .thinking, typicalMs: 2400, detail: nil))
    }
    func testUnknownTurnStageIsIgnored() {
        let json = #"{"v":2,"type":"turn","turn_id":"t1","stage":"dancing","at_ms":5}"#
        XCTAssertEqual(ServerMessage.decode(Data(json.utf8)), .ignored(type: "turn"))
    }
    func testReceivedStartsWaitAndThinkingKeepsItsStart() {
        let s = CallState.initial
            .reduce(turn("t1", .received), nowMs: 1000)
            .reduce(turn("t1", .thinking, typicalMs: 3000), nowMs: 1200)
        XCTAssertEqual(s.wait, Wait(turnId: "t1", agent: .helper, stage: .thinking, startedAtMs: 1000, typicalMs: 3000))
    }
    func testNewTurnRestartsTheClock() {
        let s = CallState.initial
            .reduce(turn("t1", .thinking), nowMs: 1000)
            .reduce(turn("t2", .received), nowMs: 5000)
        XCTAssertEqual(s.wait?.turnId, "t2")
        XCTAssertEqual(s.wait?.startedAtMs, 5000)
    }
    func testSpeakingAndDoneEndTheWait() {
        let base = CallState.initial.reduce(turn("t1", .thinking), nowMs: 0)
        XCTAssertNil(base.reduce(turn("t1", .speaking), nowMs: 1).wait)
        XCTAssertNil(base.reduce(turn("t1", .done), nowMs: 1).wait)
        XCTAssertNil(base.reduce(.server(.agentState(agent: .helper, state: .speaking)), nowMs: 1).wait)
        XCTAssertNil(base.reduce(.disconnected, nowMs: 1).wait)
    }
    func testFailedTurnShowsItsReason() {
        let s = CallState.initial
            .reduce(turn("t1", .received), nowMs: 0)
            .reduce(turn("t1", .failed, detail: "Didn't catch that"), nowMs: 1)
        XCTAssertNil(s.wait)
        XCTAssertEqual(s.errorText, "Didn't catch that")
    }
    func testThinkingPresenceWithoutTurnStillShowsWait() {
        let s = CallState.initial.reduce(.server(.agentState(agent: .shopper, state: .thinking)), nowMs: 700)
        XCTAssertEqual(s.wait, Wait(turnId: nil, agent: .shopper, stage: .thinking, startedAtMs: 700, typicalMs: nil))
    }
    func testThinkingPresenceDuringTurnKeepsTheTurnClock() {
        let s = CallState.initial
            .reduce(turn("t1", .thinking, typicalMs: 2000), nowMs: 100)
            .reduce(.server(.agentState(agent: .helper, state: .thinking)), nowMs: 400)
        XCTAssertEqual(s.wait?.startedAtMs, 100)
        XCTAssertEqual(s.wait?.typicalMs, 2000)
    }
    func testStaleWaitIsHidden() {
        let s = CallState.initial.reduce(turn("t1", .thinking), nowMs: 1000)
        XCTAssertNotNil(s.visibleWait(nowMs: 1000 + Tuning.maxWaitMs))
        XCTAssertNil(s.visibleWait(nowMs: 1001 + Tuning.maxWaitMs))
    }
    func testProgressRisesThenSlowsButNeverFinishes() {
        XCTAssertEqual(waitProgress(elapsedMs: 0, typicalMs: 2000), 0)
        XCTAssertEqual(waitProgress(elapsedMs: 1000, typicalMs: 2000), 0.45, accuracy: 0.001)
        XCTAssertEqual(waitProgress(elapsedMs: 2000, typicalMs: 2000), 0.9, accuracy: 0.001)
        let late = waitProgress(elapsedMs: 6000, typicalMs: 2000)
        XCTAssertGreaterThan(late, 0.9)
        XCTAssertLessThan(late, 0.99)
        XCTAssertLessThan(waitProgress(elapsedMs: 1_000_000, typicalMs: 2000), 1)
    }
    func testProgressUsesDefaultWhenTypicalUnknownOrZero() {
        let fallback = waitProgress(elapsedMs: 1000, typicalMs: nil)
        XCTAssertEqual(fallback, waitProgress(elapsedMs: 1000, typicalMs: Int(Tuning.defaultTypicalWaitMs)))
        XCTAssertEqual(waitProgress(elapsedMs: 1000, typicalMs: 0), fallback)
        XCTAssertEqual(waitProgress(elapsedMs: -50, typicalMs: 2000), 0)
    }
}
