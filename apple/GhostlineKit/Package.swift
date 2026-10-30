// swift-tools-version: 6.2
// GhostlineKit: the API client, the models and the small pieces of logic the iPhone and Mac app share, testable
// without a simulator (`swift test`).
import PackageDescription

let package = Package(
    name: "GhostlineKit",
    platforms: [.iOS(.v26), .macOS(.v26)],
    products: [
        .library(name: "GhostlineKit", targets: ["GhostlineKit"]),
    ],
    targets: [
        .target(name: "GhostlineKit", swiftSettings: [.swiftLanguageMode(.v6)]),
        .testTarget(name: "GhostlineKitTests", dependencies: ["GhostlineKit"], swiftSettings: [.swiftLanguageMode(.v6)]),
    ]
)
