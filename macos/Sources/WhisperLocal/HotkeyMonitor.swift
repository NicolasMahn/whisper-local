import AppKit
import ApplicationServices
import CoreGraphics
import Foundation

enum DictationKey: String, CaseIterable, Identifiable {
    case rightAlt = "right_alt"
    case rightCommand = "right_cmd"
    case rightControl = "right_ctrl"
    case rightShift = "right_shift"
    case f13, f14, f15

    var id: String { rawValue }

    var title: String {
        switch self {
        case .rightAlt: "Right Option"
        case .rightCommand: "Right Command"
        case .rightControl: "Right Control"
        case .rightShift: "Right Shift"
        case .f13: "F13"
        case .f14: "F14"
        case .f15: "F15"
        }
    }

    var keyCode: Int {
        switch self {
        case .rightAlt: 61
        case .rightCommand: 54
        case .rightControl: 62
        case .rightShift: 60
        case .f13: 105
        case .f14: 107
        case .f15: 113
        }
    }

    var sideFlag: UInt64? {
        switch self {
        case .rightAlt: 0x40
        case .rightCommand: 0x10
        case .rightControl: 0x2000
        case .rightShift: 0x04
        case .f13, .f14, .f15: nil
        }
    }
}

enum HotkeyInput: Equatable {
    case pressed
    case released
    case handsFreePressed
    case handsFreeReleased
    case otherKeyPressed
    case escapePressed
}

enum HotkeyEventKind {
    case flagsChanged
    case keyDown
    case keyUp
}

struct HotkeyInterpreter {
    var selectedKey: DictationKey
    var handsFreeKey: DictationKey?
    private var activeDictationKey: DictationKey?
    private var activeHandsFreeKey: DictationKey?

    init(selectedKey: DictationKey, handsFreeKey: DictationKey? = nil) {
        self.selectedKey = selectedKey
        self.handsFreeKey = handsFreeKey
    }

    mutating func receive(kind: HotkeyEventKind, code: Int, flags: UInt64, repeated: Bool = false) -> HotkeyInput? {
        if code == 53, kind == .keyDown {
            return repeated ? nil : .escapePressed
        }
        if let key = activeDictationKey, code == key.keyCode {
            if key.isReleased(kind: kind, flags: flags) {
                activeDictationKey = nil
                return .released
            }
            return nil
        }
        if let key = activeHandsFreeKey, code == key.keyCode {
            if key.isReleased(kind: kind, flags: flags) {
                activeHandsFreeKey = nil
                return .handsFreeReleased
            }
            return nil
        }
        if code == selectedKey.keyCode {
            if selectedKey.isPressed(kind: kind, flags: flags, repeated: repeated) {
                activeDictationKey = selectedKey
                return .pressed
            }
            return nil
        }
        if let handsFreeKey, code == handsFreeKey.keyCode {
            if handsFreeKey.isPressed(kind: kind, flags: flags, repeated: repeated) {
                activeHandsFreeKey = handsFreeKey
                return .handsFreePressed
            }
            return nil
        }
        if kind == .keyDown {
            return .otherKeyPressed
        }
        if kind == .flagsChanged, code == 57 {
            // Caps Lock toggles its flag on each press, including when turning it off.
            return .otherKeyPressed
        }
        if kind == .flagsChanged, let sideFlag = Self.modifierSideFlags[code],
           flags & sideFlag != 0 {
            return .otherKeyPressed
        }
        return nil
    }

    private static let modifierSideFlags: [Int: UInt64] = [
        61: 0x40, 54: 0x10, 62: 0x2000, 60: 0x04,
        58: 0x20, 55: 0x08, 59: 0x01, 56: 0x02,
        63: CGEventFlags.maskSecondaryFn.rawValue,
    ]
}

private extension DictationKey {
    func isPressed(kind: HotkeyEventKind, flags: UInt64, repeated: Bool) -> Bool {
        if let sideFlag { return kind == .flagsChanged && flags & sideFlag != 0 }
        return kind == .keyDown && !repeated
    }

