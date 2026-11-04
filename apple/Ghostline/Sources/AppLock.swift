import GhostlineKit
import LocalAuthentication
import Observation
import SwiftUI

/// What asks the device. A stand-in takes its place in the unit tests.
@MainActor protocol LockPrompt {
    /// Why this device cannot ask; nil when it can.
    func unavailable() -> Error?
    /// Waits for the answer. Only `true` opens the lock.
    func ask() async throws -> Bool
    /// Ends a request still waiting: its `ask` throws.
    func cancel()
}

/// Face ID, Touch ID or the passcode, whichever the device has.
@MainActor final class DevicePrompt: LockPrompt {
    private var context: LAContext?

    func unavailable() -> Error? {
        var error: NSError?
        guard !LAContext().canEvaluatePolicy(.deviceOwnerAuthentication, error: &error) else { return nil }
        return error ?? LAError(.biometryNotAvailable)
    }

    func ask() async throws -> Bool {
        let context = LAContext()
        self.context = context
        return try await withCheckedThrowingContinuation { answer in
            context.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: "Unlock Ghostline") { @Sendable ok, error in
                if let error { answer.resume(throwing: error) } else { answer.resume(returning: ok) }
            }
        }
    }

    func cancel() {
        context?.invalidate()
        context = nil
    }
}

/// The app lock (the owner, 2026-11-01): Face ID, or Touch ID on a Mac, with the device passcode as the fallback.
/// It asks by itself once each time it locks, when the app opens and when it comes back after more than a minute away;
/// after that only the button asks, so a cancelled prompt does not come back again and again. On by default; a switch
/// in Settings. A device with no passcode cannot lock anything, so the lock turns itself off there and says so: it
/// never opens silently and never locks the owner out for good.
@MainActor @Observable
final class AppLock {
    static let enabledKey = "appLockEnabled"
    static let grace: TimeInterval = {
        #if DEBUG
        if let seconds = UITestFixture.lockGrace { return seconds }
        #endif
        return 60
    }()

    private let defaults: UserDefaults
    private let prompt: LockPrompt
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
    /// The next time the app is on screen, the lock asks by itself.
    private var armed: Bool
    /// It already asked again after the system took the prompt away. Only a real absence allows another, because the
    /// prompt itself makes the app inactive and active again: a system that keeps cancelling must not make a loop.
    private var askedAgain = false
    /// The newest request. An older one's late answer changes nothing.
    private var request = 0

    init(defaults: UserDefaults = .standard, prompt: LockPrompt = DevicePrompt()) {
        self.defaults = defaults
        self.prompt = prompt
        let on = defaults.object(forKey: Self.enabledKey) as? Bool ?? true
        enabled = on
        locked = on
        armed = on
    }

    /// The lock screen should ask by itself, if the app is on screen. A request made while the app leaves fails.
    var asksByItself: Bool { enabled && locked && armed }

    /// The app went to the background.
    func didLeave(at now: Date = .now) {
        leftAt = now
        askedAgain = false
    }

    /// The app is active again: locked when it was away longer than the grace.
    func didReturn(at now: Date = .now) {
        defer { leftAt = nil }
        guard enabled, let leftAt, now.timeIntervalSince(leftAt) > Self.grace else { return }
        locked = true
        armed = true
        message = nil
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

    /// The lock screen's own ask: once each time the app locks.
    func askByItself() async {
        guard asksByItself else { return }
        await unlock()
    }

    /// The button's ask. A new request replaces one still waiting, so one is alive at a time and the button never goes
    /// dead behind a request that did not end.
    func unlock() async {
        armed = false
        request += 1
        let mine = request
        prompt.cancel()
        message = nil
        needsPasscode = false
        if let reason = prompt.unavailable() {
            failed(reason)
            return
        }
        do {
            let opened = try await prompt.ask()
            guard mine == request, opened else { return }
            locked = false
            askedAgain = false
        } catch {
            guard mine == request else { return }
            failed(error)
        }
    }

    private func failed(_ error: Error) {
        switch (error as? LAError)?.code {
        case .systemCancel, .appCancel, .notInteractive:    // the system took the prompt away: not the owner's failure
            if askedAgain {
                message = "\(method) was interrupted. Tap Unlock to try again."
            } else {
                askedAgain = true
                armed = true
            }
        case .userCancel:
            message = "Cancelled. Tap Unlock to try again."
        case .authenticationFailed:
            message = "\(method) did not recognize you. Tap Unlock to try again."
        case .biometryLockout:
            message = "Too many tries. Unlock the device with its passcode, then try again."
        case .passcodeNotSet:
            needsPasscode = true
            message = "This device has no passcode, so the app lock cannot ask. Set a passcode in the device's "
                + "settings, or continue without the lock (Settings turns it back on)."
        default:
            message = error.localizedDescription
        }
    }
}

/// What covers the app while it is locked.
struct LockView: View {
    @Environment(AppLock.self) private var lock
    @Environment(\.scenePhase) private var scenePhase

    /// This screen also covers the app while it leaves, for the app switcher's snapshot: only an app on screen asks.
    private var asksNow: Bool { scenePhase == .active && lock.asksByItself }

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
        .onChange(of: asksNow, initial: true) { _, asks in
            if asks { Task { await lock.askByItself() } }
        }
    }
}
