import AEyeCore
import Combine
import PhotosUI
import SwiftUI
import UIKit

/// The picture A-eye looks at on Vision Pro: one photo the user picked. It stands in for the iPhone's
/// `CameraService`, because visionOS gives apps no main-camera frames without Apple's enterprise entitlement
/// (see `visionos/PLAN.md`). The photo is drawn upright at the wire size and kept in memory only; nothing is
/// written to disk or logged.
@MainActor
final class PhotoSource: ObservableObject {
    /// Upright, already scaled to the wire size. nil until a photo is picked.
    @Published private(set) var image: UIImage?
    @Published private(set) var isLoading = false
    @Published private(set) var loadFailed = false

    private var jpeg: (data: Data, width: Int, height: Int)?

    /// Loads the picked item. A photo that cannot be read leaves the previous one in place.
    func load(_ item: PhotosPickerItem) async {
        isLoading = true
        loadFailed = false
        defer { isLoading = false }
        guard let data = try? await item.loadTransferable(type: Data.self), let picked = UIImage(data: data) else {
            loadFailed = true
            return
        }
        use(picked)
    }

    /// Same as the iPhone: long side at most `Tuning.maxFrameSide`, JPEG quality `Tuning.jpegQuality`.
    func use(_ picked: UIImage) {
        let pixelW = Int(picked.size.width * picked.scale)
        let pixelH = Int(picked.size.height * picked.scale)
        let target = frameSize(width: pixelW, height: pixelH)
        guard target.width > 0, target.height > 0 else {
            loadFailed = true
            return
        }
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        format.opaque = true
        let size = CGSize(width: target.width, height: target.height)
        // Drawing through the renderer applies the EXIF orientation, so the pixels are upright (v2 has no
        // orientation field).
        let upright = UIGraphicsImageRenderer(size: size, format: format).image { _ in
            picked.draw(in: CGRect(origin: .zero, size: size))
        }
        guard let data = upright.jpegData(compressionQuality: Tuning.jpegQuality) else {
            loadFailed = true
            return
        }
        jpeg = (data, target.width, target.height)
        image = upright
    }

    /// The photo as the frame loop sends it. Same shape as `CameraService.latestJPEG()` on the iPhone.
    func latestJPEG() -> (data: Data, width: Int, height: Int)? {
        jpeg
    }
}
