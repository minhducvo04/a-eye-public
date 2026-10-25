import XCTest
@testable import AEyeCore

final class TuningTests: XCTestCase {
    func testFrameSizeKeepsAspect() {
        XCTAssertTrue(frameSize(width: 1920, height: 1080) == (640, 360))
        XCTAssertTrue(frameSize(width: 1080, height: 1920) == (360, 640))
        XCTAssertTrue(frameSize(width: 500, height: 400) == (500, 400))   // never upscales
    }
    func testServerURLNormalises() {
        XCTAssertEqual(serverURL(from: "ws://192.168.1.5:8000/ws2")?.absoluteString, "ws://192.168.1.5:8000/ws2")
        XCTAssertEqual(serverURL(from: "192.168.1.5:8000")?.absoluteString, "ws://192.168.1.5:8000/ws2")
        XCTAssertEqual(serverURL(from: "https://mac.example.ts.net")?.absoluteString, "wss://mac.example.ts.net/ws2")
        XCTAssertEqual(serverURL(from: " http://10.0.0.2:8000/ ")?.absoluteString, "ws://10.0.0.2:8000/ws2")
        XCTAssertNil(serverURL(from: ""))
        XCTAssertNil(serverURL(from: "   "))
    }
}

final class ServerURLTokenTests: XCTestCase {
    func testTokenBecomesQueryItem() {
        XCTAssertEqual(serverURL(from: "192.168.1.5:8000", token: "abc123")?.absoluteString,
                       "ws://192.168.1.5:8000/ws2?token=abc123")
        XCTAssertEqual(serverURL(from: "https://mac.example.ts.net", token: " s3cret ")?.absoluteString,
                       "wss://mac.example.ts.net/ws2?token=s3cret")
    }

    func testEmptyTokenLeavesURLAlone() {
        XCTAssertEqual(serverURL(from: "192.168.1.5:8000", token: "")?.absoluteString, "ws://192.168.1.5:8000/ws2")
        XCTAssertEqual(serverURL(from: "192.168.1.5:8000", token: "  ")?.absoluteString, "ws://192.168.1.5:8000/ws2")
    }

    func testTypedTokenReplacesOneInTheURL() {
        XCTAssertEqual(serverURL(from: "ws://h:8000/ws2?token=old&x=1", token: "new")?.absoluteString,
                       "ws://h:8000/ws2?x=1&token=new")
        XCTAssertNil(serverURL(from: "", token: "new"))
    }
}
