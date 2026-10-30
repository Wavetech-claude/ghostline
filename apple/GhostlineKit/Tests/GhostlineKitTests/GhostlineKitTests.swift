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

/// A URLProtocol that answers from a table, so the client is tested without a server.
final class FakeServer: URLProtocol, @unchecked Sendable {
    nonisolated(unsafe) static var answers: [String: (Int, String)] = [:]
    nonisolated(unsafe) static var seen: [URLRequest] = []

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        Self.seen.append(request)
        let path = request.url?.path() ?? ""
        let (code, body) = Self.answers[path] ?? (404, #"{"detail":"Not Found"}"#)
        let response = HTTPURLResponse(url: request.url!, statusCode: code, httpVersion: nil,
                                       headerFields: ["Content-Type": "application/json"])!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data(body.utf8))
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}

@Suite(.serialized) struct APIClientTests {
    private func client(tokens: TokenStore = InMemoryTokenStore()) -> APIClient {
        let cfg = URLSessionConfiguration.ephemeral
        cfg.protocolClasses = [FakeServer.self]
        FakeServer.seen = []
        return APIClient(baseURL: URL(string: "https://example.invalid")!, tokens: tokens, session: URLSession(configuration: cfg))
    }

    @Test func versionIsReadWithoutASession() async throws {
        FakeServer.answers = ["/version": (200, #"{"version":"0.1.0","commit":"abc","env":"staging"}"#)]
        let v = try await client().version()
        #expect(v.version == "0.1.0")
        #expect(FakeServer.seen.first?.value(forHTTPHeaderField: "Authorization") == nil)
    }

    @Test func signInStoresTheSessionAndAccountUsesIt() async throws {
        FakeServer.answers = ["/auth/apple": (200, #"{"session_token":"sess","expires_at":"x","sub":"001"}"#),
                              "/account": (200, #"{"sub":"001","email_masked":"q…@example.com","created_at":"x","last_sign_in_at":null}"#)]
        let tokens = InMemoryTokenStore()
        let c = client(tokens: tokens)
        let s = try await c.signIn(identityToken: "apple-token")
        #expect(s.sub == "001" && tokens.read() == "sess")
        let sent = try #require(FakeServer.seen.first?.httpBody ?? FakeServer.seen.first?.httpBodyStream.map { _ in Data() })
        _ = sent
        let a = try await c.account()
        #expect(a.sub == "001")
        #expect(FakeServer.seen.last?.value(forHTTPHeaderField: "Authorization") == "Bearer sess")
    }

    @Test func aRefusedSignInIsAnHttpError() async {
        FakeServer.answers = ["/auth/apple": (401, #"{"detail":"sign-in refused"}"#)]
        await #expect(throws: GhostlineError.http(401, "sign-in refused")) {
            _ = try await client().signIn(identityToken: "x")
        }
    }

    @Test func aCallWithoutASessionNeverLeavesTheApp() async {
        FakeServer.answers = [:]
        await #expect(throws: GhostlineError.notSignedIn) {
            _ = try await client().account()
        }
        #expect(FakeServer.seen.isEmpty)
    }

    @Test func a401ClearsTheSession() async {
        FakeServer.answers = ["/account": (401, #"{"detail":"session not valid"}"#)]
        let tokens = InMemoryTokenStore(token: "old")
        _ = try? await client(tokens: tokens).account()
        #expect(tokens.read() == nil)
    }

    @Test func healthReadsA503Body() async throws {
        FakeServer.answers = ["/health": (503, #"{"status":"unhealthy","version":"0.1.0","commit":"abc","reason":"migrations pending: x"}"#)]
        let h = try await client().health()
        #expect(!h.isHealthy && h.reason == "migrations pending: x")
    }
}
