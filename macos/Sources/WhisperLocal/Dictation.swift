import Foundation

enum DictationState: Equatable {
    case idle
    case listening
    case handsFree
    case transcribing
}

enum Cue {
    case start
    case stop
    case error
}

struct DictationServices {
    var startRecording: @MainActor () throws -> Void
    var stopRecording: @MainActor () throws -> Data
    var cancelRecording: @MainActor () -> Void
    var transcribe: @MainActor (Data) async throws -> String
    var paste: @MainActor (String) throws -> Void
    var cue: @MainActor (Cue) -> Void
    var clock: @MainActor () -> TimeInterval
}

@MainActor
final class Dictation {
    private let services: DictationServices
    private let minimumSeconds: TimeInterval
    private let doubleTapSeconds: TimeInterval
    private var startedAt: TimeInterval?
    private var isHandsFree = false
    private var handsFreeKeyIsDown = false
    private var lastShortRelease: TimeInterval?
    private var keyIsDown = false
    private var jobs: [Data] = []
    private var pendingJobs = 0
    private var worker: Task<Void, Never>?
    private var keyUpWaiter: CheckedContinuation<Void, Never>?

    var usesDedicatedHandsFreeKey = false {
        didSet {
            if usesDedicatedHandsFreeKey != oldValue {
                lastShortRelease = nil
                cancelRecording()
            }
        }
    }

    var enabled = true {
        didSet {
            if !enabled {
                lastShortRelease = nil
                cancelRecording()
            }
        }
    }
    private(set) var state: DictationState = .idle {
        didSet {
            if state != oldValue { onState(state) }
        }
    }
    var onState: @MainActor (DictationState) -> Void = { _ in }
    var onTranscript: @MainActor (String) -> Void = { _ in }
    var onError: @MainActor (Error) -> Void = { _ in }

    init(
        services: DictationServices,
        minimumSeconds: TimeInterval = 0.3,
        doubleTapSeconds: TimeInterval = 0.4
    ) {
        self.services = services
        self.minimumSeconds = minimumSeconds
        self.doubleTapSeconds = doubleTapSeconds
    }

    func pressed() {
        guard !keyIsDown else { return }
        keyIsDown = true
        if startedAt != nil, isHandsFree {
            if !usesDedicatedHandsFreeKey { finishRecording() }
            return
        }
        guard enabled, startedAt == nil else { return }
        let now = services.clock()
        let handsFree = !usesDedicatedHandsFreeKey &&
            (lastShortRelease.map { now >= $0 && now - $0 <= doubleTapSeconds } ?? false)
        lastShortRelease = nil
        startRecording(at: now, handsFree: handsFree)
    }

    func handsFreePressed() {
        guard !handsFreeKeyIsDown else { return }
        handsFreeKeyIsDown = true
        lastShortRelease = nil
        guard usesDedicatedHandsFreeKey else { return }
        if startedAt != nil {
            if isHandsFree { finishRecording() }
            else { cancelRecording() }
            return
        }
        guard enabled else { return }
        startRecording(at: services.clock(), handsFree: true)
    }

    func handsFreeReleased() {
        guard handsFreeKeyIsDown else { return }
        handsFreeKeyIsDown = false
        resumePasteIfKeysAreUp()
    }

    private func startRecording(at now: TimeInterval, handsFree: Bool) {
        do {
            try services.startRecording()
            startedAt = now
            isHandsFree = handsFree
            services.cue(.start)
            updateState()
        } catch {
            fail(error)
        }
    }

    func interrupted() {
        lastShortRelease = nil
        guard !isHandsFree else { return }
        cancelRecording()
    }

    func escaped() {
        lastShortRelease = nil
        cancelRecording()
    }

    func released() {
        guard keyIsDown else { return }
        keyIsDown = false
        resumePasteIfKeysAreUp()
        guard !isHandsFree else { return }
        guard let startedAt else { return }
        let releasedAt = services.clock()
        let held = releasedAt - startedAt
        if held < minimumSeconds {
            cancelRecording()
            if !usesDedicatedHandsFreeKey { lastShortRelease = releasedAt }
            return
        }
        finishRecording()
    }

    private func finishRecording() {
        guard startedAt != nil else { return }
        startedAt = nil
        isHandsFree = false
        do {
            let wav = try services.stopRecording()
            services.cue(.stop)
            jobs.append(wav)
            pendingJobs += 1
            updateState()
            startWorkerIfNeeded()
        } catch {
            fail(error)
            updateState()
        }
    }

    private func cancelRecording() {
        guard startedAt != nil else { return }
        startedAt = nil
        isHandsFree = false
        services.cancelRecording()
        updateState()
    }

    private func startWorkerIfNeeded() {
        guard worker == nil else { return }
        worker = Task { [weak self] in
            guard let self else { return }
            await self.drainJobs()
        }
    }

    private func drainJobs() async {
        while !jobs.isEmpty {
            let wav = jobs.removeFirst()
            do {
                let text = try await services.transcribe(wav)
                if !text.isEmpty {
                    onTranscript(text)
                    while keyIsDown || handsFreeKeyIsDown {
                        await withCheckedContinuation { keyUpWaiter = $0 }
                    }
                    try services.paste(text)
                }
            } catch {
                fail(error)
            }
            pendingJobs -= 1
            updateState()
        }
        worker = nil
    }

    private func updateState() {
        if startedAt != nil {
            state = isHandsFree ? .handsFree : .listening
        } else {
            state = pendingJobs > 0 ? .transcribing : .idle
        }
    }

    private func resumePasteIfKeysAreUp() {
        guard !keyIsDown, !handsFreeKeyIsDown else { return }
        keyUpWaiter?.resume()
        keyUpWaiter = nil
    }

    private func fail(_ error: Error) {
        services.cue(.error)
        onError(error)
    }
}
