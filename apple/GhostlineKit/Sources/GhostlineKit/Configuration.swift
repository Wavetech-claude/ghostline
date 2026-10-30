import Foundation

/// Where the app points. Never a hard-coded address: Debug builds read the staging address and Release builds the
/// production address from Info.plist, which the build configuration (Config/*.xcconfig) fills in.
public struct AppConfiguration: Sendable, Equatable {
    public let baseURL: URL
    public let environment: String

    public init(baseURL: URL, environment: String) {
        self.baseURL = baseURL
        self.environment = environment
    }

    public enum Error: Swift.Error, Equatable {
        case missingKey(String)
        case badURL(String)
    }

    /// Reads `GHOSTLINE_API_BASE_URL` and `GHOSTLINE_ENVIRONMENT` from an Info.plist dictionary.
    public static func from(info: [String: Any]) throws -> AppConfiguration {
        guard let raw = info["GHOSTLINE_API_BASE_URL"] as? String, !raw.isEmpty else {
            throw Error.missingKey("GHOSTLINE_API_BASE_URL")
        }
        guard let url = URL(string: raw), let scheme = url.scheme, scheme == "https", url.host() != nil else {
            throw Error.badURL(raw)
        }
        let env = (info["GHOSTLINE_ENVIRONMENT"] as? String).flatMap { $0.isEmpty ? nil : $0 } ?? "staging"
        return AppConfiguration(baseURL: url, environment: env)
    }

    public static func from(bundle: Bundle) throws -> AppConfiguration {
        try from(info: bundle.infoDictionary ?? [:])
    }
}
