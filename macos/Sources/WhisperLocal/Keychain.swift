import Foundation
import Security

enum Keychain {
    private static let service = "io.github.nicolasmahn.WhisperLocal"
    private static let account = "apiKey"

    static func apiKey() -> String? {
        var query = baseQuery
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    static func setAPIKey(_ value: String) throws {
        if value.isEmpty {
            let status = SecItemDelete(baseQuery as CFDictionary)
            guard status == errSecSuccess || status == errSecItemNotFound else { throw KeychainError(status) }
            return
        }
        let data = Data(value.utf8)
        let updated = SecItemUpdate(baseQuery as CFDictionary, [kSecValueData as String: data] as CFDictionary)
        if updated == errSecSuccess { return }
        guard updated == errSecItemNotFound else { throw KeychainError(updated) }
        var query = baseQuery
        query[kSecValueData as String] = data
        let added = SecItemAdd(query as CFDictionary, nil)
        guard added == errSecSuccess else { throw KeychainError(added) }
    }

    private static var baseQuery: [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
    }
}

private struct KeychainError: LocalizedError {
    let status: OSStatus
    init(_ status: OSStatus) { self.status = status }
    var errorDescription: String? { "Could not save API key (\(status))" }
}
