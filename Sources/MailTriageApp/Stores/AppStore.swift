import AppKit
import Foundation
import SwiftUI

@MainActor
final class AppStore: ObservableObject {
    @Published var selection: SidebarDestination = .overview
    @Published var source: MailSource
    @Published var runMode: RunMode = .preview
    @Published var markRead: Bool
    @Published var useAgent: Bool
    @Published var includeProcessed: Bool
    @Published var inputFile: String
    @Published var ollamaHost: String
    @Published var ollamaModel: String
    @Published var cdpURL: String
    @Published var maxMessages: Int
    @Published var outputDirectory: String

    @Published private(set) var diagnostic: DiagnosticReport?
    @Published private(set) var liveProbe: LiveProbeReport?
    @Published private(set) var ollamaAvailable: Bool?
    @Published private(set) var ollamaStatusDetail = "Checking local Ollama service…"
    @Published private(set) var results: [TriageRecord] = []
    @Published var selectedResultID: TriageRecord.ID?
    @Published private(set) var activity: [ActivityEntry] = []
    @Published private(set) var isRunning = false
    @Published var errorMessage: String?
    @Published var showApplyConfirmation = false

    private let engine = EngineService()
    private let defaults: UserDefaults
    private var currentOperationID: UUID?
    private var currentOperationTask: Task<Void, Never>?
    private var ollamaCheckTask: Task<Void, Never>?

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        source = MailSource(rawValue: defaults.string(forKey: "source") ?? "owa") ?? .owa
        markRead = defaults.bool(forKey: "markRead")
        useAgent = defaults.object(forKey: "useAgent") == nil
            ? true : defaults.bool(forKey: "useAgent")
        includeProcessed = defaults.bool(forKey: "includeProcessed")
        inputFile = defaults.string(forKey: "inputFile") ?? ""
        ollamaHost = defaults.string(forKey: "ollamaHost") ?? "http://127.0.0.1:11434"
        ollamaModel = defaults.string(forKey: "ollamaModel") ?? ""
        cdpURL = defaults.string(forKey: "cdpURL") ?? "http://127.0.0.1:9222"
        maxMessages = max(1, defaults.object(forKey: "maxMessages") as? Int ?? 20)
        outputDirectory = defaults.string(forKey: "outputDirectory")
            ?? EnginePaths.defaultOutputDirectory.path
        activity = [ActivityEntry("Mail Triage ready. Run diagnostics to check Outlook and Ollama.")]
    }

    var configuration: EngineConfiguration {
        EngineConfiguration(
            source: source,
            runMode: runMode,
            markRead: markRead,
            useAgent: useAgent,
            includeProcessed: includeProcessed,
            inputFile: inputFile,
            ollamaHost: ollamaHost,
            ollamaModel: ollamaModel,
            cdpURL: cdpURL,
            maxMessages: maxMessages,
            outputDirectory: outputDirectory
        )
    }

    var canRun: Bool {
        !isRunning && (!source.needsInputFile || !inputFile.isEmpty)
    }

    func persistSettings() {
        defaults.set(source.rawValue, forKey: "source")
        defaults.set(markRead, forKey: "markRead")
        defaults.set(useAgent, forKey: "useAgent")
        defaults.set(includeProcessed, forKey: "includeProcessed")
        defaults.set(inputFile, forKey: "inputFile")
        defaults.set(ollamaHost, forKey: "ollamaHost")
        defaults.set(ollamaModel, forKey: "ollamaModel")
        defaults.set(cdpURL, forKey: "cdpURL")
        defaults.set(maxMessages, forKey: "maxMessages")
        defaults.set(outputDirectory, forKey: "outputDirectory")
    }

    func selectInputFile() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.json, .emailMessage, .plainText]
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false
        if panel.runModal() == .OK, let url = panel.url {
            inputFile = url.path
            persistSettings()
        }
    }

    func selectOutputDirectory() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.canCreateDirectories = true
        if panel.runModal() == .OK, let url = panel.url {
            outputDirectory = url.path
            persistSettings()
        }
    }

    func requestRun() {
        if runMode == .apply {
            showApplyConfirmation = true
        } else {
            runTriage()
        }
    }

    func startOutlook() {
        launch(label: "Starting dedicated Outlook Edge session") {
            try EngineCommandBuilder.outlookSession(self.configuration)
        } completion: { output in
            self.appendProcessOutput(output)
            if output.status == 0 {
                self.activity.insert(ActivityEntry("Outlook Edge session is ready.", kind: .success), at: 0)
                self.runDiagnostic()
            } else {
                self.presentFailure(output, operation: "Outlook session")
            }
        }
    }

    func runDiagnostic() {
        launch(label: "Checking \(source.title)") {
            try EngineCommandBuilder.diagnostic(self.configuration)
        } completion: { output in
            self.appendProcessOutput(output)
            do {
                self.diagnostic = try EngineParser.diagnostic(from: output.stdout)
                let ready = self.diagnostic?.readiness.available == true
                self.activity.insert(
                    ActivityEntry(
                        self.diagnostic?.readiness.detail ?? "Diagnostic complete.",
                        kind: ready ? .success : .warning
                    ),
                    at: 0
                )
            } catch {
                self.handle(error)
            }
        }
    }

    func checkOllama() {
        ollamaCheckTask?.cancel()
        let requestedHost = ollamaHost
        guard EnginePaths.isLoopbackHTTPURL(requestedHost),
              let base = URL(string: requestedHost),
              let url = URL(string: "api/tags", relativeTo: base)?.absoluteURL else {
            ollamaAvailable = false
            ollamaStatusDetail = "Ollama must use a loopback HTTP address such as http://127.0.0.1:11434."
            return
        }

        ollamaCheckTask = Task {
            do {
                var request = URLRequest(url: url)
                request.timeoutInterval = 3
                let (data, response) = try await URLSession.shared.data(for: request)
                try Task.checkCancellation()
                guard let http = response as? HTTPURLResponse,
                      (200..<300).contains(http.statusCode) else {
                    throw URLError(.badServerResponse)
                }
                let payload = try JSONSerialization.jsonObject(with: data) as? [String: Any]
                let models = payload?["models"] as? [[String: Any]] ?? []
                guard requestedHost == self.ollamaHost else { return }
                self.ollamaAvailable = true
                self.ollamaStatusDetail = models.isEmpty
                    ? "Ollama is running; install a model before triage."
                    : "Ollama is ready with \(models.count) installed model\(models.count == 1 ? "" : "s")."
            } catch is CancellationError {
                return
            } catch {
                guard requestedHost == self.ollamaHost else { return }
                self.ollamaAvailable = false
                self.ollamaStatusDetail = "Ollama is not reachable at \(requestedHost)."
            }
        }
    }

    func startOllama() {
        let application = URL(fileURLWithPath: "/Applications/Ollama.app")
        guard FileManager.default.fileExists(atPath: application.path) else {
            errorMessage = "Ollama.app is not installed in Applications."
            return
        }
        NSWorkspace.shared.open(application)
        activity.insert(ActivityEntry("Opening Ollama."), at: 0)
        Task {
            try? await Task.sleep(for: .seconds(2))
            checkOllama()
        }
    }

    func runLiveProbe() {
        launch(label: "Running a redacted live probe") {
            try EngineCommandBuilder.liveProbe(self.configuration)
        } completion: { output in
            self.appendProcessOutput(output)
            do {
                self.liveProbe = try EngineParser.liveProbe(from: output.stdout)
                let kind: ActivityEntry.Kind = self.liveProbe?.available == true ? .success : .warning
                self.activity.insert(
                    ActivityEntry(self.liveProbe?.detail ?? "Live probe finished.", kind: kind),
                    at: 0
                )
            } catch {
                self.handle(error)
            }
        }
    }

    func runTriage() {
        persistSettings()
        launch(label: "Running \(runMode.title.lowercased()) triage") {
            try FileManager.default.createDirectory(
                atPath: self.outputDirectory,
                withIntermediateDirectories: true
            )
            return try EngineCommandBuilder.triage(self.configuration)
        } completion: { output in
            self.appendProcessOutput(output)
            do {
                let newRecords = try EngineParser.records(from: output.stdout)
                self.results = newRecords
                self.selectedResultID = newRecords.first?.id
                self.selection = .results
                let kind: ActivityEntry.Kind = output.status == 0 ? .success : .warning
                self.activity.insert(
                    ActivityEntry(
                        "Triage finished with \(newRecords.count) result\(newRecords.count == 1 ? "" : "s").",
                        kind: kind
                    ),
                    at: 0
                )
                if output.status != 0 {
                    self.presentFailure(output, operation: "Triage")
                }
            } catch {
                self.handle(error)
            }
        }
    }

    func cancel() {
        guard let operationID = currentOperationID else { return }
        activity.insert(ActivityEntry("Cancelling current operation…", kind: .warning), at: 0)
        currentOperationTask?.cancel()
        Task {
            await engine.cancel()
            guard self.currentOperationID == operationID else { return }
            self.currentOperationID = nil
            self.currentOperationTask = nil
            self.isRunning = false
            self.activity.insert(ActivityEntry("Operation cancelled.", kind: .warning), at: 0)
        }
    }

    func shutdown() async {
        persistSettings()
        ollamaCheckTask?.cancel()
        currentOperationTask?.cancel()
        await engine.cancel()
        currentOperationID = nil
        currentOperationTask = nil
        isRunning = false
    }

    func revealOutputDirectory() {
        try? FileManager.default.createDirectory(
            atPath: outputDirectory,
            withIntermediateDirectories: true
        )
        NSWorkspace.shared.open(URL(fileURLWithPath: outputDirectory))
    }

    private func launch(
        label: String,
        command: @escaping () throws -> EngineCommand,
        completion: @escaping (EngineOutput) -> Void
    ) {
        guard !isRunning else { return }
        errorMessage = nil
        isRunning = true
        activity.insert(ActivityEntry(label), at: 0)
        let operationID = UUID()
        currentOperationID = operationID

        currentOperationTask = Task {
            do {
                let output = try await engine.run(command())
                guard !Task.isCancelled, self.currentOperationID == operationID else { return }
                self.currentOperationID = nil
                self.currentOperationTask = nil
                self.isRunning = false
                completion(output)
            } catch is CancellationError {
                return
            } catch {
                guard self.currentOperationID == operationID else { return }
                self.currentOperationID = nil
                self.currentOperationTask = nil
                self.isRunning = false
                self.handle(error)
            }
        }
    }

    private func appendProcessOutput(_ output: EngineOutput) {
        let lines = output.stderr.split(whereSeparator: \.isNewline)
            .map(String.init)
            .filter { !$0.isEmpty }
        for line in lines.reversed() {
            activity.insert(ActivityEntry(line, kind: output.status == 0 ? .info : .error), at: 0)
        }
    }

    private func presentFailure(_ output: EngineOutput, operation: String) {
        let detail = output.stderr.trimmingCharacters(in: .whitespacesAndNewlines)
        errorMessage = detail.isEmpty
            ? "\(operation) exited with status \(output.status)."
            : detail
    }

    private func handle(_ error: Error) {
        let message = (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
        errorMessage = message
        activity.insert(ActivityEntry(message, kind: .error), at: 0)
    }
}
