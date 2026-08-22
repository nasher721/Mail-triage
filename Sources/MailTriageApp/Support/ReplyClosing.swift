import Foundation

enum ReplyClosing {
    static let defaultValue = "Best,\nNick"

    static func normalize(_ raw: String) -> String {
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? defaultValue : trimmed
    }

    static func validationFailure(_ raw: String) -> String? {
        let text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if text.isEmpty { return nil }
        if text.count > 120 {
            return "Reply closing must be at most 120 characters."
        }
        if text.split(separator: "\n", omittingEmptySubsequences: false).count > 4 {
            return "Reply closing must be at most four lines."
        }
        if text.contains(where: { character in
            character.unicodeScalars.allSatisfy { $0.value < 32 } && character != "\n"
        }) {
            return "Reply closing may only use newline as a control character."
        }
        if text.contains("<") || text.contains(">") {
            return "Reply closing cannot contain HTML."
        }
        return nil
    }
}
