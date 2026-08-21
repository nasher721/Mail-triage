import Foundation

enum SidebarDestination: String, CaseIterable, Identifiable {
    case overview
    case results
    case activity

    var id: String { rawValue }

    var title: String {
        switch self {
        case .overview: "Overview"
        case .results: "Triage Results"
        case .activity: "Activity"
        }
    }

    var systemImage: String {
        switch self {
        case .overview: "rectangle.grid.2x2"
        case .results: "tray.full"
        case .activity: "text.alignleft"
        }
    }
}

enum MailSource: String, CaseIterable, Identifiable, Codable {
    case owa
    case accessibility
    case desktop
    case local

    var id: String { rawValue }

    var title: String {
        switch self {
        case .owa: "Outlook on the Web"
        case .accessibility: "Visible Outlook Inbox"
        case .desktop: "Frontmost Outlook Message"
        case .local: "Local Export"
        }
    }

    var detail: String {
        switch self {
        case .owa: "Uses your signed-in Edge session; no Graph credentials"
        case .accessibility: "Reads visible unread row metadata through macOS Accessibility"
        case .desktop: "Reads only the frontmost Outlook window title"
        case .local: "Screens JSON, JSONL, or EML files without mailbox access"
        }
    }

    var supportsApply: Bool { self == .owa }
    var needsInputFile: Bool { self == .local }
}

enum RunMode: String, CaseIterable, Identifiable {
    case preview
    case apply

    var id: String { rawValue }
    var title: String { self == .preview ? "Preview" : "Apply Changes" }
}

struct DiagnosticReadiness: Codable, Equatable {
    let available: Bool
    let code: String
    let detail: String
}

struct DiagnosticReport: Codable, Equatable {
    let capabilities: Capabilities
    let readiness: DiagnosticReadiness

    struct Capabilities: Codable, Equatable {
        let source: String
        let authentication: String
        let readScope: String
        let supportsApply: Bool
        let metadataPrefilter: Bool

        enum CodingKeys: String, CodingKey {
            case source
            case authentication
            case readScope = "read_scope"
            case supportsApply = "supports_apply"
            case metadataPrefilter = "metadata_prefilter"
        }
    }
}

struct LiveProbeReport: Codable, Equatable {
    let source: String
    let available: Bool
    let code: String
    let detail: String
    let requestScope: String
    let retainedMailData: Bool
    let modelContacted: Bool
    let mailboxMutated: Bool

    enum CodingKeys: String, CodingKey {
        case source, available, code, detail
        case requestScope = "request_scope"
        case retainedMailData = "retained_mail_data"
        case modelContacted = "model_contacted"
        case mailboxMutated = "mailbox_mutated"
    }
}

struct TriageRecord: Codable, Identifiable, Equatable {
    let messageID: String
    let subject: String
    let senderName: String
    let senderAddress: String
    let targetFolder: String
    let categories: [String]
    let analysis: Analysis
    let planSource: String?
    let actions: [AppliedAction]

    var id: String { messageID }

    enum CodingKeys: String, CodingKey {
        case messageID = "message_id"
        case subject
        case senderName = "sender_name"
        case senderAddress = "sender_address"
        case targetFolder = "target_folder"
        case categories
        case analysis
        case planSource = "plan_source"
        case actions
    }

    struct Analysis: Codable, Equatable {
        let route: String
        let urgency: String
        let topic: String
        let confidence: String
        let priorityScore: Int
        let summary: String
        let actionItems: [String]
        let suggestedReply: String?
        let manualReviewReason: String?
        let deadline: String?

        enum CodingKeys: String, CodingKey {
            case route, urgency, topic, confidence, summary, deadline
            case priorityScore = "priority_score"
            case actionItems = "action_items"
            case suggestedReply = "suggested_reply"
            case manualReviewReason = "manual_review_reason"
        }
    }

    struct AppliedAction: Codable, Equatable, Identifiable {
        let kind: String
        let description: String
        let status: String
        let detail: String?

        var id: String { "\(kind)-\(status)-\(detail ?? "")" }
    }
}

struct ActivityEntry: Identifiable, Equatable {
    enum Kind {
        case info
        case success
        case warning
        case error
    }

    let id = UUID()
    let date: Date
    let kind: Kind
    let message: String

    init(_ message: String, kind: Kind = .info, date: Date = Date()) {
        self.message = message
        self.kind = kind
        self.date = date
    }
}

struct EngineCommand: Equatable {
    let executable: String
    let arguments: [String]
    let environment: [String: String]
}

struct EngineOutput: Equatable {
    let status: Int32
    let stdout: String
    let stderr: String
}

enum EngineFailure: LocalizedError, Equatable {
    case pythonUnavailable
    case engineUnavailable
    case invalidOutput(String)
    case launchFailed(String)
    case commandFailed(status: Int32, message: String)

    var errorDescription: String? {
        switch self {
        case .pythonUnavailable:
            "Python 3.11 or newer was not found. Install Python from python.org or Homebrew."
        case .engineUnavailable:
            "The bundled Mail-triage engine could not be found. Rebuild the application."
        case .invalidOutput(let detail):
            "Mail-triage returned unreadable data: \(detail)"
        case .launchFailed(let detail):
            "Mail-triage could not start: \(detail)"
        case .commandFailed(_, let message):
            message
        }
    }
}
