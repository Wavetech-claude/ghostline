import GhostlineKit
import SwiftUI

struct RootView: View {
    @Environment(SessionModel.self) private var session
    @Environment(AppLock.self) private var lock
    @Environment(\.scenePhase) private var scenePhase

    /// The app switcher's snapshot is covered on iPhone and iPad. A Mac window is inactive whenever another app has the
    /// focus, so covering it there would blank the window on every click elsewhere.
    private var covered: Bool {
        #if os(iOS)
        scenePhase != .active
        #else
        false
        #endif
    }

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
        .overlay {
            // the app lock covers a signed-in app; the app switcher never shows its numbers while the lock is on
            if session.isSignedIn && lock.enabled && (lock.locked || covered) {
                LockView()
            }
        }
        .onChange(of: scenePhase) { _, phase in
            switch phase {
            case .background: lock.didLeave()
            case .active: lock.didReturn()
            default: break
            }
        }
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
