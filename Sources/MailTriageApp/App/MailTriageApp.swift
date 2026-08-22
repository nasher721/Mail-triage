import AppKit
import SwiftUI

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    var shutdownHandler: (() async -> Void)?
    private var terminationPending = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard !terminationPending, let shutdownHandler else {
            return terminationPending ? .terminateLater : .terminateNow
        }
        terminationPending = true
        Task {
            await shutdownHandler()
            sender.reply(toApplicationShouldTerminate: true)
        }
        return .terminateLater
    }
}

@main
struct MailTriageApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var store = AppStore()

    var body: some Scene {
        WindowGroup("Mail Triage", id: "main") {
            ContentView()
                .environmentObject(store)
                .frame(minWidth: 960, minHeight: 640)
                .onAppear {
                    appDelegate.shutdownHandler = { await store.shutdown() }
                }
        }
        .defaultSize(width: 1120, height: 760)
        .commands {
            CommandMenu("Triage") {
                Button("Run Preview") {
                    store.runMode = .preview
                    store.requestRun()
                }
                .keyboardShortcut("r", modifiers: [.command])
                .disabled(!store.canRun)

                Button("Apply Changes to Outlook…") {
                    store.runMode = .apply
                    store.requestRun()
                }
                .keyboardShortcut("r", modifiers: [.command, .shift])
                .disabled(!store.canApplySelection)

                Button("Run Diagnostics") { store.runDiagnostic() }
                    .keyboardShortcut("d", modifiers: [.command, .shift])
                    .disabled(store.isRunning)

                Divider()

                Menu("AI Provider") {
                    ForEach(AIProvider.allCases) { provider in
                        Button {
                            store.selectScreeningProvider(provider)
                        } label: {
                            Text(provider == store.screeningProvider ? "\(provider.title) ✓" : provider.title)
                        }
                    }
                }

                Button("Check AI Provider") { store.checkSelectedProviders() }
                    .disabled(store.isRunning)

                Toggle("Run Previews Automatically", isOn: Binding(
                    get: { store.automationEnabled },
                    set: { store.setAutomation(enabled: $0) }
                ))

                Divider()

                Button("Export Results as JSON…") { store.exportResults(asCSV: false) }
                    .disabled(store.results.isEmpty)
                Button("Export Results as CSV…") { store.exportResults(asCSV: true) }
                    .disabled(store.results.isEmpty)

                Divider()

                Button("Cancel Current Operation") { store.cancel() }
                    .keyboardShortcut(".", modifiers: [.command])
                    .disabled(!store.isRunning)
            }
        }

        Settings {
            SettingsView()
                .environmentObject(store)
        }
    }
}
