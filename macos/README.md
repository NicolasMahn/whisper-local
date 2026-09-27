# Whisper Local for macOS

A menu bar app that does what the Linux app does: hold Right Option, speak, let
go, and the text is pasted where you type. See the [main README](../README.md)
for how it behaves and how to run a speech server.

This app is experimental. It has not yet been built on a Mac; only its core
logic (dictation, WAV and multipart encoding) is compiled and tested, on Linux.
Expect rough edges, and please report what breaks.

## Build and install

It needs macOS 14 or later and Xcode 15 or later. From this directory:

    swift test
    ./build.sh
    cp -R "build/Whisper Local.app" /Applications/

Open it from `/Applications`. It lives in the menu bar, with no Dock icon.

## Permissions

Allow Input Monitoring (to see the dictation key), Accessibility (to paste),
Microphone, and Local Network access (to reach a server on your network) when
macOS asks. If one is missing, the menu says so; click that line to open the
right page of System Settings.

`build.sh` signs the app ad hoc, so every rebuild looks like a new app to macOS
and can reset these permissions. Grant them again after installing a rebuild;
if a switch already looks on but nothing works, remove the app from the list
and add it again.

## Server settings

Open Settings from the menu. Server URL defaults to `http://localhost:8000/v1`;
the API key is optional and kept in the Keychain. The app asks for the model
`qwen3-asr`, so serve it under that name (see the main README). Check
connection tells you whether the server answers.

Settings also has the dictation and hands-free keys, language, known words
(one per line, sent as transcription context), sounds and launch at login; the
API key stays on the configured server through redirects. The menu keeps the
last five transcripts in memory; click one to copy it.

## Checking changes from Linux

`./check-on-linux.sh` compiles and tests the Foundation-only core in the
official Swift container with Podman. The AppKit and SwiftUI parts need a Mac.
