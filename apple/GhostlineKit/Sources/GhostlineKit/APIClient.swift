import Foundation

public enum GhostlineError: Error, Equatable, Sendable {
    case http(Int, String)
    case notSignedIn
    case decoding(String)
    case transport(String)

    public var userMessage: String {
        switch self {
        case .http(401, _): return "Sign in again."
        case .http(let code, let detail): return "The server answered \(code)\(detail.isEmpty ? "" : ": \(detail)")."
        case .notSignedIn: return "Sign in first."
        case .decoding: return "The server's answer could not be read."
        case .transport(let why): return "Could not reach Ghostline (\(why))."
        }
    }
}

/// The one way the app talks to the backend. Every call except `version`, `health` and `signIn` carries the session.
public actor APIClient {
    public nonisolated let baseURL: URL
    private let session: URLSession
    private let tokens: TokenStore
    private let decoder = GhostlineJSON.decoder()
    private let encoder = GhostlineJSON.encoder()

    public init(baseURL: URL, tokens: TokenStore, session: URLSession = .shared) {
        self.baseURL = baseURL
        self.tokens = tokens
        self.session = session
    }

    public var isSignedIn: Bool { tokens.read() != nil }

    // MARK: public, unauthenticated

    public func version() async throws -> VersionInfo {
        try await get("/version", authenticated: false)
    }

    public func health() async throws -> HealthInfo {
        try await get("/health", authenticated: false, acceptErrorBodies: true)
    }

    public func signIn(identityToken: String) async throws -> SessionInfo {
        let payload = AppleSignInPayload(identityToken: identityToken, platform: Platform.current)
        let info: SessionInfo = try await post("/auth/apple", body: payload, authenticated: false)
        tokens.write(info.sessionToken)
        return info
    }

    // MARK: the owner's session

    public func account() async throws -> Account {
        try await get("/account", authenticated: true)
    }

    public func signOut() async {
        _ = try? await post("/auth/sign-out", body: Empty(), authenticated: true) as Ack
        tokens.clear()
    }

    public func registerDevice(token: String, environment: String) async throws {
        let body = DeviceRegistration(token: token, platform: Platform.current, environment: environment)
        _ = try await post("/devices", body: body, authenticated: true) as Ack
    }

    // MARK: plumbing

    func request(_ path: String, method: String, authenticated: Bool, query: [URLQueryItem] = []) throws -> URLRequest {
        var url = baseURL.appending(path: path)
        if !query.isEmpty { url.append(queryItems: query) }
        var r = URLRequest(url: url)
        r.httpMethod = method
        r.setValue("application/json", forHTTPHeaderField: "Accept")
        r.timeoutInterval = 20
        if authenticated {
            guard let token = tokens.read() else { throw GhostlineError.notSignedIn }
            r.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        return r
    }

    /// (bytes, content type) of a successful answer; the error body's `detail` on anything else.
    func sendRaw(_ r: URLRequest, acceptErrorBodies: Bool = false) async throws -> (Data, String) {
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: r)
        } catch {
            throw GhostlineError.transport(error.localizedDescription)
        }
        let http = response as? HTTPURLResponse
        let code = http?.statusCode ?? 0
        if code == 401 { tokens.clear() }
        if !(200..<300).contains(code) && !acceptErrorBodies {
            let detail = (try? decoder.decode(Problem.self, from: data))?.detail ?? ""
            throw GhostlineError.http(code, detail)
        }
        return (data, http?.value(forHTTPHeaderField: "Content-Type") ?? "application/octet-stream")
    }

    func send<T: Decodable>(_ r: URLRequest, acceptErrorBodies: Bool = false) async throws -> T {
        let (data, _) = try await sendRaw(r, acceptErrorBodies: acceptErrorBodies)
        do {
            return try decoder.decode(T.self, from: data)
        } catch {
            throw GhostlineError.decoding(String(describing: error))
        }
    }

    func get<T: Decodable>(_ path: String, query: [URLQueryItem] = [], authenticated: Bool,
                           acceptErrorBodies: Bool = false) async throws -> T {
        try await send(try request(path, method: "GET", authenticated: authenticated, query: query),
                       acceptErrorBodies: acceptErrorBodies)
    }

    func getBytes(_ path: String) async throws -> (Data, String) {
        try await sendRaw(try request(path, method: "GET", authenticated: true))
    }

    func post<B: Encodable, T: Decodable>(_ path: String, body: B, authenticated: Bool) async throws -> T {
        try await sendJSON(path, method: "POST", body: body, authenticated: authenticated)
    }

    func put<B: Encodable, T: Decodable>(_ path: String, body: B, authenticated: Bool) async throws -> T {
        try await sendJSON(path, method: "PUT", body: body, authenticated: authenticated)
    }

    func postRaw<T: Decodable>(_ path: String, query: [URLQueryItem], body: Data, contentType: String) async throws -> T {
        var r = try request(path, method: "POST", authenticated: true, query: query)
        r.setValue(contentType, forHTTPHeaderField: "Content-Type")
        r.httpBody = body
        return try await send(r)
    }

    private func sendJSON<B: Encodable, T: Decodable>(_ path: String, method: String, body: B,
                                                      authenticated: Bool) async throws -> T {
        var r = try request(path, method: method, authenticated: authenticated)
        r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        r.httpBody = try encoder.encode(body)
        return try await send(r)
    }
}

/// FastAPI's error body, and the small answers the client reads but never shows.
private struct Problem: Decodable { let detail: String? }
private struct Empty: Codable {}
private struct Ack: Decodable { let ok: Bool? }
