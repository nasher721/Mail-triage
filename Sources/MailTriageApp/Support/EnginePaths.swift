import Foundation

enum EnginePaths {
    static var developmentRoot: URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
    }

    static func resource(named name: String) -> URL? {
        if let bundled = Bundle.main.resourceURL?.appendingPathComponent(name),
           FileManager.default.isReadableFile(atPath: bundled.path) {
            return bundled
        }
        let development = developmentRoot.appendingPathComponent(name)
        return FileManager.default.isReadableFile(atPath: development.path) ? development : nil
    }

    static func pythonExecutable(
        requiresPlaywright: Bool = false,
        fileManager: FileManager = .default,
        environment: [String: String] = ProcessInfo.processInfo.environment
    ) -> String? {
        var candidates: [String] = []
        if let configured = environment["MAIL_TRIAGE_PYTHON"], !configured.isEmpty {
            candidates.append(configured)
        }
        candidates.append(developmentRoot.appendingPathComponent(".venv/bin/python3").path)
        candidates.append(contentsOf: [
            "/opt/homebrew/bin/python3",
            "/usr/local/bin/python3",
            "/Library/Frameworks/Python.framework/Versions/Current/bin/python3",
            "/usr/bin/python3",
        ])

        var seen = Set<String>()
        return candidates.first { candidate in
            guard seen.insert(candidate).inserted,
                  fileManager.isExecutableFile(atPath: candidate) else {
                return false
            }
            return supportsRuntime(candidate, requiresPlaywright: requiresPlaywright)
        }
    }

    static func supportsRuntime(_ executable: String, requiresPlaywright: Bool) -> Bool {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: executable)
        var probe = "import sys; assert sys.version_info >= (3, 11)"
        if requiresPlaywright {
            probe += "; import playwright.sync_api"
        }
        process.arguments = ["-c", probe]
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        do {
            try process.run()
            process.waitUntilExit()
            return process.terminationStatus == 0
        } catch {
            return false
        }
    }

    static func isLoopbackHTTPURL(_ value: String) -> Bool {
        guard let components = URLComponents(string: value),
              components.scheme?.lowercased() == "http",
              components.user == nil,
              components.password == nil,
              let host = components.host?.lowercased() else {
            return false
        }
        return host == "127.0.0.1" || host == "localhost" || host == "::1" || host == "[::1]"
    }

    static var applicationSupportDirectory: URL {
        let base = FileManager.default.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        ).first ?? FileManager.default.homeDirectoryForCurrentUser
        return base.appendingPathComponent("MailTriage", isDirectory: true)
    }

    static var defaultOutputDirectory: URL {
        applicationSupportDirectory.appendingPathComponent("Results", isDirectory: true)
    }

    static var edgeProfileDirectory: URL {
        applicationSupportDirectory.appendingPathComponent("EdgeProfile", isDirectory: true)
    }

    static var learningPreferencesFile: URL {
        applicationSupportDirectory
            .appendingPathComponent("Learning", isDirectory: true)
            .appendingPathComponent("preferences.json")
    }
}
