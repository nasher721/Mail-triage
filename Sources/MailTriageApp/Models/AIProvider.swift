import Foundation

/// The AI systems Mail Triage can route messages through.
///
/// This mirrors the registry in `email_triage/providers.py`. The engine is the
/// authority: the app only selects a provider and passes its endpoint, model,
/// and key through the environment.
enum AIProvider: String, CaseIterable, Identifiable, Codable {
    case ollama
    case lmstudio
    case llamacpp
    case opencode
    case openai
    case anthropic
    case openrouter
    case azureOpenAI = "azure-openai"
    case gemini
    case groq
    case mistral
    case deepseek
    case together
    case xai
    case custom

    var id: String { rawValue }

    /// Providers whose inference runs on the operator's own machine.
    static var localProviders: [AIProvider] { allCases.filter(\.isLocal) }

    /// Hosted providers, which require an explicit external-AI approval.
    static var hostedProviders: [AIProvider] { allCases.filter { !$0.isLocal } }

    var title: String {
        switch self {
        case .ollama: "Ollama"
        case .lmstudio: "LM Studio"
        case .llamacpp: "llama.cpp server"
        case .opencode: "OpenCode"
        case .openai: "OpenAI (ChatGPT)"
        case .anthropic: "Anthropic (Claude)"
        case .openrouter: "OpenRouter"
        case .azureOpenAI: "Azure OpenAI"
        case .gemini: "Google Gemini"
        case .groq: "Groq"
        case .mistral: "Mistral"
        case .deepseek: "DeepSeek"
        case .together: "Together AI"
        case .xai: "xAI (Grok)"
        case .custom: "Custom endpoint"
        }
    }

    var detail: String {
        switch self {
        case .ollama: "Local models over Ollama. Nothing leaves this Mac."
        case .lmstudio: "LM Studio's local OpenAI-compatible server."
        case .llamacpp: "A local llama-server endpoint."
        case .opencode: "A local OpenCode server (`opencode serve`)."
        case .openai: "GPT models through the OpenAI Responses API."
        case .anthropic: "Claude models through the Messages API."
        case .openrouter: "One key, many upstream vendors."
        case .azureOpenAI: "An Azure OpenAI deployment in your tenant."
        case .gemini: "Gemini through Google's OpenAI-compatible endpoint."
        case .groq: "Groq's hosted open models."
        case .mistral: "Mistral's hosted models."
        case .deepseek: "DeepSeek's hosted models."
        case .together: "Together AI's hosted open models."
        case .xai: "Grok models from xAI."
        case .custom: "Any gateway that speaks /chat/completions."
        }
    }

    var symbol: String {
        switch self {
        case .ollama, .lmstudio, .llamacpp, .opencode: "desktopcomputer"
        case .custom: "slider.horizontal.3"
        default: "cloud"
        }
    }

    /// True when inference stays on this machine, so no approval is needed.
    var isLocal: Bool {
        switch self {
        case .ollama, .lmstudio, .llamacpp, .opencode: true
        default: false
        }
    }

    var defaultBaseURL: String {
        switch self {
        case .ollama: "http://127.0.0.1:11434"
        case .lmstudio: "http://127.0.0.1:1234/v1"
        case .llamacpp: "http://127.0.0.1:8080/v1"
        case .opencode: "http://127.0.0.1:4096/v1"
        case .openai: "https://api.openai.com/v1"
        case .anthropic: "https://api.anthropic.com"
        case .openrouter: "https://openrouter.ai/api/v1"
        case .azureOpenAI: ""
        case .gemini: "https://generativelanguage.googleapis.com/v1beta/openai"
        case .groq: "https://api.groq.com/openai/v1"
        case .mistral: "https://api.mistral.ai/v1"
        case .deepseek: "https://api.deepseek.com/v1"
        case .together: "https://api.together.xyz/v1"
        case .xai: "https://api.x.ai/v1"
        case .custom: ""
        }
    }

    var defaultModel: String {
        switch self {
        case .ollama: "qwen3:8b"
        case .lmstudio, .llamacpp: "local-model"
        case .opencode: "opencode/default"
        case .openai: "gpt-4o"
        case .anthropic: "claude-sonnet-4-5"
        case .openrouter: "anthropic/claude-sonnet-4.5"
        case .azureOpenAI: "gpt-4o"
        case .gemini: "gemini-2.5-flash"
        case .groq: "llama-3.3-70b-versatile"
        case .mistral: "mistral-large-latest"
        case .deepseek: "deepseek-chat"
        case .together: "meta-llama/Llama-3.3-70B-Instruct-Turbo"
        case .xai: "grok-4"
        case .custom: ""
        }
    }

