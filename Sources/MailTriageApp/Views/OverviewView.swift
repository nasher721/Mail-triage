import SwiftUI

struct OverviewView: View {
    @EnvironmentObject private var store: AppStore

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                header
                statusGrid
                sourceCard
                privacyCard
            }
            .padding(28)
            .frame(maxWidth: 920, alignment: .leading)
        }
        .navigationTitle("Overview")
        .task {
            if store.diagnostic == nil { store.runDiagnostic() }
            store.checkSelectedProviders()
        }
    }

    private var header: some View {
        HStack(alignment: .top, spacing: 18) {
            Image(systemName: "envelope.badge.shield.half.filled")
                .font(.system(size: 44))
                .foregroundStyle(.tint)
                .accessibilityHidden(true)

            VStack(alignment: .leading, spacing: 6) {
                Text("Local-first Outlook triage")
                    .font(.largeTitle.bold())
                Text("Screen unread mail with your signed-in Edge session and the AI system of your choice—local Ollama, Claude, ChatGPT, OpenRouter, and more. No Microsoft Graph registration required.")
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private var statusGrid: some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 235), spacing: 14)], spacing: 14) {
            StatusCard(
                title: "Edge Browser",
                detail: outlookDetail,
                symbol: "globe",
                state: outlookState,
                actionTitle: outlookReady ? "Check Again" : "Open Outlook Session",
                action: outlookReady ? store.runDiagnostic : store.startOutlook
            )

            StatusCard(
                title: "Outlook Access",
                detail: store.liveProbe?.detail ?? "Verify the signed-in session with a metadata-only request.",
                symbol: "person.crop.circle.badge.checkmark",
                state: liveProbeState,
                actionTitle: "Verify Sign-In",
                action: store.runLiveProbe
            )

            StatusCard(
                title: aiTitle,
                detail: store.status(for: store.screeningProvider).detail,
                symbol: store.screeningProvider.symbol,
                state: aiState,
                actionTitle: aiActionTitle,
                action: aiAction
            )

            StatusCard(
                title: "Run Mode",
                detail: store.runMode == .preview
                    ? "Preview only—no mailbox changes"
                    : "Moves, categories, and unsent drafts",
                symbol: store.runMode == .preview ? "eye" : "checkmark.shield",
                state: store.runMode == .preview ? .ready : .warning,
                actionTitle: "Run Triage",
                action: store.requestRun
            )
        }
    }

    private var sourceCard: some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 14) {
                Picker("Mail source", selection: $store.source) {
                    ForEach(MailSource.allCases) { source in
                        Text(source.title).tag(source)
                    }
                }
                .onChange(of: store.source) { _, source in
                    if !source.supportsApply { store.runMode = .preview }
                    store.persistSettings()
                    store.runDiagnostic()
                }

                Text(store.source.detail)
                    .font(.callout)
                    .foregroundStyle(.secondary)

                if store.source.needsInputFile {
                    HStack {
                        Text(store.inputFile.isEmpty ? "No input selected" : store.inputFile)
                            .lineLimit(1)
                            .truncationMode(.middle)
                            .foregroundStyle(store.inputFile.isEmpty ? .secondary : .primary)
                        Spacer()
                        Button("Choose File…") { store.selectInputFile() }
                    }
                }

                HStack {
                    Button("Run Diagnostics") { store.runDiagnostic() }
                    Button("Redacted Live Probe") { store.runLiveProbe() }
                        .disabled(store.source == .local)
                    Button("Choose AI Provider") { store.selection = .providers }
                    Spacer()
                    Button("Open Output Folder") { store.revealOutputDirectory() }
                }

                if let reason = store.runBlockedReason {
                    Label(reason, systemImage: "exclamationmark.triangle")
                        .font(.callout)
                        .foregroundStyle(.orange)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .padding(6)
        } label: {
            Label("Source and readiness", systemImage: "mail.stack")
                .font(.headline)
        }
    }

    private var privacyCard: some View {
        GroupBox {
            HStack(alignment: .top, spacing: 12) {
                Image(systemName: "lock.shield")
                    .font(.title2)
                    .foregroundStyle(.green)
                VStack(alignment: .leading, spacing: 5) {
                    Text("Safety boundaries stay enforced")
                        .font(.headline)
                    Text(privacyDetail)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .padding(6)
        }
    }

    private var privacyDetail: String {
        let base = "Mail Triage keeps captured bearer and cookie values in memory; Edge persists login state in its owner-only Application Support profile. Mail Triage cannot send, forward, delete, or download attachment content. Clinical and suspicious content is routed to manual review."
        return store.keepsDataOnThisMac
            ? base + " Screening and filing both run on this Mac."
            : base + " A hosted AI provider is selected, so message text leaves this Mac for screening."
    }

    private var outlookReady: Bool { store.diagnostic?.readiness.available == true }
    private var outlookDetail: String {
        store.diagnostic?.readiness.detail ?? "Checking the Edge debugging endpoint…"
    }
    private var outlookState: StatusCard.State {
        guard let diagnostic = store.diagnostic else { return .neutral }
        return diagnostic.readiness.available ? .ready : .warning
    }
    private var liveProbeState: StatusCard.State {
        guard let probe = store.liveProbe else { return .neutral }
        return probe.available ? .ready : .warning
    }
    private var aiTitle: String {
        store.useAgent && store.useSeparateAgentProvider
            ? "\(store.screeningProvider.title) + \(store.agentProvider.title)"
            : store.screeningProvider.title
    }

    private var aiState: StatusCard.State {
        switch store.status(for: store.screeningProvider) {
        case .ready: .ready
        case .needsAttention: .warning
        case .checking, .unknown: .neutral
        }
    }

    private var needsOllama: Bool {
        store.screeningProvider == .ollama
            && !store.status(for: .ollama).isReady
    }

    private var aiActionTitle: String { needsOllama ? "Open Ollama" : "Check Again" }

    private var aiAction: () -> Void {
        needsOllama ? store.startOllama : store.checkSelectedProviders
    }
}

private struct StatusCard: View {
    enum State {
        case ready, warning, neutral

        var color: Color {
            switch self {
            case .ready: .green
            case .warning: .orange
            case .neutral: .secondary
            }
        }
    }

    let title: String
    let detail: String
    let symbol: String
    let state: State
    let actionTitle: String
    let action: () -> Void

    var body: some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Image(systemName: symbol)
                        .font(.title2)
                        .foregroundStyle(state.color)
                    Spacer()
                    Circle()
                        .fill(state.color)
                        .frame(width: 9, height: 9)
                }
                Text(title).font(.headline)
                Text(detail)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .lineLimit(3)
                    .frame(maxWidth: .infinity, alignment: .leading)
                Button(actionTitle, action: action)
            }
            .padding(6)
            .frame(maxWidth: .infinity, minHeight: 155, alignment: .leading)
        }
    }
}
