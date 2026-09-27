import XCTest
@testable import WhisperLocal

final class EncodingTests: XCTestCase {
    func testWAVHeaderAndSamples() {
        let samples = Data([0x34, 0x12, 0xFE, 0xFF])
        let wav = WAV.encode(pcm16: samples)
        XCTAssertEqual(wav.count, 48)
        XCTAssertEqual(Array(wav[0..<12]), Array("RIFF".utf8) + [40, 0, 0, 0] + Array("WAVE".utf8))
        XCTAssertEqual(Array(wav[12..<36]), Array("fmt ".utf8) + [
            16, 0, 0, 0, 1, 0, 1, 0, 0x80, 0x3E, 0, 0,
            0, 0x7D, 0, 0, 2, 0, 16, 0,
        ])
        XCTAssertEqual(Array(wav[36..<44]), Array("data".utf8) + [4, 0, 0, 0])
        XCTAssertEqual(Data(wav[44...]), samples)
    }

    func testMultipartBytesAndOptionalFields() {
        let wav = Data([0, 13, 10, 255])
        let body = Multipart.body(wav: wav, language: "fr", knownWords: ["Qwen", "Whisper Local"], boundary: "BOUNDARY")
        var expected = Data("--BOUNDARY\r\nContent-Disposition: form-data; name=\"file\"; filename=\"dictation.wav\"\r\nContent-Type: audio/wav\r\n\r\n".utf8)
        expected.append(wav)
        expected.append(Data("\r\n--BOUNDARY\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\nqwen3-asr\r\n--BOUNDARY\r\nContent-Disposition: form-data; name=\"language\"\r\n\r\nfr\r\n--BOUNDARY\r\nContent-Disposition: form-data; name=\"prompt\"\r\n\r\nQwen, Whisper Local\r\n--BOUNDARY--\r\n".utf8))
        XCTAssertEqual(body, expected)

        let automatic = Multipart.body(wav: wav, language: "", knownWords: [], boundary: "BOUNDARY")
        XCTAssertNil(automatic.range(of: Data("name=\"language\"".utf8)))
        XCTAssertNil(automatic.range(of: Data("name=\"prompt\"".utf8)))
    }
}
