import AuthenticationServices
import Foundation
import GhostlineKit
import Observation

/// The app's one source of truth about who is signed in and what the backend reports.
@MainActor
@Observable
final class SessionModel {
    enum State: Equatable {
        case checking
        case signedOut
        case signedIn(Account)
    }

    let client: APIClient
    let configuration: AppConfiguration
    private(set) var state: State = .checking
    private(set) var backend: VersionInfo?
    private(set) var lastError: String?
    private(set) var pushToken: String?

    init(client: APIClient, configuration: AppConfiguration) {
        self.client = client
        self.configuration = configuration
    }

    static func fromBundle() -> SessionModel {
        // A missing address is a build error, not a runtime surprise: the xcconfig fills it for every configuration.
        let cfg = (try? AppConfiguration.from(bundle: .main))
            ?? AppConfiguration(baseURL: URL(string: "https://ghostline-staging.atwavetech.com")!, environment: "staging")
        let tokens = KeychainTokenStore(service: "com.wavetech.ghostline.\(cfg.environment)")
        return SessionModel(client: APIClient(baseURL: cfg.baseURL, tokens: tokens), configuration: cfg)
    }

    var isSignedIn: Bool {
        if case .signedIn = state { return true }
        return false
    }

    /// At launch: read the backend's version, then the account if a session is stored.
    func start() async {
        await refreshBackend()
        guard await client.isSignedIn else {
            state = .signedOut
            return
        }
        await loadAccount()
    }

    func refreshBackend() async {
        do {
            backend = try await client.version()
            lastError = nil
        } catch {
            lastError = (error as? GhostlineError)?.userMessage ?? error.localizedDescription
        }
    }

    func loadAccount() async {
        do {
            state = .signedIn(try await client.account())
            lastError = nil
            await sendPushTokenIfKnown()
        } catch {
            state = .signedOut
            lastError = (error as? GhostlineError)?.userMessage ?? error.localizedDescription
        }
    }

    /// Sign in with Apple's answer → the backend's session.
    func signIn(with result: Result<ASAuthorization, any Error>) async {
        switch result {
        case .failure(let error):
            if (error as? ASAuthorizationError)?.code == .canceled { return }
            lastError = error.localizedDescription
        case .success(let authorization):
            guard let credential = authorization.credential as? ASAuthorizationAppleIDCredential,
                  let data = credential.identityToken, let token = String(data: data, encoding: .utf8) else {
                lastError = "Apple returned no identity token."
                return
            }
            do {
                _ = try await client.signIn(identityToken: token)
                await loadAccount()
            } catch {
                lastError = (error as? GhostlineError)?.userMessage ?? error.localizedDescription
            }
        }
    }

    func signOut() async {
        await client.signOut()
        state = .signedOut
    }

    // MARK: push

    func pushTokenReceived(_ token: String) async {
        pushToken = token
        await sendPushTokenIfKnown()
    }

    private func sendPushTokenIfKnown() async {
        guard let token = pushToken, isSignedIn else { return }
        #if DEBUG
        let environment = "sandbox"
        #else
        let environment = "production"
        #endif
        try? await client.registerDevice(token: token, environment: environment)
    }
}
