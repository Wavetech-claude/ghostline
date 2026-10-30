import Foundation

/// The backend's `/version` and `/health` answers. The commit is what the deploy watch verifies.
public struct VersionInfo: Codable, Sendable, Equatable {
    public let version: String
    public let commit: String
    public let env: String?

    public init(version: String, commit: String, env: String?) {
        self.version = version
        self.commit = commit
        self.env = env
    }

    /// "0.1.0 (c21adb93)" — the first eight characters of the commit, as the pipeline prints it.
    public var shortDescription: String {
        let short = commit == "unknown" ? commit : String(commit.prefix(8))
        return "\(version) (\(short))"
    }
}

public struct HealthInfo: Codable, Sendable, Equatable {
    public let status: String
    public let version: String
    public let commit: String
    public let reason: String?

    public var isHealthy: Bool { status == "ok" }
}

/// The backend's own session, issued after Sign in with Apple.
public struct SessionInfo: Codable, Sendable, Equatable {
    public let sessionToken: String
    public let expiresAt: String
    public let sub: String
}

public struct Account: Codable, Sendable, Equatable {
    public let sub: String
    public let emailMasked: String
    public let createdAt: String
    public let lastSignInAt: String?
}

/// What the app sends to `/auth/apple`.
public struct AppleSignInPayload: Codable, Sendable, Equatable {
    public let identityToken: String
    public let platform: String

    public init(identityToken: String, platform: String) {
        self.identityToken = identityToken
        self.platform = platform
    }
}

public struct DeviceRegistration: Codable, Sendable, Equatable {
    public let token: String
    public let platform: String
    public let environment: String

    public init(token: String, platform: String, environment: String) {
        self.token = token
        self.platform = platform
        self.environment = environment
    }
}

public enum Platform {
    /// "ios" or "macos", as the backend spells them.
    public static var current: String {
        #if os(macOS)
        return "macos"
        #else
        return "ios"
        #endif
    }
}
