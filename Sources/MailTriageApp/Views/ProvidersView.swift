import SwiftUI

/// Pick which AI system screens mail, which one files it, and store the keys.
struct ProvidersView: View {
    @EnvironmentObject private var store: AppStore
    @State private var expanded: AIProvider?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                header
                roleCard
                providerSection(
                    "On this Mac",
                    detail: "Inference stays local. No approval and no per-request billing.",
                    providers: AIProvider.localProviders
                )
                providerSection(
                    "Hosted services",
                    detail: "Message text leaves this Mac. Approve external AI before running.",
                    providers: AIProvider.hostedProviders
                )
            }
            .padding(28)
            .frame(maxWidth: 980, alignment: .leading)
        }
        .navigationTitle("AI Providers")
        .task { store.checkSelectedProviders() }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Connect an AI system")
                .font(.largeTitle.bold())
            Text("Mail Triage routes screening and filing through whichever provider you select. Keys are stored in the login keychain and passed to the engine for one run at a time.")
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var roleCard: some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 14) {
                LabeledContent("Screening") {
                    Picker("Screening", selection: screeningSelection) {
                        ForEach(AIProvider.allCases) { provider in
                            Text(provider.title).tag(provider)
                        }
                    }
                    .labelsHidden()
                    .frame(maxWidth: 260)
                }

                Toggle("Use a different provider for the sorting agent", isOn: separateAgentBinding)
                    .disabled(!store.useAgent)

                if store.useSeparateAgentProvider {
                    LabeledContent("Sorting agent") {
                        Picker("Sorting agent", selection: agentSelection) {
                            ForEach(AIProvider.allCases) { provider in
                                Text(provider.title).tag(provider)
                            }
                        }
                        .labelsHidden()
                        .frame(maxWidth: 260)
                    }
                }

                Divider()

                Label(
                    store.keepsDataOnThisMac
                        ? "Message text stays on this Mac."
                        : "Message text is sent to a hosted provider.",
                    systemImage: store.keepsDataOnThisMac ? "lock.shield" : "cloud"
                )
                .foregroundStyle(store.keepsDataOnThisMac ? .green : .orange)

                if !store.keepsDataOnThisMac {
                    Toggle("External AI use is approved for this mailbox", isOn: approvalBinding)
                        .toggleStyle(.checkbox)
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
            Label("Roles", systemImage: "arrow.triangle.branch").font(.headline)
        }
    }

    private func providerSection(
        _ title: String,
        detail: String,
        providers: [AIProvider]
    ) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(title).font(.title3.bold())
            Text(detail)
                .font(.callout)
                .foregroundStyle(.secondary)
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 300), spacing: 14)], spacing: 14) {
                ForEach(providers) { provider in
                    ProviderCard(
                        provider: provider,
                        isExpanded: expanded == provider,
                        toggleExpanded: {
                            expanded = expanded == provider ? nil : provider
                        }
                    )
                }
            }
        }
    }

    private var screeningSelection: Binding<AIProvider> {
        Binding(
            get: { store.screeningProvider },
            set: { store.selectScreeningProvider($0) }
        )
    }

    private var agentSelection: Binding<AIProvider> {
        Binding(
            get: { store.agentProvider },
            set: { store.selectAgentProvider($0) }
        )
    }

    private var separateAgentBinding: Binding<Bool> {
        Binding(
            get: { store.useSeparateAgentProvider },
            set: { value in
                store.useSeparateAgentProvider = value
                if !value { store.agentProvider = store.screeningProvider }
                store.persistSettings()
                store.checkSelectedProviders()
            }
        )
    }

    private var approvalBinding: Binding<Bool> {
        Binding(
            get: { store.externalAIApproved },
            set: { value in
                store.externalAIApproved = value
                store.persistSettings()
            }
        )
    }
}

private struct ProviderCard: View {
    @EnvironmentObject private var store: AppStore
    let provider: AIProvider
    let isExpanded: Bool
    let toggleExpanded: () -> Void

    @State private var keyDraft = ""
    @State private var showKey = false

