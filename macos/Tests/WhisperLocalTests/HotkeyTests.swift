import CoreGraphics
import XCTest
@testable import WhisperLocal

final class HotkeyTests: XCTestCase {
    func testRightOptionWorksWhileLeftOptionIsHeld() {
        var hotkey = HotkeyInterpreter(selectedKey: .rightAlt)
        XCTAssertEqual(hotkey.receive(kind: .flagsChanged, code: 61, flags: 0x20 | 0x40), .pressed)
        XCTAssertEqual(hotkey.receive(kind: .flagsChanged, code: 61, flags: 0x20), .released)
    }

    func testOtherModifierDownInterruptsButItsReleaseDoesNot() {
        var hotkey = HotkeyInterpreter(selectedKey: .rightAlt)
        XCTAssertEqual(hotkey.receive(kind: .flagsChanged, code: 61, flags: 0x40), .pressed)
        XCTAssertEqual(hotkey.receive(kind: .flagsChanged, code: 59, flags: 0x41), .otherKeyPressed)
        XCTAssertNil(hotkey.receive(kind: .flagsChanged, code: 59, flags: 0x40))
    }

    func testFunctionKeyRepeatIsIgnored() {
        var hotkey = HotkeyInterpreter(selectedKey: .f13)
        XCTAssertEqual(hotkey.receive(kind: .keyDown, code: 105, flags: 0), .pressed)
        XCTAssertNil(hotkey.receive(kind: .keyDown, code: 105, flags: 0, repeated: true))
        XCTAssertEqual(hotkey.receive(kind: .keyUp, code: 105, flags: 0), .released)
    }

    func testFnDownInterruptsButItsReleaseDoesNot() {
        var hotkey = HotkeyInterpreter(selectedKey: .rightAlt)
        let fn = CGEventFlags.maskSecondaryFn.rawValue
        XCTAssertEqual(hotkey.receive(kind: .flagsChanged, code: 61, flags: 0x40), .pressed)
        XCTAssertEqual(hotkey.receive(kind: .flagsChanged, code: 63, flags: 0x40 | fn), .otherKeyPressed)
        XCTAssertNil(hotkey.receive(kind: .flagsChanged, code: 63, flags: 0x40))
    }

    func testCapsLockInterruptsOnEitherToggle() {
        var hotkey = HotkeyInterpreter(selectedKey: .rightAlt)
        XCTAssertEqual(hotkey.receive(kind: .flagsChanged, code: 61, flags: 0x40), .pressed)
        XCTAssertEqual(hotkey.receive(kind: .flagsChanged, code: 57, flags: 0x40 | CGEventFlags.maskAlphaShift.rawValue), .otherKeyPressed)
        XCTAssertEqual(hotkey.receive(kind: .flagsChanged, code: 57, flags: 0x40), .otherKeyPressed)
    }

    func testEscapeIsReportedWhetherDictationKeyIsHeldOrNot() {
        var hotkey = HotkeyInterpreter(selectedKey: .rightAlt)
        XCTAssertEqual(hotkey.receive(kind: .keyDown, code: 53, flags: 0), .escapePressed)
        XCTAssertNil(hotkey.receive(kind: .keyDown, code: 53, flags: 0, repeated: true))
        XCTAssertEqual(hotkey.receive(kind: .flagsChanged, code: 61, flags: 0x40), .pressed)
        XCTAssertEqual(hotkey.receive(kind: .keyDown, code: 53, flags: 0x40), .escapePressed)
        XCTAssertEqual(hotkey.receive(kind: .flagsChanged, code: 61, flags: 0), .released)
    }

    func testDedicatedModifierHasIndependentPressAndRelease() {
        var hotkey = HotkeyInterpreter(selectedKey: .rightAlt, handsFreeKey: .rightControl)
        XCTAssertEqual(hotkey.receive(kind: .flagsChanged, code: 62, flags: 0x2000), .handsFreePressed)
        XCTAssertNil(hotkey.receive(kind: .flagsChanged, code: 62, flags: 0x2000))
        XCTAssertEqual(hotkey.receive(kind: .flagsChanged, code: 61, flags: 0x2040), .pressed)
        XCTAssertEqual(hotkey.receive(kind: .flagsChanged, code: 62, flags: 0x40), .handsFreeReleased)
        XCTAssertEqual(hotkey.receive(kind: .flagsChanged, code: 61, flags: 0), .released)
    }

    func testDedicatedFunctionKeyIgnoresRepeat() {
        var hotkey = HotkeyInterpreter(selectedKey: .rightAlt, handsFreeKey: .f13)
        XCTAssertEqual(hotkey.receive(kind: .keyDown, code: 105, flags: 0), .handsFreePressed)
        XCTAssertNil(hotkey.receive(kind: .keyDown, code: 105, flags: 0, repeated: true))
        XCTAssertEqual(hotkey.receive(kind: .keyUp, code: 105, flags: 0), .handsFreeReleased)
        XCTAssertEqual(hotkey.receive(kind: .keyDown, code: 105, flags: 0), .handsFreePressed)
    }
}
