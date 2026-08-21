import Foundation
import Testing
@testable import MailTriageApp

private var configuration: EngineConfiguration {
    EngineConfiguration(
        source: .owa,
        runMode: .preview,
        markRead: false,
        useAgent: true,
        includeProcessed: false,
        inputFile: "",
        screening: ProviderBinding(provider: .ollama, model: "qwen3.5:4b"),
        agent: ProviderBinding(provider: .ollama, model: "qwen3.5:4b"),
        temperature: nil,
        requestTimeout: 180,
        agentMaxRounds: 4,
        externalAIApproved: false,
        cdpURL: "http://127.0.0.1:9222",
        maxMessages: 20,
        maxBodyCharacters: 12_000,
        maxRetrievalPages: 10,
        outputDirectory: "/tmp/mail-triage-tests"
    )
}

private func hosted(
    _ provider: AIProvider,
    key: String = "synthetic-key",
    approved: Bool = true
) -> EngineConfiguration {
    var hosted = configuration
    hosted.screening = ProviderBinding(provider: provider, apiKey: key)
    hosted.agent = hosted.screening
    hosted.externalAIApproved = approved
    return hosted
}

@Test func previewCommandUsesCredentialFreeOWAWithoutShellInterpolation() throws {
    let command = try EngineCommandBuilder.triage(configuration)

    #expect(command.executable.hasSuffix("python3"))
    #expect(Array(command.arguments.suffix(3)) == ["--source", "owa", "--non-interactive"])
    #expect(command.environment["TRIAGE_PROVIDER"] == "ollama")
    #expect(command.environment["EDGE_CDP_URL"] == "http://127.0.0.1:9222")
    #expect(command.environment["TRIAGE_FEEDBACK_FILE"] == EnginePaths.learningPreferencesFile.path)
    #expect(!command.arguments.contains("--apply"))
}

@Test func applyCommandIsExplicitAndRejectedForPreviewOnlySource() throws {
    var apply = configuration
    apply.runMode = .apply
    #expect(try EngineCommandBuilder.triage(apply).arguments.contains("--apply"))

    apply.source = .accessibility
    #expect(throws: EngineFailure.self) {
        try EngineCommandBuilder.triage(apply)
    }
}

@Test func localCommandRequiresAndPreservesLiteralInputPath() throws {
    var local = configuration
    local.source = .local
    local.inputFile = "/tmp/mail;not-a-shell.eml"

    let command = try EngineCommandBuilder.triage(local)

    #expect(command.arguments.contains("/tmp/mail;not-a-shell.eml"))
    #expect(!command.executable.contains("sh"))
}

@Test func everyProviderIsRoutedThroughTheEngineEnvironment() throws {
    for provider in [AIProvider.openai, .anthropic, .openrouter, .gemini, .groq] {
        let command = try EngineCommandBuilder.triage(hosted(provider))

        #expect(command.environment["TRIAGE_PROVIDER"] == provider.rawValue)
        #expect(command.environment["TRIAGE_BASE_URL"] == provider.defaultBaseURL)
        #expect(command.environment["TRIAGE_MODEL"] == provider.defaultModel)
        #expect(command.environment["TRIAGE_API_KEY"] == "synthetic-key")
        #expect(command.environment["EXTERNAL_AI_APPROVED"] == "true")
    }
}

@Test func screeningAndSortingCanUseDifferentProviders() throws {
    var split = configuration
    split.screening = ProviderBinding(provider: .ollama, model: "qwen3:8b")
    split.agent = ProviderBinding(provider: .anthropic, apiKey: "synthetic-key")
    split.externalAIApproved = true

    let environment = try EngineCommandBuilder.triage(split).environment

    #expect(environment["TRIAGE_PROVIDER"] == "ollama")
    #expect(environment["TRIAGE_AGENT_PROVIDER"] == "anthropic")
    #expect(environment["TRIAGE_AGENT_MODEL"] == AIProvider.anthropic.defaultModel)
    #expect(environment["TRIAGE_AGENT_API_KEY"] == "synthetic-key")
    #expect(environment["TRIAGE_API_KEY"] == nil)
    #expect(!split.keepsDataOnThisMac)
}

