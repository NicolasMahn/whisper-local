import AVFoundation
import Foundation

enum RecordingError: LocalizedError {
    case alreadyRecording
    case notRecording
    case unsupportedFormat
    case conversionFailed
    case noAudio

    var errorDescription: String? {
        switch self {
        case .alreadyRecording: "Already recording"
        case .notRecording: "No recording in progress"
        case .unsupportedFormat: "Microphone format unsupported"
        case .conversionFailed: "Audio conversion failed"
        case .noAudio: "No microphone audio captured"
        }
    }
}

final class AudioRecorder {
    private final class Capture {
        private let lock = NSLock()
        private let callbacks = DispatchGroup()
        private let converter: AVAudioConverter
        private let outputFormat: AVAudioFormat
        private let inputRate: Double
        private let onLevel: @Sendable (Double) -> Void
        private var bytes = Data()
        private var failed = false
        private var finished = false

        init(
            converter: AVAudioConverter,
            outputFormat: AVAudioFormat,
            inputRate: Double,
            onLevel: @escaping @Sendable (Double) -> Void
        ) {
            self.converter = converter
            self.outputFormat = outputFormat
            self.inputRate = inputRate
            self.onLevel = onLevel
        }

        func receive(_ buffer: AVAudioPCMBuffer) {
            callbacks.enter()
            defer { callbacks.leave() }
            lock.lock()
            defer { lock.unlock() }
            guard !finished, !failed else { return }
            let capacity = AVAudioFrameCount(max(
                1_024,
                Int((Double(buffer.frameLength) * outputFormat.sampleRate / inputRate).rounded(.up)) + 64
            ))
            guard let output = AVAudioPCMBuffer(pcmFormat: outputFormat, frameCapacity: capacity) else {
                failed = true
                return
            }
            var supplied = false
            var conversionError: NSError?
            let status = converter.convert(to: output, error: &conversionError) { _, inputStatus in
                if supplied {
                    inputStatus.pointee = .noDataNow
                    return nil
                }
                supplied = true
                inputStatus.pointee = .haveData
                return buffer
            }
            if conversionError != nil || status == .error {
                failed = true
                return
            }
            if let level = Self.level(from: output) { onLevel(level) }
            append(output)
        }

        func finish() throws -> Data {
            // The engine has stopped; finish any tap conversion before flushing its tail.
            callbacks.wait()
            lock.lock()
            defer { lock.unlock() }
            finished = true
            if failed { throw RecordingError.conversionFailed }
            while true {
                guard let output = AVAudioPCMBuffer(pcmFormat: outputFormat, frameCapacity: 1_024) else {
                    throw RecordingError.conversionFailed
                }
                var conversionError: NSError?
                let status = converter.convert(to: output, error: &conversionError) { _, inputStatus in
                    inputStatus.pointee = .endOfStream
                    return nil
                }
                if conversionError != nil || status == .error { throw RecordingError.conversionFailed }
                append(output)
                if status == .endOfStream { break }
                guard status == .haveData, output.frameLength > 0 else { throw RecordingError.conversionFailed }
            }
            if bytes.isEmpty { throw RecordingError.noAudio }
            return bytes
        }

        private func append(_ output: AVAudioPCMBuffer) {
            if output.frameLength > 0, let samples = output.int16ChannelData?[0] {
                bytes.append(Data(bytes: samples, count: Int(output.frameLength) * MemoryLayout<Int16>.size))
            }
        }

        private static func level(from buffer: AVAudioPCMBuffer) -> Double? {
            guard buffer.frameLength > 0, let samples = buffer.int16ChannelData?[0] else { return nil }
            let count = Int(buffer.frameLength)
            var sumOfSquares = 0.0
            for index in 0..<count {
                let sample = Double(samples[index]) / 32_768
                sumOfSquares += sample * sample
            }
            let rms = sqrt(sumOfSquares / Double(count))
            let decibels = 20 * log10(max(rms, 1e-9))
            return min(1, max(0, (decibels + 60) / 50))
        }
    }

    private let onLevel: @Sendable (Double) -> Void
    private var engine: AVAudioEngine?
    private var capture: Capture?

    init(onLevel: @escaping @Sendable (Double) -> Void = { _ in }) {
        self.onLevel = onLevel
    }

    func start() throws {
        guard engine == nil else { throw RecordingError.alreadyRecording }
        let engine = AVAudioEngine()
        let input = engine.inputNode
        let inputFormat = input.outputFormat(forBus: 0)
        guard inputFormat.sampleRate > 0,
              let outputFormat = AVAudioFormat(
                commonFormat: .pcmFormatInt16, sampleRate: 16_000,
                channels: 1, interleaved: false
              ),
              let converter = AVAudioConverter(from: inputFormat, to: outputFormat)
        else { throw RecordingError.unsupportedFormat }

        let capture = Capture(
            converter: converter, outputFormat: outputFormat,
            inputRate: inputFormat.sampleRate, onLevel: onLevel
        )
        input.installTap(onBus: 0, bufferSize: 4_096, format: inputFormat) { buffer, _ in
            capture.receive(buffer)
        }
        do {
            try engine.start()
            self.engine = engine
            self.capture = capture
        } catch {
            input.removeTap(onBus: 0)
            engine.stop()
            throw error
        }
    }

    func stop() throws -> Data {
        guard let engine, let capture else { throw RecordingError.notRecording }
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        self.engine = nil
        self.capture = nil
        return WAV.encode(pcm16: try capture.finish())
    }

    func cancel() {
        guard let engine else { return }
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        self.engine = nil
        capture = nil
    }
}
