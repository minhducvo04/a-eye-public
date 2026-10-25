import XCTest
@testable import AEyeCore

final class CallStateTests: XCTestCase {
    func testReconnectResetsPowerOffAndDrawing() {
        let box = DrawOp(kind: "box", id: "a", role: nil, label: nil, ttlMs: nil, bbox: [0, 0, 1, 1], center: nil,
                         r: nil, from: nil, to: nil, points: nil, at: nil, text: nil)
        let s = CallState.initial
            .reduce(.connected, nowMs: 0)
            .reduce(.powerOffTapped, nowMs: 0)
            .reduce(.server(.draw(agent: .helper, seq: 1, ops: [box])), nowMs: 0)
            .reduce(.disconnected, nowMs: 0)
        XCTAssertEqual(s.link, .reconnecting)
        let back = s.reduce(.connected, nowMs: 0)
        XCTAssertEqual(back.link, .live)
        XCTAssertFalse(back.powerOffConfirmed)
        XCTAssertTrue(back.draw.visible(nowMs: 0).isEmpty)
        XCTAssertTrue(back.showsPowerOffButton(for: Modes.all[1]))
    }
    func testSayBecomesCaptionAndClearsError() {
        let s = CallState.initial
            .reduce(.server(.error(code: "vision_unavailable", message: "Vision is unavailable.")), nowMs: 0)
        XCTAssertEqual(s.errorText, "Vision is unavailable.")
        let t = s.reduce(.server(.say(agent: .helper, utteranceId: "u1", text: "Hi")), nowMs: 0)
        XCTAssertEqual(t.caption, Caption(agent: .helper, text: "Hi", utteranceId: "u1")); XCTAssertNil(t.errorText)
    }
    func testPresenceAndMarket() {
        let r = MarketResponse(category: "ram", items: [], note: nil)
        let s = CallState.initial
            .reduce(.server(.agentState(agent: .shopper, state: .thinking)), nowMs: 0)
            .reduce(.server(.marketResults(r)), nowMs: 0)
        XCTAssertEqual(s.presence[.shopper], .thinking); XCTAssertEqual(s.market, r)
        XCTAssertNil(s.reduce(.marketDismissed, nowMs: 0).market)
    }
    func testHelperSpeakingAfterShopperSpeakingLeavesShopperIdle() {
        let s = CallState.initial
            .reduce(.server(.agentState(agent: .shopper, state: .speaking)), nowMs: 0)
            .reduce(.server(.agentState(agent: .helper, state: .speaking)), nowMs: 0)
        XCTAssertEqual(s.presence[.helper], .speaking)
        XCTAssertEqual(s.presence[.shopper], .idle)
    }
    func testConnectedClearsPresence() {
        let s = CallState.initial
            .reduce(.server(.agentState(agent: .shopper, state: .speaking)), nowMs: 0)
            .reduce(.connected, nowMs: 0)
        XCTAssertTrue(s.presence.isEmpty)
    }
    func testIgnoredMessageLeavesStateUnchanged() {
        let s = CallState.initial.reduce(.connected, nowMs: 0)
        XCTAssertEqual(s.reduce(.server(.ignored(type: "metrics")), nowMs: 0), s)
    }
    func testPowerOffButtonOnlyForPCBuild() {
        XCTAssertFalse(CallState.initial.showsPowerOffButton(for: Modes.all[0]))
        XCTAssertFalse(CallState.initial.reduce(.powerOffTapped, nowMs: 0).showsPowerOffButton(for: Modes.all[1]))
    }
}

final class ExpertBadgeTests: XCTestCase {
    private func turn(_ id: String, _ stage: TurnStage) -> CallEvent {
        .server(.turn(id: id, stage: stage, typicalMs: nil, detail: nil))
    }

    func testExpertBadgeFollowsItsTurn() {
        let live = CallState.initial.reduce(.connected, nowMs: 0)
        let checking = live.reduce(turn("t1", .expert), nowMs: 1)
        XCTAssertEqual(checking.expertTurnId, "t1")
        XCTAssertTrue(checking.isExpertChecking)

        let otherDone = checking.reduce(turn("t2", .expertDone), nowMs: 2)
        XCTAssertEqual(otherDone.expertTurnId, "t1", "another turn's expert_done does not clear the badge")

        XCTAssertNil(otherDone.reduce(turn("t1", .expertDone), nowMs: 3).expertTurnId)
        XCTAssertNil(checking.reduce(.disconnected, nowMs: 4).expertTurnId)
        XCTAssertNil(checking.reduce(.connected, nowMs: 5).expertTurnId)
    }

    func testExpertStageLeavesTheWaitAlone() {
        let waiting = CallState.initial.reduce(.connected, nowMs: 0).reduce(turn("t1", .received), nowMs: 10)
        XCTAssertNotNil(waiting.wait)
        let checking = waiting.reduce(turn("t1", .expert), nowMs: 20)
        XCTAssertEqual(checking.wait, waiting.wait)
        XCTAssertEqual(checking.reduce(turn("t1", .expertDone), nowMs: 30).wait, waiting.wait)
    }

    func testExpertStagesDecode() {
        let json = #"{"v":2,"type":"turn","turn_id":"t9","stage":"expert_done"}"#
        guard case .turn(_, let stage, _, _)? = ServerMessage.decode(Data(json.utf8)) else { return XCTFail() }
        XCTAssertEqual(stage, .expertDone)
    }
}
