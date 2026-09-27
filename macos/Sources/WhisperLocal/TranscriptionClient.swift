import Foundation

enum SpeechError: LocalizedError {
    case invalidURL
    case unreachable
    case unauthorized
    case response(Int)
    case invalidJSON
    case network

    var errorDescription: String? {
        switch self {
        case .invalidURL: "Invalid server URL"
        case .unreachable: "Speech server unreachable"
        case .unauthorized: "API key rejected"
        case .response(let code): "Speech server returned HTTP \(code)"
        case .invalidJSON: "Invalid response from speech server"
        case .network: "Speech server request failed"
        }
    }
}

private final class SameOriginRedirectDelegate: NSObject, URLSessionTaskDelegate {
    func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        willPerformHTTPRedirection response: HTTPURLResponse,
        newRequest request: URLRequest,
        completionHandler: @escaping (URLRequest?) -> Void
    ) {
        guard let source = response.url, let destination = request.url,
              let sourceOrigin = Self.origin(of: source),
              let destinationOrigin = Self.origin(of: destination),
              sourceOrigin == destinationOrigin
        else {
            completionHandler(nil)
            return
        }
        completionHandler(request)
    }

    private static func origin(of url: URL) -> String? {
        guard let scheme = url.scheme?.lowercased(),
              let host = url.host?.lowercased(),
              scheme == "http" || scheme == "https"
        else { return nil }
        let port = url.port ?? (scheme == "https" ? 443 : 80)
        return "\(scheme)://\(host):\(port)"
    }
}

struct TranscriptionClient {
    var session: URLSession = .shared

    func transcribe(wav: Data, serverURL: String, apiKey: String, language: String, knownWords: [String]) async throws -> String {
        let url = try endpoint(serverURL)
        let boundary = "WhisperLocal-\(UUID().uuidString)"
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = 30
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        if !apiKey.isEmpty {
            request.setValue("Bearer \(apiKey)", forHTTPHeaderField: "Authorization")
        }
        request.httpBody = Multipart.body(
            wav: wav, language: language.trimmingCharacters(in: .whitespacesAndNewlines),
            knownWords: knownWords, boundary: boundary
        )
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request, delegate: SameOriginRedirectDelegate())
        } catch let error as URLError where [
            .notConnectedToInternet, .cannotFindHost, .cannotConnectToHost,
            .timedOut, .networkConnectionLost, .dnsLookupFailed,
        ].contains(error.code) {
            throw SpeechError.unreachable
        } catch {
            throw SpeechError.network
        }
        guard let http = response as? HTTPURLResponse else { throw SpeechError.invalidJSON }
        if http.statusCode == 401 { throw SpeechError.unauthorized }
        guard (200..<300).contains(http.statusCode) else { throw SpeechError.response(http.statusCode) }
        guard let result = try? JSONDecoder().decode(Transcript.self, from: data) else {
            throw SpeechError.invalidJSON
        }
        return result.text
    }

    func reachable(serverURL: String) async -> Bool {
        guard let url = try? endpoint(serverURL) else { return false }
        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.timeoutInterval = 3
        do {
            let (_, response) = try await session.data(for: request)
            return response is HTTPURLResponse
        } catch {
            return false
        }
    }

    private func endpoint(_ serverURL: String) throws -> URL {
        guard let base = URL(string: serverURL.trimmingCharacters(in: .whitespacesAndNewlines)),
              ["http", "https"].contains(base.scheme?.lowercased() ?? ""),
              base.host != nil else { throw SpeechError.invalidURL }
        return base.appending(path: "audio/transcriptions")
    }

    private struct Transcript: Decodable { let text: String }
}
