import Foundation
import GhostlineKit
import Testing
@testable import Ghostline

/// The app's state machine, driven by a fake backend.
@MainActor
@Suite struct SessionModelTests {
    private func model(signedIn: Bool, answers: [String: (Int, String)]) -> SessionModel {
        AppFakeServer.answers = answers
        let cfg = URLSessionConfiguration.ephemeral
        cfg.protocolClasses = [AppFakeServer.self]
        let tokens = InMemoryTokenStore(token: signedIn ? "sess" : nil)
        let client = APIClient(baseURL: URL(string: "https://example.invalid")!, tokens: tokens,
                               session: URLSession(configuration: cfg))
        return SessionModel(client: client, configuration: AppConfiguration(baseURL: client.baseURL, environment: "test"))
    }

    @Test func startsSignedOutWithoutASession() async {
        let m = model(signedIn: false, answers: ["/version": (200, #"{"version":"0.1.0","commit":"abc","env":"test"}"#)])
        await m.start()
        #expect(m.state == .signedOut)
        #expect(m.backend?.version == "0.1.0")
    }

    @Test func startsSignedInWithAStoredSession() async {
        let m = model(signedIn: true, answers: [
            "/version": (200, #"{"version":"0.1.0","commit":"abc","env":"test"}"#),
            "/account": (200, #"{"sub":"001","email_masked":"q…@example.com","created_at":"2026-10-30T00:00:00+00:00","last_sign_in_at":null}"#)])
        await m.start()
        guard case .signedIn(let account) = m.state else { Issue.record("not signed in"); return }
        #expect(account.sub == "001")
    }

    @Test func aDeadSessionFallsBackToSignedOut() async {
        let m = model(signedIn: true, answers: [
            "/version": (200, #"{"version":"0.1.0","commit":"abc","env":"test"}"#),
            "/account": (401, #"{"detail":"session not valid"}"#)])
        await m.start()
        #expect(m.state == .signedOut)
        #expect(m.lastError == "Sign in again.")
    }

    @Test func anUnreachableBackendIsSaidNotHidden() async {
        let m = model(signedIn: false, answers: [:])
        await m.start()
        #expect(m.backend == nil)
        #expect(m.lastError?.isEmpty == false)
    }
}

final class AppFakeServer: URLProtocol, @unchecked Sendable {
    nonisolated(unsafe) static var answers: [String: (Int, String)] = [:]

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
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
