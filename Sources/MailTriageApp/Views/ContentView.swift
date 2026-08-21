import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var store: AppStore

    var body: some View {
        NavigationSplitView {
            SidebarView(selection: $store.selection)
                .navigationSplitViewColumnWidth(min: 190, ideal: 220, max: 260)
        } detail: {
            detail
                .toolbar { toolbar }
        }
        .alert("Mail Triage", isPresented: errorPresented) {
            Button("OK") { store.errorMessage = nil }
        } message: {
            Text(store.errorMessage ?? "Unknown error")
        }
        .confirmationDialog(
            "Apply changes to Outlook?",
            isPresented: $store.showApplyConfirmation,
            titleVisibility: .visible
        ) {
            Button("Apply Moves, Categories, and Drafts", role: .destructive) {
                store.runTriage()
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Mail Triage never sends, forwards, or deletes messages. It may move messages, add categories, save unsent drafts, and optionally mark filed messages read.")
        }
    }

    @ViewBuilder
    private var detail: some View {
        switch store.selection {
        case .overview:
            OverviewView()
        case .results:
            ResultsView()
        case .activity:
            ActivityView()
        }
    }

    @ToolbarContentBuilder
    private var toolbar: some ToolbarContent {
        ToolbarItemGroup(placement: .primaryAction) {
            if store.isRunning {
                ProgressView()
                    .controlSize(.small)
                Button("Cancel", role: .cancel) { store.cancel() }
            } else {
                Picker("Mode", selection: $store.runMode) {
                    ForEach(RunMode.allCases) { mode in
                        Text(mode.title).tag(mode)
                    }
                }
                .pickerStyle(.segmented)
                .frame(width: 190)
                .disabled(!store.source.supportsApply)

                Button {
                    store.requestRun()
                } label: {
                    Label(
                        store.runMode == .preview ? "Run Triage" : "Apply Triage",
                        systemImage: store.runMode == .preview ? "play.fill" : "checkmark.circle.fill"
                    )
                }
                .buttonStyle(.borderedProminent)
                .disabled(!store.canRun)
                .help("Run Mail Triage (Command-R)")
            }
        }
    }

    private var errorPresented: Binding<Bool> {
        Binding(
            get: { store.errorMessage != nil },
            set: { if !$0 { store.errorMessage = nil } }
        )
    }
}
