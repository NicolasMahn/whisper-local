#!/bin/sh
set -eu

cd "$(dirname "$0")"
swift build -c release
binary_dir=$(swift build -c release --show-bin-path)
app="$PWD/build/Whisper Local.app"
mkdir -p "$app/Contents/MacOS"
cp "$binary_dir/WhisperLocal" "$app/Contents/MacOS/WhisperLocal"
cat > "$app/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleIdentifier</key><string>io.github.nicolasmahn.WhisperLocal</string>
  <key>CFBundleExecutable</key><string>WhisperLocal</string>
  <key>CFBundleName</key><string>Whisper Local</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>NSPrincipalClass</key><string>NSApplication</string>
  <key>CFBundleShortVersionString</key><string>0.1.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>LSUIElement</key><true/>
  <key>NSMicrophoneUsageDescription</key><string>Record speech during held-key or hands-free dictation.</string>
  <key>NSLocalNetworkUsageDescription</key><string>Send dictation audio to your speech server on the local network.</string>
  <key>NSAppTransportSecurity</key>
  <dict>
    <key>NSAllowsArbitraryLoads</key><true/>
  </dict>
</dict>
</plist>
PLIST
codesign --force --deep --sign - "$app"
printf 'Built: %s\nInstall: cp -R "%s" /Applications/\n' "$app" "$app"
