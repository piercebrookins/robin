// swift-tools-version: 6.0

import PackageDescription

let package = Package(
    name: "RobinBridge",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "robin-macos-bridge", targets: ["RobinBridge"])
    ],
    targets: [
        .executableTarget(name: "RobinBridge")
    ]
)
