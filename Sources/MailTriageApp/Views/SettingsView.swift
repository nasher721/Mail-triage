import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var store: AppStore

    var body: some View {
        TabView {
            generalTab
                .tabItem { Label("General", systemImage: "gearshape") }
            aiTab
                .tabItem { Label("AI", systemImage: "cpu") }
            connectionsTab
                .tabItem { Label("Connections", systemImage: "point.3.connected.trianglepath.dotted") }
            automationTab
                .tabItem { Label("Automation", systemImage: "clock.arrow.2.circlepath") }
            privacyTab
                .tabItem { Label("Privacy", systemImage: "lock.shield") }
        }
        .frame(width: 600, height: 520)
        .onDisappear { store.persistSettings() }
        .onChange(of: store.source) { _, source in
            if !source.supportsApply {
                store.runMode = .preview
                store.markRead = false
            }
            store.persistSettings()
        }
        .onChange(of: store.maxMessages) { _, _ in store.persistSettings() }
        .onChange(of: store.maxBodyCharacters) { _, _ in store.persistSettings() }
        .onChange(of: store.maxRetrievalPages) { _, _ in store.persistSettings() }
        .onChange(of: store.useAgent) { _, _ in store.persistSettings() }
        .onChange(of: store.includeProcessed) { _, _ in store.persistSettings() }
        .onChange(of: store.markRead) { _, _ in store.persistSettings() }
        .onChange(of: store.cdpURL) { _, _ in store.persistSettings() }
        .onChange(of: store.agentMaxRounds) { _, _ in store.persistSettings() }
        .onChange(of: store.requestTimeout) { _, _ in store.persistSettings() }
        .onChange(of: store.temperature) { _, _ in store.persistSettings() }
        .onChange(of: store.overrideTemperature) { _, _ in store.persistSettings() }
        .onChange(of: store.screeningBaseURL) { _, _ in
            store.persistSettings()
            store.checkProvider(store.screeningProvider)
        }
        .onChange(of: store.screeningModel) { _, _ in store.persistSettings() }
        .onChange(of: store.agentBaseURL) { _, _ in
            store.persistSettings()
            store.checkProvider(store.agentProvider)
        }
        .onChange(of: store.agentModel) { _, _ in store.persistSettings() }
    }

    private var generalTab: some View {
        Form {
            Section("Mail source") {
                Picker("Source", selection: $store.source) {
                    ForEach(MailSource.allCases) { source in
                        Text(source.title).tag(source)
                    }
                }
                Text(store.source.detail)
                    .font(.caption)
                    .foregroundStyle(.secondary)

                if store.source.needsInputFile {
                    LabeledContent("Input file") {
                        Button(store.inputFile.isEmpty ? "Choose…" : "Change…") {
                            store.selectInputFile()
                        }
                    }
                }
            }

            Section("Processing") {
                Stepper("Maximum messages: \(store.maxMessages)", value: $store.maxMessages, in: 1...200)
                Stepper(
                    "Body characters per message: \(store.maxBodyCharacters)",
                    value: $store.maxBodyCharacters,
                    in: 1_000...60_000,
                    step: 1_000
                )
                Stepper(
                    "Mailbox pages scanned: \(store.maxRetrievalPages)",
                    value: $store.maxRetrievalPages,
                    in: 1...50
                )
                Toggle("Use the sorting agent", isOn: $store.useAgent)
                Toggle("Include previously processed messages", isOn: $store.includeProcessed)
                Toggle("Mark filed messages as read", isOn: $store.markRead)
                    .disabled(!store.source.supportsApply)
            }

            Section("Local state") {
                LabeledContent("Output folder") {
                    Button("Choose…") { store.selectOutputDirectory() }
                }
                Text(store.outputDirectory)
                    .font(.caption.monospaced())
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
            }
        }
        .formStyle(.grouped)
    }

    private var aiTab: some View {
        Form {
            Section("Screening provider") {
                Picker("Provider", selection: screeningSelection) {
                    ForEach(AIProvider.allCases) { provider in
                        Text(provider.title).tag(provider)
                    }
                }
                TextField("Base URL", text: $store.screeningBaseURL)
                TextField("Model", text: $store.screeningModel)
                Text(store.status(for: store.screeningProvider).detail)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Section("Sorting agent") {
                Toggle("Run the agent on a different provider", isOn: separateAgentBinding)
                    .disabled(!store.useAgent)
                if store.useSeparateAgentProvider {
                    Picker("Provider", selection: agentSelection) {
                        ForEach(AIProvider.allCases) { provider in
                            Text(provider.title).tag(provider)
                        }
                    }
                    TextField("Base URL", text: $store.agentBaseURL)
                    TextField("Model", text: $store.agentModel)
                }
                Stepper("Maximum tool rounds: \(store.agentMaxRounds)", value: $store.agentMaxRounds, in: 1...10)
            }

            Section("Requests") {
                Toggle("Set the sampling temperature", isOn: $store.overrideTemperature)
                if store.overrideTemperature {
                    Slider(value: $store.temperature, in: 0...1, step: 0.05) {
                        Text("Temperature")
                    } minimumValueLabel: {
                        Text("0")
                    } maximumValueLabel: {
                        Text("1")
                    }
                    Text(String(format: "Temperature %.2f. Lower values keep routing decisions stable.", store.temperature))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Stepper("Request timeout: \(store.requestTimeout)s", value: $store.requestTimeout, in: 15...900, step: 15)
                LabeledContent("API keys") {
                    Button("Manage in AI Providers") { store.selection = .providers }
                }
            }
        }
        .formStyle(.grouped)
    }

    private var connectionsTab: some View {
        Form {
            Section("Outlook browser session") {
                TextField("Edge debugging URL", text: $store.cdpURL)
                Text("Only loopback HTTP endpoints are accepted by the engine.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Section("Local model hosts") {
                ForEach(AIProvider.localProviders) { provider in
                    LabeledContent(provider.title) {
                        Text(store.status(for: provider).detail)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .multilineTextAlignment(.trailing)
                    }
                }
                Button("Check Local Providers") {
                    AIProvider.localProviders.forEach(store.checkProvider)
                }
            }
        }
        .formStyle(.grouped)
    }

    private var automationTab: some View {
        Form {
            Section("Scheduled previews") {
                Toggle("Run a preview automatically", isOn: automationBinding)
                Stepper(
                    "Every \(store.automationMinutes) minutes",
                    value: $store.automationMinutes,
                    in: 5...480,
                    step: 5
                )
                .disabled(!store.automationEnabled)
                .onChange(of: store.automationMinutes) { _, _ in store.rescheduleAutomation() }
                Text("Scheduled runs are always previews. Applying changes to Outlook stays a manual, confirmed action.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Section("Last run") {
                LabeledContent("Completed") {
                    Text(store.lastRunDate.map { $0.formatted(date: .abbreviated, time: .shortened) } ?? "Never")
                }
                LabeledContent("Results held") { Text("\(store.results.count)") }
                Button("Open Output Folder") { store.revealOutputDirectory() }
            }
        }
        .formStyle(.grouped)
    }

    private var privacyTab: some View {
        Form {
            Section("External AI") {
                Toggle("External AI use is approved for this mailbox", isOn: approvalBinding)
                Text("Required before any hosted provider receives message text. Local providers never need it.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Label(
                    store.keepsDataOnThisMac
                        ? "Current selection keeps message text on this Mac"
                        : "Current selection sends message text to a hosted provider",
                    systemImage: store.keepsDataOnThisMac ? "checkmark.shield" : "cloud"
                )
                .foregroundStyle(store.keepsDataOnThisMac ? .green : .orange)
            }
            Section("Always enforced") {
                Label("No message bodies are written to activity logs", systemImage: "checkmark.shield")
                Label("Mail sending, forwarding, deletion, and attachment downloads are unavailable", systemImage: "checkmark.shield")
                Label("API keys are stored in the login keychain, never in preferences", systemImage: "key")
                Label("The dedicated Edge profile stores login state locally in Application Support", systemImage: "externaldrive.badge.checkmark")
                Label("The sorting agent sees the screening result only, never the message body", systemImage: "eye.slash")
            }
        }
        .formStyle(.grouped)
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

    private var automationBinding: Binding<Bool> {
        Binding(
            get: { store.automationEnabled },
            set: { store.setAutomation(enabled: $0) }
        )
    }
}
