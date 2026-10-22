// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "BarkVoiceNotes",
    platforms: [
        .macOS(.v14)
    ],
    targets: [
        .executableTarget(
            name: "BarkVoiceNotes",
            path: "Sources/BarkVoiceNotes",
            linkerSettings: [
                .linkedFramework("AppKit"),
                .linkedFramework("WebKit"),
                .linkedFramework("AVFoundation"),
                .linkedFramework("UniformTypeIdentifiers"),
            ]
        )
    ]
)
