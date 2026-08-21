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
        ollamaHost: "http://127.0.0.1:11434",
        ollamaModel: "qwen3.5:4b",
        cdpURL: "http://127.0.0.1:9222",
        maxMessages: 20,
        outputDirectory: "/tmp/mail-triage-tests"
    )
}

@Test func previewCommandUsesCredentialFreeOWAWithoutShellInterpolation() throws {
    let command = try EngineCommandBuilder.triage(configuration)

    #expect(command.executable.hasSuffix("python3"))
    #expect(Array(command.arguments.suffix(3)) == ["--source", "owa", "--non-interactive"])
    #expect(command.environment["TRIAGE_BACKEND"] == "ollama")
    #expect(command.environment["EDGE_CDP_URL"] == "http://127.0.0.1:9222")
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