    func isReleased(kind: HotkeyEventKind, flags: UInt64) -> Bool {
        if let sideFlag { return kind == .flagsChanged && flags & sideFlag == 0 }
        return kind == .keyUp
    }
}

private let syntheticEventTag: Int64 = 0x57484C5041535445

final class HotkeyMonitor {
    private var tap: CFMachPort?
    private var source: CFRunLoopSource?
    private var interpreter: HotkeyInterpreter
    private let onInput: @MainActor (HotkeyInput) -> Void

    init(key: DictationKey, handsFreeKey: DictationKey? = nil, onInput: @escaping @MainActor (HotkeyInput) -> Void) {
        interpreter = HotkeyInterpreter(selectedKey: key, handsFreeKey: handsFreeKey)
        self.onInput = onInput
    }

    func setKey(_ key: DictationKey) { interpreter.selectedKey = key }
    func setHandsFreeKey(_ key: DictationKey?) { interpreter.handsFreeKey = key }

    func start() -> Bool {
        guard tap == nil else { return true }
        let mask: CGEventMask = [CGEventType.flagsChanged, .keyDown, .keyUp]
            .reduce(0) { $0 | (CGEventMask(1) << $1.rawValue) }
        guard let tap = CGEvent.tapCreate(
            tap: .cgSessionEventTap,
            place: .headInsertEventTap,
            options: .listenOnly,
            eventsOfInterest: mask,
            callback: hotkeyCallback,
            userInfo: Unmanaged.passUnretained(self).toOpaque()
        ) else { return false }
        guard let source = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, tap, 0) else { return false }
        CFRunLoopAddSource(CFRunLoopGetMain(), source, kCFRunLoopCommonModes)
        CGEvent.tapEnable(tap: tap, enable: true)
        self.tap = tap
        self.source = source
        return true
    }

    func stop() {
        if let source { CFRunLoopRemoveSource(CFRunLoopGetMain(), source, kCFRunLoopCommonModes) }
        if let tap { CGEvent.tapEnable(tap: tap, enable: false) }
        source = nil
        tap = nil
    }

    fileprivate func reenable() {
        if let tap { CGEvent.tapEnable(tap: tap, enable: true) }
    }

    fileprivate func receive(type: CGEventType, event: CGEvent) {
        guard event.getIntegerValueField(.eventSourceUserData) != syntheticEventTag else { return }
        let code = Int(event.getIntegerValueField(.keyboardEventKeycode))
        let flags = event.flags.rawValue
        let kind: HotkeyEventKind
        switch type {
        case .flagsChanged: kind = .flagsChanged
        case .keyDown: kind = .keyDown
        case .keyUp: kind = .keyUp
        default: return
        }
        let input = interpreter.receive(
            kind: kind, code: code, flags: flags,
            repeated: event.getIntegerValueField(.keyboardEventAutorepeat) != 0
        )
        // The tap is on the main run loop; update key state before another task resumes.
        if let input { MainActor.assumeIsolated { onInput(input) } }
    }

    deinit { stop() }
}

private let hotkeyCallback: CGEventTapCallBack = { _, type, event, userInfo in
    guard let userInfo else { return Unmanaged.passUnretained(event) }
    let monitor = Unmanaged<HotkeyMonitor>.fromOpaque(userInfo).takeUnretainedValue()
    if type == .tapDisabledByTimeout {
        monitor.reenable()
    } else if type == .flagsChanged || type == .keyDown || type == .keyUp {
        monitor.receive(type: type, event: event)
    }
    return Unmanaged.passUnretained(event)
}

enum KeyboardPaste {
    static func paste(_ text: String) throws {
        guard AXIsProcessTrusted() else { throw AppIssue.accessibility }
        let clipboard = NSPasteboard.general
        clipboard.clearContents()
        guard clipboard.setString(text, forType: .string) else { throw AppIssue.clipboard }
        for down in [true, false] {
            guard let event = CGEvent(keyboardEventSource: nil, virtualKey: 9, keyDown: down) else {
                throw AppIssue.keyboardEvent
            }
            event.flags = .maskCommand
            event.setIntegerValueField(.eventSourceUserData, value: syntheticEventTag)
            event.post(tap: .cghidEventTap)
        }
    }
}
