import GhostlineKit
import LocalAuthentication
import Observation
import SwiftUI

/// The app lock (the owner, 2026-11-01): Face ID, or Touch ID on a Mac, with the device passcode as the fallback.
/// It asks when the app opens and when it comes back after more than a minute away. On by default; a switch in
/// Settings. A device with no passcode cannot lock anything, so the lock turns itself off there and says so: it never
/// opens silently and never locks the owner out for good.
@MainActor @Observable
final class AppLock {
    static let enabledKey = "appLockEnabled"
    static let grace: TimeInterval = 60

    private let defaults: UserDefaults
    var enabled: Bool {
        didSet {
            defaults.set(enabled, forKey: Self.enabledKey)
            if !enabled { locked = false }
        }
    }
    private(set) var locked: Bool
    private(set) var message: String?
    /// The device has no passcode: the lock cannot ask. The lock screen says so and offers to continue without it.
    private(set) var needsPasscode = false
    private var leftAt: Date?

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        let on = defaults.object(forKey: Self.enabledKey) as? Bool ?? true
        enabled = on
        locked = on
    }

    /// The app went to the background.
    func didLeave(at now: Date = .now) {
        leftAt = now
    }

    /// The app is active again: locked when it was away longer than the grace.
    func didReturn(at now: Date = .now) {
        defer { leftAt = nil }
        guard enabled, let leftAt, now.timeIntervalSince(leftAt) > Self.grace else { return }
        locked = true
    }

    /// The name of what unlocks it on this device, for labels.
    var method: String {
        let context = LAContext()
        _ = context.canEvaluatePolicy(.deviceOwnerAuthenticationWithBiometrics, error: nil)
        switch context.biometryType {
        case .faceID: return "Face ID"
        case .touchID: return "Touch ID"
        case .opticID: return "Optic ID"
        default: return "Passcode"
        }
    }

    func unlock() async {
        let context = LAContext()
        var error: NSError?
        guard context.canEvaluatePolicy(.deviceOwnerAuthentication, error: &error) else {
            if (error as? LAError)?.code == .passcodeNotSet {
                needsPasscode = true
                message = "This device has no passcode, so the app lock cannot ask. Set a passcode in the device's "
                    + "settings, or continue without the lock (Settings turns it back on)."
            } else {
                message = error?.localizedDescription ?? "The app lock cannot ask right now."
            }
            return
        }
        do {
            if try await context.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: "Unlock Ghostline") {
                locked = false
                message = nil
            }
        } catch {
            message = "Not unlocked. Try again."
        }
    }
}

/// What covers the app while it is locked.
struct LockView: View {
    @Environment(AppLock.self) private var lock

    var body: some View {
        VStack(spacing: 18) {
            Image(systemName: "lock.fill")
                .font(.system(size: 44, weight: .semibold))
                .foregroundStyle(Color.ghost)
                .accessibilityHidden(true)
            Text("Ghostline is locked").font(.title2.weight(.semibold))
            Button {
                Task { await lock.unlock() }
            } label: {
                Label("Unlock with \(lock.method)", systemImage: lock.method == "Touch ID" ? "touchid" : "faceid")
                    .padding(.horizontal, 8)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            if let message = lock.message {
                Text(message).font(.footnote).foregroundStyle(.secondary).multilineTextAlignment(.center)
            }
            if lock.needsPasscode {
                Button("Continue Without the Lock") { lock.enabled = false }
            }
        }
        .padding(32)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(.background)
        .task { await lock.unlock() }
    }
}
