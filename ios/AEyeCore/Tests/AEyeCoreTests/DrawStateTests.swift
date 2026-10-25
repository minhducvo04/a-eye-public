import XCTest
@testable import AEyeCore

final class DrawStateTests: XCTestCase {
    func box(_ id: String, ttl: Int? = nil) -> DrawOp {
        DrawOp(kind: "box", id: id, role: .target, label: nil, ttlMs: ttl, bbox: [0, 0, 0.5, 0.5],
               center: nil, r: nil, from: nil, to: nil, points: nil, at: nil, text: nil)
    }
    let clear = DrawOp(kind: "clear", id: "*", role: nil, label: nil, ttlMs: nil, bbox: nil, center: nil,
                       r: nil, from: nil, to: nil, points: nil, at: nil, text: nil)

    func testClearOnlyWipesThatAgent() {
        let s = DrawState.empty
            .applying(agent: .helper, seq: 1, ops: [box("a")], nowMs: 0)
            .applying(agent: .shopper, seq: 1, ops: [box("b")], nowMs: 0)
            .applying(agent: .helper, seq: 2, ops: [clear, box("c")], nowMs: 0)
        XCTAssertEqual(s.visible(nowMs: 1000).map(\.item.op.id).sorted(), ["b", "c"])
    }
    func testLowerSeqIsIgnoredEqualIsAccepted() {
        let s = DrawState.empty.applying(agent: .helper, seq: 5, ops: [box("a")], nowMs: 0)
        XCTAssertEqual(s.applying(agent: .helper, seq: 4, ops: [clear], nowMs: 0), s)
        XCTAssertEqual(s.applying(agent: .helper, seq: 5, ops: [clear], nowMs: 0).visible(nowMs: 0).count, 0)
    }
    func testSameIdReplaces() {
        let s = DrawState.empty.applying(agent: .helper, seq: 1, ops: [box("a"), box("a")], nowMs: 0)
        XCTAssertEqual(s.visible(nowMs: 0).count, 1)
    }
    func testProgressAndFade() {
        let s = DrawState.empty.applying(agent: .helper, seq: 1, ops: [box("a", ttl: 1000)], nowMs: 0)
        XCTAssertEqual(s.visible(nowMs: 125).first?.progress, 0.5)
        XCTAssertEqual(s.visible(nowMs: 600).first?.fade, 1)          // fade window starts at 650
        XCTAssertEqual(s.visible(nowMs: 825).first!.fade, 0.5, accuracy: 1e-9)
        XCTAssertTrue(s.visible(nowMs: 1000).isEmpty)                 // expired
    }
    func testNoTtlIsPersistent() {
        let s = DrawState.empty.applying(agent: .helper, seq: 1, ops: [box("a", ttl: 0)], nowMs: 0)
        XCTAssertEqual(s.visible(nowMs: 1e9).first?.fade, 1)
    }
}
