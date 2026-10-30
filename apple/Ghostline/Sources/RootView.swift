import GhostlineKit
import SwiftUI

struct RootView: View {
    @Environment(SessionModel.self) private var session

    var body: some View {
        Group {
            switch session.state {
            case .checking:
                ProgressView("Connecting…")
                    .task { await session.start() }
            case .signedOut:
                SignInView()
            case .signedIn:
                MainView()
            }
        }
        .animation(.smooth, value: session.isSignedIn)
    }
}

/// One line about the backend, shown under the sign-in and in Settings: version, commit and environment.
struct BackendLine: View {
    @Environment(SessionModel.self) private var session

    var body: some View {
        HStack(spacing: 6) {
            Image(systemName: session.backend == nil ? "bolt.slash" : "bolt.horizontal")
                .foregroundStyle(.secondary)
            if let backend = session.backend {
                Text("Backend \(backend.shortDescription) · \(session.configuration.environment)")
            } else if let error = session.lastError {
                Text(error)
            } else {
                Text("Backend not reached yet")
            }
        }
        .font(.footnote)
        .foregroundStyle(.secondary)
        .accessibilityElement(children: .combine)
    }
}
