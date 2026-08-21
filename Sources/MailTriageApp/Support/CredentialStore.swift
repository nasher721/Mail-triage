import Foundation
import Security

/// API keys live in the login keychain, never in UserDefaults and never in logs.
///
/// One generic-password item per provider, scoped to this app's service name.
/// Reads are best effort: a missing item, a locked keychain, or a denied prompt
/// all return `nil`, and the caller falls back to the provider's environment
/// variable so an operator can keep using a shell-managed key.
struct CredentialStore {
    static let defaultService = "com.mailtriage.provider-keys"

    private let service: String

    init(service: String = CredentialStore.defaultService) {
        self.service = service
    }

    private func query(for provider: AIProvider) -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: provider.rawValue
        ]
    }

    func key(for provider: AIProvider) -> String? {
        var query = query(for: provider)
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne

        var item: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess,
              let data = item as? Data,
              let value = String(data: data, encoding: .utf8),
              !value.isEmpty else {
            return nil
        }
        return value
    }

    /// Store a key, or remove it when `value` is empty. Returns false on failure.
    @discardableResult
    func setKey(_ value: String, for provider: AIProvider) -> Bool {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return removeKey(for: provider) }
        guard let data = trimmed.data(using: .utf8) else { return false }

        let query = query(for: provider)
        let attributes: [String: Any] = [kSecValueData as String: data]
        let update = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
        if update == errSecSuccess { return true }
        guard update == errSecItemNotFound else { return false }

        var insert = query
        insert[kSecValueData as String] = data
        insert[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlocked
        return SecItemAdd(insert as CFDictionary, nil) == errSecSuccess
    }

    @discardableResult
    func removeKey(for provider: AIProvider) -> Bool {
        let status = SecItemDelete(query(for: provider) as CFDictionary)
        return status == errSecSuccess || status == errSecItemNotFound
    }

    /// The key the engine should use: the stored one, else the provider's env var.
    func resolvedKey(
        for provider: AIProvider,
        environment: [String: String] = ProcessInfo.processInfo.environment
    ) -> String {
        if let stored = key(for: provider) { return stored }
        guard let variable = provider.apiKeyEnvironmentVariable else { return "" }
        return environment[variable]?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    }

    func hasKey(
        for provider: AIProvider,
        environment: [String: String] = ProcessInfo.processInfo.environment
    ) -> Bool {
        !resolvedKey(for: provider, environment: environment).isEmpty
    }
}
