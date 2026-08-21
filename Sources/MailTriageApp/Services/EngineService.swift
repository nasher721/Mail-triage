import Darwin
import Foundation

struct EngineConfiguration: Equatable {
    var source: MailSource
    var runMode: RunMode
    var markRead: Bool
    var useAgent: Bool
    var includeProcessed: Bool
    var inputFile: String
    var ollamaHost: String
    var ollamaModel: String
    var cdpURL: String
    var maxMessages: Int
    var outputDirectory: String
}

enum EngineCommandBuilder {
    static func diagnostic(_ configuration: EngineConfiguration) throws -> EngineCommand {
        try command(configuration, operation: ["--diagnose"])
    }

    static func liveProbe(_ configuration: EngineConfiguration) throws -> EngineCommand {
        try command(configuration, operation: ["--live-probe"])
    }

    static func triage(_ configuration: EngineConfiguration) throws -> EngineCommand {
        var operation: [String] = ["--non-interactive"]
        if configuration.runMode == .apply {
            guard configuration.source.supportsApply else {
                throw EngineFailure.launchFailed("The selected source is preview-only.")
            }
            operation.append("--apply")
        }
        if configuration.markRead { operation.append("--mark-read") }
        if !configuration.useAgent { operation.append("--no-agent") }
        if configuration.includeProcessed { operation.append("--include-previously-processed") }
        return try command(configuration, operation: operation)
    }

    static func outlookSession(_ configuration: EngineConfiguration) throws -> EngineCommand {
        guard let helper = EnginePaths.resource(named: "open_outlook_in_edge.sh") else {
            throw EngineFailure.engineUnavailable
        }
        var environment = baseEnvironment(configuration)
        environment["OWA_URL"] = "https://outlook.office.com/mail/inbox"
        return EngineCommand(
            executable: "/bin/zsh",
            arguments: [helper.path],
            environment: environment
        )
    }

    private static func command(
        _ configuration: EngineConfiguration,
        operation: [String]
    ) throws -> EngineCommand {
        guard EnginePaths.isLoopbackHTTPURL(configuration.ollamaHost) else {
            throw EngineFailure.launchFailed("The Ollama host must be a loopback HTTP URL.")
        }
        if configuration.source == .owa,
           !EnginePaths.isLoopbackHTTPURL(configuration.cdpURL) {
            throw EngineFailure.launchFailed("The Edge debugging URL must be a loopback HTTP URL.")
        }
        guard let python = EnginePaths.pythonExecutable(
            requiresPlaywright: configuration.source == .owa
        ) else {
            throw EngineFailure.pythonUnavailable
        }
        guard let engine = EnginePaths.resource(named: "email_triage_standalone.py") else {
            throw EngineFailure.engineUnavailable
        }

        var arguments = [engine.path, "--source", configuration.source.rawValue]
        if configuration.source.needsInputFile {
            guard !configuration.inputFile.isEmpty else {
                throw EngineFailure.launchFailed("Choose a local JSON, JSONL, or EML file first.")
            }
            arguments += ["--input", configuration.inputFile]
        }
        arguments += operation

        return EngineCommand(
            executable: python,
            arguments: arguments,
            environment: baseEnvironment(configuration)
        )
    }

    private static func baseEnvironment(_ configuration: EngineConfiguration) -> [String: String] {
        var environment = ProcessInfo.processInfo.environment
        environment["TRIAGE_BACKEND"] = "ollama"
        environment["TRIAGE_SOURCE"] = configuration.source.rawValue
        environment["OLLAMA_HOST"] = configuration.ollamaHost
        environment["OLLAMA_MODEL"] = configuration.ollamaModel
        environment["EDGE_CDP_URL"] = configuration.cdpURL
        environment["MAX_UNREAD_MESSAGES"] = String(configuration.maxMessages)
        environment["TRIAGE_OUTPUT_DIR"] = configuration.outputDirectory
        environment["EDGE_PROFILE_DIR"] = EnginePaths.edgeProfileDirectory.path
        environment["PYTHONUNBUFFERED"] = "1"
        return environment
    }
}

