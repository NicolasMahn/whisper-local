#!/usr/bin/env bash
# Compiles and tests the Foundation-only core (dictation logic, WAV, multipart)
# in the official Swift container, for checking Mac changes from Linux.
# The AppKit/SwiftUI parts only build on a Mac.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/Sources/Core" "$work/Tests/CoreTests"
cp "$here"/Sources/WhisperLocal/{Dictation,Multipart,WAV}.swift "$work/Sources/Core/"
cp "$here"/Tests/WhisperLocalTests/{DictationTests,EncodingTests}.swift "$work/Tests/CoreTests/"
sed -i 's/@testable import WhisperLocal/@testable import Core/' "$work"/Tests/CoreTests/*.swift
cat > "$work/Package.swift" <<'SWIFT'
// swift-tools-version:5.9
import PackageDescription
let package = Package(name: "Core", targets: [
    .target(name: "Core"),
    .testTarget(name: "CoreTests", dependencies: ["Core"]),
])
SWIFT
podman run --rm -v "$work":/src:Z -w /src docker.io/library/swift:6.2 swift test
