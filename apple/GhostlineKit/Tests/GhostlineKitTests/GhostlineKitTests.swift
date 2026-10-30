import Foundation
import Testing
@testable import GhostlineKit

@Suite struct ConfigurationTests {
    @Test func readsTheAddressAndEnvironmentFromInfoPlist() throws {
        let cfg = try AppConfiguration.from(info: ["GHOSTLINE_API_BASE_URL": "https://ghostline-staging.atwavetech.com",
                                                   "GHOSTLINE_ENVIRONMENT": "staging"])
        #expect(cfg.baseURL.absoluteString == "https://ghostline-staging.atwavetech.com")
        #expect(cfg.environment == "staging")
    }

    @Test func refusesAMissingOrPlainHttpAddress() {
        #expect(throws: AppConfiguration.Error.missingKey("GHOSTLINE_API_BASE_URL")) {
            try AppConfiguration.from(info: [:])
        }
        #expect(throws: AppConfiguration.Error.badURL("http://ghostline.atwavetech.com")) {
            try AppConfiguration.from(info: ["GHOSTLINE_API_BASE_URL": "http://ghostline.atwavetech.com"])
        }
    }
}

@Suite struct ModelTests {
    @Test func decodesSnakeCaseAnswers() throws {
        let v = try GhostlineJSON.decoder().decode(VersionInfo.self, from: Data(#"{"version":"0.1.0","commit":"c21adb937fe7aaaaaaaaaaaaaaaaaaaaaaaaaaaa","env":"staging"}"#.utf8))
        #expect(v.shortDescription == "0.1.0 (c21adb93)")
        let s = try GhostlineJSON.decoder().decode(SessionInfo.self, from: Data(#"{"session_token":"t","expires_at":"2026-11-29T00:00:00+00:00","sub":"001"}"#.utf8))
        #expect(s.sessionToken == "t" && s.sub == "001")
        let a = try GhostlineJSON.decoder().decode(Account.self, from: Data(#"{"sub":"001","email_masked":"qa…@example.com","created_at":"x","last_sign_in_at":null}"#.utf8))
        #expect(a.emailMasked == "qa…@example.com" && a.lastSignInAt == nil)
    }

    @Test func encodesThePayloadInSnakeCase() throws {
        let data = try GhostlineJSON.encoder().encode(AppleSignInPayload(identityToken: "abc", platform: "ios"))
        let json = try #require(try JSONSerialization.jsonObject(with: data) as? [String: String])
        #expect(json == ["identity_token": "abc", "platform": "ios"])
    }

    @Test func unknownCommitStaysUnknown() {
        #expect(VersionInfo(version: "0.1.0", commit: "unknown", env: nil).shortDescription == "0.1.0 (unknown)")
    }
}

@Suite struct TokenStoreTests {
    @Test func memoryStoreKeepsAndClears() {
        let store = InMemoryTokenStore()
        #expect(store.read() == nil)
        store.write("abc")
        #expect(store.read() == "abc")
        store.clear()
        #expect(store.read() == nil)
    }
}

@Suite struct MoneyTests {
    @Test func formatsDollarsAndShares() {
        #expect(Money.format(Decimal(string: "1234.5")!).contains("1,234.50"))
        #expect(Money.percent(Decimal(string: "0.015")!).contains("1.5"))
    }
}

/// A URLProtocol that answers from a table per fake host, so the client is tested without a server and suites can
/// run in parallel (each client gets its own host).
final class FakeServer: URLProtocol, @unchecked Sendable {
    private static let lock = NSLock()
    nonisolated(unsafe) private static var tables: [String: [String: (Int, String)]] = [:]
    nonisolated(unsafe) private static var requests: [String: [URLRequest]] = [:]

    static func host(answers: [String: (Int, String)]) -> String {
        let h = "\(UUID().uuidString.lowercased()).invalid"
        lock.withLock { tables[h] = answers; requests[h] = [] }
        return h
    }

    static func set(_ host: String, answers: [String: (Int, String)]) {
        lock.withLock { tables[host] = answers }
    }

    static func seen(_ host: String) -> [URLRequest] {
        lock.withLock { requests[host] ?? [] }
    }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        let host = request.url?.host() ?? ""
        let path = request.url?.path() ?? ""
        let (code, body) = Self.lock.withLock {
            Self.requests[host, default: []].append(request)
            return Self.tables[host]?[path] ?? (404, #"{"detail":"Not Found"}"#)
        }
        let response = HTTPURLResponse(url: request.url!, statusCode: code, httpVersion: nil,
                                       headerFields: ["Content-Type": "application/json"])!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data(body.utf8))
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}

/// (client, host): a client on its own fake host with the given answers.
func fakeClient(answers: [String: (Int, String)], tokens: TokenStore = InMemoryTokenStore()) -> (APIClient, String) {
    let host = FakeServer.host(answers: answers)
    let cfg = URLSessionConfiguration.ephemeral
    cfg.protocolClasses = [FakeServer.self]
    return (APIClient(baseURL: URL(string: "https://\(host)")!, tokens: tokens, session: URLSession(configuration: cfg)), host)
}

@Suite struct APIClientTests {
    @Test func versionIsReadWithoutASession() async throws {
        let (c, host) = fakeClient(answers: ["/version": (200, #"{"version":"0.1.0","commit":"abc","env":"staging"}"#)])
        let v = try await c.version()
        #expect(v.version == "0.1.0")
        #expect(FakeServer.seen(host).first?.value(forHTTPHeaderField: "Authorization") == nil)
    }

    @Test func signInStoresTheSessionAndAccountUsesIt() async throws {
        let tokens = InMemoryTokenStore()
        let (c, host) = fakeClient(answers: [
            "/auth/apple": (200, #"{"session_token":"sess","expires_at":"x","sub":"001"}"#),
            "/account": (200, #"{"sub":"001","email_masked":"q…@example.com","created_at":"x","last_sign_in_at":null}"#)],
            tokens: tokens)
        let s = try await c.signIn(identityToken: "apple-token")
        #expect(s.sub == "001" && tokens.read() == "sess")
        let a = try await c.account()
        #expect(a.sub == "001")
        #expect(FakeServer.seen(host).last?.value(forHTTPHeaderField: "Authorization") == "Bearer sess")
    }

    @Test func aRefusedSignInIsAnHttpError() async {
        let (c, _) = fakeClient(answers: ["/auth/apple": (401, #"{"detail":"sign-in refused"}"#)])
        await #expect(throws: GhostlineError.http(401, "sign-in refused")) {
            _ = try await c.signIn(identityToken: "x")
        }
    }

    @Test func aCallWithoutASessionNeverLeavesTheApp() async {
        let (c, host) = fakeClient(answers: [:])
        await #expect(throws: GhostlineError.notSignedIn) {
            _ = try await c.account()
        }
        #expect(FakeServer.seen(host).isEmpty)
    }

    @Test func a401ClearsTheSession() async {
        let tokens = InMemoryTokenStore(token: "old")
        let (c, _) = fakeClient(answers: ["/account": (401, #"{"detail":"session not valid"}"#)], tokens: tokens)
        _ = try? await c.account()
        #expect(tokens.read() == nil)
    }

    @Test func healthReadsA503Body() async throws {
        let (c, _) = fakeClient(answers: ["/health": (503, #"{"status":"unhealthy","version":"0.1.0","commit":"abc","reason":"migrations pending: x"}"#)])
        let h = try await c.health()
        #expect(!h.isHealthy && h.reason == "migrations pending: x")
    }
}
