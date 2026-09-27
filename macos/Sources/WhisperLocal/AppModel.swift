import AppKit
import ApplicationServices
import AVFoundation
import CoreGraphics
import ServiceManagement
import SwiftUI

enum SettingsPane: Equatable {
    case inputMonitoring
    case accessibility
    case microphone

    var url: URL? {
        let anchor: String
        switch self {
        case .inputMonitoring: anchor = "Privacy_ListenEvent"
        case .accessibility: anchor = "Privacy_Accessibility"
        case .microphone: anchor = "Privacy_Microphone"
        }
        return URL(string: "x-apple.systempreferences:com.apple.preference.security?\(anchor)")
    }
}

struct Problem: Equatable {
    let text: String
    let pane: SettingsPane?

    static func == (lhs: Self, rhs: Self) -> Bool {
        lhs.text == rhs.text && lhs.pane == rhs.pane
    }
}

enum AppIssue: LocalizedError {
    case inputMonitoring
    case accessibility
    case microphone
    case clipboard
    case keyboardEvent
    case eventTap
    case apiKey
    case launchAtLogin

    var errorDescription: String? {
        switch self {
        case .inputMonitoring: "Input Monitoring needed"
        case .accessibility: "Accessibility access needed"
        case .microphone: "Microphone access needed"
        case .clipboard: "Could not copy transcript"
        case .keyboardEvent: "Could not paste transcript"
        case .eventTap: "Could not monitor keyboard"
        case .apiKey: "Could not save API key"
        case .launchAtLogin: "Could not change launch at login"
        }
    }

    var pane: SettingsPane? {
        switch self {
        case .inputMonitoring, .eventTap: .inputMonitoring
        case .accessibility: .accessibility
        case .microphone: .microphone
        case .clipboard, .keyboardEvent, .apiKey, .launchAtLogin: nil
        }
    }
}

@MainActor
final class AppModel: ObservableObject {
    @Published private(set) var state: DictationState = .idle {
        didSet {
            if state != oldValue {
                level = 0
                lastLevelAt = nil
                updateOverlay()
            }
        }
    }
    @Published private(set) var recentTranscripts: [String] = []
    @Published private(set) var problem: Problem? = nil
    @Published private(set) var serverReachable: Bool? = nil
    @Published private(set) var launchAtLogin: Bool

    @Published var enabled: Bool {
        didSet {
            defaults.set(enabled, forKey: "enabled")
            dictation.enabled = enabled && permissionProblem == nil
        }
    }
    @Published var serverURL: String {
        didSet { defaults.set(serverURL, forKey: "serverURL") }
    }
    @Published var key: DictationKey {
        didSet {
            if handsFreeKey == key { handsFreeKey = nil }
            defaults.set(key.rawValue, forKey: "dictationKey")
            hotkey?.setKey(key)
            updateOverlay()
        }
    }
    @Published var handsFreeKey: DictationKey? {
        didSet {
            defaults.set(handsFreeKey?.rawValue ?? "", forKey: "handsFreeKey")
            hotkey?.setHandsFreeKey(handsFreeKey)
            dictation.usesDedicatedHandsFreeKey = handsFreeKey != nil
            updateOverlay()
        }
    }
    @Published var language: String {
        didSet { defaults.set(language, forKey: "language") }
    }
    @Published var knownWords: [String] {
        didSet { defaults.set(knownWords, forKey: "knownWords") }
    }
    @Published var sounds: Bool {
        didSet { defaults.set(sounds, forKey: "sounds") }
    }
    @Published private(set) var apiKey: String

    private let defaults = UserDefaults.standard
    private lazy var recorder = AudioRecorder(onLevel: { [weak self] level in
        Task { @MainActor [weak self] in self?.receiveLevel(level) }
    })
    private let client = TranscriptionClient()
    private var overlay: DictationOverlay?
    private var hotkey: HotkeyMonitor?
    private var permissionProblem: Problem?
    private var transientProblem: Problem?
    private var clearProblemTask: Task<Void, Never>?
    private var level = 0.0
    private var lastLevelAt: TimeInterval?
    private var started = false

    private lazy var dictation: Dictation = {
        let dictation = Dictation(services: DictationServices(
            startRecording: { [recorder] in try recorder.start() },
            stopRecording: { [recorder] in try recorder.stop() },
            cancelRecording: { [recorder] in recorder.cancel() },
            transcribe: { [weak self] wav in
                guard let self else { throw CancellationError() }
                return try await self.client.transcribe(
                    wav: wav, serverURL: self.serverURL,
                    apiKey: self.apiKey, language: self.language,
                    knownWords: self.knownWords
                )
            },
            paste: { text in try KeyboardPaste.paste(text) },
            cue: { [weak self] cue in self?.play(cue) },
            clock: { ProcessInfo.processInfo.systemUptime }
        ))
        dictation.enabled = enabled
        dictation.usesDedicatedHandsFreeKey = handsFreeKey != nil
        dictation.onState = { [weak self] in self?.state = $0 }
        dictation.onTranscript = { [weak self] text in
            guard let self else { return }
            self.recentTranscripts = Array(([text] + self.recentTranscripts).prefix(5))
        }
        dictation.onError = { [weak self] in self?.report($0) }
        return dictation
    }()