@Test func hostedProvidersNeedApprovalAndAKeyBeforeAnyRun() {
    #expect(hosted(.openai, approved: false).validationFailure != nil)
    #expect(hosted(.openai, key: "").validationFailure != nil)
    #expect(hosted(.openai).validationFailure == nil)
    #expect(configuration.validationFailure == nil)
    #expect(configuration.keepsDataOnThisMac)
}

@Test func localProvidersMustStayOnLoopback() {
    var offMachine = configuration
    offMachine.screening = ProviderBinding(
        provider: .ollama,
        model: "qwen3:8b",
        baseURL: "http://ollama.example.org:11434"
    )
    #expect(offMachine.validationFailure != nil)
    #expect(throws: EngineFailure.self) {
        try EngineCommandBuilder.triage(offMachine)
    }
    #expect(AIProvider.anthropic.validate(baseURL: "http://api.anthropic.com") != nil)
    #expect(AIProvider.custom.validate(baseURL: "http://127.0.0.1:9000/v1") == nil)
}

@Test func optionalTuningIsForwardedOnlyWhenSet() throws {
    var tuned = configuration
    tuned.temperature = 0.35
    tuned.requestTimeout = 240
    tuned.agentMaxRounds = 6
    tuned.maxBodyCharacters = 4_000
    tuned.maxRetrievalPages = 3

    let environment = try EngineCommandBuilder.triage(tuned).environment

    #expect(environment["TRIAGE_TEMPERATURE"] == "0.35")
    #expect(environment["TRIAGE_REQUEST_TIMEOUT"] == "240")
    #expect(environment["TRIAGE_AGENT_MAX_ROUNDS"] == "6")
    #expect(environment["MAX_BODY_CHARACTERS"] == "4000")
    #expect(environment["MAX_RETRIEVAL_PAGES"] == "3")

    let untuned = try EngineCommandBuilder.triage(configuration).environment
    #expect(untuned["TRIAGE_TEMPERATURE"] == nil)
}

@Test func disablingTheAgentIsForwardedAndSkipsAgentValidation() throws {
    var noAgent = configuration
    noAgent.useAgent = false
    noAgent.agent = ProviderBinding(provider: .openai, apiKey: "")

    let command = try EngineCommandBuilder.triage(noAgent)

    #expect(command.arguments.contains("--no-agent"))
    #expect(command.environment["TRIAGE_AGENT"] == "false")
    #expect(noAgent.keepsDataOnThisMac)
}

