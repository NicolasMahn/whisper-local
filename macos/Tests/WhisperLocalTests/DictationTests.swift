import XCTest
@testable import WhisperLocal

@MainActor
final class DictationTests: XCTestCase {
    func testShortHoldDoesNotUpload() async {
        var now: TimeInterval = 0
        var starts = 0
        var cancels = 0
        var uploads = 0
        let dictation = Dictation(services: DictationServices(
            startRecording: { starts += 1 },
            stopRecording: { XCTFail("short hold stopped"); return Data([1]) },
            cancelRecording: { cancels += 1 },
            transcribe: { _ in uploads += 1; return "hello" },
            paste: { _ in XCTFail("short hold pasted") },
            cue: { _ in },
            clock: { now }
        ))

        dictation.pressed()
        XCTAssertEqual(dictation.state, .listening)
        now = 0.29
        dictation.released()
        await Task.yield()

        XCTAssertEqual(starts, 1)
        XCTAssertEqual(cancels, 1)
        XCTAssertEqual(uploads, 0)
        XCTAssertEqual(dictation.state, .idle)
    }

    func testAnotherKeyCancelsAndClosesRecording() {
        var cancels = 0
        let dictation = Dictation(services: DictationServices(
            startRecording: {},
            stopRecording: { XCTFail("cancelled recording stopped"); return Data() },
            cancelRecording: { cancels += 1 },
            transcribe: { _ in XCTFail("cancelled recording uploaded"); return "" },
            paste: { _ in XCTFail("cancelled recording pasted") },
            cue: { _ in },
            clock: { 1 }
        ))

        dictation.pressed()
        dictation.interrupted()
        dictation.released()

        XCTAssertEqual(cancels, 1)
        XCTAssertEqual(dictation.state, .idle)
    }

    func testDisablingCancelsAndPreventsAnotherRecording() {
        var starts = 0
        var cancels = 0
        let dictation = Dictation(services: DictationServices(
            startRecording: { starts += 1 },
            stopRecording: { XCTFail("disabled recording stopped"); return Data() },
            cancelRecording: { cancels += 1 },
            transcribe: { _ in XCTFail("disabled recording uploaded"); return "" },
            paste: { _ in XCTFail("disabled recording pasted") },
            cue: { _ in },
            clock: { 1 }
        ))

        dictation.pressed()
        dictation.enabled = false
        dictation.released()
        dictation.pressed()
        dictation.released()

        XCTAssertEqual(starts, 1)
        XCTAssertEqual(cancels, 1)
        XCTAssertEqual(dictation.state, .idle)
    }

    func testJobsPasteInOrderAfterKeyUp() async {
        var now: TimeInterval = 0
        var recording = 0
        var pasted: [String] = []
        let firstTranscribed = expectation(description: "first transcript ready")
        let bothPasted = expectation(description: "both jobs pasted")
        bothPasted.expectedFulfillmentCount = 2
        let dictation = Dictation(services: DictationServices(
            startRecording: { recording += 1 },
            stopRecording: { Data([UInt8(recording)]) },
            cancelRecording: {},
            transcribe: { wav in
                if wav == Data([1]) { firstTranscribed.fulfill() }
                return wav == Data([1]) ? "first" : "second"
            },
            paste: { text in
                pasted.append(text)
                bothPasted.fulfill()
            },
            cue: { _ in },
            clock: { now }
        ))

        dictation.pressed()
        now = 0.4
        dictation.released()
        dictation.pressed()
        await fulfillment(of: [firstTranscribed], timeout: 1)
        XCTAssertTrue(pasted.isEmpty)

        now = 0.8
        dictation.released()
        await fulfillment(of: [bothPasted], timeout: 1)
        XCTAssertEqual(pasted, ["first", "second"])
    }

