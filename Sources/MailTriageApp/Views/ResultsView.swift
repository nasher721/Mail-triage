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
                    VStack(spacing: 0) {
                        filterBar
                        Divider()
                        List(store.filteredResults, selection: $store.selectedResultID) { record in
                            ResultRow(record: record)
                                .tag(record.id)
                        }
                        .overlay {
                            if store.filteredResults.isEmpty {
                                ContentUnavailableView(
                                    "No Matching Results",
                                    systemImage: "line.3.horizontal.decrease.circle",
                                    description: Text("Change the filter or search text.")
                                )
                            }
                        }
                    }
                    .frame(minWidth: 300, idealWidth: 380)

                    if let record = selectedRecord {
                        ResultDetailView(record: record)
                            .frame(minWidth: 430)
                    } else {
                        ContentUnavailableView("Select a Message", systemImage: "envelope.open")
                            .frame(minWidth: 430)
                    }
                }
                .onAppear {
                    store.selectedResultID = store.selectedResultID ?? store.filteredResults.first?.id
                }
            }
        }
        .navigationTitle("Triage Results")
        .toolbar {
            ToolbarItemGroup(placement: .secondaryAction) {
                Button {
                    store.exportResults(asCSV: false)
                } label: {
                    Label("Export JSON", systemImage: "square.and.arrow.up")
                }
                .disabled(store.results.isEmpty)

                Button {
                    store.exportResults(asCSV: true)
                } label: {
                    Label("Export CSV", systemImage: "tablecells")
                }
                .disabled(store.results.isEmpty)
            }
        }
    }

    /// Local filtering only: nothing is re-screened and no body text is shown.
    private var filterBar: some View {
        VStack(spacing: 8) {
            Picker("Route", selection: $store.routeFilter) {
                ForEach(RouteFilter.allCases) { filter in
                    Text(filter.title).tag(filter)
                }
            }
            .pickerStyle(.segmented)
            .labelsHidden()

            HStack(spacing: 6) {
                Image(systemName: "magnifyingglass").foregroundStyle(.secondary)
                TextField("Search subject, sender, or summary", text: $store.resultSearch)
                    .textFieldStyle(.plain)
                if !store.resultSearch.isEmpty {
                    Button {
                        store.resultSearch = ""
                    } label: {
                        Image(systemName: "xmark.circle.fill")
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(.secondary)
                }
            }

            HStack {
                Text("\(store.filteredResults.count) of \(store.results.count) message\(store.results.count == 1 ? "" : "s")")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Spacer()
                Button("Select All") {
                    store.selectAllFilteredForApply()
                }
                .buttonStyle(.borderless)
                .font(.caption)
                Button("Select None") {
                    store.selectNoneFilteredForApply()
                }
                .buttonStyle(.borderless)
                .font(.caption)
                if let date = store.lastRunDate {
                    Text("Run \(date.formatted(date: .omitted, time: .shortened))")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
        }
        .padding(10)
    }

    private var selectedRecord: TriageRecord? {
        store.results.first { $0.id == store.selectedResultID }
    }
}

private struct ResultRow: View {
    @EnvironmentObject private var store: AppStore
    let record: TriageRecord

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Toggle(
                "",
                isOn: Binding(
                    get: { store.selectedApplyIDs.contains(record.messageID) },
                    set: { isOn in
                        if isOn {
                            store.selectedApplyIDs.insert(record.messageID)
                        } else {
                            store.selectedApplyIDs.remove(record.messageID)
                        }
                    }
                )
            )
            .labelsHidden()
            .toggleStyle(.checkbox)
            .disabled(record.isApplied)

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

                if record.unsubscribeSuggestion == true {
                    detailSection("Unsubscribe suggestion", symbol: "envelope.badge") {
                        Text("This looks like a newsletter with an opt-out option. Open the original message in Outlook and use its unsubscribe control after checking the sender.")
                            .foregroundStyle(.secondary)
                        Text("Mail Triage never opens links or changes subscriptions automatically.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
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

                LearningControls(record: record)

                if !record.actions.isEmpty {
                    detailSection("Plan", symbol: "list.bullet.clipboard") {
                        ForEach(record.actions) { action in
                            VStack(alignment: .leading, spacing: 4) {
                                HStack {
                                    Image(systemName: action.status == "failed" ? "xmark.circle.fill" : "checkmark.circle")
                                        .foregroundStyle(action.status == "failed" ? .red : .green)
                                    Text(action.description)
                                    Spacer()
                                    Text(action.status.capitalized).foregroundStyle(.secondary)
                                }
                                if action.status == "failed", let detail = action.detail, !detail.isEmpty {
                                    Text(detail)
                                        .font(.caption)
                                        .foregroundStyle(.red)
                                        .textSelection(.enabled)
                                }
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

private final class LearningControlsState: ObservableObject {
    @Published var routePreference = ""
    @Published var replyGuidance = ""
    @Published var destinationFolder = ""
    @Published var loaded = false
}

/// Saves bounded, sender-domain preferences locally for future triage runs.
private struct LearningControls: View {
    @EnvironmentObject private var store: AppStore
    let record: TriageRecord

    @StateObject private var state = LearningControlsState()

    var body: some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 10) {
                Text("These local preferences affect future messages from this sender domain. They never override clinical, prompt-injection, or low-confidence safeguards.")
                    .font(.caption)
                    .foregroundStyle(.secondary)

                Picker("Future triage", selection: $state.routePreference) {
                    Text("Keep automatic routing").tag("")
                    Text("Prefer no reply").tag("no_reply")
                    Text("Always review").tag("needs_review")
                }

                TextField("Future folder", text: $state.destinationFolder)
                    .textFieldStyle(.roundedBorder)
                Text("Optional. Use a path beneath AI Triage, such as AI Triage/Receipts. Mail Triage creates it only when you apply a reviewed run.")
                    .font(.caption)
                    .foregroundStyle(.secondary)

                TextEditor(text: $state.replyGuidance)
                    .font(.body)
                    .frame(minHeight: 80)
                    .overlay(alignment: .topLeading) {
                        if state.replyGuidance.isEmpty {
                            Text("Optional future reply guidance (for example, tone or preferred sign-off)")
                                .foregroundStyle(.tertiary)
                                .padding(.horizontal, 5)
                                .padding(.vertical, 8)
                                .allowsHitTesting(false)
                        }
                    }

                HStack {
                    Spacer()
                    Button("Save for Future Messages") {
                        store.saveLearningPreference(
                            for: record,
                            route: state.routePreference.isEmpty ? nil : state.routePreference,
                            replyGuidance: state.replyGuidance,
                            destinationFolder: state.destinationFolder
                        )
                    }
                }
            }
        } label: {
            Label("Teach Mail Triage", systemImage: "brain").font(.headline)
        }
        .onAppear(perform: load)
        .onChange(of: record.id) { _, _ in
            state.loaded = false
            load()
        }
    }

    private func load() {
        guard !state.loaded else { return }
        let preference = store.learningPreference(for: record)
        state.routePreference = preference?.route ?? ""
        state.replyGuidance = preference?.replyGuidance ?? ""
        state.destinationFolder = preference?.destinationFolder ?? ""
        state.loaded = true
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
