import AppKit
import Foundation
import SwiftUI

/// Which role a provider check is for, when screening and the sorting agent
/// might otherwise share the same `AIProvider` case with different endpoints.
enum ProviderRole {
    case screening
    case agent
}

/// Readiness of one AI provider, as far as the app can tell without spending money.
enum ProviderStatus: Equatable {
    case unknown
    case checking
    case ready(String)
    case needsAttention(String)

    var detail: String {
        switch self {
        case .unknown: "Not checked yet."
        case .checking: "Checking…"
        case .ready(let detail), .needsAttention(let detail): detail
        }
    }

    var isReady: Bool {
        if case .ready = self { return true }
        return false
    }
}

/// Filters applied to the results list. Purely local; nothing is re-run.
enum RouteFilter: String, CaseIterable, Identifiable {
    case all
    case needsReview = "needs_review"
    case needsReply = "needs_reply"
    case noReply = "no_reply"

    var id: String { rawValue }

    var title: String {
        switch self {
        case .all: "All"
        case .needsReview: "Needs Review"
        case .needsReply: "Needs Reply"
        case .noReply: "No Reply"
        }
    }
}

@MainActor
final class AppStore: ObservableObject {
    @Published var selection: SidebarDestination = .overview
    @Published var source: MailSource
    @Published var runMode: RunMode = .preview
    @Published var markRead: Bool
    @Published var useAgent: Bool
    @Published var includeProcessed: Bool
    @Published var inputFile: String

    // AI providers
    @Published var screeningProvider: AIProvider
    @Published var screeningModel: String
    @Published var screeningBaseURL: String
    @Published var useSeparateAgentProvider: Bool
    @Published var agentProvider: AIProvider
    @Published var agentModel: String
    @Published var agentBaseURL: String
    @Published var externalAIApproved: Bool
    @Published var overrideTemperature: Bool
    @Published var temperature: Double
    @Published var requestTimeout: Int
    @Published var agentMaxRounds: Int

    // Mailbox and processing limits
    @Published var cdpURL: String
    @Published var maxMessages: Int
    @Published var maxBodyCharacters: Int
    @Published var maxRetrievalPages: Int
    @Published var outputDirectory: String

    // Automation
    @Published var automationEnabled: Bool
    @Published var automationMinutes: Int

    // Results presentation
    @Published var routeFilter: RouteFilter = .all
    @Published var resultSearch: String = ""
    @Published var selectedResultID: TriageRecord.ID?

    @Published private(set) var diagnostic: DiagnosticReport?
    @Published private(set) var liveProbe: LiveProbeReport?
    @Published private(set) var providerStatuses: [AIProvider: ProviderStatus] = [:]
    @Published private(set) var installedModels: [AIProvider: [String]] = [:]
    @Published private(set) var results: [TriageRecord] = []
    @Published private(set) var activity: [ActivityEntry] = []
    @Published private(set) var isRunning = false
    @Published private(set) var lastRunDate: Date?
    @Published var errorMessage: String?
    @Published var showApplyConfirmation = false

    private let engine = EngineService()
    private let defaults: UserDefaults
    private let credentials: CredentialStore
    private var currentOperationID: UUID?
    private var currentOperationTask: Task<Void, Never>?
    private var providerCheckTasks: [AIProvider: Task<Void, Never>] = [:]
    private var automationTimer: Timer?