    func testDoubleTapRecordsHandsFreeUntilNextPressAndWaitsForKeyUpToPaste() async {
        var now: TimeInterval = 0
        var starts = 0
        var cancels = 0
        var stops = 0
        var pasted: [String] = []
        let transcribed = expectation(description: "hands-free transcript ready")
        let pastedExpectation = expectation(description: "hands-free transcript pasted")
        let dictation = Dictation(services: DictationServices(
            startRecording: { starts += 1 },
            stopRecording: { stops += 1; return Data([1]) },
            cancelRecording: { cancels += 1 },
            transcribe: { _ in transcribed.fulfill(); return "ramble" },
            paste: { text in pasted.append(text); pastedExpectation.fulfill() },
            cue: { _ in },
            clock: { now }
        ))

        dictation.pressed()
        now = 0.1
        dictation.released()
        now = 0.35
        dictation.pressed()
        XCTAssertEqual(dictation.state, .handsFree)
        dictation.released()
        dictation.interrupted()
        XCTAssertEqual(dictation.state, .handsFree)
        XCTAssertEqual(stops, 0)

        now = 1.5
        dictation.pressed()
        XCTAssertEqual(dictation.state, .transcribing)
        XCTAssertEqual(stops, 1)
        await fulfillment(of: [transcribed], timeout: 1)
        XCTAssertTrue(pasted.isEmpty)
        dictation.released()
        await fulfillment(of: [pastedExpectation], timeout: 1)

        XCTAssertEqual(starts, 2)
        XCTAssertEqual(cancels, 1)
        XCTAssertEqual(pasted, ["ramble"])
        XCTAssertEqual(dictation.state, .idle)
    }

    func testEscapeCancelsHeldRecordingIdempotently() {
        var cancels = 0
        let dictation = Dictation(services: DictationServices(
            startRecording: {},
            stopRecording: { XCTFail("cancelled recording stopped"); return Data() },
            cancelRecording: { cancels += 1 },
            transcribe: { _ in XCTFail("cancelled recording uploaded"); return "" },
            paste: { _ in XCTFail("cancelled recording pasted") },
            cue: { _ in },
            clock: { 1 }
        ))

        dictation.pressed()
        dictation.escaped()
        dictation.interrupted()
        dictation.released()

        XCTAssertEqual(cancels, 1)
        XCTAssertEqual(dictation.state, .idle)
    }

    func testEscapeCancelsHandsFreeWithKeyUpOrDown() {
        var now: TimeInterval = 0
        var cancels = 0
        let dictation = Dictation(services: DictationServices(
            startRecording: {},
            stopRecording: { XCTFail("cancelled recording stopped"); return Data() },
            cancelRecording: { cancels += 1 },
            transcribe: { _ in XCTFail("cancelled recording uploaded"); return "" },
            paste: { _ in XCTFail("cancelled recording pasted") },
            cue: { _ in },
            clock: { now }
        ))

        dictation.pressed()
        now = 0.1
        dictation.released()
        now = 0.2
        dictation.pressed()
        XCTAssertEqual(dictation.state, .handsFree)
        dictation.escaped()
        dictation.interrupted()
        dictation.released()
        XCTAssertEqual(dictation.state, .idle)

        now = 1
        dictation.pressed()
        now = 1.1
        dictation.released()
        now = 1.2
        dictation.pressed()
        dictation.released()
        XCTAssertEqual(dictation.state, .handsFree)
        dictation.escaped()
        dictation.interrupted()

        XCTAssertEqual(cancels, 4)
        XCTAssertEqual(dictation.state, .idle)
    }

    func testSecondPressAfterWindowStartsNormalHold() {
        var now: TimeInterval = 0
        var stops = 0
        let dictation = Dictation(services: DictationServices(
            startRecording: {},
            stopRecording: { stops += 1; return Data([1]) },
            cancelRecording: {},
            transcribe: { _ in "" },
            paste: { _ in },
            cue: { _ in },
            clock: { now }
        ))

        dictation.pressed()
        now = 0.1
        dictation.released()
        now = 0.51
        dictation.pressed()
        XCTAssertEqual(dictation.state, .listening)
        now = 0.9
        dictation.released()

        XCTAssertEqual(stops, 1)
    }

