import Foundation

enum Multipart {
    static func body(wav: Data, language: String, knownWords: [String], boundary: String) -> Data {
        var body = Data()
        func field(_ name: String, _ value: String) {
            body.append(contentsOf: "--\(boundary)\r\nContent-Disposition: form-data; name=\"\(name)\"\r\n\r\n\(value)\r\n".utf8)
        }
        body.append(contentsOf: "--\(boundary)\r\nContent-Disposition: form-data; name=\"file\"; filename=\"dictation.wav\"\r\nContent-Type: audio/wav\r\n\r\n".utf8)
        body.append(wav)
        body.append(contentsOf: "\r\n".utf8)
        field("model", "qwen3-asr")
        if !language.isEmpty { field("language", language) }
        if !knownWords.isEmpty { field("prompt", knownWords.joined(separator: ", ")) }
        body.append(contentsOf: "--\(boundary)--\r\n".utf8)
        return body
    }
}
