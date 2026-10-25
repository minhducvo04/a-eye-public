import XCTest
@testable import AEyeCore

final class ModesTests: XCTestCase {
    func testRingContents() {
        XCTAssertEqual(Modes.all.filter(\.isAvailable).compactMap(\.pack),
                       ["general", "pc_build", "cooking", "assemble", "memory", "home", "general", "general"])
        XCTAssertEqual(Modes.all.filter { !$0.isAvailable }.map(\.title), ["Mechanic"])
        XCTAssertEqual(Modes.all[Modes.defaultIndex].id, "gemini-live")
        XCTAssertEqual(Modes.all.filter(\.requiresPowerOff).map(\.id), ["pc_build"])
    }

    func testRingOrderMatchesWeb() {
        // Same ids and order as client-web/agent.js MODES.
        XCTAssertEqual(Modes.all.map(\.id),
                       ["general", "pc_build", "cooking", "assemble", "memory", "mechanic", "plumbing", "engineering", "gemini-live"])
        XCTAssertEqual(Modes.all.first { $0.id == "plumbing" }?.title, "Sink")
    }

    func testEngineering() {
        let mode = Modes.all.first { $0.id == "engineering" }!
        XCTAssertTrue(mode.isAvailable)
        XCTAssertEqual(mode.pack, "general")
        XCTAssertEqual(Modes.startText(for: mode), "light an LED")
        let base = URL(string: "ws://localhost/ws2?token=fixture")!
        let url = LiveConfiguration.url(base, live: false, google: true, privateMode: true, engineering: true)!
        let query = URLComponents(url: url, resolvingAgainstBaseURL: false)!.queryItems!
        XCTAssertTrue(query.contains(URLQueryItem(name: "mode", value: "engineering")))
        XCTAssertFalse(query.contains { $0.name == "google" })
    }

    func testHomeStart() {
        XCTAssertEqual(Modes.homePack, "home")
        XCTAssertEqual(Modes.homeStartText, "sink")
        XCTAssertTrue(Modes.all.contains { $0.pack == Modes.homePack })
    }
}
