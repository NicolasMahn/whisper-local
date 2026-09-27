import AppKit
import Foundation
import SwiftUI

@MainActor
private final class OverlayDisplay: ObservableObject {
    @Published var state: DictationState = .idle
    @Published var level = 0.0
    @Published var keyName = "Right Option"
    @Published var problem: String?
}

@MainActor
final class DictationOverlay {
    private let display = OverlayDisplay()
    private let panel: NSPanel
    private var visible = false
    private var visibilityChange = 0

    init() {
        let size = NSSize(width: 440, height: 72)
        panel = NSPanel(
            contentRect: NSRect(origin: .zero, size: size),
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )
        panel.isOpaque = false
        panel.backgroundColor = Theme.overlayWindowBackground
        panel.hasShadow = false
        panel.level = .statusBar
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.hidesOnDeactivate = false
        panel.ignoresMouseEvents = true
        panel.contentView = NSHostingView(rootView: OverlayView(display: display).frame(width: size.width, height: size.height))
    }

    func update(state: DictationState, level: Double, keyName: String, problem: String?) {
        if display.state != state { display.state = state }
        display.level = state == .listening || state == .handsFree
            ? display.level * 0.65 + level * 0.35 : 0
        if display.keyName != keyName { display.keyName = keyName }
        if display.problem != problem { display.problem = problem }

        let shouldShow = state != .idle || problem != nil
        guard shouldShow != visible else { return }
        visible = shouldShow
        visibilityChange += 1
        let change = visibilityChange
        if shouldShow {
            position()
            panel.alphaValue = 0
            panel.orderFrontRegardless()
            NSAnimationContext.runAnimationGroup { context in
                context.duration = 0.15
                self.panel.animator().alphaValue = 1
            }
        } else {
            NSAnimationContext.runAnimationGroup({ context in
                context.duration = 0.15
                self.panel.animator().alphaValue = 0
            }, completionHandler: { [weak self] in
                Task { @MainActor [weak self] in
                    guard let self, !self.visible, self.visibilityChange == change else { return }
                    self.panel.orderOut(nil)
                }
            })
        }
    }

    private func position() {
        guard let screen = NSScreen.screens.first ?? NSScreen.main else { return }
        let frame = screen.visibleFrame
        panel.setFrameOrigin(NSPoint(
            x: frame.midX - panel.frame.width / 2,
            y: frame.minY + 18
        ))
    }
}

private struct OverlayView: View {
    @ObservedObject var display: OverlayDisplay

    var body: some View {
        HStack(spacing: 12) {
            if let problem = display.problem {
                Image(systemName: "exclamationmark.circle.fill")
                    .foregroundStyle(DictationStyle.current(
                        state: display.state, enabled: true, hasProblem: true
                    ).color)
                Text(problem)
                    .foregroundStyle(Theme.neutralStrong)
                    .lineLimit(1)
            } else {
                switch display.state {
                case .listening, .handsFree:
                    LevelBars(level: display.level, color: DictationStyle.current(
                        state: display.state, enabled: true, hasProblem: false
                    ).color)
                    if display.state == .handsFree {
                        Text("Tap \(display.keyName) to finish · Esc to cancel")
                            .font(.system(size: 12))
                            .foregroundStyle(Theme.neutralStrong)
                            .lineLimit(1)
                    }
                case .transcribing:
                    BusyDots(color: DictationStyle.current(
                        state: .transcribing, enabled: true, hasProblem: false
                    ).color)
                case .idle:
                    EmptyView()
                }
            }
        }
        .padding(.horizontal, 18)
        .padding(.vertical, 12)
        .background(.ultraThinMaterial, in: Capsule())
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

private struct LevelBars: View {
    let level: Double
    let color: Color
    private let weights = [0.42, 0.68, 0.86, 1.0, 0.82, 0.62, 0.38]

    var body: some View {
        HStack(alignment: .center, spacing: 4) {
            ForEach(weights.indices, id: \.self) { index in
                Capsule()
                    .fill(color)
                    .frame(width: 4, height: 4 + 25 * level * weights[index])
            }
        }
        .frame(height: 30)
        .animation(.easeOut(duration: 0.12), value: level)
        .accessibilityLabel("Audio level")
    }
}

private struct BusyDots: View {
    let color: Color

    var body: some View {
        TimelineView(.animation(minimumInterval: 0.16)) { context in
            let phase = context.date.timeIntervalSinceReferenceDate * 3
            HStack(spacing: 6) {
                ForEach(0..<3) { index in
                    Circle()
                        .fill(color)
                        .frame(width: 6, height: 6)
                        .opacity(0.3 + 0.7 * (1 + sin(phase - Double(index) * 0.8)) / 2)
                }
            }
        }
        .frame(height: 30)
        .accessibilityLabel("Transcribing")
    }
}