    init(defaults: UserDefaults = .standard, credentials: CredentialStore = CredentialStore()) {
        self.defaults = defaults
        self.credentials = credentials
        source = MailSource(rawValue: defaults.string(forKey: "source") ?? "owa") ?? .owa
        markRead = defaults.bool(forKey: "markRead")
        useAgent = defaults.object(forKey: "useAgent") == nil
            ? true : defaults.bool(forKey: "useAgent")
        includeProcessed = defaults.bool(forKey: "includeProcessed")
        inputFile = defaults.string(forKey: "inputFile") ?? ""

        let screening = AIProvider(rawValue: defaults.string(forKey: "screeningProvider") ?? "")
            ?? .ollama
        screeningProvider = screening
        screeningModel = defaults.string(forKey: "screeningModel") ?? screening.defaultModel
        // Older builds stored only an Ollama host; keep it as the local base URL.
        screeningBaseURL = defaults.string(forKey: "screeningBaseURL")
            ?? defaults.string(forKey: "ollamaHost")
            ?? screening.defaultBaseURL
        useSeparateAgentProvider = defaults.bool(forKey: "useSeparateAgentProvider")
        let agent = AIProvider(rawValue: defaults.string(forKey: "agentProvider") ?? "") ?? screening
        agentProvider = agent
        agentModel = defaults.string(forKey: "agentModel") ?? agent.defaultModel
        agentBaseURL = defaults.string(forKey: "agentBaseURL") ?? agent.defaultBaseURL
        externalAIApproved = defaults.bool(forKey: "externalAIApproved")
        overrideTemperature = defaults.bool(forKey: "overrideTemperature")
        temperature = defaults.object(forKey: "temperature") as? Double ?? 0.2
        requestTimeout = max(10, defaults.object(forKey: "requestTimeout") as? Int ?? 180)
        agentMaxRounds = max(1, defaults.object(forKey: "agentMaxRounds") as? Int ?? 4)

        cdpURL = defaults.string(forKey: "cdpURL") ?? "http://127.0.0.1:9222"
        maxMessages = max(1, defaults.object(forKey: "maxMessages") as? Int ?? 20)
        maxBodyCharacters = max(500, defaults.object(forKey: "maxBodyCharacters") as? Int ?? 12_000)
        maxRetrievalPages = max(1, defaults.object(forKey: "maxRetrievalPages") as? Int ?? 10)
        outputDirectory = defaults.string(forKey: "outputDirectory")
            ?? EnginePaths.defaultOutputDirectory.path
        automationEnabled = defaults.bool(forKey: "automationEnabled")
        automationMinutes = max(5, defaults.object(forKey: "automationMinutes") as? Int ?? 30)

        activity = [ActivityEntry("Mail Triage ready. Run diagnostics to check Outlook and your AI provider.")]
        if automationEnabled { scheduleAutomation() }
    }

    // MARK: - Configuration

    var screeningBinding: ProviderBinding {
        ProviderBinding(
            provider: screeningProvider,
            model: screeningModel,
            baseURL: screeningBaseURL,
            apiKey: credentials.resolvedKey(for: screeningProvider)
        )
    }

    var agentBinding: ProviderBinding {
        guard useSeparateAgentProvider else { return screeningBinding }
        return ProviderBinding(
            provider: agentProvider,
            model: agentModel,
            baseURL: agentBaseURL,
            apiKey: credentials.resolvedKey(for: agentProvider)
        )
    }

    var configuration: EngineConfiguration {
        EngineConfiguration(
            source: source,
            runMode: runMode,
            markRead: markRead,
            useAgent: useAgent,
            includeProcessed: includeProcessed,
            inputFile: inputFile,
            screening: screeningBinding,
            agent: agentBinding,
            temperature: overrideTemperature ? temperature : nil,
            requestTimeout: requestTimeout,
            agentMaxRounds: agentMaxRounds,
            externalAIApproved: externalAIApproved,
            cdpURL: cdpURL,
            maxMessages: maxMessages,
            maxBodyCharacters: maxBodyCharacters,
            maxRetrievalPages: maxRetrievalPages,
            outputDirectory: outputDirectory
        )
    }

    var canRun: Bool {
        !isRunning
            && (!source.needsInputFile || !inputFile.isEmpty)
            && configuration.validationFailure == nil
    }

    /// Why the run button is unavailable, for display next to it.
    var runBlockedReason: String? {
        if source.needsInputFile, inputFile.isEmpty {
            return "Choose a local JSON, JSONL, or EML file first."
        }
        return configuration.validationFailure
    }

    var keepsDataOnThisMac: Bool { configuration.keepsDataOnThisMac }

