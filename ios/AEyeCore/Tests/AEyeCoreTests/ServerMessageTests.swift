import XCTest
@testable import AEyeCore

final class ServerMessageTests: XCTestCase {
    func d(_ s: String) -> ServerMessage? { ServerMessage.decode(Data(s.utf8)) }

    func testSay() {
        XCTAssertEqual(d(#"{"v":2,"type":"say","agent":"helper","utterance_id":"u1","text":"Hi"}"#),
                       .say(agent: .helper, utteranceId: "u1", text: "Hi"))
    }
    func testDrawWithClearAndBox() {
        let msg = d(#"{"v":2,"type":"draw","agent":"helper","seq":7,"ops":[{"kind":"clear","id":"*"},{"kind":"box","id":"draw-0","role":"target","bbox":[0.1,0.2,0.3,0.4]}]}"#)
        guard case let .draw(agent, seq, ops)? = msg else { return XCTFail() }
        XCTAssertEqual(agent, .helper); XCTAssertEqual(seq, 7); XCTAssertEqual(ops.count, 2)
        XCTAssertEqual(ops[1].bbox, [0.1, 0.2, 0.3, 0.4]); XCTAssertEqual(ops[1].role, .target)
    }
    func testAgentStateWithFocus() {
        XCTAssertEqual(d(#"{"v":2,"type":"agent_state","agent":"shopper","state":"thinking","focus":[0.5,0.5]}"#),
                       .agentState(agent: .shopper, state: .thinking))
    }
    func testMarketResults() {
        let json = #"{"v":2,"type":"market_results","agent":"shopper","result":{"v":1,"query_key":"k","category":"ram","items":[{"title":"32GB DDR5","retailer":"Shop","price_usd":89.5,"url":"https://x","source":"fixture","fetched_at":"2026-10-23T00:00:00Z","compat":{"status":"fits","reasons":[],"unknown":[]}}]}}"#
        guard case let .marketResults(r)? = d(json) else { return XCTFail() }
        XCTAssertEqual(r.items.first?.priceUsd, 89.5); XCTAssertEqual(r.items.first?.compat.status, "fits")
    }
    func testError() {
        XCTAssertEqual(d(#"{"v":2,"type":"error","code":"vision_unavailable","message":"Vision is unavailable."}"#),
                       .error(code: "vision_unavailable", message: "Vision is unavailable."))
    }
    func testUnknownTypeIsIgnored() {
        XCTAssertEqual(d(#"{"v":2,"type":"metrics","x":1}"#), .ignored(type: "metrics"))
        XCTAssertEqual(d(#"{"v":2,"type":"audio","agent":"helper","utterance_id":"u","pcm16_b64":"invalid"}"#), .ignored(type: "audio"))
    }
    func testGarbageIsNil() {
        XCTAssertNil(d("not json")); XCTAssertNil(d(#"{"v":2}"#))
        XCTAssertEqual(d(#"{"v":2,"type":"say","agent":"robot","utterance_id":"u","text":"x"}"#), .ignored(type: "say"))
    }
}
