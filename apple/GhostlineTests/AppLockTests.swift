import Foundation
import Testing
@testable import Ghostline

/// The app lock's timing (the owner, 2026-11-01): locked at launch when on, again after more than a minute away.
@MainActor
@Suite struct AppLockTests {
    private func lock(stored: Bool? = nil) -> AppLock {
        let defaults = UserDefaults(suiteName: "AppLockTests-\(UUID().uuidString)")!
        if let stored { defaults.set(stored, forKey: AppLock.enabledKey) }
        return AppLock(defaults: defaults)
    }

    @Test func isOnAndLockedAtLaunchByDefault() {
        let l = lock()
        #expect(l.enabled && l.locked)
    }

    @Test func turnedOffItNeitherLocksNorLocksAgain() {
        let l = lock(stored: false)
        #expect(!l.enabled && !l.locked)
        let t = Date()
        l.didLeave(at: t)
        l.didReturn(at: t.addingTimeInterval(3600))
        #expect(!l.locked)
    }

    @Test func aShortAbsenceStaysOpenALongOneLocks() async {
        let l = lock()
        l.enabled = false          // unlocked, as after a successful Face ID
        l.enabled = true
        #expect(!l.locked)
        let t = Date()
        l.didLeave(at: t)
        l.didReturn(at: t.addingTimeInterval(AppLock.grace - 1))
        #expect(!l.locked)
        l.didLeave(at: t)
        l.didReturn(at: t.addingTimeInterval(AppLock.grace + 1))
        #expect(l.locked)
    }

    @Test func turningItOffUnlocks() {
        let l = lock()
        #expect(l.locked)
        l.enabled = false
        #expect(!l.locked)
    }

    @Test func theSwitchIsRemembered() {
        let defaults = UserDefaults(suiteName: "AppLockTests-\(UUID().uuidString)")!
        let l = AppLock(defaults: defaults)
        l.enabled = false
        #expect(AppLock(defaults: defaults).enabled == false)
    }
}