    init() {
        let stored = UserDefaults.standard
        enabled = stored.object(forKey: "enabled") as? Bool ?? true
        serverURL = stored.string(forKey: "serverURL") ?? "http://localhost:8000/v1"
        key = DictationKey(rawValue: stored.string(forKey: "dictationKey") ?? "") ?? .rightAlt
        let storedHandsFreeKey = DictationKey(rawValue: stored.string(forKey: "handsFreeKey") ?? "")
        handsFreeKey = storedHandsFreeKey == key ? nil : storedHandsFreeKey
        language = stored.string(forKey: "language") ?? ""
        knownWords = stored.stringArray(forKey: "knownWords") ?? []
        sounds = stored.object(forKey: "sounds") as? Bool ?? true
        apiKey = Keychain.apiKey() ?? ""
        let status = SMAppService.mainApp.status
        launchAtLogin = status == .enabled || status == .requiresApproval
        Task { [weak self] in await self?.start() }
    }

    func start() async {
        guard !started else { return }
        started = true
        _ = dictation
        overlay = DictationOverlay()
        updateOverlay()
        await refreshPermissions(prompt: true)
        await checkReachability()
    }

    func refreshPermissions(prompt: Bool = false) async {
        var canListen = CGPreflightListenEventAccess()
        if !canListen && prompt { canListen = CGRequestListenEventAccess() }

        let axOptions = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: prompt] as CFDictionary
        let canPost = AXIsProcessTrustedWithOptions(axOptions)

        let microphoneStatus = AVCaptureDevice.authorizationStatus(for: .audio)
        var canRecord = microphoneStatus == .authorized
        if microphoneStatus == .notDetermined && prompt {
            canRecord = await AVCaptureDevice.requestAccess(for: .audio)
        }

        if canListen {
            if hotkey == nil {
                hotkey = HotkeyMonitor(key: key, handsFreeKey: handsFreeKey) { [weak self] input in
                    guard let self else { return }
                    switch input {
                    case .pressed: self.dictation.pressed()
                    case .released: self.dictation.released()
                    case .handsFreePressed: self.dictation.handsFreePressed()
                    case .handsFreeReleased: self.dictation.handsFreeReleased()
                    case .otherKeyPressed: self.dictation.interrupted()
                    case .escapePressed:
                        self.dictation.escaped()
                        self.dictation.interrupted()
                    }
                }
            }
            if hotkey?.start() != true { permissionProblem = problem(for: .eventTap) }
            else if !canPost { permissionProblem = problem(for: .accessibility) }
            else if !canRecord { permissionProblem = problem(for: .microphone) }
            else { permissionProblem = nil }
        } else {
            hotkey?.stop()
            permissionProblem = problem(for: .inputMonitoring)
        }
        publishProblem()
        dictation.enabled = enabled && permissionProblem == nil
    }

    func checkReachability() async {
        serverReachable = await client.reachable(serverURL: serverURL)
    }

    func updateAPIKey(_ value: String) {
        do {
            try Keychain.setAPIKey(value)
            apiKey = value
        } catch {
            report(AppIssue.apiKey)
        }
    }

    func setLaunchAtLogin(_ value: Bool) {
        do {
            if value { try SMAppService.mainApp.register() }
            else { try SMAppService.mainApp.unregister() }
            let status = SMAppService.mainApp.status
            launchAtLogin = status == .enabled || status == .requiresApproval
        } catch {
            report(AppIssue.launchAtLogin)
        }
    }

    func copyTranscript(_ text: String) {
        NSPasteboard.general.clearContents()
        if !NSPasteboard.general.setString(text, forType: .string) {
            report(AppIssue.clipboard)
        }
    }

    func openProblemSettings() {
        guard let pane = problem?.pane, let url = pane.url else { return }
        NSWorkspace.shared.open(url)
    }

    private func report(_ error: Error) {
        let issue = error as? AppIssue
        let message: String
        switch error {
        case let known as AppIssue: message = known.localizedDescription
        case let known as SpeechError: message = known.localizedDescription
        case let known as RecordingError: message = known.localizedDescription
        default: message = "Operation failed"
        }
        if let issue, issue.pane != nil {
            permissionProblem = Problem(text: message, pane: issue.pane)
        }
        transientProblem = Problem(text: message, pane: nil)
        clearProblemTask?.cancel()
        clearProblemTask = Task { [weak self] in
            try? await Task.sleep(for: .seconds(5))
            guard !Task.isCancelled else { return }
            self?.transientProblem = nil
            self?.publishProblem()
        }
        publishProblem()
    }

    private func publishProblem() {
        problem = permissionProblem ?? transientProblem
        updateOverlay()
    }

    private func receiveLevel(_ newLevel: Double) {
        guard state == .listening || state == .handsFree else { return }
        let now = ProcessInfo.processInfo.systemUptime
        if let lastLevelAt, now - lastLevelAt < 0.04 { return }
        lastLevelAt = now
        level = newLevel
        updateOverlay()
    }

    private func updateOverlay() {
        overlay?.update(
            state: state, level: level, keyName: (handsFreeKey ?? key).title,
            problem: transientProblem?.text
        )
    }

    private func problem(for issue: AppIssue) -> Problem {
        Problem(text: issue.localizedDescription, pane: issue.pane)
    }

    private func play(_ cue: Cue) {
        guard sounds else { return }
        switch cue {
        case .start: playSound("Tink")
        case .stop: playSound("Pop")
        case .error:
            playSound("Basso")
            Task { [weak self] in
                try? await Task.sleep(nanoseconds: 220_000_000)
                self?.playSound("Basso")
            }
        }
    }

    private func playSound(_ name: String) {
        guard sounds else { return }
        (NSSound(named: NSSound.Name(name))?.copy() as? NSSound)?.play()
    }
}
