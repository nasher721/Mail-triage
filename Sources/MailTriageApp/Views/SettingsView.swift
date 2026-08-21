import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var store: AppStore

    var body: some View {
        TabView {
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
                    Stepper("Maximum messages: \(store.maxMessages)", value: $store.maxMessages, in: 1...100)
                    Toggle("Use the local sorting agent", isOn: $store.useAgent)
                    Toggle("Include previously processed messages", isOn: $store.includeProcessed)
                    Toggle("Mark filed messages as read", isOn: $store.markRead)
                        .disabled(!store.source.supportsApply)
                }
            }
            .formStyle(.grouped)
            .tabItem { Label("General", systemImage: "gearshape") }

            Form {
                Section("Ollama") {
                    TextField("Host", text: $store.ollamaHost)
                    TextField("Model (blank selects automatically)", text: $store.ollamaModel)
                }
                Section("Outlook browser session") {
                    TextField("Edge debugging URL", text: $store.cdpURL)
                    Text("Only loopback HTTP endpoints are accepted by the engine.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            .formStyle(.grouped)
            .tabItem { Label("Connections", systemImage: "point.3.connected.trianglepath.dotted") }

            Form {
                Section("Local state") {
                    LabeledContent("Output folder") {
                        Button("Choose…") { store.selectOutputDirectory() }
                    }
                    Text(store.outputDirectory)
                        .font(.caption.monospaced())
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                }
                Section("Privacy") {
                    Label("No message bodies are written to activity logs", systemImage: "checkmark.shield")
                    Label("Mail sending, forwarding, deletion, and attachment downloads are unavailable", systemImage: "checkmark.shield")
                    Label("The dedicated Edge profile stores login state locally in Application Support", systemImage: "externaldrive.badge.checkmark")
                    Label("Mail Triage never stores raw browser tokens", systemImage: "checkmark.shield")
                }
            }
            .formStyle(.grouped)
            .tabItem { Label("Privacy", systemImage: "lock.shield") }
        }
        .frame(width: 560, height: 470)
        .onDisappear { store.persistSettings() }
        .onChange(of: store.source) { _, source in
            if !source.supportsApply {
                store.runMode = .preview
                store.markRead = false
            }
            store.persistSettings()
        }
        .onChange(of: store.maxMessages) { _, _ in store.persistSettings() }
        .onChange(of: store.useAgent) { _, _ in store.persistSettings() }
        .onChange(of: store.includeProcessed) { _, _ in store.persistSettings() }
        .onChange(of: store.markRead) { _, _ in store.persistSettings() }
        .onChange(of: store.ollamaHost) { _, _ in store.persistSettings() }
        .onChange(of: store.ollamaModel) { _, _ in store.persistSettings() }
        .onChange(of: store.cdpURL) { _, _ in store.persistSettings() }
    }
}
