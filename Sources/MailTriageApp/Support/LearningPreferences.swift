import Foundation

/// A narrow, local-only correction that improves future screens for one sender domain.
struct LearningPreference: Codable, Equatable, Identifiable {
    var senderDomain: String
    var route: String?
    var replyGuidance: String
    var destinationFolder: String?

    var id: String { senderDomain }

    enum CodingKeys: String, CodingKey {
        case senderDomain = "sender_domain"
        case route
        case replyGuidance = "reply_guidance"
        case destinationFolder = "destination_folder"
    }
}

struct LearningPreferenceDocument: Codable {
    var version = 1
    var preferences: [LearningPreference]
}

/// Persists operator feedback outside the mailbox. The Python engine reads this file on its next run.
final class LearningPreferenceStore {
    private(set) var preferences: [LearningPreference] = []
    private let fileURL: URL

    init(fileURL: URL = EnginePaths.learningPreferencesFile) {
        self.fileURL = fileURL
        load()
    }

    func preference(for senderAddress: String) -> LearningPreference? {
        guard let domain = senderAddress.split(separator: "@", maxSplits: 1).last,
              senderAddress.contains("@") else { return nil }
        return preferences.first { $0.senderDomain == domain.lowercased() }
    }

    func save(
        senderAddress: String,
        route: String?,
        replyGuidance: String,
        destinationFolder: String
    ) throws {
        guard let domain = senderAddress.split(separator: "@", maxSplits: 1).last,
              senderAddress.contains("@") else {
            throw LearningPreferenceError.missingSenderDomain
        }
        let normalizedRoute = route == "no_reply" || route == "needs_review" ? route : nil
        let normalizedFolder = normalizedDestination(destinationFolder)
        let preference = LearningPreference(
            senderDomain: domain.lowercased(),
            route: normalizedRoute,
            replyGuidance: String(replyGuidance.trimmingCharacters(in: .whitespacesAndNewlines).prefix(400)),
            destinationFolder: normalizedFolder.isEmpty ? nil : normalizedFolder
        )
        preferences.removeAll { $0.senderDomain == preference.senderDomain }
        if preference.route != nil || !preference.replyGuidance.isEmpty || preference.destinationFolder != nil {
            preferences.append(preference)
        }
        try persist()
    }

    private func load() {
        guard let data = try? Data(contentsOf: fileURL),
              let document = try? JSONDecoder().decode(LearningPreferenceDocument.self, from: data) else {
            return
        }
        preferences = document.preferences
    }

    private func persist() throws {
        try FileManager.default.createDirectory(
            at: fileURL.deletingLastPathComponent(),
            withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o700]
        )
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        try encoder.encode(LearningPreferenceDocument(preferences: preferences)).write(to: fileURL, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: fileURL.path)
    }

    private func normalizedDestination(_ value: String) -> String {
        let parts = value.split(separator: "/").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
        guard (2...4).contains(parts.count), parts.first == "AI Triage",
              !parts.contains(where: { $0 == "." || $0 == ".." || $0.count > 64 }) else {
            return ""
        }
        return parts.joined(separator: "/")
    }
}

enum LearningPreferenceError: LocalizedError {
    case missingSenderDomain

    var errorDescription: String? { "A sender email address is required to save a learning preference." }
}
