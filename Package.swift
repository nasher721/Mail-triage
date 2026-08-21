// swift-tools-version: 5.10

import PackageDescription

let package = Package(
    name: "MailTriage",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "MailTriage", targets: ["MailTriageApp"])
    ],
    targets: [
        .executableTarget(
            name: "MailTriageApp",
            path: "Sources/MailTriageApp"
        ),
        .testTarget(
            name: "MailTriageAppTests",
            dependencies: ["MailTriageApp"],
            path: "Tests/MailTriageAppTests",
            swiftSettings: [
                // The standalone Command Line Tools distribution ships the
                // Testing macro library but does not auto-discover it.
                .unsafeFlags([
                    "-load-plugin-library",
                    "/Library/Developer/CommandLineTools/usr/lib/swift/host/plugins/testing/libTestingMacros.dylib"
                ])
            ]
        )
    ]
)