    func testDedicatedKeyTogglesHandsFreeAndWaitsForItsReleaseToPaste() async {
        var starts = 0
        var stops = 0
        var cancels = 0
        var pasted: [String] = []
        let transcribed = expectation(description: "dedicated transcript ready")
        let pastedExpectation = expectation(description: "dedicated transcript pasted")
        let dictation = Dictation(services: DictationServices(
            startRecording: { starts += 1 },
            stopRecording: { stops += 1; return Data([1]) },
            cancelRecording: { cancels += 1 },
            transcribe: { _ in transcribed.fulfill(); return "ramble" },
            paste: { text in pasted.append(text); pastedExpectation.fulfill() },
            cue: { _ in },
            clock: { 1 }
        ))
        dictation.usesDedicatedHandsFreeKey = true

        dictation.handsFreePressed()
        XCTAssertEqual(dictation.state, .handsFree)
        dictation.handsFreeReleased()
        dictation.pressed()
        dictation.released()
        dictation.interrupted()
        XCTAssertEqual(dictation.state, .handsFree)

        dictation.handsFreePressed()
        XCTAssertEqual(dictation.state, .transcribing)
        XCTAssertEqual(stops, 1)
        await fulfillment(of: [transcribed], timeout: 1)
        XCTAssertTrue(pasted.isEmpty)
        dictation.handsFreeReleased()
        await fulfillment(of: [pastedExpectation], timeout: 1)

        XCTAssertEqual(starts, 1)
        XCTAssertEqual(cancels, 0)
        XCTAssertEqual(pasted, ["ramble"])
        XCTAssertEqual(dictation.state, .idle)
    }

    func testDedicatedKeyDisablesDoubleTap() {
        var now: TimeInterval = 0
        var starts = 0
        var cancels = 0
        let dictation = Dictation(services: DictationServices(
            startRecording: { starts += 1 },
            stopRecording: { XCTFail("short hold stopped"); return Data() },
            cancelRecording: { cancels += 1 },
            transcribe: { _ in XCTFail("short hold uploaded"); return "" },
            paste: { _ in XCTFail("short hold pasted") },
            cue: { _ in },
            clock: { now }
        ))
        dictation.usesDedicatedHandsFreeKey = true

        dictation.pressed()
        now = 0.1
        dictation.released()
        now = 0.2
        dictation.pressed()
        XCTAssertEqual(dictation.state, .listening)
        now = 0.3
        dictation.released()

        XCTAssertEqual(starts, 2)
        XCTAssertEqual(cancels, 2)
        XCTAssertEqual(dictation.state, .idle)
    }

    func testDedicatedKeyCancelsHeldRecordingAndEscapeCancelsHandsFree() {
        var cancels = 0
        let dictation = Dictation(services: DictationServices(
            startRecording: {},
            stopRecording: { XCTFail("cancelled recording stopped"); return Data() },
            cancelRecording: { cancels += 1 },
            transcribe: { _ in XCTFail("cancelled recording uploaded"); return "" },
            paste: { _ in XCTFail("cancelled recording pasted") },
            cue: { _ in },
            clock: { 1 }
        ))
        dictation.usesDedicatedHandsFreeKey = true

        dictation.pressed()
        dictation.handsFreePressed()
        XCTAssertEqual(dictation.state, .idle)
        dictation.interrupted()
        dictation.handsFreeReleased()
        dictation.released()

        dictation.handsFreePressed()
        dictation.handsFreeReleased()
        XCTAssertEqual(dictation.state, .handsFree)
        dictation.escaped()
        dictation.interrupted()

        XCTAssertEqual(cancels, 2)
        XCTAssertEqual(dictation.state, .idle)
    }
}