    /// Models offered in the picker. The field stays editable for anything else.
    var suggestedModels: [String] {
        switch self {
        case .ollama: ["qwen3:14b", "qwen3:8b", "qwen2.5:14b", "llama3.1:8b", "qwen3:4b"]
        case .openai: ["gpt-5", "gpt-5-mini", "gpt-5-nano", "gpt-4.1", "gpt-4o", "gpt-4o-mini", "o4-mini"]
        case .anthropic: ["claude-sonnet-4-5", "claude-opus-4-1", "claude-haiku-4-5"]
        case .openrouter: [
            "anthropic/claude-sonnet-4.5",
            "openai/gpt-4o",
            "google/gemini-2.5-flash",
            "meta-llama/llama-3.3-70b-instruct"
        ]
        case .gemini: ["gemini-2.5-flash", "gemini-2.5-pro"]
        case .groq: ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]
        case .mistral: ["mistral-large-latest", "mistral-small-latest"]
        case .deepseek: ["deepseek-chat", "deepseek-reasoner"]
        case .together: ["meta-llama/Llama-3.3-70B-Instruct-Turbo"]
        case .xai: ["grok-4", "grok-3-mini"]
        case .lmstudio, .llamacpp, .opencode, .azureOpenAI, .custom: []
        }
    }

    /// The engine refuses to start without a key for these providers.
    var requiresAPIKey: Bool {
        switch self {
        case .ollama, .lmstudio, .llamacpp, .opencode, .custom: false
        default: true
        }
    }

    /// The environment variable the engine reads when no key is stored in the app.
    var apiKeyEnvironmentVariable: String? {
        switch self {
        case .openai: "OPENAI_API_KEY"
        case .anthropic: "ANTHROPIC_API_KEY"
        case .openrouter: "OPENROUTER_API_KEY"
        case .azureOpenAI: "AZURE_OPENAI_API_KEY"
        case .gemini: "GEMINI_API_KEY"
        case .groq: "GROQ_API_KEY"
        case .mistral: "MISTRAL_API_KEY"
        case .deepseek: "DEEPSEEK_API_KEY"
        case .together: "TOGETHER_API_KEY"
        case .xai: "XAI_API_KEY"
        case .ollama, .lmstudio, .llamacpp, .opencode, .custom: nil
        }
    }

    /// Where a key is obtained, shown next to the key field.
    var credentialHint: String {
        switch self {
        case .ollama: "No key needed. Run `ollama serve` and pull a model."
        case .lmstudio: "No key needed. Start the LM Studio local server."
        case .llamacpp: "No key needed. Start llama-server with an OpenAI-compatible route."
        case .opencode: "No key needed. Start `opencode serve` and set the base URL."
        case .openai: "Create a key at platform.openai.com."
        case .anthropic: "Create a key at console.anthropic.com."
        case .openrouter: "Create a key at openrouter.ai/keys."
        case .azureOpenAI: "Use the deployment key from your Azure resource."
        case .gemini: "Create a key at aistudio.google.com."
        case .groq: "Create a key at console.groq.com."
        case .mistral: "Create a key at console.mistral.ai."
        case .deepseek: "Create a key at platform.deepseek.com."
        case .together: "Create a key at api.together.xyz."
        case .xai: "Create a key at console.x.ai."
        case .custom: "Optional. Sent as a bearer token when set."
        }
    }

    /// Providers that publish an installed-model list the app can read cheaply.
    /// OpenAI's `/models` catalogue is the authoritative, account-specific list
    /// of available GPT models. The app fetches it without running inference.
    var listsModels: Bool { isLocal || self == .openai }

    /// The relative path used to list models, when the provider offers one.
    var modelListPath: String { self == .ollama ? "/api/tags" : "/models" }

    /// Only local providers may talk to a loopback address; hosted ones must not.
    func validate(baseURL: String) -> String? {
        let trimmed = baseURL.trimmingCharacters(in: .whitespaces)
        if trimmed.isEmpty {
            return "\(title) needs a base URL."
        }
        if isLocal, !EnginePaths.isLoopbackHTTPURL(trimmed) {
            return "\(title) must use a loopback HTTP address such as \(defaultBaseURL)."
        }
        if self == .custom {
            // A self-hosted gateway may be either loopback or TLS-protected.
            guard EnginePaths.isLoopbackHTTPURL(trimmed)
                || trimmed.lowercased().hasPrefix("https://") else {
                return "A custom endpoint must be loopback HTTP or an https:// URL."
            }
            return nil
        }
        if !isLocal, !trimmed.lowercased().hasPrefix("https://") {
            return "\(title) must use an https:// endpoint."
        }
        return nil
    }
}
