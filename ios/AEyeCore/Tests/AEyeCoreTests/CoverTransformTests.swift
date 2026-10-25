import XCTest
import CoreGraphics
@testable import AEyeCore

final class CoverTransformTests: XCTestCase {
    // 360x640 portrait frame on a 390x844 screen: scale = max(390/360, 844/640) = 1.31875
    let t = CoverTransform(srcW: 360, srcH: 640, viewW: 390, viewH: 844)
    func testCoverTransformCentresPortraitFrame() {
        XCTAssertEqual(t.point([0.5, 0.5]).x, 195, accuracy: 1e-6)
        XCTAssertEqual(t.point([0.5, 0.5]).y, 422, accuracy: 1e-6)
        XCTAssertEqual(t.point([0, 0]).x, -42.375, accuracy: 1e-6)
    }
    func testRectIsClampedToView() {
        XCTAssertEqual(t.rect([0, 0, 1, 1]), CGRect(x: 0, y: 0, width: 390, height: 844))
    }
    func testRadiusUsesShorterSide() {
        XCTAssertEqual(t.radius(0.1), 47.475, accuracy: 1e-6)
    }
}