    var body: some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    Image(systemName: provider.symbol)
                        .foregroundStyle(statusColor)
                    Text(provider.title).font(.headline)
                    Spacer()
                    if isSelected {
                        Text(roleLabel)
                            .font(.caption.weight(.semibold))
                            .padding(.horizontal, 7)
                            .padding(.vertical, 3)
                            .background(.tint.opacity(0.16), in: Capsule())
                    }
                }

                Text(provider.detail)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)

                HStack(spacing: 6) {
                    Circle().fill(statusColor).frame(width: 8, height: 8)
                    Text(store.status(for: provider).detail)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                }

                HStack {
                    Button(isExpanded ? "Hide Details" : "Configure", action: toggleExpanded)
                    Button("Check") {
                        store.checkProvider(provider, role: isSelected ? (isAgentRole ? .agent : .screening) : nil)
                    }
                    Spacer()
                    if !isSelected {
                        Button("Use for Screening") { store.selectScreeningProvider(provider) }
                    }
                }
                .buttonStyle(.link)

                if isExpanded { configuration }
            }
            .padding(6)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .onAppear { keyDraft = store.apiKey(for: provider) }
    }

    @ViewBuilder
    private var configuration: some View {
        Divider()
        VStack(alignment: .leading, spacing: 10) {
            if provider.requiresAPIKey || provider == .custom {
                VStack(alignment: .leading, spacing: 6) {
                    HStack {
                        if showKey {
                            TextField("API key", text: $keyDraft)
                        } else {
                            SecureField("API key", text: $keyDraft)
                        }
                        Button(showKey ? "Hide" : "Show") { showKey.toggle() }
                            .buttonStyle(.link)
                    }
                    HStack {
                        Button("Save Key") { store.setAPIKey(keyDraft, for: provider) }
                        Button("Remove") {
                            keyDraft = ""
                            store.setAPIKey("", for: provider)
                        }
                        .disabled(!store.hasKey(for: provider))
                    }
                    Text(
                        store.usesEnvironmentKey(for: provider)
                            ? "Currently using the key from \(provider.apiKeyEnvironmentVariable ?? "the environment")."
                            : provider.credentialHint
                    )
                    .font(.caption)
                    .foregroundStyle(.secondary)
                }
            } else {
                Text(provider.credentialHint)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            if isSelected {
                TextField("Base URL", text: baseURLBinding)
                    .textFieldStyle(.roundedBorder)
                modelField
            } else {
                LabeledContent("Default endpoint", value: provider.defaultBaseURL.isEmpty ? "Set when selected" : provider.defaultBaseURL)
                    .font(.caption)
            }
        }
    }

    @ViewBuilder
    private var modelField: some View {
        let suggestions = store.models(for: provider)
        TextField("Model", text: modelBinding)
            .textFieldStyle(.roundedBorder)
        if !suggestions.isEmpty {
            Menu("Choose an available model") {
                ForEach(suggestions.prefix(25), id: \.self) { name in
                    Button(name) { modelBinding.wrappedValue = name }
                }
            }
            .menuStyle(.borderlessButton)
            .frame(maxWidth: 240, alignment: .leading)
        }
    }

    private var isSelected: Bool {
        provider == store.screeningProvider
            || (store.useSeparateAgentProvider && provider == store.agentProvider)
    }

    private var isAgentRole: Bool {
        store.useSeparateAgentProvider && provider == store.agentProvider
            && provider != store.screeningProvider
    }

    private var roleLabel: String { isAgentRole ? "Sorting agent" : "Screening" }

    private var statusColor: Color {
        switch store.status(for: provider) {
        case .ready: .green
        case .needsAttention: .orange
        case .checking, .unknown: .secondary
        }
    }

    private var baseURLBinding: Binding<String> {
        isAgentRole
            ? Binding(
                get: { store.agentBaseURL },
                set: { store.agentBaseURL = $0; store.persistSettings() }
            )
            : Binding(
                get: { store.screeningBaseURL },
                set: { store.screeningBaseURL = $0; store.persistSettings() }
            )
    }

    private var modelBinding: Binding<String> {
        isAgentRole
            ? Binding(
                get: { store.agentModel },
                set: { store.agentModel = $0; store.persistSettings() }
            )
            : Binding(
                get: { store.screeningModel },
                set: { store.screeningModel = $0; store.persistSettings() }
            )
    }
}