@Test func providerModelListingsAreParsedPerDialect() {
    let ollama = Data(#"{"models":[{"name":"qwen3:8b"},{"name":"llama3.1:8b"}]}"#.utf8)
    let openAI = Data(#"{"data":[{"id":"gpt-4o"},{"id":"gpt-4o-mini"}]}"#.utf8)

    #expect(AppStore.modelNames(from: ollama, provider: .ollama) == ["qwen3:8b", "llama3.1:8b"])
    #expect(AppStore.modelNames(from: openAI, provider: .lmstudio) == ["gpt-4o", "gpt-4o-mini"])
    #expect(AIProvider.openai.listsModels)
    #expect(AppStore.modelNames(from: Data("not json".utf8), provider: .ollama).isEmpty)
}

@Test func diagnosticParserDecodesCapabilities() throws {
    let json = #"{"capabilities":{"source":"owa","authentication":"existing_edge_session","read_scope":"unread_inbox","supports_apply":true,"metadata_prefilter":true},"readiness":{"available":false,"code":"cdp_unreachable","detail":"No session"}}"#

    let report = try EngineParser.diagnostic(from: json)

    #expect(report.capabilities.source == "owa")
    #expect(report.capabilities.supportsApply)
    #expect(report.readiness.code == "cdp_unreachable")
}

@Test func recordParserDecodesJSONLines() throws {
    let line = #"{"message_id":"m1","subject":"Quarterly update","sender_name":"Alex","sender_address":"alex@example.org","target_folder":"AI Triage/Needs Reply","categories":["AI Triage"],"analysis":{"route":"needs_reply","urgency":"soon","topic":"administrative","confidence":"high","priority_score":4,"summary":"Needs a response.","action_items":["Reply"],"suggested_reply":"Thanks\n\nBest,\nNick","manual_review_reason":null,"deadline":null},"plan_source":"agent","actions":[{"kind":"draft_reply","description":"save an unsent reply draft","status":"dry-run","detail":null}]}"#

    let records = try EngineParser.records(from: line + "\n")

    #expect(records.count == 1)
    #expect(records[0].analysis.priorityScore == 4)
    #expect(records[0].actions.first?.kind == "draft_reply")
}

@Test func exportedRowsCarrySummaryColumnsOnly() throws {
    let line = #"{"message_id":"m1","subject":"Quarterly \"update\"","sender_name":"Alex","sender_address":"alex@example.org","target_folder":"AI Triage/Needs Reply","categories":["AI Triage"],"analysis":{"route":"needs_reply","urgency":"soon","topic":"administrative","confidence":"high","priority_score":4,"summary":"Needs a response.","action_items":[],"suggested_reply":null,"manual_review_reason":null,"deadline":null},"plan_source":"agent","actions":[]}"#
    let records = try EngineParser.records(from: line)

    let csv = String(decoding: AppStore.csv(from: records), as: UTF8.self)

    #expect(csv.hasPrefix("subject,sender,route,urgency,priority,target_folder,plan_source"))
    #expect(csv.contains("\"Quarterly \"\"update\"\"\""))
    #expect(!csv.contains("Needs a response."))
}

@Test func liveProbeParserPreservesPrivacyAssertions() throws {
    let json = #"{"available":false,"code":"session_probe_failed","detail":"Sign in first.","mailbox_mutated":false,"model_contacted":false,"request_scope":"metadata_only","retained_mail_data":false,"source":"owa"}"#

    let report = try EngineParser.liveProbe(from: json)

    #expect(!report.available)
    #expect(report.requestScope == "metadata_only")
    #expect(!report.retainedMailData)
    #expect(!report.modelContacted)
    #expect(!report.mailboxMutated)
}

@Test func engineServiceDrainsLargeStdoutAndStderrWithoutDeadlock() async throws {
    let python = try #require(EnginePaths.pythonExecutable())
    let command = EngineCommand(
        executable: python,
        arguments: ["-c", "import sys; print('x' * 200000); print('y' * 200000, file=sys.stderr)"],
        environment: ProcessInfo.processInfo.environment
    )

    let output = try await EngineService().run(command)

    #expect(output.status == 0)
    #expect(output.stdout.count == 200001)
    #expect(output.stderr.count == 200001)
}

@Test func connectionURLsMustRemainOnLoopbackHTTP() {
    #expect(EnginePaths.isLoopbackHTTPURL("http://127.0.0.1:11434"))
    #expect(EnginePaths.isLoopbackHTTPURL("http://localhost:9222"))
    #expect(EnginePaths.isLoopbackHTTPURL("http://[::1]:9222"))
    #expect(!EnginePaths.isLoopbackHTTPURL("https://127.0.0.1:11434"))
    #expect(!EnginePaths.isLoopbackHTTPURL("http://example.com:11434"))
    #expect(!EnginePaths.isLoopbackHTTPURL("http://user@127.0.0.1:11434"))
}

@Test func runtimeProbeRejectsUnsupportedExecutables() {
    #expect(!EnginePaths.supportsRuntime("/usr/bin/false", requiresPlaywright: false))
}

@Test func cancellationWaitsForProcessAndTerminatesItsChild() async throws {
    let python = try #require(EnginePaths.pythonExecutable())
    let sentinel = FileManager.default.temporaryDirectory
        .appendingPathComponent("mail-triage-child-\(UUID().uuidString)")
    defer { try? FileManager.default.removeItem(at: sentinel) }

    let child = "import time, pathlib; time.sleep(0.8); pathlib.Path(\(String(reflecting: sentinel.path))).write_text('orphaned')"
    let parent = "import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', \(String(reflecting: child))]); time.sleep(30)"
    let command = EngineCommand(
        executable: python,
        arguments: ["-c", parent],
        environment: ProcessInfo.processInfo.environment
    )
    let engine = EngineService()
    let running = Task { try await engine.run(command) }
    try await Task.sleep(for: .milliseconds(150))
    await engine.cancel()
    let cancelledOutput = try await running.value
    #expect(cancelledOutput.status != 0)

    let followup = try await engine.run(EngineCommand(
        executable: python,
        arguments: ["-c", "print('ready')"],
        environment: ProcessInfo.processInfo.environment
    ))
    #expect(followup.status == 0)
    #expect(followup.stdout == "ready\n")

    try await Task.sleep(for: .seconds(1))
    #expect(!FileManager.default.fileExists(atPath: sentinel.path))
}
