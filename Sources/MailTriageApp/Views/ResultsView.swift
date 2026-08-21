import SwiftUI

struct ResultsView: View {
    @EnvironmentObject private var store: AppStore

    var body: some View {
        Group {
            if store.results.isEmpty {
                ContentUnavailableView {
                    Label("No Triage Results", systemImage: "tray")
                } description: {
                    Text("Run a preview to screen unread messages. Results appear here without exposing message bodies in app logs.")
                } actions: {
                    Button("Run Preview") {
                        store.runMode = .preview
                        store.requestRun()
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(!store.canRun)
                }
            } else {
                HSplitView {
                    List(store.results, selection: $store.selectedResultID) { record in
                        ResultRow(record: record)
                            .tag(record.id)
                    }
                    .frame(minWidth: 300, idealWidth: 360)

                    if let record = selectedRecord {
                        ResultDetailView(record: record)
                            .frame(minWidth: 430)
                    } else {
                        ContentUnavailableView("Select a Message", systemImage: "envelope.open")
                            .frame(minWidth: 430)
                    }
                }
                .onAppear {
                    store.selectedResultID = store.selectedResultID ?? store.results.first?.id
                }
            }
        }
        .navigationTitle("Triage Results")
    }

    private var selectedRecord: TriageRecord? {
        store.results.first { $0.id == store.selectedResultID }
    }
}

private struct ResultRow: View {
    let record: TriageRecord

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(record.subject.isEmpty ? "(No subject)" : record.subject)
                    .font(.headline)
                    .lineLimit(1)
                Spacer()
                PriorityBadge(score: record.analysis.priorityScore)
            }
            Text(sender)
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .lineLimit(1)
            Text(record.analysis.summary)
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(2)
        }
        .padding(.vertical, 5)
    }

    private var sender: String {
        if !record.senderName.isEmpty { return record.senderName }
        if !record.senderAddress.isEmpty { return record.senderAddress }
        return "Unknown sender"
    }
}

private struct ResultDetailView: View {
    let record: TriageRecord

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                VStack(alignment: .leading, spacing: 8) {
                    Text(record.subject.isEmpty ? "(No subject)" : record.subject)
                        .font(.title.bold())
                    Text(sender)
                        .foregroundStyle(.secondary)
                    HStack(spacing: 8) {
                        RouteBadge(route: record.analysis.route)
                        PriorityBadge(score: record.analysis.priorityScore)
                        Text(record.analysis.urgency.capitalized)
                            .font(.caption.weight(.medium))
                            .padding(.horizontal, 8)
                            .padding(.vertical, 4)
                            .background(.quaternary, in: Capsule())
                    }
                }

                detailSection("Summary", symbol: "text.quote") {
                    Text(record.analysis.summary)
                        .textSelection(.enabled)
                }

                detailSection("Classification", symbol: "tag") {
                    LabeledContent("Topic", value: record.analysis.topic.capitalized)
                    LabeledContent("Confidence", value: record.analysis.confidence.capitalized)
                    LabeledContent("Target folder", value: record.targetFolder)
                    if let reason = record.analysis.manualReviewReason {
                        LabeledContent("Review reason", value: reason.replacingOccurrences(of: "_", with: " ").capitalized)
                    }
                }

                if !record.analysis.actionItems.isEmpty {
                    detailSection("Action Items", symbol: "checklist") {
                        ForEach(record.analysis.actionItems, id: \.self) { item in
                            Label(item, systemImage: "circle")
                        }
                    }
                }

                if let reply = record.analysis.suggestedReply, !reply.isEmpty {
                    detailSection("Suggested Reply", symbol: "arrowshape.turn.up.left") {
                        Text(reply)
                            .textSelection(.enabled)
                            .fontDesign(.monospaced)
                    }
                }

                if !record.actions.isEmpty {
                    detailSection("Plan", symbol: "list.bullet.clipboard") {
                        ForEach(record.actions) { action in
                            HStack {
                                Image(systemName: action.status == "failed" ? "xmark.circle.fill" : "checkmark.circle")
                                    .foregroundStyle(action.status == "failed" ? .red : .green)
                                Text(action.description)
                                Spacer()
                                Text(action.status.capitalized).foregroundStyle(.secondary)
                            }
                        }
                    }
                }
            }
            .padding(28)
            .frame(maxWidth: 760, alignment: .leading)
        }
    }

    private var sender: String {
        let name = record.senderName.isEmpty ? "Unknown sender" : record.senderName
        return record.senderAddress.isEmpty ? name : "\(name) <\(record.senderAddress)>"
    }

    private func detailSection<Content: View>(
        _ title: String,
        symbol: String,
        @ViewBuilder content: () -> Content
    ) -> some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 10, content: content)
                .padding(6)
                .frame(maxWidth: .infinity, alignment: .leading)
        } label: {
            Label(title, systemImage: symbol).font(.headline)
        }
    }
}

private struct PriorityBadge: View {
    let score: Int

    var body: some View {
        Text("P\(score)")
            .font(.caption.bold())
            .foregroundStyle(score >= 4 ? .red : score >= 3 ? .orange : .secondary)
            .accessibilityLabel("Priority \(score) of 5")
    }
}

private struct RouteBadge: View {
    let route: String

    var body: some View {
        Text(route.replacingOccurrences(of: "_", with: " ").capitalized)
            .font(.caption.weight(.semibold))
            .padding(.horizontal, 9)
            .padding(.vertical, 4)
            .background(color.opacity(0.16), in: Capsule())
            .foregroundStyle(color)
    }

    private var color: Color {
        switch route {
        case "needs_review": .orange
        case "needs_reply": .blue
        default: .secondary
        }
    }
}
