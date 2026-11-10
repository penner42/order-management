// swift-tools-version: 5.9
import PackageDescription
let package = Package(name: "OrderManager", platforms: [.macOS("15.0")], products: [.executable(name: "OrderManager", targets: ["OrderManager"])], targets: [
    .executableTarget(name: "OrderManager"),
    .testTarget(name: "OrderManagerTests", dependencies: ["OrderManager"])
])