enum EngineParser {
    static func diagnostic(from output: String) throws -> DiagnosticReport {
        guard let data = output.trimmingCharacters(in: .whitespacesAndNewlines)
            .data(using: .utf8) else {
            throw EngineFailure.invalidOutput("empty diagnostic response")
        }
        do {
            return try JSONDecoder().decode(DiagnosticReport.self, from: data)
        } catch {
            throw EngineFailure.invalidOutput(error.localizedDescription)
        }
    }

    static func records(from output: String) throws -> [TriageRecord] {
        let decoder = JSONDecoder()
        return try output.split(whereSeparator: \.isNewline).map { line in
            do {
                return try decoder.decode(TriageRecord.self, from: Data(line.utf8))
            } catch {
                throw EngineFailure.invalidOutput(error.localizedDescription)
            }
        }
    }

    static func liveProbe(from output: String) throws -> LiveProbeReport {
        guard let data = output.trimmingCharacters(in: .whitespacesAndNewlines)
            .data(using: .utf8) else {
            throw EngineFailure.invalidOutput("empty live-probe response")
        }
        do {
            return try JSONDecoder().decode(LiveProbeReport.self, from: data)
        } catch {
            throw EngineFailure.invalidOutput(error.localizedDescription)
        }
    }
}

actor EngineService {
    private var process: Process?
    private var activeRunID: UUID?
    private var cancellationWaiters: [CheckedContinuation<Void, Never>] = []

    func run(_ command: EngineCommand) async throws -> EngineOutput {
        try Task.checkCancellation()
        if process != nil {
            throw EngineFailure.launchFailed("Another Mail-triage operation is already running.")
        }

        let task = Process()
        let stdoutPipe = Pipe()
        let stderrPipe = Pipe()
        task.executableURL = URL(fileURLWithPath: command.executable)
        task.arguments = command.arguments
        task.environment = command.environment
        task.standardOutput = stdoutPipe
        task.standardError = stderrPipe

        do {
            try task.run()
        } catch {
            throw EngineFailure.launchFailed(error.localizedDescription)
        }
        let runID = UUID()
        process = task
        activeRunID = runID

        let stdoutTask = Task.detached(priority: .userInitiated) {
            stdoutPipe.fileHandleForReading.readDataToEndOfFile()
        }
        let stderrTask = Task.detached(priority: .userInitiated) {
            stderrPipe.fileHandleForReading.readDataToEndOfFile()
        }
        let status = await Task.detached(priority: .userInitiated) {
            task.waitUntilExit()
            return task.terminationStatus
        }.value
        let stdout = await stdoutTask.value
        let stderr = await stderrTask.value
        let output = EngineOutput(
            status: status,
            stdout: String(decoding: stdout, as: UTF8.self),
            stderr: String(decoding: stderr, as: UTF8.self)
        )
        finish(runID: runID)
        return output
    }

    func cancel() async {
        guard let task = process else { return }
        await withCheckedContinuation { continuation in
            cancellationWaiters.append(continuation)
            Self.terminateTree(task, signal: SIGTERM)
            Task.detached(priority: .utility) {
                try? await Task.sleep(for: .seconds(2))
                if task.isRunning {
                    Self.terminateTree(task, signal: SIGKILL)
                }
            }
        }
    }

    private func finish(runID: UUID) {
        guard activeRunID == runID else { return }
        process = nil
        activeRunID = nil
        let waiters = cancellationWaiters
        cancellationWaiters.removeAll()
        waiters.forEach { $0.resume() }
    }

    nonisolated private static func terminateTree(_ task: Process, signal: Int32) {
        guard task.isRunning else { return }
        let descendants = Process()
        descendants.executableURL = URL(fileURLWithPath: "/usr/bin/pkill")
        descendants.arguments = [signal == SIGKILL ? "-KILL" : "-TERM", "-P", String(task.processIdentifier)]
        descendants.standardOutput = FileHandle.nullDevice
        descendants.standardError = FileHandle.nullDevice
        try? descendants.run()
        descendants.waitUntilExit()
        _ = Darwin.kill(task.processIdentifier, signal)
    }
}