    func persistSettings() {
        defaults.set(source.rawValue, forKey: "source")
        defaults.set(markRead, forKey: "markRead")
        defaults.set(useAgent, forKey: "useAgent")
        defaults.set(includeProcessed, forKey: "includeProcessed")
        defaults.set(inputFile, forKey: "inputFile")
        defaults.set(screeningProvider.rawValue, forKey: "screeningProvider")
        defaults.set(screeningModel, forKey: "screeningModel")
        defaults.set(screeningBaseURL, forKey: "screeningBaseURL")
        defaults.set(useSeparateAgentProvider, forKey: "useSeparateAgentProvider")
        defaults.set(agentProvider.rawValue, forKey: "agentProvider")
        defaults.set(agentModel, forKey: "agentModel")
        defaults.set(agentBaseURL, forKey: "agentBaseURL")
        defaults.set(externalAIApproved, forKey: "externalAIApproved")
        defaults.set(overrideTemperature, forKey: "overrideTemperature")
        defaults.set(temperature, forKey: "temperature")
        defaults.set(requestTimeout, forKey: "requestTimeout")
        defaults.set(agentMaxRounds, forKey: "agentMaxRounds")
        defaults.set(cdpURL, forKey: "cdpURL")
        defaults.set(maxMessages, forKey: "maxMessages")
        defaults.set(maxBodyCharacters, forKey: "maxBodyCharacters")
        defaults.set(maxRetrievalPages, forKey: "maxRetrievalPages")
        defaults.set(outputDirectory, forKey: "outputDirectory")
        defaults.set(automationEnabled, forKey: "automationEnabled")
        defaults.set(automationMinutes, forKey: "automationMinutes")
    }

    // MARK: - Providers

    func apiKey(for provider: AIProvider) -> String {
        credentials.key(for: provider) ?? ""
    }

    func hasKey(for provider: AIProvider) -> Bool {
        credentials.hasKey(for: provider)
    }

    /// True when the key comes from the environment rather than the keychain.
    func usesEnvironmentKey(for provider: AIProvider) -> Bool {
        credentials.key(for: provider) == nil && credentials.hasKey(for: provider)
    }

    func setAPIKey(_ value: String, for provider: AIProvider) {
        guard credentials.setKey(value, for: provider) else {
            errorMessage = "The API key could not be saved to the login keychain."
            return
        }
        activity.insert(
            ActivityEntry(
                value.isEmpty
                    ? "Removed the stored \(provider.title) key."
                    : "Stored a \(provider.title) key in the login keychain.",
                kind: .success
            ),
            at: 0
        )
        checkProvider(provider)
    }

    func selectScreeningProvider(_ provider: AIProvider) {
        screeningProvider = provider
        screeningModel = provider.defaultModel
        screeningBaseURL = provider.defaultBaseURL
        if !useSeparateAgentProvider { agentProvider = provider }
        persistSettings()
        checkProvider(provider, role: .screening)
    }

    func selectAgentProvider(_ provider: AIProvider) {
        agentProvider = provider
        agentModel = provider.defaultModel
        agentBaseURL = provider.defaultBaseURL
        persistSettings()
        checkProvider(provider, role: .agent)
    }

    func status(for provider: AIProvider) -> ProviderStatus {
        providerStatuses[provider] ?? .unknown
    }

    func models(for provider: AIProvider) -> [String] {
        let discovered = installedModels[provider] ?? []
        return discovered.isEmpty ? provider.suggestedModels : discovered
    }

