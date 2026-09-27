// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "WhisperLocal",
    platforms: [.macOS(.v14)],
    products: [.executable(name: "WhisperLocal", targets: ["WhisperLocal"])],
    targets: [
        .executableTarget(name: "WhisperLocal"),
        .testTarget(name: "WhisperLocalTests", dependencies: ["WhisperLocal"]),
    ]
)
