import Foundation
import Security

/// Where the session token lives. The app uses the keychain; tests use memory.
public protocol TokenStore: Sendable {
    func read() -> String?
    func write(_ token: String)
    func clear()
}

public final class InMemoryTokenStore: TokenStore, @unchecked Sendable {
    private let lock = NSLock()
    private var token: String?

    public init(token: String? = nil) { self.token = token }

    public func read() -> String? { lock.withLock { token } }
    public func write(_ token: String) { lock.withLock { self.token = token } }
    public func clear() { lock.withLock { token = nil } }
}

/// The session token in the keychain, one item per service name (staging and production keep their own).
public final class KeychainTokenStore: TokenStore, Sendable {
    private let service: String
    private let account = "session"

    public init(service: String) { self.service = service }

    private var query: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
         kSecAttrAccount as String: account]
    }

    public func read() -> String? {
        var q = query
        q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        var out: AnyObject?
        guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess, let data = out as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    public func write(_ token: String) {
        let data = Data(token.utf8)
        var add = query
        add[kSecValueData as String] = data
        add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        let status = SecItemAdd(add as CFDictionary, nil)
        if status == errSecDuplicateItem {
            SecItemUpdate(query as CFDictionary, [kSecValueData as String: data] as CFDictionary)
        }
    }

    public func clear() { SecItemDelete(query as CFDictionary) }
}