    /// Check one provider. Local endpoints are probed; hosted ones are only
    /// checked for a usable key, so no billable request is ever made here.
    ///
    /// `role` disambiguates which base URL to probe when screening and the
    /// sorting agent share the same `AIProvider` case but point at different
    /// endpoints (e.g. two local Ollama instances on different ports). Pass
    /// it whenever the caller knows which role it is checking; omit it only
    /// for provider-identity-only checks (a card's generic "Check" button).
    func checkProvider(_ provider: AIProvider, role: ProviderRole? = nil) {
        providerCheckTasks[provider]?.cancel()
        let endpoint = resolvedBaseURL(for: provider, role: role)

        if let failure = provider.validate(baseURL: endpoint) {
            providerStatuses[provider] = .needsAttention(failure)
            return
        }
        guard provider.listsModels else {
            if provider.requiresAPIKey, !credentials.hasKey(for: provider) {
                let variable = provider.apiKeyEnvironmentVariable ?? "an API key"
                providerStatuses[provider] = .needsAttention(
                    "Add a \(provider.title) key, or export \(variable)."
                )
            } else {
                providerStatuses[provider] = .ready(
                    "\(provider.title) is configured. Requests are billed by the provider."
                )
            }
            return
        }

        guard let url = URL(string: endpoint + provider.modelListPath) else {
            providerStatuses[provider] = .needsAttention("\(endpoint) is not a valid URL.")
            return
        }
        providerStatuses[provider] = .checking

        providerCheckTasks[provider] = Task {
            do {
                var request = URLRequest(url: url)
                request.timeoutInterval = 4
                let key = credentials.resolvedKey(for: provider)
                if !key.isEmpty {
                    request.setValue("Bearer \(key)", forHTTPHeaderField: "Authorization")
                }
                let (data, response) = try await URLSession.shared.data(for: request)
                try Task.checkCancellation()
                guard let http = response as? HTTPURLResponse,
                      (200..<300).contains(http.statusCode) else {
                    throw URLError(.badServerResponse)
                }
                let names = Self.modelNames(from: data, provider: provider)
                guard endpoint == self.resolvedBaseURL(for: provider, role: role) else { return }
                self.installedModels[provider] = names
                self.providerStatuses[provider] = names.isEmpty
                    ? .needsAttention("\(provider.title) is running but has no models installed.")
                    : .ready("\(provider.title) is ready with \(names.count) model\(names.count == 1 ? "" : "s").")
            } catch is CancellationError {
                return
            } catch {
                guard endpoint == self.resolvedBaseURL(for: provider, role: role) else { return }
                self.providerStatuses[provider] = .needsAttention(
                    "\(provider.title) is not reachable at \(endpoint)."
                )
            }
        }
    }

    func checkSelectedProviders() {
        checkProvider(screeningProvider, role: .screening)
        if useSeparateAgentProvider { checkProvider(agentProvider, role: .agent) }
    }

    /// The endpoint currently configured for a provider, without a trailing slash.
    ///
    /// When `role` is given, it resolves that role's own base URL directly, so
    /// screening and the sorting agent never get conflated when they share the
    /// same `AIProvider` case. Without a role, it falls back to guessing from
    /// provider identity (screening takes priority), which is only correct
    /// when the two roles use different providers.
    private func resolvedBaseURL(for provider: AIProvider, role: ProviderRole? = nil) -> String {
        var value = provider.defaultBaseURL
        switch role {
        case .screening:
            if !screeningBaseURL.isEmpty { value = screeningBaseURL }
        case .agent:
            if !agentBaseURL.isEmpty { value = agentBaseURL }
        case nil:
            if provider == screeningProvider, !screeningBaseURL.isEmpty {
                value = screeningBaseURL
            } else if useSeparateAgentProvider, provider == agentProvider, !agentBaseURL.isEmpty {
                value = agentBaseURL
            }
        }
        var trimmed = value.trimmingCharacters(in: .whitespaces)
        while trimmed.hasSuffix("/") { trimmed.removeLast() }
        return trimmed
    }

