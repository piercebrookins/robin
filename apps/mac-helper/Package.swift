// swift-tools-version: 6.0
import PackageDescription

let package = Package(
  name: "RobinMacHelper",
  platforms: [.macOS(.v14)],
  products: [.executable(name: "RobinMacHelper", targets: ["RobinMacHelper"])],
  targets: [.executableTarget(name: "RobinMacHelper", swiftSettings: [.unsafeFlags(["-parse-as-library"])], linkerSettings: [.linkedFramework("ScreenCaptureKit"), .linkedFramework("ApplicationServices"), .linkedFramework("AVFoundation"), .linkedFramework("CoreAudio"), .linkedFramework("AudioToolbox")])]
)
