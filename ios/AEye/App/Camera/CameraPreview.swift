import AVFoundation
import SwiftUI
import UIKit

/// Backs the preview with an `AVCaptureVideoPreviewLayer`. `.resizeAspectFill` matches `CoverTransform`.
final class PreviewView: UIView {
    override class var layerClass: AnyClass { AVCaptureVideoPreviewLayer.self }

    var previewLayer: AVCaptureVideoPreviewLayer? { layer as? AVCaptureVideoPreviewLayer }

    // No rotation code here on purpose. The app is portrait-only, and a preview layer's connection already
    // defaults to portrait (90 degrees). The connection does not even exist until the session has its input,
    // which happens later on the session queue, so setting it from here would not reliably apply anyway.
    // Only the video-data-output connection needs an explicit angle; see `CaptureRig.configureIfNeeded()`.
}

struct CameraPreview: UIViewRepresentable {
    let session: AVCaptureSession
    var isFront = false

    func makeUIView(context: Context) -> PreviewView {
        let view = PreviewView()
        view.backgroundColor = .black
        view.previewLayer?.session = session
        view.previewLayer?.videoGravity = .resizeAspectFill
        return view
    }

    func updateUIView(_ uiView: PreviewView, context: Context) {
        guard let connection = uiView.previewLayer?.connection,
              connection.isVideoMirroringSupported else { return }
        connection.automaticallyAdjustsVideoMirroring = false
        connection.isVideoMirrored = isFront
    }
}
