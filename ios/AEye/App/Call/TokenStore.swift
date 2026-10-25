import Foundation
import Security

/// The server's shared access token (`A_EYE_TOKEN`), kept in the Keychain rather than `UserDefaults`.
/// Only this device can read it (`kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly`); it never goes in a log.
enum TokenStore {
    private static let service = "com.example.aeye.server-token"
    private static let account = "a-eye"

    static func load() -> String {
        let query: [CFString: Any] = [
            kSecClass: kSecClassGenericPassword,
            kSecAttrService: service,
            kSecAttrAccount: account,
            kSecReturnData: true,
            kSecMatchLimit: kSecMatchLimitOne,
        ]
        var item: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess,
              let data = item as? Data
        else { return "" }
        return String(decoding: data, as: UTF8.self)
    }

    /// An empty value removes the item.
    static func save(_ token: String) {
        let base: [CFString: Any] = [
            kSecClass: kSecClassGenericPassword,
            kSecAttrService: service,
            kSecAttrAccount: account,
        ]
        SecItemDelete(base as CFDictionary)
        let clean = token.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !clean.isEmpty else { return }
        let attributes = base.merging([
            kSecValueData: Data(clean.utf8),
            kSecAttrAccessible: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
        ]) { _, new in new }
        SecItemAdd(attributes as CFDictionary, nil)
    }
}
