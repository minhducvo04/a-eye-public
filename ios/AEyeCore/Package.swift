// swift-tools-version: 5.10
import PackageDescription

let package = Package(
    name: "AEyeCore",
    platforms: [.iOS(.v17), .macOS(.v14), .visionOS(.v1)],
    products: [
        .library(name: "AEyeCore", targets: ["AEyeCore"]),
    ],
    targets: [
        .target(name: "AEyeCore"),
        .testTarget(name: "AEyeCoreTests", dependencies: ["AEyeCore"]),
    ]
)
