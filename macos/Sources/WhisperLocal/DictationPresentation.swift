import SwiftUI

struct DictationStyle {
    let symbol: String
    let color: Color
    let showsRecordingDot: Bool

    static func current(state: DictationState, enabled: Bool, hasProblem: Bool) -> Self {
        if !enabled { return Self(symbol: "mic.slash", color: Theme.neutralMuted, showsRecordingDot: false) }
        if hasProblem { return Self(symbol: "mic.slash.fill", color: Theme.amber, showsRecordingDot: false) }
        return switch state {
        case .idle: Self(symbol: "mic", color: Theme.neutralStrong, showsRecordingDot: false)
        case .listening: Self(symbol: "mic.fill", color: Theme.red, showsRecordingDot: true)
        case .handsFree: Self(symbol: "mic.fill", color: Theme.red, showsRecordingDot: true)
        case .transcribing: Self(symbol: "mic.fill", color: Theme.blue, showsRecordingDot: false)
        }
    }
}