    nonisolated static func modelNames(from data: Data, provider: AIProvider) -> [String] {
        guard let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return []
        }
        let listKey = provider == .ollama ? "models" : "data"
        let nameKey = provider == .ollama ? "name" : "id"
        let entries = payload[listKey] as? [[String: Any]] ?? []
        return entries.compactMap { $0[nameKey] as? String }.filter { !$0.isEmpty }
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
            checkProvider(.ollama)
        }
    }

    // MARK: - Files

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

    func revealOutputDirectory() {
        try? FileManager.default.createDirectory(
            atPath: outputDirectory,
            withIntermediateDirectories: true
        )
        NSWorkspace.shared.open(URL(fileURLWithPath: outputDirectory))
    }

    // MARK: - Results

    var filteredResults: [TriageRecord] {
        let query = resultSearch.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        return results.filter { record in
            let matchesRoute = routeFilter == .all || record.analysis.route == routeFilter.rawValue
            guard matchesRoute else { return false }
            guard !query.isEmpty else { return true }
            return record.subject.lowercased().contains(query)
                || record.senderName.lowercased().contains(query)
                || record.senderAddress.lowercased().contains(query)
                || record.analysis.summary.lowercased().contains(query)
        }
    }

    func exportResults(asCSV: Bool) {
        guard !results.isEmpty else {
            errorMessage = "There are no results to export yet."
            return
        }
        let panel = NSSavePanel()
        panel.nameFieldStringValue = asCSV ? "mail-triage.csv" : "mail-triage.json"
        panel.allowedContentTypes = asCSV ? [.commaSeparatedText] : [.json]
        guard panel.runModal() == .OK, let url = panel.url else { return }
        do {
            let data = asCSV ? Self.csv(from: filteredResults) : try Self.json(from: filteredResults)
            try data.write(to: url, options: .atomic)
            activity.insert(
                ActivityEntry("Exported \(filteredResults.count) result(s) to \(url.lastPathComponent).", kind: .success),
                at: 0
            )
        } catch {
            handle(error)
        }
    }

    nonisolated static func json(from records: [TriageRecord]) throws -> Data {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        return try encoder.encode(records)
    }

    /// Summary columns only: message bodies are never part of a record.
    nonisolated static func csv(from records: [TriageRecord]) -> Data {
        var rows = ["subject,sender,route,urgency,priority,target_folder,plan_source"]
        for record in records {
            let fields = [
                record.subject,
                record.senderAddress.isEmpty ? record.senderName : record.senderAddress,
                record.analysis.route,
                record.analysis.urgency,
                String(record.analysis.priorityScore),
                record.targetFolder,
                record.planSource ?? ""
            ]
            rows.append(fields.map(escapeCSV).joined(separator: ","))
        }
        return Data(rows.joined(separator: "\n").utf8)
    }

    nonisolated private static func escapeCSV(_ value: String) -> String {
        let escaped = value.replacingOccurrences(of: "\"", with: "\"\"")
        return "\"\(escaped)\""
    }

    // MARK: - Automation

    func setAutomation(enabled: Bool) {
        automationEnabled = enabled
        persistSettings()
        if enabled {
            scheduleAutomation()
            activity.insert(
                ActivityEntry("Automatic preview runs every \(automationMinutes) minutes.", kind: .info),
                at: 0
            )
        } else {
            automationTimer?.invalidate()
            automationTimer = nil
            activity.insert(ActivityEntry("Automatic runs stopped.", kind: .info), at: 0)
        }
    }

    func rescheduleAutomation() {
        persistSettings()
        guard automationEnabled else { return }
        scheduleAutomation()
    }

    private func scheduleAutomation() {
        automationTimer?.invalidate()
        let interval = TimeInterval(max(5, automationMinutes) * 60)
        let timer = Timer(timeInterval: interval, repeats: true) { [weak self] _ in
            Task { @MainActor [weak self] in self?.runScheduledTriage() }
        }
        RunLoop.main.add(timer, forMode: .common)
        automationTimer = timer
    }

    /// Scheduled runs never write to the mailbox and never queue behind a manual run.
    private func runScheduledTriage() {
        guard !isRunning, canRun else { return }
        var scheduled = configuration
        scheduled.runMode = .preview
        activity.insert(ActivityEntry("Scheduled preview run starting.", kind: .info), at: 0)
        runTriage(using: scheduled)
    }

    // MARK: - Operations

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

    func runTriage(using override: EngineConfiguration? = nil) {
        persistSettings()
        let configuration = override ?? self.configuration
        let providerLabel = useAgent && useSeparateAgentProvider
            ? "\(screeningProvider.title) + \(agentProvider.title)"
            : screeningProvider.title
        launch(label: "Running \(configuration.runMode.title.lowercased()) triage with \(providerLabel)") {
            try FileManager.default.createDirectory(
                atPath: configuration.outputDirectory,
                withIntermediateDirectories: true
            )
            return try EngineCommandBuilder.triage(configuration)
        } completion: { output in
            self.appendProcessOutput(output)
            do {
                let newRecords = try EngineParser.records(from: output.stdout)
                self.results = newRecords
                self.selectedResultID = newRecords.first?.id
                self.selection = .results
                self.lastRunDate = Date()
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
        automationTimer?.invalidate()
        automationTimer = nil
        providerCheckTasks.values.forEach { $0.cancel() }
        providerCheckTasks.removeAll()
        currentOperationTask?.cancel()
        await engine.cancel()
        currentOperationID = nil
        currentOperationTask = nil
        isRunning = false
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
