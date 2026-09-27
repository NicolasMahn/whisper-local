import AppKit
import SwiftUI

@main
struct WhisperLocalApp: App {
    @StateObject private var model = AppModel()

    var body: some Scene {
        MenuBarExtra(content: {
            MenuContent(model: model)
        }, label: {
            let style = DictationStyle.current(
                state: model.state, enabled: model.enabled, hasProblem: model.problem != nil
            )
            HStack(spacing: 3) {
                Image(systemName: style.symbol)
                    .foregroundStyle(style.color)
                if style.showsRecordingDot {
                    Circle()
                        .fill(style.color)
                        .frame(width: 5, height: 5)
                }
            }
            .accessibilityLabel("Whisper Local")
        })
        Settings { SettingsView(model: model) }
    }
}

private struct MenuContent: View {
    @ObservedObject var model: AppModel

    var body: some View {
        Group {
            Toggle("Enabled", isOn: $model.enabled)
            if model.recentTranscripts.isEmpty {
                Text("No transcript yet")
            } else {
                ForEach(model.recentTranscripts.indices, id: \.self) { index in
                    let transcript = model.recentTranscripts[index]
                    Button(label(for: transcript)) {
                        model.copyTranscript(transcript)
                    }
                }
            }
            if let problem = model.problem {
                if problem.pane == nil {
                    Text(problem.text)
                } else {
                    Button(problem.text) { model.openProblemSettings() }
                }
            }
            SettingsLink { Text("Settings…") }
            Button("Quit") { NSApplication.shared.terminate(nil) }
        }
        .task { await model.refreshPermissions() }
    }

    private func label(for transcript: String) -> String {
        let text = transcript.split(whereSeparator: \.isWhitespace).joined(separator: " ")
        return text.count > 80 ? String(text.prefix(80)) + "…" : text
    }
}

private struct SettingsView: View {
    @ObservedObject var model: AppModel
    @State private var knownWordsText = ""

    var body: some View {
        Form {
            TextField("Server URL", text: $model.serverURL)
                .onSubmit { Task { await model.checkReachability() } }
            SecureField("API key", text: Binding(
                get: { model.apiKey },
                set: { model.updateAPIKey($0) }
            ))
            Picker("Dictation key", selection: $model.key) {
                ForEach(DictationKey.allCases) { key in
                    Text(key.title).tag(key)
                }
            }
            Picker("Hands-free", selection: $model.handsFreeKey) {
                Text("Double-tap \(model.key.title)").tag(Optional<DictationKey>.none)
                ForEach(DictationKey.allCases.filter { $0 != model.key }) { key in
                    Text(key.title).tag(Optional<DictationKey>.some(key))
                }
            }
            TextField("Language (blank for auto)", text: $model.language)
            VStack(alignment: .leading) {
                Text("Known words (one per line)")
                TextEditor(text: $knownWordsText)
                    .frame(height: 80)
                    .accessibilityLabel("Known words")
            }
            .onAppear { knownWordsText = model.knownWords.joined(separator: "\n") }
            .onChange(of: knownWordsText) { _, value in
                model.knownWords = value.components(separatedBy: .newlines)
                    .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
                    .filter { !$0.isEmpty }
            }
            Toggle("Sounds", isOn: $model.sounds)
            Toggle("Launch at login", isOn: Binding(
                get: { model.launchAtLogin },
                set: { model.setLaunchAtLogin($0) }
            ))
            HStack {
                Text(connectionText)
                Spacer()
                Button("Check connection") { Task { await model.checkReachability() } }
            }
        }
        .formStyle(.grouped)
        .frame(width: 480)
        .padding()
        .task {
            await model.refreshPermissions()
            await model.checkReachability()
        }
    }

    private var connectionText: String {
        switch model.serverReachable {
        case true: "Server reachable"
        case false: "Server unreachable"
        case nil: "Checking server…"
        }
    }
}
