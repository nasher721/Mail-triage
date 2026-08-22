import Foundation

enum ApplySelection {
    static func selectAllFiltered(current: Set<String>, filtered: [TriageRecord]) -> Set<String> {
        current.union(filtered.filter { !$0.isApplied }.map(\.messageID))
    }

    static func selectNoneFiltered(current: Set<String>, filtered: [TriageRecord]) -> Set<String> {
        current.subtracting(filtered.map(\.messageID))
    }

    static func merge(existing: [TriageRecord], applied: [TriageRecord]) -> [TriageRecord] {
        var updates: [String: TriageRecord] = [:]
        for record in applied {
            updates[record.messageID] = record
        }
        return existing.map { updates[$0.messageID] ?? $0 }
    }

    static func idsForApply(selected: Set<String>, records: [TriageRecord]) -> [String] {
        records
            .filter { selected.contains($0.messageID) && !$0.isApplied }
            .map(\.messageID)
    }

    static func jsonDocument(messageIDs: [String]) throws -> Data {
        let payload = ["message_ids": messageIDs]
        return try JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys])
    }
}
